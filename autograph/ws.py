"""autoflow.ws

Stdlib-only websocket client utilities for ComfyUI progress events.

Design goals:
- stdlib-only by default (no required deps)
- sync/blocking (works with ApiFlow.submit(wait=True, on_event=...))
- easy to wrap later with optional websocket libraries via a tiny adapter

ComfyUI websocket endpoint (typical):
  GET /ws?clientId=<client_id>

Messages are JSON objects like:
  {"type": "progress", "data": {...}}

This module is intentionally conservative: it focuses on receiving server events.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import threading
import time
import urllib.parse
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union


_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WsEvent(dict):
    """Tiny dict subclass for normalized websocket events."""

    @property
    def type(self) -> Optional[str]:
        t = self.get("type")
        return t if isinstance(t, str) else None


def chain_callbacks(*callbacks: Optional[Callable[[Dict[str, Any]], None]]) -> Callable[[Dict[str, Any]], None]:
    """Combine multiple callbacks into a single callback."""

    cbs = [cb for cb in callbacks if cb is not None]

    def _call(event: Dict[str, Any]) -> None:
        for cb in cbs:
            cb(event)

    return _call


class ProgressTracker:
    """Maintains state across ComfyUI events to compute enriched progress metrics."""

    def __init__(
        self,
        *,
        nodes_total: Optional[List[str]] = None,
        time_submitted: Optional[float] = None,
        cached_nodes: Optional[List[str]] = None,
        deps: Optional[Dict[str, List[str]]] = None,
    ):
        self.nodes_total = nodes_total or []
        # Initialize with cached nodes already completed
        self.nodes_completed: List[str] = list(cached_nodes) if cached_nodes else []
        # Assumed cached/completed nodes inferred from dependencies (best-effort)
        self.nodes_skipped: List[str] = []
        self.node_current: Optional[str] = None
        self.time_submitted = time_submitted or time.time()
        self.time_started: Optional[float] = None
        self.node_start_time: Optional[float] = None
        self._deps = deps or {}
        self._lock = threading.Lock()

    def add_completed_nodes(self, nodes: List[str]) -> None:
        """Add nodes to the completed list (deduped). Thread-safe."""
        if not nodes:
            return
        with self._lock:
            for n in nodes:
                nid = str(n)
                if nid not in self.nodes_completed:
                    self.nodes_completed.append(nid)

    def add_skipped_nodes(self, nodes: List[str]) -> None:
        """Add nodes to the skipped list (deduped). Thread-safe."""
        if not nodes:
            return
        with self._lock:
            for n in nodes:
                nid = str(n)
                if nid not in self.nodes_skipped and nid not in self.nodes_completed:
                    self.nodes_skipped.append(nid)

    def update(self, event: WsEvent) -> WsEvent:
        """Enrich an event with computed progress fields."""
        ev_type = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        ts = event.get("ts", time.time())

        with self._lock:
            # Ingest cached nodes from any message-like fields (e.g. status messages).
            self._ingest_cached_nodes_from_any(data)
            raw_obj = event.get("raw")
            if isinstance(raw_obj, dict):
                self._ingest_cached_nodes_from_any(raw_obj)

            # Track execution start (first non-submitted event)
            if self.time_started is None and ev_type not in ("submitted",):
                self.time_started = ts

            # Update node tracking
            if ev_type == "executing":
                node_id = data.get("node")
                if node_id is not None:
                    if self.node_current and self.node_current not in self.nodes_completed:
                        self.nodes_completed.append(self.node_current)
                    self.node_current = str(node_id)
                    self.node_start_time = ts
                elif node_id is None:
                    # executing with node=None means completion
                    if self.node_current and self.node_current not in self.nodes_completed:
                        self.nodes_completed.append(self.node_current)
                    self.node_current = None

            # Some ComfyUI event streams emit `executed` without a following `executing` transition
            # (or for fast nodes). Best-effort: count the node as completed.
            if ev_type == "executed":
                node_id = data.get("node")
                if node_id is not None:
                    nid = str(node_id)
                    if nid not in self.nodes_completed:
                        self.nodes_completed.append(nid)

            # Infer skipped/cached nodes: if a node is executing, its ancestors must be complete
            # (either executed earlier or cached). We record missing ancestors as nodes_skipped.
            if self.node_current:
                anc = _ancestors(self.node_current, self._deps)
                for a in anc:
                    if a != self.node_current and a not in self.nodes_completed and a not in self.nodes_skipped:
                        self.nodes_skipped.append(a)

            # Add computed fields
            enriched = WsEvent(event)

            # Node tracking
            enriched["node_current"] = self.node_current
            enriched["nodes_completed"] = list(self.nodes_completed)
            enriched["nodes_skipped"] = list(self.nodes_skipped)
            enriched["nodes_total"] = list(self.nodes_total)
            # Convenience union (what we consider "done" for progress)
            done = []
            seen = set()
            for n in self.nodes_completed + self.nodes_skipped:
                if n in seen:
                    continue
                seen.add(n)
                done.append(n)
            enriched["nodes_done"] = done

            # Current node progress (from ComfyUI progress events)
            if ev_type == "progress":
                value = data.get("value")
                max_val = data.get("max")
                if isinstance(value, (int, float)) and isinstance(max_val, (int, float)) and max_val > 0:
                    enriched["node_progress"] = int((value * 100) / max_val)
                    enriched["node_progress_value"] = value
                    enriched["node_progress_max"] = max_val
                else:
                    enriched["node_progress"] = 0
                    enriched["node_progress_value"] = 0
                    enriched["node_progress_max"] = 0
            else:
                enriched["node_progress"] = 0
                enriched["node_progress_value"] = 0
                enriched["node_progress_max"] = 0

            # Overall workflow progress (nodes_completed + current node progress / 100) / nodes_total
            nodes_total_count = len(self.nodes_total)
            nodes_done_count = len(done)
            node_progress_frac = enriched.get("node_progress", 0) / 100.0
            if nodes_total_count <= 0:
                np = 0.0
            else:
                np = (nodes_done_count + node_progress_frac) / nodes_total_count
            # Clamp for safety (e.g. if cached info arrives late or node lists mismatch)
            if np < 0.0:
                np = 0.0
            elif np > 1.0:
                np = 1.0
            enriched["nodes_progress"] = np

            # Time tracking
            enriched["time_submitted"] = self.time_submitted
            enriched["time_queued_s"] = (self.time_started - self.time_submitted) if self.time_started else 0.0
            enriched["time_elapsed_s"] = ts - self.time_submitted
            enriched["node_execution_time_s"] = (ts - self.node_start_time) if self.node_start_time else 0.0

            return enriched

    def _ingest_cached_nodes_from_any(self, data: Dict[str, Any]) -> None:
        """
        Best-effort extraction of cached nodes from different ComfyUI message shapes.

        Known shapes observed:
        - {"messages": [["execution_cached", {"nodes": ["4","5"]}], ...]}
        - {"status": {"messages": [["execution_cached", {"nodes": [...]}], ...]}}
        """

        def _extract_messages(obj: Any) -> List[Any]:
            """
            Extract a ComfyUI-style messages list from various shapes.

            Supported shapes:
            - {"messages": [[...], ...]}
            - {"data": [[...], ...]}  (data is a list)
            - {"data": {"messages": [[...], ...]}}
            """
            if not isinstance(obj, dict):
                return []

            msgs = obj.get("messages")
            if isinstance(msgs, list):
                return msgs

            d = obj.get("data")
            if isinstance(d, list):
                return d
            if isinstance(d, dict):
                msgs2 = d.get("messages")
                if isinstance(msgs2, list):
                    return msgs2

            return []

        msgs = []
        msgs.extend(_extract_messages(data))
        if isinstance(data.get("status"), dict):
            msgs.extend(_extract_messages(data.get("status")))

        for msg in msgs:
            # expected: ["execution_cached", {"nodes": [...]}]
            if not (isinstance(msg, list) and len(msg) >= 2):
                continue
            msg_type, msg_data = msg[0], msg[1]
            if msg_type != "execution_cached" or not isinstance(msg_data, dict):
                continue
            nodes = msg_data.get("nodes")
            if not isinstance(nodes, list):
                continue
            for n in nodes:
                nid = str(n)
                if nid not in self.nodes_completed:
                    self.nodes_completed.append(nid)


def _ancestors(node_id: str, deps: Dict[str, List[str]]) -> List[str]:
    """Return all upstream ancestors (transitive) using a deps map."""
    out: List[str] = []
    seen = set()
    stack = list(deps.get(node_id, []) or [])
    while stack:
        cur = str(stack.pop())
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        for p in deps.get(cur, []) or []:
            if p not in seen:
                stack.append(str(p))
    return out


class ProgressPrinter:
    """Progress callback that outputs enriched event data.
    
    Supports JSON output or custom string formatting with optional event type filtering.
    """

    def __init__(
        self,
        *,
        file=None,
        format: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        raw: bool = False,
    ):
        """
        Args:
            file: Output stream (default: stdout)
            format: Output format (default: "json")
                   - "json" or None: Output compact JSON per event
                   - Any other string: Format string with tokens like {node_current}, {node_progress}, etc.
                     Example: "minor:{node_current}:{node_progress} major:{nodes_progress:.2%}"
            event_types: Optional list of event types to include (e.g., ["progress", "submitted", "completed"])
                        If None, all events are shown. Acts as an allowlist filter.
            raw: If True, JSON output prints the original websocket payload (event["raw"]) when present.
                 This is useful for debugging ComfyUI event shapes. (Formatting mode still uses enriched fields.)
        """
        self._file = file
        self._format = format
        self._event_types = set(event_types) if event_types else None
        self._raw = bool(raw)
        self._last_json = None

    def __call__(self, event: Dict[str, Any]) -> None:
        # Filter by event type if specified
        if self._event_types is not None:
            event_type = event.get("type")
            if event_type not in self._event_types:
                return
        
        if self._format and self._format != "json":
            self._print_formatted(event)
        else:
            self._print_json(event)

    def _print_json(self, event: Dict[str, Any]) -> None:
        """Output event as compact JSON."""
        obj = event
        if self._raw:
            raw_obj = event.get("raw")
            if isinstance(raw_obj, dict):
                obj = raw_obj
        json_str = json.dumps(obj, separators=(",", ":"))
        # Avoid duplicate output
        if json_str != self._last_json:
            print(json_str, file=self._file)
            self._last_json = json_str

    def _print_formatted(self, event: Dict[str, Any]) -> None:
        """Format event using format string with token replacement."""
        try:
            # Build format kwargs from event fields
            kwargs = {}
            for key, value in event.items():
                if isinstance(value, (str, int, float)):
                    kwargs[key] = value
                elif isinstance(value, list):
                    kwargs[key] = len(value)
                else:
                    kwargs[key] = str(value)

            # Special handling for nodes_progress as percentage
            if "nodes_progress" in kwargs:
                kwargs["nodes_progress"] = float(kwargs["nodes_progress"])

            output = self._format.format(**kwargs)
            print(output, file=self._file)
        except (KeyError, ValueError) as e:
            # Fallback to JSON if format fails
            print(f"Format error: {e}", file=self._file)
            self._print_json(event)


def _http_to_ws_url(server_url: str, *, client_id: str) -> str:
    u = urllib.parse.urlparse(server_url)
    scheme = u.scheme.lower()
    if scheme not in ("http", "https", "ws", "wss"):
        raise ValueError(f"Unsupported server_url scheme: {u.scheme!r}")

    ws_scheme = "wss" if scheme in ("https", "wss") else "ws"
    host = u.hostname or "localhost"
    port = u.port
    if port is None:
        port = 443 if ws_scheme == "wss" else 80

    # ComfyUI uses /ws?clientId=...
    path = "/ws"
    query = urllib.parse.urlencode({"clientId": client_id})
    return f"{ws_scheme}://{host}:{port}{path}?{query}"


def _make_sec_websocket_key() -> str:
    return base64.b64encode(os.urandom(16)).decode("ascii")


def _expected_accept(key: str) -> str:
    raw = (key + _GUID).encode("ascii")
    digest = hashlib.sha1(raw).digest()  # nosec - RFC6455
    return base64.b64encode(digest).decode("ascii")


class StdlibWsTransport:
    """Minimal RFC6455 client for receiving ComfyUI websocket messages."""

    def __init__(self) -> None:
        self._sock: Optional[socket.socket] = None
        self._buf = bytearray()

    def connect(self, ws_url: str, *, timeout: float = 30.0) -> None:
        u = urllib.parse.urlparse(ws_url)
        scheme = u.scheme.lower()
        if scheme not in ("ws", "wss"):
            raise ValueError(f"ws_url must be ws:// or wss://, got: {ws_url!r}")

        host = u.hostname or "localhost"
        port = u.port or (443 if scheme == "wss" else 80)
        path = u.path or "/"
        if u.query:
            path = f"{path}?{u.query}"

        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)

        if scheme == "wss":
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(sock, server_hostname=host)

        key = _make_sec_websocket_key()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(req.encode("ascii"))

        status_line, headers = self._read_http_response(sock)
        if "101" not in status_line:
            raise ConnectionError(f"WebSocket upgrade failed: {status_line.strip()}")

        accept = headers.get("sec-websocket-accept")
        exp = _expected_accept(key)
        if accept and accept.strip() != exp:
            raise ConnectionError("WebSocket upgrade failed: bad Sec-WebSocket-Accept")

        self._sock = sock

    def close(self) -> None:
        try:
            if self._sock is not None:
                try:
                    self._sock.close()
                finally:
                    self._sock = None
        finally:
            self._buf = bytearray()

    @staticmethod
    def _read_http_response(sock: socket.socket) -> Tuple[str, Dict[str, str]]:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1", errors="replace")
        lines = head.split("\r\n")
        status = lines[0] if lines else ""
        hdrs: Dict[str, str] = {}
        for ln in lines[1:]:
            if ":" not in ln:
                continue
            k, v = ln.split(":", 1)
            hdrs[k.strip().lower()] = v.strip()
        return status, hdrs

    def recv(self) -> Optional[Union[str, bytes]]:
        """Receive the next websocket message (text or bytes). None means closed."""
        if self._sock is None:
            raise RuntimeError("WebSocket not connected")

        while True:
            frame = self._recv_frame()
            if frame is None:
                return None
            opcode, payload = frame

            # Text
            if opcode == 0x1:
                try:
                    return payload.decode("utf-8")
                except UnicodeDecodeError:
                    return payload.decode("latin-1", errors="replace")

            # Binary
            if opcode == 0x2:
                return payload

            # Close
            if opcode == 0x8:
                return None

            # Ping -> Pong
            if opcode == 0x9:
                self._send_control(opcode=0xA, payload=payload)
                continue

            # Pong
            if opcode == 0xA:
                continue

            # Ignore anything else

    def _send_control(self, *, opcode: int, payload: bytes = b"") -> None:
        if self._sock is None:
            return

        # Client-to-server frames MUST be masked.
        fin_opcode = 0x80 | (opcode & 0x0F)
        mask_bit = 0x80
        ln = len(payload)
        if ln > 125:
            payload = payload[:125]
            ln = len(payload)

        header = bytearray([fin_opcode, mask_bit | ln])
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _recv_exact(self, n: int) -> bytes:
        if self._sock is None:
            raise RuntimeError("WebSocket not connected")

        while len(self._buf) < n:
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            self._buf.extend(chunk)

        if len(self._buf) < n:
            out = bytes(self._buf)
            self._buf = bytearray()
            return out

        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def _recv_frame(self) -> Optional[Tuple[int, bytes]]:
        b1b2 = self._recv_exact(2)
        if len(b1b2) < 2:
            return None
        b1, b2 = b1b2[0], b1b2[1]
        fin = (b1 & 0x80) != 0
        opcode = b1 & 0x0F
        masked = (b2 & 0x80) != 0
        ln = b2 & 0x7F

        if ln == 126:
            ext = self._recv_exact(2)
            if len(ext) < 2:
                return None
            ln = int.from_bytes(ext, "big")
        elif ln == 127:
            ext = self._recv_exact(8)
            if len(ext) < 8:
                return None
            ln = int.from_bytes(ext, "big")

        mask_key = b""
        if masked:
            mask_key = self._recv_exact(4)
            if len(mask_key) < 4:
                return None

        payload = self._recv_exact(ln)
        if len(payload) < ln:
            return None

        if masked and mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        # ComfyUI should send FIN frames; fragmentation is rare for these messages.
        if not fin:
            return opcode, payload

        return opcode, payload


def _iter_json_objects(text: str) -> Iterator[Dict[str, Any]]:
    """Yield one or more JSON objects from a text message."""

    dec = json.JSONDecoder()
    s = text.strip()
    i = 0
    n = len(s)
    while i < n:
        try:
            obj, end = dec.raw_decode(s, i)
        except Exception:
            return
        if isinstance(obj, dict):
            yield obj
        i = end
        while i < n and s[i].isspace():
            i += 1


def parse_comfy_event(
    raw_message: Union[str, bytes],
    *,
    client_id: Optional[str] = None,
    prompt_id: Optional[str] = None,
) -> List[WsEvent]:
    """Parse and normalize ComfyUI websocket messages.

    Returns a list because one websocket text frame may contain multiple JSON objects.
    """

    if isinstance(raw_message, bytes):
        try:
            text = raw_message.decode("utf-8")
        except UnicodeDecodeError:
            text = raw_message.decode("latin-1", errors="replace")
    else:
        text = raw_message

    out: List[WsEvent] = []
    for obj in _iter_json_objects(text):
        mtype = obj.get("type")
        raw_data = obj.get("data")
        # Keep `data` a dict for downstream logic.
        # If ComfyUI sends a list payload, wrap it as {"messages": [...]}.
        if isinstance(raw_data, dict):
            data = raw_data
        elif isinstance(raw_data, list):
            data = {"messages": raw_data}
        else:
            data = {}

        ev_type = mtype if isinstance(mtype, str) else "message"
        ts = time.time()
        ev = WsEvent(
            type=ev_type,
            data=data,
            ts=ts,
            client_id=client_id,
            prompt_id=prompt_id,
            raw=obj,
        )

        # Completion heuristic (mirrors comfyui_job.py intent)
        if ev_type == "executing" and data.get("node") is None:
            out.append(
                WsEvent(
                    type="completed",
                    data=data,
                    ts=ts,
                    client_id=client_id,
                    prompt_id=prompt_id,
                    raw=obj,
                )
            )
        elif ev_type in ("execution_error", "error"):
            out.append(
                WsEvent(
                    type="error",
                    data=data,
                    ts=ts,
                    client_id=client_id,
                    prompt_id=prompt_id,
                    raw=obj,
                )
            )

        out.append(ev)

    return out


def stream_comfy_events(
    server_url: str,
    *,
    client_id: str,
    prompt_id: Optional[str] = None,
    timeout: float = 30.0,
    wait_timeout: float = 60.0,
    idle_timeout: Optional[float] = None,
    transport: Optional[StdlibWsTransport] = None,
    prompt: Optional[Dict[str, Any]] = None,
    cached_nodes: Optional[List[str]] = None,
) -> Iterator[WsEvent]:
    """Connect to ComfyUI websocket and yield enriched events until completion/timeout.
    
    Args:
        server_url: ComfyUI server URL
        client_id: Client identifier
        prompt_id: Prompt identifier (optional)
        timeout: Connection/request timeout
        wait_timeout: Maximum time to wait for completion
        idle_timeout: Max seconds to wait without receiving any websocket messages before
                      raising (so callers can fall back to /history polling). If None,
                      uses env AUTOFLOW_WS_IDLE_TIMEOUT_S else defaults to 5.0.
        transport: Optional custom transport
        prompt: Optional prompt dict (API payload) to extract nodes_total from
        cached_nodes: Optional list of cached node IDs that won't execute
    
    Yields:
        WsEvent dicts with enriched progress fields
    """

    # Extract nodes_total from prompt if available
    nodes_total = []
    deps: Dict[str, List[str]] = {}
    if prompt and isinstance(prompt, dict):
        # Get node IDs in order (dict insertion order preserved in Python 3.7+)
        nodes_total = list(prompt.keys())
        # Build deps map + topo order for stable progress ordering
        try:
            from .dag import build_api_dag

            d = build_api_dag(prompt)
            nodes_total = d.nodes.toposort()
            deps = {n: d.deps(n) for n in nodes_total}
        except Exception:
            deps = {}

    # Initialize progress tracker (owned by stream_comfy_events so deps inference is always present)
    submission_time = time.time()
    tracker = ProgressTracker(
        nodes_total=nodes_total,
        time_submitted=submission_time,
        cached_nodes=cached_nodes or [],
        deps=deps,
    )

    try:
        if prompt_id:
            submitted_ev = WsEvent(
                type="submitted",
                ts=submission_time,
                client_id=client_id,
                prompt_id=prompt_id,
            )
            yield tracker.update(submitted_ev)

        # Connect websocket after emitting submitted (so the callback sees prompt_id immediately).
        ws_url = _http_to_ws_url(server_url, client_id=client_id)
        tr = transport or StdlibWsTransport()
        tr.connect(ws_url, timeout=timeout)

        eff_idle = idle_timeout
        if eff_idle is None:
            try:
                eff_idle = float(os.environ.get("AUTOFLOW_WS_IDLE_TIMEOUT_S", "5.0"))
            except Exception:
                eff_idle = 5.0
        eff_idle = max(0.5, float(eff_idle))
        last_msg_ts = time.time()

        deadline = time.time() + max(1.0, float(wait_timeout))
        done = False
        while not done and time.time() < deadline:
            try:
                msg = tr.recv()
            except (TimeoutError, socket.timeout):
                # No frame received within socket timeout. This is normal when queued/idle.
                # Keep waiting until wait_timeout is reached.
                if time.time() - last_msg_ts >= eff_idle:
                    raise TimeoutError(f"WebSocket idle for {eff_idle:.1f}s (no messages received)")
                continue
            if msg is None:
                break
            last_msg_ts = time.time()
            for ev in parse_comfy_event(msg, client_id=client_id, prompt_id=prompt_id):
                enriched = tracker.update(ev)
                yield enriched
                if enriched.get("type") in ("completed", "error"):
                    done = True
                    break

    finally:
        try:
            tr.close()
        except Exception:
            pass
