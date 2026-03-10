"""autograph.results

Submission + registered output helpers:
- submit API payloads (/prompt)
- poll history (/history/<id>)
- download outputs (/view)
- ergonomic dict/list subclasses: SubmissionResult, FilesResult, ImagesResult

Network interactions are explicit and opt-in; this module is stdlib-first. Pillow is an
optional dependency used only for image transcoding/decoding.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple, Union

from .defaults import (
    DEFAULT_FETCH_IMAGES,
    DEFAULT_GET_IMAGES_INCLUDE_IMAGE_BYTES,
    DEFAULT_GET_IMAGES_WAIT,
    DEFAULT_HTTP_TIMEOUT_S,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_POLL_QUEUE,
    DEFAULT_QUEUE_POLL_INTERVAL_S,
    DEFAULT_SUBMIT_CLIENT_ID,
    DEFAULT_SUBMIT_WAIT,
    DEFAULT_WAIT_TIMEOUT_S,
)
from . import net as _net

logger = logging.getLogger(__name__)


# Optional dependency: Pillow (PIL). Used only for PNG->JPEG transcoding when saving images.
try:
    from PIL import Image as _PIL_Image  # type: ignore
except Exception:
    _PIL_Image = None


def _sanitize_api_prompt(
    api_prompt: Dict[str, Any],
    *,
    node_info: Optional[Dict[str, Any]] = None,
    drop_unknown: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Sanitize an API-format prompt before submitting to ComfyUI.

    - If node_info is provided (recommended), drops any nodes whose class_type is
      not present in node_info (because ComfyUI cannot execute unknown node types).

    This is intentionally conservative: in absence of node_info, we do NOT attempt to
    infer unknown nodes.
    """
    if drop_unknown is None:
        drop_unknown = bool(isinstance(node_info, dict) and node_info)

    out: Dict[str, Any] = {}
    for nid, node in api_prompt.items():
        nid_s = str(nid)  # ComfyUI API uses string keys.
        if isinstance(node, dict):
            ct = node.get("class_type")
            if drop_unknown and isinstance(ct, str):
                if not (isinstance(node_info, dict) and ct in node_info):
                    continue
        out[nid_s] = node
    return out


def _submit_impl(
    prompt: Union["ApiFlow", Dict[str, Any]],
    server_url: Optional[str] = None,
    *,
    client_id: str = DEFAULT_SUBMIT_CLIENT_ID,
    extra: Optional[Dict[str, Any]] = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT_S,
    wait: bool = DEFAULT_SUBMIT_WAIT,
    poll_interval: float = DEFAULT_POLL_INTERVAL_S,
    wait_timeout: int = DEFAULT_WAIT_TIMEOUT_S,
    poll_queue: Optional[bool] = None,
    queue_poll_interval: Optional[float] = None,
    fetch_outputs: bool = DEFAULT_FETCH_IMAGES,
    output_path: Optional[Union[str, Path]] = None,
    include_bytes: bool = False,
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> "SubmissionResult":
    """
    Submit an API-format workflow to a running ComfyUI server via POST /prompt.

    If wait=True, polls GET /history/<prompt_id>. If on_event is provided, will try to
    stream websocket events first (and fall back to polling if it fails).
    """
    base = _net.resolve_comfy_server_url(server_url)

    node_info = getattr(prompt, "node_info", None) if hasattr(prompt, "node_info") else None
    prompt_dict = _sanitize_api_prompt(dict(prompt), node_info=node_info)
    payload: Dict[str, Any] = {"prompt": prompt_dict, "client_id": client_id}
    if extra:
        payload.update(extra)

    resp = _net.http_json(_net.comfy_url(base, "/prompt"), payload=payload, timeout=timeout, method="POST")
    time_submitted = time.time()

    if fetch_outputs and not wait:
        raise ValueError("fetch_outputs=True requires wait=True (history is needed to locate output images).")

    if on_event is not None and not wait:
        raise ValueError("on_event requires wait=True (websocket events are only streamed when waiting).")

    if not wait:
        return SubmissionResult(resp, server_url=base)

    prompt_id = resp.get("prompt_id")
    if not prompt_id:
        return SubmissionResult({"submit": resp, "history": None}, server_url=base)

    history_prefetch = None

    # Optional websocket event stream. Explicit and opt-in: only used when on_event is provided.
    ws_delivered_terminal = False
    if on_event is not None:
        try:
            from .ws import stream_comfy_events
            from .ws import ProgressTracker, WsEvent
            from .ws import StdlibWsTransport
            import queue as _queue
            import threading

            eff_poll_queue = DEFAULT_POLL_QUEUE if poll_queue is None else bool(poll_queue)
            eff_queue_poll_interval = (
                DEFAULT_QUEUE_POLL_INTERVAL_S if queue_poll_interval is None else float(queue_poll_interval)
            )
            if eff_queue_poll_interval <= 0:
                eff_queue_poll_interval = DEFAULT_QUEUE_POLL_INTERVAL_S

            def _extract_cached_nodes_from_messages(messages: Any) -> List[str]:
                out_nodes: List[str] = []
                if not isinstance(messages, list):
                    return out_nodes
                for msg in messages:
                    if not (isinstance(msg, list) and len(msg) >= 2):
                        continue
                    msg_type, msg_data = msg[0], msg[1]
                    if msg_type != "execution_cached" or not isinstance(msg_data, dict):
                        continue
                    nodes = msg_data.get("nodes")
                    if not isinstance(nodes, list):
                        continue
                    out_nodes.extend([str(n) for n in nodes])
                return out_nodes

            # Extract cached nodes from submit response
            cached_nodes = []
            if isinstance(resp, dict):
                messages = resp.get("messages", [])
                for msg in messages:
                    if isinstance(msg, list) and len(msg) >= 2:
                        msg_type, msg_data = msg[0], msg[1]
                        if msg_type == "execution_cached" and isinstance(msg_data, dict):
                            nodes = msg_data.get("nodes", [])
                            if isinstance(nodes, list):
                                cached_nodes.extend([str(n) for n in nodes])

            # Fast path: if the submit response already says everything is cached, ComfyUI may
            # complete before the websocket produces any frames. Emit completed immediately and
            # skip WS (prevents idle-timeout tracebacks and feels instant).
            if isinstance(prompt_dict, dict) and prompt_dict and len(cached_nodes) >= len(prompt_dict):
                try:
                    # Keep event shape consistent with ws-enriched events.
                    nodes_total = list(prompt_dict.keys())
                    ts = time_submitted
                    tracker = ProgressTracker(nodes_total=nodes_total, time_submitted=ts, cached_nodes=cached_nodes)

                    on_event(dict(tracker.update(WsEvent(type="submitted", data={}, ts=ts, client_id=client_id, prompt_id=str(prompt_id), raw={}))))
                    on_event(dict(tracker.update(WsEvent(type="completed", data={}, ts=ts, client_id=client_id, prompt_id=str(prompt_id), raw={}))))
                    ws_delivered_terminal = True
                except Exception:
                    logger.exception("on_event callback raised; continuing")
            else:
                stop_queue = threading.Event()

                def _poll_queue_bg() -> None:
                    if not eff_poll_queue:
                        return
                    q_url = _net.comfy_url(base, "/queue")
                    last_emit = 0.0
                    while not stop_queue.is_set():
                        now = time.time()
                        if now - last_emit < eff_queue_poll_interval:
                            time.sleep(0.05)
                            continue
                        last_emit = now
                        try:
                            q = _net.http_json(q_url, payload=None, timeout=timeout, method="GET")
                        except Exception:
                            q = None
                        try:
                            ev = {
                                "type": "queue",
                                "data": {"queue": q} if q is not None else {},
                                "ts": now,
                                "client_id": client_id,
                                "prompt_id": str(prompt_id),
                                "time_submitted": time_submitted,
                                "time_queued_s": max(0.0, now - time_submitted),
                                "time_elapsed_s": max(0.0, now - time_submitted),
                            }
                            on_event(ev)
                        except Exception:
                            logger.exception("on_event callback raised; continuing")
                        time.sleep(0.05)

                t = threading.Thread(target=_poll_queue_bg, daemon=True)
                t.start()

                # NOTE: We intentionally do NOT probe /history in the background at start.
                # Some servers take a long time to populate history, and we want websocket
                # progress to begin immediately.

                ws_q: "_queue.Queue" = _queue.Queue()
                tr = StdlibWsTransport()
                history_url_ws = _net.comfy_url(base, f"/history/{prompt_id}")

                def _ws_worker() -> None:
                    try:
                        for ev in stream_comfy_events(
                            base,
                            client_id=client_id,
                            prompt_id=str(prompt_id),
                            timeout=float(timeout),
                            wait_timeout=float(wait_timeout),
                            # We handle idle by polling history in results.py, so don't raise idle timeout here.
                            idle_timeout=None,
                            transport=tr,
                            prompt=prompt_dict,
                            cached_nodes=cached_nodes,
                        ):
                            ws_q.put(("ev", ev))
                    except Exception as e:
                        ws_q.put(("err", e))
                    finally:
                        ws_q.put(("end", None))

                ws_t = threading.Thread(target=_ws_worker, daemon=True)
                ws_t.start()

                # If we see no WS events for ~1s, probe /history. This covers cases where:
                # - everything is cached and ComfyUI finishes instantly
                # - the queue is full and WS stays silent while waiting
                deadline_ws = time.time() + max(1.0, float(wait_timeout))
                while time.time() < deadline_ws and not ws_delivered_terminal:
                    try:
                        kind, payload = ws_q.get(timeout=1.0)
                    except _queue.Empty:
                        # No WS activity for 1s: check history.
                        try:
                            h = _net.http_json(history_url_ws, payload=None, timeout=timeout, method="GET")
                        except Exception:
                            h = None
                        if isinstance(h, dict) and str(prompt_id) in h and isinstance(h[str(prompt_id)], dict):
                            item = h[str(prompt_id)]
                            st = h[str(prompt_id)].get("status")
                            if isinstance(st, dict) and st.get("completed") is True:
                                history_prefetch = h
                                # Emit completed via tracker (includes cached nodes if present).
                                cn = _extract_cached_nodes_from_messages(st.get("messages"))
                                nodes_total = list(prompt_dict.keys()) if isinstance(prompt_dict, dict) else []
                                tracker = ProgressTracker(nodes_total=nodes_total, time_submitted=time_submitted, cached_nodes=cn)
                                ts = time.time()
                                data = {
                                    "status": item.get("status") if isinstance(item, dict) else None,
                                    "outputs": item.get("outputs") if isinstance(item, dict) else None,
                                    "meta": item.get("meta") if isinstance(item, dict) else None,
                                    "prompt": item.get("prompt") if isinstance(item, dict) else None,
                                }
                                try:
                                    on_event(
                                        dict(
                                            tracker.update(
                                                WsEvent(
                                                    type="completed",
                                                    data=data,
                                                    ts=ts,
                                                    client_id=client_id,
                                                    prompt_id=str(prompt_id),
                                                    detected_by="history",
                                                    raw={"type": "history_completed", "data": item},
                                                )
                                            )
                                        )
                                    )
                                except Exception:
                                    logger.exception("on_event callback raised; continuing")
                                ws_delivered_terminal = True
                                stop_queue.set()
                                try:
                                    tr.close()
                                except Exception:
                                    pass
                                break
                        continue

                    if kind == "ev":
                        ev = payload
                        try:
                            # Any real execution/progress means we're no longer "just queued".
                            if isinstance(ev, dict) and ev.get("type") in (
                                "executing",
                                "progress",
                                "progress_state",
                                "executed",
                                "completed",
                                "error",
                            ):
                                stop_queue.set()
                            if isinstance(ev, dict) and ev.get("type") in ("completed", "error"):
                                ws_delivered_terminal = True

                            # On completion, do a quick final cached-node harvest so the `completed` event reflects cache.
                            if isinstance(ev, dict) and ev.get("type") == "completed":
                                if len(ev.get("nodes_done", [])) < len(ev.get("nodes_total", [])):
                                    history_url3 = _net.comfy_url(base, f"/history/{prompt_id}")
                                    t0 = time.time()
                                    while time.time() - t0 < 1.5:
                                        try:
                                            h3 = _net.http_json(history_url3, payload=None, timeout=timeout, method="GET")
                                            if isinstance(h3, dict) and str(prompt_id) in h3 and isinstance(h3[str(prompt_id)], dict):
                                                st3 = h3[str(prompt_id)].get("status")
                                                if isinstance(st3, dict):
                                                    cn3 = _extract_cached_nodes_from_messages(st3.get("messages"))
                                                    if cn3:
                                                        # Patch event: treat cached nodes as skipped/done.
                                                        done = list(ev.get("nodes_done", [])) if isinstance(ev.get("nodes_done"), list) else []
                                                        done_set = {str(x) for x in done}
                                                        for n in cn3:
                                                            nid = str(n)
                                                            if nid not in done_set:
                                                                done.append(nid)
                                                                done_set.add(nid)
                                                        ev["nodes_skipped"] = list(ev.get("nodes_skipped", [])) + [
                                                            str(n)
                                                            for n in cn3
                                                            if str(n)
                                                            not in set(
                                                                ev.get("nodes_skipped", []) if isinstance(ev.get("nodes_skipped"), list) else []
                                                            )
                                                        ]
                                                        ev["nodes_done"] = done
                                                        # Recompute nodes_progress (completed event has no node_progress contribution)
                                                        total = len(ev.get("nodes_total", [])) if isinstance(ev.get("nodes_total"), list) else 0
                                                        ev["nodes_progress"] = (
                                                            1.0 if total and len(done) >= total else (len(done) / total if total else 0.0)
                                                        )
                                                        break
                                        except Exception:
                                            pass
                                        time.sleep(0.1)
                            on_event(dict(ev))
                        except Exception:
                            logger.exception("on_event callback raised; continuing")
                    elif kind == "err":
                        # We'll fall back to history polling below.
                        stop_queue.set()
                        break
                    elif kind == "end":
                        stop_queue.set()
                        break

                stop_queue.set()
                try:
                    tr.close()
                except Exception:
                    pass
        except TimeoutError as e:
            # WS idle timeout is expected when ComfyUI completes instantly (all cached) or is queued.
            if "idle" in str(e).lower():
                logger.info("WebSocket idle timeout; falling back to /history polling.")
            else:
                logger.warning("WebSocket timeout; falling back to /history polling.", exc_info=True)
        except Exception:
            logger.warning("WebSocket event stream failed; falling back to /history polling.", exc_info=True)

    history_url = _net.comfy_url(base, f"/history/{prompt_id}")
    deadline = time.time() + max(1, wait_timeout)
    history = history_prefetch
    while time.time() < deadline:
        try:
            if not (isinstance(history, dict) and prompt_id in history):
                history = _net.http_json(history_url, payload=None, timeout=timeout, method="GET")
            if isinstance(history, dict) and prompt_id in history:
                break
        except Exception:
            history = None
        time.sleep(max(0.05, poll_interval))

    # If WS never produced a terminal event, but history indicates completion, emit a final completed event.
    if on_event is not None and not ws_delivered_terminal and isinstance(history, dict) and prompt_id in history:
        try:
            item = history.get(prompt_id)
            status = item.get("status") if isinstance(item, dict) else None
            is_completed = bool(isinstance(status, dict) and status.get("completed") is True)
            if is_completed:
                # Pull cached nodes from history messages (common when everything is cached).
                cached_nodes_h: List[str] = []
                msgs = status.get("messages") if isinstance(status, dict) else None
                if isinstance(msgs, list):
                    for msg in msgs:
                        if isinstance(msg, list) and len(msg) >= 2 and msg[0] == "execution_cached" and isinstance(msg[1], dict):
                            nodes = msg[1].get("nodes")
                            if isinstance(nodes, list):
                                cached_nodes_h.extend([str(n) for n in nodes])

                nodes_total = list(prompt_dict.keys()) if isinstance(prompt_dict, dict) else []
                tracker = ProgressTracker(nodes_total=nodes_total, time_submitted=time_submitted, cached_nodes=cached_nodes_h)
                ts = time.time()
                data = {
                    "status": item.get("status") if isinstance(item, dict) else None,
                    "outputs": item.get("outputs") if isinstance(item, dict) else None,
                    "meta": item.get("meta") if isinstance(item, dict) else None,
                    "prompt": item.get("prompt") if isinstance(item, dict) else None,
                }
                ev = tracker.update(
                    WsEvent(
                        type="completed",
                        data=data,
                        ts=ts,
                        client_id=client_id,
                        prompt_id=str(prompt_id),
                        detected_by="history",
                        raw={"type": "history_completed", "data": item},
                    )
                )
                on_event(dict(ev))
        except Exception:
            logger.exception("on_event callback raised while emitting final completed event; continuing")

    out: Dict[str, Any] = {"submit": resp, "history": history}

    if fetch_outputs and isinstance(history, dict):
        image_refs = list(_extract_output_refs(history, str(prompt_id), output_types=["images"]))
        out["images"] = _fetch_images_from_refs(
            base,
            image_refs,
            timeout=timeout,
            output_path=output_path,
            include_bytes=include_bytes,
        )

    return SubmissionResult(out, server_url=base)


class SubmissionResult(dict):
    """
    Dict-like result returned by ApiFlow.submit() / Flow.submit().

    Contains at least the `/prompt` response. When wait=True it also contains history.
    Provides `.fetch_images()` / `.fetch_files()` as follow-ups.
    """

    def __init__(self, *args, server_url: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._server_url = server_url

    @classmethod
    def from_prompt_id(cls, prompt_id: Union[str, int], server_url: Optional[str] = None) -> "SubmissionResult":
        base = _net.resolve_comfy_server_url(server_url)
        return cls({"prompt_id": str(prompt_id), "history": None}, server_url=base)

    @property
    def server_url(self) -> str:
        return self._server_url

    @property
    def prompt_id(self) -> Optional[str]:
        submit_obj = self.get("submit")
        if isinstance(submit_obj, dict) and "prompt_id" in submit_obj:
            return submit_obj.get("prompt_id")
        if isinstance(self.get("prompt_id"), str):
            return self.get("prompt_id")
        return None

    def fetch_files(
        self,
        *,
        output_types: Optional[Union[str, Iterable[str]]] = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        wait: bool = DEFAULT_GET_IMAGES_WAIT,
        poll_interval: float = DEFAULT_POLL_INTERVAL_S,
        wait_timeout: int = DEFAULT_WAIT_TIMEOUT_S,
        output_path: Optional[Union[str, Path]] = None,
        include_bytes: bool = False,
        refresh: bool = False,
    ) -> "FilesResult":
        prompt_id = self.prompt_id
        if not prompt_id:
            return FilesResult([])

        history = self.get("history")
        if wait and not isinstance(history, dict):
            history_url = _net.comfy_url(self._server_url, f"/history/{prompt_id}")
            deadline = time.time() + max(1, wait_timeout)
            while time.time() < deadline:
                try:
                    history = _net.http_json(history_url, payload=None, timeout=timeout, method="GET")
                    if isinstance(history, dict) and prompt_id in history:
                        break
                except Exception:
                    history = None
                time.sleep(max(0.05, poll_interval))

            if isinstance(history, dict):
                self["history"] = history

        if not isinstance(history, dict):
            return FilesResult([])

        if isinstance(output_types, str):
            kinds_iter: Optional[Iterable[str]] = [output_types]
        else:
            kinds_iter = output_types

        refs = list(_extract_output_refs(history, prompt_id, output_types=kinds_iter))

        cached = self.get("files")
        cache_map: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
        if isinstance(cached, list):
            for it in cached:
                if not isinstance(it, dict):
                    continue
                ref = it.get("ref") if isinstance(it.get("ref"), dict) else None
                if not isinstance(ref, dict):
                    continue
                key = (
                    str(ref.get("kind", "")),
                    str(ref.get("filename", "")),
                    str(ref.get("subfolder", "")),
                    str(ref.get("type", "")),
                )
                if key[1]:
                    cache_map[key] = it

        need: List[Dict[str, str]] = []
        out_items: List[Dict[str, Any]] = []
        seen: set = set()

        for ref in refs:
            key = (
                str(ref.get("kind", "")),
                str(ref.get("filename", "")),
                str(ref.get("subfolder", "")),
                str(ref.get("type", "")),
            )
            if key in seen:
                continue
            seen.add(key)

            if not refresh and key in cache_map:
                existing = cache_map[key]
                has_bytes = isinstance(existing.get("bytes"), (bytes, bytearray))
                has_path = isinstance(existing.get("path"), str) and existing.get("path")
                if (include_bytes and not has_bytes) or (output_path is not None and not has_path):
                    need.append(ref)
                else:
                    out_items.append(existing)
            else:
                need.append(ref)

        if need:
            fetched = _fetch_files_from_refs(
                self._server_url,
                need,
                timeout=timeout,
                output_path=output_path,
                include_bytes=include_bytes,
            )
            out_items.extend(fetched)

        out = FilesResult([it if isinstance(it, FileResult) else FileResult(it) for it in out_items])
        try:
            setattr(out, "_AUTOGRAPH_server_url", self._server_url)
        except Exception:
            pass
        self["files"] = out
        return out

    def fetch_images(
        self,
        *,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        wait: bool = DEFAULT_GET_IMAGES_WAIT,
        poll_interval: float = DEFAULT_POLL_INTERVAL_S,
        wait_timeout: int = DEFAULT_WAIT_TIMEOUT_S,
        output_path: Optional[Union[str, Path]] = None,
        include_bytes: bool = DEFAULT_GET_IMAGES_INCLUDE_IMAGE_BYTES,
        refresh: bool = False,
    ) -> "ImagesResult":
        files = self.fetch_files(
            output_types=["images"],
            timeout=timeout,
            wait=wait,
            poll_interval=poll_interval,
            wait_timeout=wait_timeout,
            output_path=output_path,
            include_bytes=bool(include_bytes),
            refresh=refresh,
        )
        imgs = ImagesResult([ImageResult(dict(it)) for it in files])
        self["images"] = imgs
        return imgs

    def save(
        self,
        *,
        kinds: Optional[Union[str, Iterable[str]]] = None,
        only: Optional[Union[str, Iterable[str], Path, Iterable[Path]]] = None,
        output_path: Optional[Union[str, Path]] = None,
        filename: str = "",
        overwrite: bool = False,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        wait: bool = DEFAULT_GET_IMAGES_WAIT,
        poll_interval: float = DEFAULT_POLL_INTERVAL_S,
        wait_timeout: int = DEFAULT_WAIT_TIMEOUT_S,
        refresh: bool = False,
        index_offset: int = 0,
        regex_parser: Any = None,
        include_bytes: bool = True,
        imagemagick_path: Optional[Union[str, Path]] = None,
        ffmpeg_path: Optional[Union[str, Path]] = None,
    ) -> List[Path]:
        files = self.fetch_files(
            output_types=kinds,
            timeout=timeout,
            wait=wait,
            poll_interval=poll_interval,
            wait_timeout=wait_timeout,
            include_bytes=bool(include_bytes),
            refresh=refresh,
        )
        if not files:
            raise ValueError(
                "No registered outputs found. The job may not be finished yet, or the workflow didn't produce outputs. "
                "Try wait=True (default) or increase wait_timeout."
            )
        out_path = Path(output_path if output_path is not None else DEFAULT_OUTPUT_PATH)
        return files.save(
            only=only,
            output_path=out_path,
            filename=filename,
            overwrite=overwrite,
            timeout=timeout,
            refresh=refresh,
            index_offset=index_offset,
            regex_parser=regex_parser,
            imagemagick_path=imagemagick_path,
            ffmpeg_path=ffmpeg_path,
        )


def _extract_image_refs(history: Dict[str, Any], prompt_id: str) -> Iterable[Dict[str, str]]:
    prompt_entry = history.get(prompt_id)
    if not isinstance(prompt_entry, dict):
        return
    outputs = prompt_entry.get("outputs")
    if not isinstance(outputs, dict):
        return
    for _node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        images = node_out.get("images")
        if not isinstance(images, list):
            continue
        for img in images:
            if not isinstance(img, dict):
                continue
            filename = img.get("filename")
            subfolder = img.get("subfolder", "") or ""
            img_type = img.get("type", "output") or "output"
            if isinstance(filename, str) and filename:
                yield {"filename": filename, "subfolder": str(subfolder), "type": str(img_type)}


def _extract_output_refs(
    history: Dict[str, Any],
    prompt_id: str,
    *,
    output_types: Optional[Iterable[str]] = None,
) -> Iterable[Dict[str, str]]:
    prompt_entry = history.get(prompt_id)
    if not isinstance(prompt_entry, dict):
        return
    outputs = prompt_entry.get("outputs")
    if not isinstance(outputs, dict):
        return

    kind_set = {k for k in output_types} if output_types is not None else None
    for _node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        for kind, items in node_out.items():
            if kind_set is not None and kind not in kind_set:
                continue
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                filename = it.get("filename")
                if not (isinstance(filename, str) and filename):
                    continue
                subfolder = it.get("subfolder", "") or ""
                out_type = it.get("type", "output") or "output"
                yield {
                    "kind": str(kind),
                    "filename": filename,
                    "subfolder": str(subfolder),
                    "type": str(out_type),
                }


def _fetch_images_from_refs(
    server_url: str,
    image_refs: List[Dict[str, str]],
    *,
    timeout: int,
    output_path: Optional[Union[str, Path]],
    include_bytes: bool,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    out_dir = Path(output_path) if output_path is not None else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    for ref in image_refs:
        params = urllib.parse.urlencode(
            {"filename": ref["filename"], "subfolder": ref.get("subfolder", ""), "type": ref.get("type", "output")}
        )
        url = _net.comfy_url(server_url, f"/view?{params}")

        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = resp.read()
        except Exception as e:
            out.append(ImageResult({"ref": ref, "error": str(e)}))
            continue

        entry: Dict[str, Any] = {"ref": ref}
        if include_bytes:
            entry["bytes"] = data

        img = ImageResult(entry)
        if out_dir is not None:
            img["bytes"] = data
            img.save(out_dir)
            if not include_bytes:
                img.pop("bytes", None)

        out.append(img)

    return out


class FileResult(dict):
    """Single registered output entry (dict-like) with a .save() convenience."""

    def save(
        self,
        output_path: Optional[Union[str, Path]] = None,
        *,
        filename: str = "",
        overwrite: bool = False,
        index_offset: int = 0,
        regex_parser: Any = None,
        imagemagick_path: Optional[Union[str, Path]] = None,
        ffmpeg_path: Optional[Union[str, Path]] = None,
    ) -> Path:
        data = self.get("bytes")
        src_path = self.get("path")
        if not isinstance(data, (bytes, bytearray)):
            # Allow saving directly from a local on-disk path (serverless / offline results).
            if not (isinstance(src_path, str) and src_path and Path(src_path).is_file()):
                raise ValueError(
                    "No bytes available. Fetch with include_bytes=True (or use output_path= when fetching), "
                    "or ensure this result has a valid local 'path'."
                )

        ref = self.get("ref") if isinstance(self.get("ref"), dict) else {}
        ref_fn = ref.get("filename") if isinstance(ref, dict) else None
        name = Path(str(ref_fn)).name if ref_fn else "output.bin"

        base = Path(output_path if output_path is not None else DEFAULT_OUTPUT_PATH)

        if filename:
            out_dir = base
            if out_dir.suffix:
                out_dir = out_dir.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            tokens = _tokens_from_ref(ref if isinstance(ref, dict) else {"filename": name}, regex_parser=regex_parser)
            name0 = _format_tokens(filename, tokens)
            name1 = _apply_index_pattern(name0, int(index_offset))
            out_path = Path(name1)
            if not out_path.is_absolute():
                out_path = out_dir / out_path
        else:
            if base.suffix:
                out_path = base
                out_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                base.mkdir(parents=True, exist_ok=True)
                out_path = base / name

        src_ext = _guess_image_ext(bytes(data))
        if src_ext and out_path.suffix:
            if not isinstance(data, (bytes, bytearray)):
                data = Path(str(src_path)).read_bytes()
            img = ImageResult({"ref": ref, "bytes": bytes(data)})
            saved = img.save(
                out_path,
                overwrite=overwrite,
                imagemagick_path=imagemagick_path,
                ffmpeg_path=ffmpeg_path,
            )
            self["path"] = str(saved)
            return saved

        if out_path.exists() and not overwrite:
            raise FileExistsError(str(out_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, (bytes, bytearray)):
            with out_path.open("wb") as f:
                f.write(bytes(data))
        else:
            # Same-extension / generic copy path (faster than loading bytes into memory).
            shutil.copy2(str(src_path), str(out_path))
        self["path"] = str(out_path)
        return out_path


class FilesResult(list):
    """List of FileResult entries returned from SubmissionResult.fetch_files()."""

    @property
    def list(self) -> List[str]:  # noqa: A003
        out: List[str] = []
        for it in self:
            ref = it.get("ref") if isinstance(it, dict) and isinstance(it.get("ref"), dict) else {}
            fn = ref.get("filename") if isinstance(ref, dict) else None
            if isinstance(fn, str) and fn:
                out.append(Path(fn).name)
        return out

    def _server_url(self) -> Optional[str]:
        v = getattr(self, "_AUTOGRAPH_server_url", None)
        return v if isinstance(v, str) and v else None

    def _ensure_bytes(self, *, timeout: int, refresh: bool = False) -> None:
        need_refs: List[Dict[str, str]] = []
        for it in self:
            if not isinstance(it, dict):
                continue
            if isinstance(it.get("bytes"), (bytes, bytearray)) and not refresh:
                continue
            ref = it.get("ref") if isinstance(it.get("ref"), dict) else None
            if not isinstance(ref, dict):
                continue
            fn = ref.get("filename")
            if not (isinstance(fn, str) and fn):
                continue
            need_refs.append(
                {
                    "kind": str(ref.get("kind", "")),
                    "filename": str(fn),
                    "subfolder": str(ref.get("subfolder", "") or ""),
                    "type": str(ref.get("type", "output") or "output"),
                }
            )

        if not need_refs:
            return

        base = self._server_url()
        if not base:
            raise ValueError(
                "No bytes available. Re-fetch with include_bytes=True, or fetch with output_path=..., "
                "or call SubmissionResult.fetch_files() and then .save()."
            )

        fetched = _fetch_files_from_refs(base, need_refs, timeout=timeout, output_path=None, include_bytes=True)
        fetched_map: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
        for f in fetched:
            if not isinstance(f, dict):
                continue
            ref = f.get("ref") if isinstance(f.get("ref"), dict) else None
            if not isinstance(ref, dict):
                continue
            key = (
                str(ref.get("kind", "")),
                str(ref.get("filename", "")),
                str(ref.get("subfolder", "")),
                str(ref.get("type", "")),
            )
            fetched_map[key] = f

        for i, it in enumerate(list(self)):
            if not isinstance(it, dict):
                continue
            ref = it.get("ref") if isinstance(it.get("ref"), dict) else None
            if not isinstance(ref, dict):
                continue
            key = (
                str(ref.get("kind", "")),
                str(ref.get("filename", "")),
                str(ref.get("subfolder", "")),
                str(ref.get("type", "")),
            )
            if key in fetched_map and isinstance(fetched_map[key].get("bytes"), (bytes, bytearray)):
                it["bytes"] = fetched_map[key]["bytes"]
                self[i] = FileResult(it)

    def save(
        self,
        only: Optional[Union[str, Iterable[str], Path, Iterable[Path]]] = None,
        *,
        output_path: Optional[Union[str, Path]] = None,
        filename: str = "",
        overwrite: bool = False,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        refresh: bool = False,
        index_offset: int = 0,
        regex_parser: Any = None,
        imagemagick_path: Optional[Union[str, Path]] = None,
        ffmpeg_path: Optional[Union[str, Path]] = None,
    ) -> List[Path]:
        out_dir = Path(output_path if output_path is not None else DEFAULT_OUTPUT_PATH)
        out_dir.mkdir(parents=True, exist_ok=True)

        want: Optional[set] = None
        if only:
            if isinstance(only, (str, Path)):
                want = {Path(str(only)).name}
            else:
                want = {Path(str(x)).name for x in only}

        items: List[FileResult] = []
        for it in self:
            if not isinstance(it, dict):
                continue
            ref = it.get("ref") if isinstance(it.get("ref"), dict) else None
            if not isinstance(ref, dict):
                continue
            fn = ref.get("filename")
            if not (isinstance(fn, str) and fn):
                continue
            if want is not None and Path(fn).name not in want:
                continue
            items.append(it if isinstance(it, FileResult) else FileResult(it))

        if not items:
            raise ValueError("No files to save (empty selection, or missing refs).")

        tmp = FilesResult(items)
        setattr(tmp, "_AUTOGRAPH_server_url", self._server_url())
        tmp._ensure_bytes(timeout=timeout, refresh=refresh)
        items2: List[FileResult] = [it if isinstance(it, FileResult) else FileResult(it) for it in tmp]

        written: List[Path] = []
        for i, it in enumerate(items2):
            data = it.get("bytes")
            if not isinstance(data, (bytes, bytearray)):
                raise ValueError(
                    "No bytes available for one or more files. Fetch with include_bytes=True, "
                    "or call files.save() on a FilesResult returned from SubmissionResult.fetch_files()."
                )

            ref = it.get("ref") if isinstance(it.get("ref"), dict) else {}
            ref_fn = ref.get("filename") if isinstance(ref, dict) else None
            ref_name = Path(str(ref_fn)).name if isinstance(ref_fn, str) else "output.bin"

            n = i + int(index_offset)

            if filename:
                tokens = _tokens_from_ref(ref if isinstance(ref, dict) else {"filename": ref_name}, regex_parser=regex_parser)
                name0 = _format_tokens(filename, tokens)
                name1 = _apply_index_pattern(name0, n)
                out_path = Path(name1)
                if not out_path.is_absolute():
                    out_path = out_dir / out_path
            else:
                out_path = out_dir / ref_name

            saved = it.save(
                out_path,
                overwrite=overwrite,
                imagemagick_path=imagemagick_path,
                ffmpeg_path=ffmpeg_path,
            )
            written.append(saved)

        for it in items2:
            ref = it.get("ref") if isinstance(it.get("ref"), dict) else None
            if not isinstance(ref, dict):
                continue
            fn = ref.get("filename")
            if not (isinstance(fn, str) and fn):
                continue
            for j, orig in enumerate(self):
                if not isinstance(orig, dict):
                    continue
                oref = orig.get("ref") if isinstance(orig.get("ref"), dict) else None
                if not isinstance(oref, dict):
                    continue
                ofn = oref.get("filename")
                if isinstance(ofn, str) and ofn and Path(ofn).name == Path(fn).name:
                    merged = dict(orig)
                    if "bytes" in it:
                        merged["bytes"] = it.get("bytes")
                    if "path" in it:
                        merged["path"] = it.get("path")
                    self[j] = FileResult(merged)
        return written


def _fetch_files_from_refs(
    server_url: str,
    refs: List[Dict[str, str]],
    *,
    timeout: int,
    output_path: Optional[Union[str, Path]],
    include_bytes: bool,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    out_dir = Path(output_path) if output_path is not None else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    for ref in refs:
        params = urllib.parse.urlencode(
            {"filename": ref["filename"], "subfolder": ref.get("subfolder", ""), "type": ref.get("type", "output")}
        )
        url = _net.comfy_url(server_url, f"/view?{params}")

        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = resp.read()
        except Exception as e:
            out.append(FileResult({"ref": ref, "error": str(e)}))
            continue

        entry: Dict[str, Any] = {"ref": ref}
        if include_bytes:
            entry["bytes"] = data

        fobj = FileResult(entry)
        if out_dir is not None:
            fobj["bytes"] = data
            fobj.save(out_dir)
            if not include_bytes:
                fobj.pop("bytes", None)

        out.append(fobj)

    return out


def _guess_image_ext(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _split_stem_last_digit_run(stem: str) -> Dict[str, str]:
    if not isinstance(stem, str):
        return {"base": "", "sequence": "", "tail": ""}
    matches = list(re.finditer(r"\d+", stem))
    if not matches:
        return {"base": stem, "sequence": "", "tail": ""}
    m = matches[-1]
    return {"base": stem[: m.start()], "sequence": m.group(0), "tail": stem[m.end() :]}


def _coerce_regex_parser(regex_parser: Any) -> Optional["re.Pattern"]:
    if regex_parser is None:
        return None
    if hasattr(regex_parser, "search") and hasattr(regex_parser, "pattern"):
        return regex_parser  # type: ignore[return-value]
    if isinstance(regex_parser, str):
        return re.compile(regex_parser)
    raise TypeError("regex_parser must be a compiled regex or a pattern string")


def _extract_frame_index_from_ref(ref: Dict[str, Any]) -> Optional[int]:
    if "frame" in ref:
        try:
            return int(ref["frame"])
        except Exception:
            pass
    filename = ref.get("filename")
    if not isinstance(filename, str):
        return None
    stem = Path(filename).stem
    parts = _split_stem_last_digit_run(stem)
    seq = parts.get("sequence")
    if isinstance(seq, str) and seq:
        try:
            return int(seq)
        except Exception:
            return None
    return None


def _tokens_from_ref(ref: Dict[str, Any], *, regex_parser: Any = None) -> Dict[str, Any]:
    filename0 = ref.get("filename") if isinstance(ref, dict) else None
    filename = Path(str(filename0)).name if isinstance(filename0, str) else ""
    stem = Path(filename).stem if filename else ""
    ext = Path(filename).suffix[1:] if filename and Path(filename).suffix else ""

    tokens: Dict[str, Any] = {"filename": filename, "stem": stem, "ext": ext}
    tokens.update(_split_stem_last_digit_run(stem))

    sf = _extract_frame_index_from_ref(ref) if isinstance(ref, dict) else None
    tokens["src_frame"] = sf if sf is not None else ""

    rx = _coerce_regex_parser(regex_parser)
    if rx is not None and filename:
        m = rx.search(filename)
        if m is not None:
            tokens.update({k: v for k, v in m.groupdict().items() if v is not None})

    return tokens


def _format_tokens(template: str, tokens: Dict[str, Any]) -> str:
    try:
        return str(template).format_map(tokens)
    except KeyError as e:
        missing = str(e).strip("'")
        have = ", ".join(sorted(tokens.keys()))
        raise ValueError(f"Unknown template key {{{missing}}}. Available keys: {have}") from None


def _apply_index_pattern(template: str, n: int) -> str:
    s = str(template)
    if "#" in s:
        while True:
            start = s.find("#")
            if start < 0:
                break
            end = start
            while end < len(s) and s[end] == "#":
                end += 1
            width = end - start
            s = s[:start] + f"{n:0{width}d}" + s[end:]
        return s

    if re.search(r"%0\d+d", s):
        try:
            return s % int(n)
        except Exception as e:
            raise ValueError(f"Invalid %0Nd pattern {s!r}: {e}") from None

    return s


def _run_imagemagick_convert(imagemagick_path: Union[str, Path], src: Path, dst: Path, *, overwrite: bool) -> None:
    exe = str(imagemagick_path)
    args = [exe, str(src), str(dst)]
    if dst.exists() and not overwrite:
        raise FileExistsError(str(dst))
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _run_ffmpeg_convert(ffmpeg_path: Union[str, Path], src: Path, dst: Path, *, overwrite: bool) -> None:
    exe = str(ffmpeg_path)
    args = [exe, "-hide_banner", "-loglevel", "error"]
    if overwrite:
        args.append("-y")
    else:
        args.append("-n")
    args += ["-i", str(src), str(dst)]
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ImageResult(dict):
    """Single image entry (dict-like) with a .save() convenience."""

    def save(
        self,
        output_path: Union[str, Path],
        *,
        overwrite: bool = False,
        imagemagick_path: Optional[Union[str, Path]] = None,
        ffmpeg_path: Optional[Union[str, Path]] = None,
    ) -> Path:
        data = self.get("bytes")
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("No image bytes available. Fetch images with include_bytes=True.")

        target_path = Path(output_path)

        if not target_path.suffix:
            ref = self.get("ref") if isinstance(self.get("ref"), dict) else {}
            filename = ref.get("filename") if isinstance(ref, dict) else None
            name = Path(str(filename)).name if filename else "image.png"
            target_path = target_path / name

        src_ext = _guess_image_ext(bytes(data))
        dst_ext = target_path.suffix.lower()
        if src_ext and dst_ext and src_ext != dst_ext:
            if _PIL_Image is None:
                if imagemagick_path is None and ffmpeg_path is None:
                    raise ValueError(
                        f"Image bytes appear to be {src_ext}, but target path ends with {dst_ext}. "
                        f"Install Pillow to enable conversion (e.g. `pip install pillow`), "
                        f"or provide imagemagick_path=/ffmpeg_path=, or save with {src_ext} instead."
                    )

        if target_path.exists() and not overwrite:
            raise FileExistsError(str(target_path))

        target_path.parent.mkdir(parents=True, exist_ok=True)
        if dst_ext and src_ext and src_ext != dst_ext:
            if _PIL_Image is None:
                with tempfile.TemporaryDirectory() as td:
                    src_path = Path(td) / f"input{src_ext}"
                    with src_path.open("wb") as f:
                        f.write(bytes(data))

                    if imagemagick_path is not None:
                        _run_imagemagick_convert(imagemagick_path, src_path, target_path, overwrite=overwrite)
                    elif ffmpeg_path is not None:
                        _run_ffmpeg_convert(ffmpeg_path, src_path, target_path, overwrite=overwrite)
                    else:
                        raise ValueError("Missing Pillow and no external converter path provided.")

                self["path"] = str(target_path)
                return target_path

            ext_to_format = {
                ".jpg": "JPEG",
                ".jpeg": "JPEG",
                ".png": "PNG",
                ".webp": "WEBP",
                ".gif": "GIF",
                ".bmp": "BMP",
                ".tif": "TIFF",
                ".tiff": "TIFF",
            }
            fmt = ext_to_format.get(dst_ext)
            if not fmt:
                raise ValueError(
                    f"Unsupported output extension {dst_ext!r}. "
                    f"Install Pillow and use a known image extension (png/jpg/webp/gif/tiff/bmp)."
                )

            img = _PIL_Image.open(io.BytesIO(bytes(data)))
            if fmt == "JPEG" and img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            if fmt == "BMP" and img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(str(target_path), format=fmt)
        else:
            with target_path.open("wb") as f:
                f.write(data)
        self["path"] = str(target_path)
        return target_path

    def to_pixels(self, mode: str = "RGB", *, as_list: bool = False) -> Dict[str, Any]:
        data = self.get("bytes")
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("No image bytes available. Fetch images with include_bytes=True.")
        if _PIL_Image is None:
            raise ValueError("Pillow is required for to_pixels(). Install with `pip install pillow`.")

        img = _PIL_Image.open(io.BytesIO(bytes(data)))
        img = img.convert(mode)
        if as_list:
            return {"mode": mode, "size": img.size, "pixels": list(img.getdata())}
        return {"mode": mode, "size": img.size, "pixels": img.tobytes()}


class ImagesResult(list):
    """List of image entries returned from SubmissionResult.fetch_images()."""

    def save(
        self,
        output_path: Optional[Union[str, Path]] = None,
        *,
        overwrite: bool = False,
        index_offset: int = 0,
        filename: str = "",
        regex_parser: Any = None,
        imagemagick_path: Optional[Union[str, Path]] = None,
        ffmpeg_path: Optional[Union[str, Path]] = None,
    ) -> Union[Path, List[Path]]:
        target_path = Path(output_path if output_path is not None else DEFAULT_OUTPUT_PATH)
        ok_items: List[ImageResult] = [it for it in self if isinstance(it, ImageResult) and it.get("bytes")]

        if not ok_items:
            raise ValueError("No image bytes to save. Ensure include_bytes=True.")

        target_str = str(target_path)
        has_hash = "#" in target_str
        has_percent = bool(re.search(r"%0\d+d", target_str))

        if filename:
            out_dir = target_path
            if out_dir.suffix:
                out_dir = out_dir.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            written: List[Path] = []
            for i, it in enumerate(ok_items):
                ref = it.get("ref") if isinstance(it.get("ref"), dict) else {}
                n = i + int(index_offset)
                tokens = _tokens_from_ref(ref if isinstance(ref, dict) else {}, regex_parser=regex_parser)
                name0 = _format_tokens(filename, tokens)
                name1 = _apply_index_pattern(name0, n)
                out_path = Path(name1)
                if not out_path.is_absolute():
                    out_path = out_dir / out_path
                written.append(
                    it.save(
                        out_path,
                        overwrite=overwrite,
                        imagemagick_path=imagemagick_path,
                        ffmpeg_path=ffmpeg_path,
                    )
                )
            return written[0] if len(written) == 1 else written

        if has_hash or has_percent:
            written2: List[Path] = []
            for i, it in enumerate(ok_items):
                n = i + int(index_offset)
                out_path = Path(_apply_index_pattern(target_str, n))
                written2.append(
                    it.save(
                        out_path,
                        overwrite=overwrite,
                        imagemagick_path=imagemagick_path,
                        ffmpeg_path=ffmpeg_path,
                    )
                )
            return written2[0] if len(written2) == 1 else written2

        if len(ok_items) == 1 and target_path.suffix:
            return ok_items[0].save(
                target_path,
                overwrite=overwrite,
                imagemagick_path=imagemagick_path,
                ffmpeg_path=ffmpeg_path,
            )

        if len(ok_items) > 1 and target_path.suffix:
            raise ValueError(
                "Multiple images but path looks like a single file. "
                "Use a pattern like 'frame.###.png' or pass filename='frame.###.png' with a directory path."
            )

        out_dir2 = target_path
        out_dir2.mkdir(parents=True, exist_ok=True)
        written3: List[Path] = []
        for it in ok_items:
            written3.append(
                it.save(
                    out_dir2,
                    overwrite=overwrite,
                    imagemagick_path=imagemagick_path,
                    ffmpeg_path=ffmpeg_path,
                )
            )
        return written3


# Type-only forward reference (avoid importing models here)
class ApiFlow(dict):  # pragma: no cover
    pass


