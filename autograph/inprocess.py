"""autoflow.inprocess

Experimental in-process execution for ComfyUI (target: ComfyUI 0.9.2).

This module is intentionally best-effort and opt-in:
- It is only used when you call execute(backend="inprocess")
- It lazy-imports ComfyUI internals at runtime
- If ComfyUI isn't importable (or internals changed), it raises a clear error and suggests using
  backend="server" (HTTP parity) instead.

Design goal: return a dict shape similar to HTTP submit:
  {"submit": {"prompt_id": ...}, "history": {prompt_id: history_item}}
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import time
import uuid
import contextlib
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

from .convert import WorkflowConverterError, comfyui_available

__all__ = [
    "InProcessSubmissionResult",
    "execute_prompt",
]


class InProcessSubmissionResult(dict):
    """
    Dict-like result similar to results.SubmissionResult, but produced in-process.

    Keys:
    - submit: {"prompt_id": "...", ...}
    - history: {prompt_id: {...}}
    """

    @property
    def prompt_id(self) -> Optional[str]:
        v = self.get("prompt_id")
        if isinstance(v, str) and v:
            return v
        sub = self.get("submit")
        if isinstance(sub, dict):
            pid = sub.get("prompt_id")
            return pid if isinstance(pid, str) else None
        return None

    def _history_item(self) -> Dict[str, Any]:
        pid = self.prompt_id
        if not pid:
            return {}
        h = self.get("history")
        if not isinstance(h, dict):
            return {}
        item = h.get(pid)
        return item if isinstance(item, dict) else {}

    def fetch_files(
        self,
        *,
        output_types: Optional[Iterable[str]] = None,
        include_bytes: bool = True,
    ):
        """
        Offline/serverless equivalent of SubmissionResult.fetch_files().

        Loads registered output refs from the in-process history and reads the corresponding
        files directly from disk (no HTTP `/view`).
        """
        item = self._history_item()
        outputs = item.get("outputs")
        if not isinstance(outputs, dict):
            from .results import FilesResult

            return FilesResult([])

        # Local import to avoid hard dependency when this module is imported outside ComfyUI env.
        from .results import FilesResult, FileResult

        want: Optional[set] = None
        if output_types is not None:
            want = {str(x) for x in output_types if isinstance(x, str) and x}

        def _iter_refs() -> Iterable[Dict[str, str]]:
            for _nid, node_out in outputs.items():
                if not isinstance(node_out, dict):
                    continue
                for kind, items in node_out.items():
                    if want is not None and str(kind) not in want:
                        continue
                    if not isinstance(items, list):
                        continue
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        fn = it.get("filename")
                        if not (isinstance(fn, str) and fn):
                            continue
                        yield {
                            "kind": str(kind),
                            "filename": str(fn),
                            "subfolder": str(it.get("subfolder", "") or ""),
                            "type": str(it.get("type", "output") or "output"),
                        }

        def _resolve_path(ref: Dict[str, str]) -> Optional[Path]:
            try:
                import folder_paths  # type: ignore

                t = str(ref.get("type", "output") or "output")
                if t == "temp":
                    base = Path(folder_paths.get_temp_directory())
                elif t == "input":
                    base = Path(folder_paths.get_input_directory())
                else:
                    # Default: output
                    base = Path(folder_paths.get_output_directory())
            except Exception:
                # Best-effort fallback: derive a ComfyUI root from imports and use its conventional dirs.
                root = _comfyui_root_from_imports()
                if root is None:
                    base = Path.cwd() / "output"
                else:
                    t = str(ref.get("type", "output") or "output")
                    if t == "temp":
                        base = root / "temp"
                    elif t == "input":
                        base = root / "input"
                    else:
                        base = root / "output"

            sub = ref.get("subfolder", "") or ""
            fn = ref.get("filename", "") or ""
            p = base / sub / fn
            return p

        out: List[FileResult] = []
        seen: set = set()
        for ref in _iter_refs():
            key = (ref.get("kind", ""), ref.get("filename", ""), ref.get("subfolder", ""), ref.get("type", ""))
            if key in seen:
                continue
            seen.add(key)

            p = _resolve_path(ref)
            entry: Dict[str, Any] = {"ref": ref}
            if p is not None:
                entry["path"] = str(p)
                if include_bytes:
                    try:
                        entry["bytes"] = p.read_bytes()
                    except Exception as e:
                        entry["error"] = str(e)
            out.append(FileResult(entry))

        return FilesResult(out)

    def fetch_images(self, *, include_bytes: bool = True):
        """
        Offline/serverless equivalent of SubmissionResult.fetch_images().
        """
        from .results import ImagesResult, ImageResult

        files = self.fetch_files(output_types=["images"], include_bytes=bool(include_bytes))
        return ImagesResult([ImageResult(dict(it)) for it in files])

    def save(
        self,
        *,
        kinds: Optional[Union[str, Iterable[str]]] = None,
        only: Optional[Union[str, Iterable[str], Path, Iterable[Path]]] = None,
        output_path: Optional[Union[str, Path]] = None,
        filename: str = "",
        overwrite: bool = False,
        index_offset: int = 0,
        regex_parser: Any = None,
        imagemagick_path: Optional[Union[str, Path]] = None,
        ffmpeg_path: Optional[Union[str, Path]] = None,
    ) -> List[Path]:
        """
        Offline/serverless equivalent of SubmissionResult.save().

        Reads registered output files from disk (bytes) and writes them using the existing
        FilesResult.save() pattern helpers.
        """
        if isinstance(kinds, str):
            want_kinds: Optional[List[str]] = [kinds]
        else:
            want_kinds = list(kinds) if kinds is not None else None

        files = self.fetch_files(output_types=want_kinds, include_bytes=True)
        if not files:
            raise ValueError("No registered outputs found to save.")
        return files.save(
            only=only,
            output_path=output_path,
            filename=filename,
            overwrite=overwrite,
            refresh=False,
            index_offset=int(index_offset),
            regex_parser=regex_parser,
            imagemagick_path=imagemagick_path,
            ffmpeg_path=ffmpeg_path,
        )


def _cleanup_comfyui(*, aggressive: bool = False) -> None:
    """
    Best-effort cleanup to reduce lingering GPU/CPU state after in-process execution.

    This cannot guarantee that all background threads created by custom nodes stop, but it
    tries to free common ComfyUI caches and GPU memory.
    """
    # ComfyUI model caches (if available)
    try:
        import comfy.model_management  # type: ignore

        mm = comfy.model_management
        for fn_name in ("unload_all_models", "cleanup_models", "soft_empty_cache", "free_memory"):
            fn = getattr(mm, fn_name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
        if aggressive:
            # Some versions expose additional cleanup hooks.
            for fn_name in ("unload_all_models", "cleanup_models"):
                fn = getattr(mm, fn_name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
    except Exception:
        pass

    # Torch GPU cache (optional)
    try:
        import torch  # type: ignore

        if hasattr(torch, "cuda") and callable(getattr(torch.cuda, "empty_cache", None)):
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
    except Exception:
        pass


_EXECUTOR_CACHE: Dict[str, Any] = {}


class _RoutesStub:
    """
    Minimal aiohttp-like routes stub for custom nodes that register endpoints.

    Supports usage patterns like:
      @PromptServer.instance.routes.post("/path")
      @PromptServer.instance.routes.get("/path")
    """

    def _decorator(self, *_args: Any, **_kwargs: Any):
        def _wrap(fn):
            return fn

        return _wrap

    def post(self, *_args: Any, **_kwargs: Any):
        return self._decorator(*_args, **_kwargs)

    def get(self, *_args: Any, **_kwargs: Any):
        return self._decorator(*_args, **_kwargs)

    def put(self, *_args: Any, **_kwargs: Any):
        return self._decorator(*_args, **_kwargs)

    def delete(self, *_args: Any, **_kwargs: Any):
        return self._decorator(*_args, **_kwargs)


class _PromptServerStub:
    """
    Minimal PromptServer-like object for import-time expectations of some custom nodes.

    This does NOT provide HTTP or websocket behavior; it exists so nodes that do
    `PromptServer.instance...` at import time don't crash.
    """

    def __init__(self):
        self.client_id = None
        self.routes = _RoutesStub()
        self.prompt_queue = None
        # Common state fields referenced by some custom nodes (import-time/runtime).
        # These are best-effort; in serverless mode they're only used for previews/progress helpers.
        self.last_node_id = None
        self.last_prompt_id = None
        self.prompt_id = None
        self.server = self  # some nodes reach via `serv.server.*`
        self._on_prompt_handlers = []

    def add_on_prompt_handler(self, fn) -> None:
        self._on_prompt_handlers.append(fn)

    def send_sync(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        return None

    def send(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        return None


def _ensure_promptserver_instance() -> Any:
    """
    Ensure `server.PromptServer.instance` exists for custom nodes that reference it at import time.
    """
    try:
        import server  # type: ignore

        ps_cls = getattr(server, "PromptServer", None)
        if ps_cls is None:
            return None
        inst = getattr(ps_cls, "instance", None)
        if inst is None:
            stub = _PromptServerStub()
            try:
                setattr(ps_cls, "instance", stub)
            except Exception:
                pass
            return stub
        return inst
    except Exception:
        return None


from .convert import _looks_like_comfyui_root  # single source of truth


def _find_comfyui_root_fs() -> Optional[Path]:
    """
    Find a ComfyUI root without importing ComfyUI (to avoid early import shadowing).
    """
    roots = [Path.cwd()]
    for sp in list(sys.path):
        if not isinstance(sp, str) or not sp:
            continue
        try:
            roots.append(Path(sp))
        except Exception:
            continue
    for r in roots:
        if _looks_like_comfyui_root(r):
            return r
    return None


def _comfyui_root_from_imports() -> Optional[Path]:
    """
    Best-effort locate ComfyUI repo root from imported module locations.
    """
    try:
        import comfy  # type: ignore

        p = Path(getattr(comfy, "__file__", "")).resolve()
        # comfy/__init__.py -> comfy/ -> repo root
        return p.parent.parent if p.name else None
    except Exception:
        return None


def _ensure_comfyui_sys_path(root: Optional[Path] = None) -> Optional[Path]:
    """
    Ensure ComfyUI root is at the front of sys.path to avoid import shadowing.

    This specifically mitigates cases where a different `utils` module is already importable,
    causing ComfyUI imports like `from utils.install_util import ...` to fail.
    """
    root = root or _find_comfyui_root_fs() or _comfyui_root_from_imports()
    if root is None:
        return None

    root_s = str(root)
    # Put ComfyUI root first so `import server`, `import utils.*`, etc. resolve correctly.
    if not sys.path or sys.path[0] != root_s:
        try:
            if root_s in sys.path:
                sys.path.remove(root_s)
        except Exception:
            pass
        sys.path.insert(0, root_s)

    # If a non-ComfyUI module is already loaded, drop it so ComfyUI's tree can load.
    def _purge(name: str) -> None:
        try:
            m = sys.modules.get(name)
            if m is None:
                return
            mfile = getattr(m, "__file__", None)
            if isinstance(mfile, str) and mfile:
                mp = Path(mfile).resolve()
                if root not in mp.parents:
                    sys.modules.pop(name, None)
                    return
                # Special case: ComfyUI expects `utils` to be a *package*.
                if name == "utils" and not hasattr(m, "__path__"):
                    sys.modules.pop(name, None)
                    return
            else:
                # No __file__ (namespace/built). Remove to be safe.
                sys.modules.pop(name, None)
        except Exception:
            return

    # Purge common shadowing culprits that affect ComfyUI imports.
    for mod in ("utils", "server", "app", "nodes"):
        _purge(mod)

    return root


def _init_comfyui_runtime(*, init_extra_nodes: bool = False) -> None:
    """
    Best-effort ComfyUI runtime init.

    In many environments, importing `nodes` triggers custom node discovery and mapping.
    """
    # Fix sys.path first to avoid shadowing during imports.
    root = _ensure_comfyui_sys_path()

    # Fast/quiet check (filesystem markers), then import-based check.
    if not comfyui_available(verify=False) and root is None:
        raise WorkflowConverterError(
            "ComfyUI python modules not available. Run inside the ComfyUI repo+venv (or use backend='server')."
        )

    try:
        # Trigger common imports used by autoflow direct-mode.
        import comfy.samplers  # noqa: F401
        import comfy.sd  # noqa: F401
        _ensure_promptserver_instance()
        import nodes  # noqa: F401
    except Exception as e:
        raise WorkflowConverterError(
            "ComfyUI python modules not available. Run inside the ComfyUI repo+venv (or use backend='server')."
        ) from e

    # Some versions expose an explicit custom-node init helper.
    # This can trigger background tasks / downloads depending on installed custom nodes,
    # so we keep it opt-in for serverless execution.
    if init_extra_nodes:
        try:
            init_extra = getattr(nodes, "init_extra_nodes", None)
            if callable(init_extra):
                r = init_extra()
                # ComfyUI 0.9.x may expose init_extra_nodes as async.
                if inspect.isawaitable(r):
                    # Prefer running to completion in sync contexts. If we're already inside a running
                    # loop, skip rather than scheduling background work (background tasks can keep the
                    # process “busy” and spam stdout after execution completes).
                    try:
                        asyncio.run(r)
                    except RuntimeError:
                        # Already running loop (e.g., notebook/async app). In serverless execute we
                        # prefer determinism: do not schedule background tasks here.
                        pass
        except Exception:
            pass


def _get_executor(*, init_extra_nodes: bool = False) -> Tuple[Any, Any]:
    """
    Return (server_like, executor) for ComfyUI execution.

    This is best-effort and version-sensitive.
    """
    cached = _EXECUTOR_CACHE.get("executor")
    if cached is not None:
        return _EXECUTOR_CACHE["server"], cached

    _init_comfyui_runtime(init_extra_nodes=bool(init_extra_nodes))

    # Import ComfyUI internals lazily.
    try:
        import execution  # type: ignore
    except Exception as e:
        raise WorkflowConverterError(
            "Could not import ComfyUI 'execution' module for in-process execution; use backend='server'."
        ) from e

    # Many versions route event sending through a PromptServer (server.py).
    server_obj = _ensure_promptserver_instance()
    try:
        import server  # type: ignore

        # Best-effort: instantiate a PromptServer if available. Some versions require an event loop;
        # for a minimal spike, we avoid hard-coupling to asyncio and allow executor to run without it.
        ps_cls = getattr(server, "PromptServer", None)
        if ps_cls is not None:
            try:
                server_obj = ps_cls()
                try:
                    # Keep import-time singleton available.
                    setattr(ps_cls, "instance", server_obj)
                except Exception:
                    pass
            except TypeError:
                # Some versions: PromptServer(loop)
                server_obj = None
    except Exception:
        pass

    if server_obj is None:
        server_obj = _PromptServerStub()

    ex_cls = getattr(execution, "PromptExecutor", None)
    if ex_cls is None:
        raise WorkflowConverterError(
            "ComfyUI 'execution.PromptExecutor' not found. Internals may have changed; use backend='server'."
        )

    # Try a few common ctor shapes.
    try:
        executor = ex_cls(server_obj)
    except TypeError:
        try:
            executor = ex_cls()
        except Exception as e:
            raise WorkflowConverterError(
                "Could not construct ComfyUI PromptExecutor. Use backend='server' for now."
            ) from e

    _EXECUTOR_CACHE["server"] = server_obj
    _EXECUTOR_CACHE["executor"] = executor
    return server_obj, executor


def _call_execute(
    executor: Any, prompt: Dict[str, Any], prompt_id: str, extra_data: Optional[Dict[str, Any]]
) -> Tuple[Any, Dict[str, Any]]:
    """
    Call executor.execute(...) across a few known signatures.
    """
    fn = getattr(executor, "execute", None)
    if not callable(fn):
        raise WorkflowConverterError("ComfyUI PromptExecutor has no execute() method; use backend='server'.")

    # Common patterns observed across ComfyUI revisions:
    # - execute(prompt, prompt_id, extra_data, outputs)
    # - execute(prompt, prompt_id, extra_data)
    # - execute(prompt, prompt_id)
    outputs: Dict[str, Any] = {}
    eff_extra = extra_data if isinstance(extra_data, dict) else {}
    for args in (
        (prompt, prompt_id, eff_extra, outputs),
        (prompt, prompt_id, eff_extra),
        (prompt, prompt_id),
    ):
        try:
            return fn(*args), outputs
        except TypeError:
            continue
    # Last try: some versions may use keywords.
    try:
        return fn(prompt=prompt, prompt_id=prompt_id, extra_data=eff_extra, outputs=outputs), outputs
    except TypeError as e:
        raise WorkflowConverterError(
            "ComfyUI PromptExecutor.execute signature not recognized. Use backend='server' for now."
        ) from e


def execute_prompt(
    prompt: Dict[str, Any],
    *,
    client_id: str = "autoflow",
    prompt_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    init_extra_nodes: bool = False,
    cleanup: bool = True,
) -> InProcessSubmissionResult:
    """
    Execute an API-format prompt dict in-process (experimental).

    Returns an InProcessSubmissionResult with:
    - submit: {"prompt_id": ...}
    - history: {prompt_id: {...}}
    """
    if not isinstance(prompt, dict) or not prompt:
        raise WorkflowConverterError("prompt must be a non-empty dict (ApiFlow format).")

    pid = str(prompt_id or uuid.uuid4())
    ts0 = time.time()

    # Ensure ComfyUI is importable/initialized before running.
    _init_comfyui_runtime(init_extra_nodes=bool(init_extra_nodes))

    def _emit(ev_type: str, data: Optional[Dict[str, Any]] = None, *, raw: Optional[Dict[str, Any]] = None) -> None:
        if on_event is None:
            return
        try:
            on_event(
                {
                    "type": ev_type,
                    "data": data or {},
                    "ts": time.time(),
                    "client_id": client_id,
                    "prompt_id": pid,
                    "detected_by": "inprocess",
                    "raw": raw or {},
                    "time_submitted": ts0,
                    "time_elapsed_s": max(0.0, time.time() - ts0),
                }
            )
        except Exception:
            # Never let callback errors break execution.
            pass

    _emit("submitted", {})

    mode = None
    if isinstance(extra, dict):
        mode = extra.get("autoflow_inprocess_mode")
    mode = (str(mode) if mode is not None else "nodes").strip().lower()

    def _infer_outputs_from_disk(prompt_dict: Dict[str, Any], *, since_ts: float) -> Dict[str, Any]:
        """
        Best-effort output inference for common nodes (notably SaveImage) by scanning ComfyUI output dir.

        This is a fallback for in-process runs where ComfyUI doesn't populate `outputs`
        in a history-compatible way.
        """
        save_prefixes: List[Tuple[str, str]] = []
        for nid, node in prompt_dict.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") != "SaveImage":
                continue
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            pref = inputs.get("filename_prefix")
            if isinstance(pref, str) and pref:
                save_prefixes.append((str(nid), pref))
        if not save_prefixes:
            return {}

        out_dir: Optional[Path] = None
        try:
            import folder_paths  # type: ignore

            god = getattr(folder_paths, "get_output_directory", None)
            if callable(god):
                out_dir = Path(str(god()))
            else:
                od = getattr(folder_paths, "output_directory", None)
                if isinstance(od, str) and od:
                    out_dir = Path(od)
        except Exception:
            out_dir = None

        if out_dir is None or not out_dir.exists():
            return {}

        def _iter_recent(prefix: str) -> Iterable[Dict[str, str]]:
            try:
                for de in out_dir.iterdir():
                    try:
                        if not de.is_file():
                            continue
                        name = de.name
                        if not name.startswith(prefix):
                            continue
                        st = de.stat()
                        if st.st_mtime < (since_ts - 1.0):
                            continue
                        yield {"filename": name, "subfolder": "", "type": "output"}
                    except Exception:
                        continue
            except Exception:
                return

        inferred: Dict[str, Any] = {}
        for nid, prefix in save_prefixes:
            imgs = list(_iter_recent(prefix))
            if imgs:
                inferred[nid] = {"images": imgs}
        return inferred

    # ---------------------------------------------------------------------
    # Mode: nodes (direct node-class execution, similar spirit to ComfyUI-to-Python)
    # ---------------------------------------------------------------------
    if mode in ("nodes", "direct", "python"):
        _init_comfyui_runtime()
        try:
            import nodes  # type: ignore

            mappings = getattr(nodes, "NODE_CLASS_MAPPINGS", None)
        except Exception as e:
            raise WorkflowConverterError("Could not import ComfyUI nodes.NODE_CLASS_MAPPINGS") from e
        if not isinstance(mappings, dict):
            raise WorkflowConverterError("ComfyUI NODE_CLASS_MAPPINGS not available; cannot execute in-process.")

        try:
            from .dag import build_api_dag

            order = build_api_dag(prompt).nodes.toposort()
        except Exception:
            order = [str(k) for k in prompt.keys()]

        # Optional torch inference_mode for performance / parity (torch is part of ComfyUI env).
        try:
            import torch  # type: ignore

            cm = torch.inference_mode()
        except Exception:
            cm = contextlib.nullcontext()

        def _resolve(v: Any, results: Dict[str, Any]) -> Any:
            # Ref: ["4", 0]
            if isinstance(v, (list, tuple)):
                if len(v) == 2 and str(v[0]) in results and isinstance(v[1], int):
                    src = str(v[0])
                    idx = int(v[1])
                    try:
                        return results[src][idx]
                    except Exception:
                        return results[src]
                return [ _resolve(x, results) for x in v ]
            if isinstance(v, dict):
                return {k: _resolve(x, results) for k, x in v.items()}
            return v

        results: Dict[str, Any] = {}
        outputs: Dict[str, Any] = {}
        inst_cache: Dict[str, Any] = {}
        ps_inst = _ensure_promptserver_instance()

        # Note: we intentionally do not emit synthetic progress events here.
        # Some nodes print their own progress to stdout (e.g. samplers), and ComfyUI-native
        # progress events are only available when the underlying executor emits them.

        with cm:
            for nid in order:
                node = prompt.get(nid)
                if not isinstance(node, dict):
                    continue
                ct = node.get("class_type")
                if not isinstance(ct, str) or not ct:
                    continue
                cls = mappings.get(ct)
                if cls is None:
                    raise WorkflowConverterError(f"Node class_type '{ct}' not found in NODE_CLASS_MAPPINGS")

                # Keep server singleton state moving for preview/progress helpers in some custom nodes.
                if ps_inst is not None:
                    try:
                        ps_inst.client_id = client_id
                        ps_inst.last_node_id = nid
                        ps_inst.last_prompt_id = pid
                        ps_inst.prompt_id = pid
                    except Exception:
                        pass

                _emit("executing", {"node": str(nid), "class_type": ct})

                inst = inst_cache.get(ct)
                if inst is None:
                    try:
                        inst = cls()
                    except Exception:
                        inst = cls
                    inst_cache[ct] = inst

                fn_name = getattr(cls, "FUNCTION", None) or getattr(inst, "FUNCTION", None)
                if not isinstance(fn_name, str) or not fn_name:
                    # Some nodes may expose __call__ directly.
                    fn_name = "__call__"
                fn = getattr(inst, fn_name, None)
                if not callable(fn):
                    raise WorkflowConverterError(f"Node '{ct}' has no callable FUNCTION '{fn_name}'")

                inps = node.get("inputs")
                if not isinstance(inps, dict):
                    inps = {}
                kwargs = {k: _resolve(v, results) for k, v in inps.items()}

                ret = fn(**kwargs)

                ui = None
                vals = ret
                if isinstance(ret, dict):
                    ui = ret.get("ui") if isinstance(ret.get("ui"), dict) else None
                    if "result" in ret:
                        vals = ret.get("result")
                if isinstance(vals, tuple):
                    out_vals = vals
                elif isinstance(vals, list):
                    out_vals = tuple(vals)
                else:
                    out_vals = (vals,)

                results[str(nid)] = out_vals

                # History-style outputs: prefer UI payloads.
                if isinstance(ui, dict) and ui:
                    node_out: Dict[str, Any] = {}
                    for kind, items in ui.items():
                        if isinstance(kind, str) and isinstance(items, list):
                            # Expect list of dict refs.
                            node_out[kind] = items
                    if node_out:
                        outputs[str(nid)] = node_out

                _emit("executed", {"node": str(nid), "class_type": ct})

        history_item: Dict[str, Any] = {
            "status": {"completed": True, "status_str": "completed", "messages": []},
            "outputs": outputs,
            "meta": {},
            "prompt": prompt,
        }

        if isinstance(history_item.get("outputs"), dict) and not history_item["outputs"]:
            inferred = _infer_outputs_from_disk(prompt, since_ts=ts0)
            if inferred:
                history_item["outputs"] = inferred

        _emit("completed", {"status": history_item.get("status"), "outputs": history_item.get("outputs")})
        return InProcessSubmissionResult({"submit": {"prompt_id": pid}, "history": {pid: history_item}, "prompt_id": pid})

    # ---------------------------------------------------------------------
    # Mode: executor (PromptExecutor; kept for future parity work)
    # ---------------------------------------------------------------------
    server_obj, executor = _get_executor(init_extra_nodes=bool(init_extra_nodes))

    recorded_events = []

    def _record_event(ev_type: Optional[str], ev_data: Optional[Dict[str, Any]], *, raw: Optional[Dict[str, Any]] = None) -> None:
        if not isinstance(ev_type, str) or not ev_type:
            return
        if not isinstance(ev_data, dict):
            ev_data = {}
        recorded_events.append({"type": ev_type, "data": ev_data, "raw": raw or {}})
        # Also forward as a normal callback event (best-effort parity).
        _emit(ev_type, ev_data, raw=raw)

    # Best-effort attempt to hook server-side event sending into on_event if possible.
    # (This may not work across versions; it's purely additive.)
    if server_obj is not None:
        try:
            send_fn = getattr(server_obj, "send_sync", None) or getattr(server_obj, "send", None)
            if callable(send_fn) and not getattr(send_fn, "_autoflow_wrapped", False):

                def _wrapped_send(*args: Any, **kwargs: Any):
                    # Try to parse common shapes like (event_type, data) or (sid, event_type, data).
                    ev_type = None
                    ev_data = None
                    if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], dict):
                        ev_type, ev_data = args[0], args[1]
                    elif len(args) >= 3 and isinstance(args[1], str) and isinstance(args[2], dict):
                        ev_type, ev_data = args[1], args[2]
                    if isinstance(ev_type, str) and isinstance(ev_data, dict):
                        _record_event(
                            ev_type,
                            ev_data,
                            raw={"args": [repr(a) for a in args[:3]], "kwargs": {k: repr(v) for k, v in kwargs.items()}},
                        )
                    return send_fn(*args, **kwargs)

                setattr(_wrapped_send, "_autoflow_wrapped", True)
                if getattr(server_obj, "send_sync", None) is send_fn:
                    server_obj.send_sync = _wrapped_send  # type: ignore[attr-defined]
                elif getattr(server_obj, "send", None) is send_fn:
                    server_obj.send = _wrapped_send  # type: ignore[attr-defined]
        except Exception:
            pass

    try:
        extra_data = extra if isinstance(extra, dict) else {}
        # Seed a couple fields commonly used by ComfyUI internals (safe no-ops if ignored).
        if "client_id" not in extra_data:
            extra_data["client_id"] = client_id
        if "preview_method" not in extra_data:
            extra_data["preview_method"] = None

        _exec_ret, exec_outputs = _call_execute(executor, prompt, pid, extra_data)
    except WorkflowConverterError:
        raise
    except Exception as e:
        _emit("error", {"error": str(e)}, raw={"exception": repr(e)})
        raise WorkflowConverterError(f"In-process execution failed: {e}") from e
    finally:
        if cleanup:
            try:
                _cleanup_comfyui()
            except Exception:
                pass
            try:
                _EXECUTOR_CACHE.clear()
            except Exception:
                pass

    def _outputs_from_recorded(evts: Any) -> Dict[str, Any]:
        """
        Best-effort build history-style outputs from recorded `executed`-like events.
        """
        out: Dict[str, Any] = {}
        if not isinstance(evts, list):
            return out
        for ev in evts:
            if not isinstance(ev, dict):
                continue
            t = ev.get("type")
            d = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if t not in ("executed", "execution_end", "execution_error", "execution_interrupted", "error"):
                continue
            node_id = d.get("node")
            if node_id is None:
                continue
            nid = str(node_id)
            # Common shapes:
            # - {"output": {...}}
            # - {"outputs": {...}}
            # - full payload itself is the output-ish dict
            if isinstance(d.get("output"), dict):
                out[nid] = d.get("output")
            elif isinstance(d.get("outputs"), dict):
                out[nid] = d.get("outputs")
            else:
                out[nid] = d
        return out

    # Best-effort history extraction: if ComfyUI provides a prompt queue/history object, use it.
    history_item: Dict[str, Any] = {
        "status": {"completed": True, "status_str": "completed", "messages": []},
        "outputs": exec_outputs if isinstance(exec_outputs, dict) and exec_outputs else _outputs_from_recorded(recorded_events),
        "meta": {},
        "prompt": prompt,
    }

    # Last resort: infer SaveImage outputs from disk.
    if isinstance(history_item.get("outputs"), dict) and not history_item["outputs"]:
        inferred = _infer_outputs_from_disk(prompt, since_ts=ts0)
        if inferred:
            history_item["outputs"] = inferred

    try:
        # Try server_obj.prompt_queue.get_history(prompt_id) shapes.
        pq = getattr(server_obj, "prompt_queue", None) if server_obj is not None else None
        if pq is not None:
            gh = getattr(pq, "get_history", None)
            if callable(gh):
                h = gh(pid)
                if isinstance(h, dict):
                    # Some implementations return a full history dict; some return item.
                    if pid in h and isinstance(h[pid], dict):
                        history_item = h[pid]
                    else:
                        history_item = h
    except Exception:
        pass

    _emit("completed", {"status": history_item.get("status"), "outputs": history_item.get("outputs")})

    out = InProcessSubmissionResult({"submit": {"prompt_id": pid}, "history": {pid: history_item}, "prompt_id": pid})
    return out


