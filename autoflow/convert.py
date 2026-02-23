"""autoflow.convert

Conversion core: ComfyUI workspace workflow.json -> API payload (ApiFlow format).

This module is stdlib-only by default. It supports:
- offline conversion with a saved node_info JSON file
- optional online node_info fetch (explicit via server_url or env var)
- optional direct mode importing ComfyUI modules (when available)
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple, Union

from .defaults import (
    DEFAULT_HTTP_TIMEOUT_S,
    DEFAULT_INCLUDE_META,
    DEFAULT_JSON_ENSURE_ASCII,
    DEFAULT_JSON_INDENT,
    DEFAULT_SUBGRAPH_MAX_DEPTH,
    DEFAULT_USE_API,
    ENV_NODE_INFO_SOURCE,
)
from .origin import NodeInfoOrigin

logger = logging.getLogger(__name__)

_NODE_INFO_SOURCE_CACHE: Dict[str, Dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Errors / structured results
# ---------------------------------------------------------------------------


class WorkflowConverterError(Exception):
    """Custom exception for workflow conversion errors."""


class NodeInfoError(Exception):
    """Custom exception for node information errors."""


class ErrorSeverity(str, Enum):
    """Error severity levels for structured error reporting."""

    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ErrorCategory(str, Enum):
    """Error categories for better error classification."""

    VALIDATION = "validation"
    IO = "io"
    NETWORK = "network"
    NODE_PROCESSING = "node_processing"
    CONVERSION = "conversion"


class ConversionError(NamedTuple):
    """Structured error information for conversion issues."""

    category: ErrorCategory
    severity: ErrorSeverity
    message: str
    node_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class ConversionResult(NamedTuple):
    """Structured result containing both data and error information."""

    success: bool
    data: Optional[Dict[str, Any]] = None
    errors: List[ConversionError] = []
    warnings: List[ConversionError] = []
    processed_nodes: int = 0
    skipped_nodes: int = 0
    total_nodes: int = 0


class ConvertResult:
    """
    Simple result object for \"convert with errors\" workflows.

    - .ok: True if conversion succeeded
    - .data: ApiFlow or None
    - .errors / .warnings: lists
    """

    def __init__(
        self,
        ok: bool,
        data: Optional[Any],
        errors: List["ConversionError"],
        warnings: List["ConversionError"],
        processed_nodes: int = 0,
        skipped_nodes: int = 0,
        total_nodes: int = 0,
    ):
        self.ok = ok
        self.data = data
        self.errors = errors
        self.warnings = warnings
        self.processed_nodes = processed_nodes
        self.skipped_nodes = skipped_nodes
        self.total_nodes = total_nodes

    def __repr__(self) -> str:
        return (
            f"ConvertResult(ok={self.ok}, errors={len(self.errors)}, "
            f"warnings={len(self.warnings)}, nodes={self.processed_nodes}/{self.total_nodes})"
        )

    @property
    def success(self) -> bool:
        return self.ok

    def save(
        self,
        output_path: Union[str, Path],
        indent: int = DEFAULT_JSON_INDENT,
        ensure_ascii: bool = DEFAULT_JSON_ENSURE_ASCII,
    ) -> Path:
        if self.data is None:
            raise ValueError("No data to save (conversion produced no output).")
        return self.data.save(output_path, indent=indent, ensure_ascii=ensure_ascii)

def validate_workflow_data(workflow_data: Dict[str, Any]) -> None:
    """Validate the structure of workspace workflow.json data."""
    if not isinstance(workflow_data, dict):
        raise WorkflowConverterError("Workflow data must be a dictionary")
    if "nodes" not in workflow_data:
        raise WorkflowConverterError("Workflow data missing 'nodes' field")
    if "links" not in workflow_data:
        raise WorkflowConverterError("Workflow data missing 'links' field")
    if not isinstance(workflow_data["nodes"], list):
        raise WorkflowConverterError("'nodes' field must be a list")
    if not isinstance(workflow_data["links"], list):
        raise WorkflowConverterError("'links' field must be a list")


# ---------------------------------------------------------------------------
# Workspace subgraphs
# ---------------------------------------------------------------------------


def _get_subgraph_defs(workflow_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    defs = workflow_data.get("definitions")
    if not isinstance(defs, dict):
        return {}
    subgraphs = defs.get("subgraphs")
    if not isinstance(subgraphs, list):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for sg in subgraphs:
        if not isinstance(sg, dict):
            continue
        sg_id = sg.get("id")
        if isinstance(sg_id, str) and sg_id:
            out[sg_id] = sg
    return out


def _flatten_subgraphs_once(workflow_data: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    sub_defs = _get_subgraph_defs(workflow_data)
    if not sub_defs:
        return workflow_data, False
    nodes = workflow_data.get("nodes")
    links = workflow_data.get("links")
    if not isinstance(nodes, list) or not isinstance(links, list):
        return workflow_data, False

    instance_nodes = [
        n for n in nodes if isinstance(n, dict) and isinstance(n.get("type"), str) and n.get("type") in sub_defs
    ]
    if not instance_nodes:
        return workflow_data, False

    wf = copy.deepcopy(workflow_data)
    nodes2: List[Dict[str, Any]] = [n for n in (wf.get("nodes") or []) if isinstance(n, dict)]
    links2: List[List[Any]] = [l for l in (wf.get("links") or []) if isinstance(l, list) and len(l) >= 6]

    orig_link_by_id: Dict[int, List[Any]] = {}
    for l in links2:
        try:
            orig_link_by_id[int(l[0])] = l
        except Exception:
            continue

    next_node_id = int(wf.get("last_node_id") or 0) + 1
    next_link_id = int(wf.get("last_link_id") or 0) + 1

    def _alloc_node_id() -> int:
        nonlocal next_node_id
        nid = next_node_id
        next_node_id += 1
        return nid

    def _alloc_link_id() -> int:
        nonlocal next_link_id
        lid = next_link_id
        next_link_id += 1
        return lid

    for inst in list(nodes2):
        inst_type = inst.get("type")
        if not isinstance(inst_type, str) or inst_type not in sub_defs:
            continue
        inst_id = inst.get("id")
        if not isinstance(inst_id, int):
            continue

        sg = sub_defs[inst_type]
        sg_nodes = sg.get("nodes")
        sg_links = sg.get("links")
        sg_inputs = sg.get("inputs")
        if not isinstance(sg_nodes, list) or not isinstance(sg_links, list) or not isinstance(sg_inputs, list):
            continue

        ext_in_link_ids = {int(l[0]) for l in links2 if len(l) >= 6 and l[3] == inst_id}
        ext_out_links = [l for l in links2 if len(l) >= 6 and l[1] == inst_id]
        ext_out_by_slot: Dict[int, List[List[Any]]] = {}
        for l in ext_out_links:
            try:
                slot = int(l[2])
            except Exception:
                continue
            ext_out_by_slot.setdefault(slot, []).append(l)
        ext_out_link_ids = {int(l[0]) for l in ext_out_links}

        nodes2 = [n for n in nodes2 if n is not inst]
        links2 = [l for l in links2 if int(l[0]) not in ext_in_link_ids and int(l[0]) not in ext_out_link_ids]

        inst_inputs_by_name: Dict[str, Optional[int]] = {}
        for inp in (inst.get("inputs") or []):
            if not isinstance(inp, dict):
                continue
            name = inp.get("name")
            link_id = inp.get("link")
            if isinstance(name, str):
                inst_inputs_by_name[name] = int(link_id) if isinstance(link_id, int) else None

        slot_origin: Dict[int, Optional[Tuple[int, int, Any]]] = {}
        for idx, sg_in in enumerate(sg_inputs):
            if not isinstance(sg_in, dict):
                continue
            nm = sg_in.get("name")
            if not isinstance(nm, str):
                continue
            ext_lid = inst_inputs_by_name.get(nm)
            if not isinstance(ext_lid, int):
                slot_origin[idx] = None
                continue
            ext_link = orig_link_by_id.get(ext_lid)
            if not ext_link or len(ext_link) < 6:
                slot_origin[idx] = None
                continue
            slot_origin[idx] = (int(ext_link[1]), int(ext_link[2]), ext_link[5])

        node_id_map: Dict[int, int] = {}
        for n in sg_nodes:
            if not isinstance(n, dict):
                continue
            old_id = n.get("id")
            if isinstance(old_id, int) and old_id >= 0:
                node_id_map[old_id] = _alloc_node_id()

        link_id_map: Dict[int, Optional[int]] = {}
        ext_link_rewire: Dict[int, int] = {}

        for lk in sg_links:
            if not isinstance(lk, dict):
                continue
            old_lid = lk.get("id")
            if not isinstance(old_lid, int):
                continue
            origin_id = lk.get("origin_id")
            target_id = lk.get("target_id")
            origin_slot = lk.get("origin_slot")
            target_slot = lk.get("target_slot")
            ltype = lk.get("type")
            if not (
                isinstance(origin_id, int)
                and isinstance(target_id, int)
                and isinstance(origin_slot, int)
                and isinstance(target_slot, int)
            ):
                continue

            if origin_id == -10:
                ext = slot_origin.get(origin_slot)
                if ext is None:
                    link_id_map[old_lid] = None
                    continue
                new_target = node_id_map.get(target_id)
                if new_target is None:
                    link_id_map[old_lid] = None
                    continue
                new_lid = _alloc_link_id()
                link_id_map[old_lid] = new_lid
                links2.append([new_lid, ext[0], ext[1], new_target, target_slot, ltype])
                continue

            if target_id == -20:
                new_origin = node_id_map.get(origin_id)
                if new_origin is None:
                    continue
                for ext_link in ext_out_by_slot.get(target_slot, []):
                    old_ext_id = int(ext_link[0])
                    new_lid = _alloc_link_id()
                    ext_link_rewire[old_ext_id] = new_lid
                    links2.append([new_lid, new_origin, origin_slot, int(ext_link[3]), int(ext_link[4]), ext_link[5]])
                continue

            new_origin = node_id_map.get(origin_id)
            new_target = node_id_map.get(target_id)
            if new_origin is None or new_target is None:
                link_id_map[old_lid] = None
                continue
            new_lid = _alloc_link_id()
            link_id_map[old_lid] = new_lid
            links2.append([new_lid, new_origin, origin_slot, new_target, target_slot, ltype])

        for n in sg_nodes:
            if not isinstance(n, dict):
                continue
            old_id = n.get("id")
            if not isinstance(old_id, int) or old_id < 0:
                continue
            new_n = copy.deepcopy(n)
            new_n["id"] = node_id_map[old_id]

            for inp in (new_n.get("inputs") or []):
                if not isinstance(inp, dict):
                    continue
                lid = inp.get("link")
                if isinstance(lid, int):
                    mapped = link_id_map.get(lid)
                    inp["link"] = mapped if isinstance(mapped, int) else None

            for outp in (new_n.get("outputs") or []):
                if not isinstance(outp, dict):
                    continue
                lst = outp.get("links")
                if isinstance(lst, list):
                    new_links = []
                    for lid in lst:
                        if not isinstance(lid, int):
                            continue
                        mapped = link_id_map.get(lid)
                        if isinstance(mapped, int):
                            new_links.append(mapped)
                    outp["links"] = new_links

            nodes2.append(new_n)

        if ext_link_rewire:
            for n in nodes2:
                for inp in (n.get("inputs") or []):
                    if not isinstance(inp, dict):
                        continue
                    lid = inp.get("link")
                    if isinstance(lid, int) and lid in ext_link_rewire:
                        inp["link"] = ext_link_rewire[lid]

    wf["nodes"] = nodes2
    wf["links"] = links2
    wf["last_node_id"] = max([int(n.get("id")) for n in nodes2 if isinstance(n.get("id"), int)] + [0])
    wf["last_link_id"] = max([int(l[0]) for l in links2 if isinstance(l, list) and l and isinstance(l[0], int)] + [0])
    return wf, True


def flatten_subgraphs(workflow_data: Dict[str, Any], *, max_depth: int = DEFAULT_SUBGRAPH_MAX_DEPTH) -> Dict[str, Any]:
    wf = copy.deepcopy(workflow_data)
    for _ in range(max_depth):
        wf2, changed = _flatten_subgraphs_once(wf)
        wf = wf2
        if not changed:
            break
    return wf


# ---------------------------------------------------------------------------
# File/node_info I/O + network fetch
# ---------------------------------------------------------------------------


def load_workflow_from_file(file_path: Union[str, Path]) -> Dict[str, Any]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = f.read()
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("Invalid workflow file: expected dictionary")
            validate_workflow_data(result)
            return result
    except FileNotFoundError:
        raise WorkflowConverterError(f"Workflow file not found: {file_path}")
    except json.JSONDecodeError as e:
        raise WorkflowConverterError(f"Invalid JSON in workflow file {file_path}: {str(e)}")
    except UnicodeDecodeError as e:
        raise WorkflowConverterError(f"Encoding error reading workflow file {file_path}: {str(e)}")
    except Exception as e:
        raise WorkflowConverterError(f"Unexpected error loading workflow file {file_path}: {str(e)}")


def save_workflow_to_file(workflow_data: Dict[str, Any], file_path: Union[str, Path]) -> None:
    try:
        output_path = Path(file_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(workflow_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        raise WorkflowConverterError(f"Failed to save workflow to {file_path}: {str(e)}")


def save_node_info_to_file(node_info: Dict[str, Any], file_path: Union[str, Path]) -> None:
    try:
        output_path = Path(file_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(node_info, f, indent=2, ensure_ascii=False)
    except Exception as e:
        raise WorkflowConverterError(f"Failed to save node_info to {file_path}: {str(e)}")


def load_node_info_from_file(file_path: Union[str, Path]) -> Dict[str, Any]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = f.read()
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("Invalid node_info file: expected dictionary")
            return result
    except FileNotFoundError:
        raise WorkflowConverterError(f"Object info file not found: {file_path}")
    except json.JSONDecodeError as e:
        raise WorkflowConverterError(f"Invalid JSON in node info file {file_path}: {str(e)}")
    except UnicodeDecodeError as e:
        raise WorkflowConverterError(f"Encoding error reading node info file {file_path}: {str(e)}")
    except Exception as e:
        raise WorkflowConverterError(f"Unexpected error loading node info file {file_path}: {str(e)}")


def fetch_node_info(server_url: str, timeout: int = DEFAULT_HTTP_TIMEOUT_S) -> Dict[str, Any]:
    url = f"{server_url.rstrip('/')}/object_info"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.code != 200:
                raise ValueError(f"Failed to fetch node_info: HTTP {response.code}")
            data = response.read().decode("utf-8")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("Invalid node_info response: expected dictionary")
            return result
    except urllib.error.URLError as e:
        raise ConnectionError(f"Could not connect to server {server_url}: {str(e)}")
    except urllib.error.HTTPError as e:
        raise ConnectionError(f"HTTP error when connecting to {server_url}: {e.code} - {e.reason}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON response from {url}: {str(e)}")
    except Exception as e:
        raise ConnectionError(f"Unexpected error connecting to {server_url}: {str(e)}")


def _is_http_url(value: Union[str, Path]) -> bool:
    """
    True if `value` looks like an HTTP(S) URL.

    NOTE: We intentionally avoid urllib.parse.urlparse-based scheme checks because Windows paths
    like `C:\\path\\file.json` can be misinterpreted as a URL scheme.
    """
    s = str(value)
    return s.startswith("http://") or s.startswith("https://")


def fetch_node_info_from_url(url: str, timeout: int = DEFAULT_HTTP_TIMEOUT_S) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.code != 200:
                raise ValueError(f"Failed to fetch node_info: HTTP {response.code}")
            data = response.read().decode("utf-8")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("Invalid node_info response: expected dictionary")
            return result
    except urllib.error.URLError as e:
        raise ConnectionError(f"Could not connect to URL {url}: {str(e)}")
    except urllib.error.HTTPError as e:
        raise ConnectionError(f"HTTP error when connecting to {url}: {e.code} - {e.reason}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON response from {url}: {str(e)}")


def _looks_like_comfyui_root(root: Path) -> bool:
    try:
        if not root.exists():
            return False
        if (root / "nodes.py").is_file() and (root / "comfy").is_dir():
            return True
    except Exception:
        return False
    return False


def _detect_comfyui_root_from_imports() -> Optional[Path]:
    """
    Best-effort detection of the ComfyUI repo root for 'modules' mode.

    This is used for provenance only (e.g. NodeInfo.source = "modules:/path/to/ComfyUI").
    """
    try:
        import nodes as nodes_mod  # type: ignore

        nf = getattr(nodes_mod, "__file__", None)
        if isinstance(nf, str) and nf:
            p = Path(nf).expanduser().resolve()
            for cand in (p.parent, p.parent.parent):
                if _looks_like_comfyui_root(cand):
                    return cand.resolve()
    except Exception:
        pass

    try:
        import comfy as comfy_mod  # type: ignore

        cf = getattr(comfy_mod, "__file__", None)
        if isinstance(cf, str) and cf:
            p = Path(cf).expanduser().resolve()
            for cand in (p.parent.parent, p.parent.parent.parent):
                if _looks_like_comfyui_root(cand):
                    return cand.resolve()
    except Exception:
        pass

    try:
        roots = [Path.cwd()] + [Path(p) for p in sys.path if isinstance(p, str) and p]
        for root in roots:
            if _looks_like_comfyui_root(root):
                return root.resolve()
    except Exception:
        pass

    return None


def comfyui_available(*, verify: bool = True) -> bool:
    """
    Check if we're in a ComfyUI environment.

    - verify=False: quick filesystem check for ComfyUI repo markers
    - verify=True: import ComfyUI modules and NODE_CLASS_MAPPINGS
    """
    if not verify:
        roots = [Path.cwd()] + [Path(p) for p in sys.path if isinstance(p, str) and p]
        for root in roots:
            if _looks_like_comfyui_root(root):
                return True
        return False
    try:
        import comfy.samplers  # noqa: F401
        import comfy.sd  # noqa: F401
        from nodes import NODE_CLASS_MAPPINGS  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def normalize_server_url(
    server_url: Optional[str],
    *,
    allow_env: bool = True,
    allow_none: bool = True,
) -> Optional[str]:
    if isinstance(server_url, str):
        server_url = server_url.strip()
        if server_url == "":
            server_url = None
    if server_url:
        return server_url
    if allow_env:
        env = os.environ.get("AUTOFLOW_COMFYUI_SERVER_URL")
        if isinstance(env, str) and env.strip():
            return env.strip()
    if allow_none:
        return None
    raise ValueError("Missing server_url. Pass server_url= or set AUTOFLOW_COMFYUI_SERVER_URL.")


def normalize_workflow_input(workflow_data: Union[Dict[str, Any], str, Path, Mapping]) -> Dict[str, Any]:
    if isinstance(workflow_data, Mapping):
        return dict(workflow_data)
    if isinstance(workflow_data, (str, Path)):
        return load_workflow_from_file(workflow_data)
    raise ValueError("workflow_data must be a dictionary or file path")


def _as_jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_as_jsonable(v) for v in value]
    if isinstance(value, list):
        return [_as_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _as_jsonable(v) for k, v in value.items()}
    return value


_EXTRA_NODES_INIT_DONE = False

# Approximate count of core nodes defined in ComfyUI's nodes.py.
# If NODE_CLASS_MAPPINGS has fewer than this many entries, extra nodes
# (comfy_extras/, comfy_api_nodes/, custom_nodes/) likely haven't been loaded.
_CORE_NODES_THRESHOLD = 100


def _fix_comfyui_imports() -> None:
    """Fix import-shadowing issues before loading extra nodes.

    **Problem 1 – ``utils`` shadowing:**
    ComfyUI has a top-level ``utils/`` package (with ``utils/install_util.py``
    etc.) that is required by ``server.py`` → ``app/frontend_management.py``.
    However, importing ``comfy.utils`` (which happens early when loading
    ``comfy.samplers`` / ``comfy.sd``) can poison Python's import resolution so
    that a bare ``import utils`` resolves to ``comfy/utils.py`` instead of the
    top-level ``utils/`` package.  This causes every node that does
    ``from server import PromptServer`` to fail with
    ``"'utils' is not a package"``.

    **Problem 2 – ``PromptServer.instance``:**
    Several custom nodes access ``PromptServer.instance`` at *import time*
    (e.g. to register routes or prompt handlers).  Outside the server,
    ``PromptServer()`` is never constructed so ``.instance`` doesn't exist.
    We set a lightweight stub so those module-level accesses don't crash.
    """
    import importlib
    import importlib.util

    # ── Fix 1: utils package ──────────────────────────────────────────
    existing = sys.modules.get("utils")
    if existing is None or not hasattr(existing, "__path__"):
        root = _detect_comfyui_root_from_imports()
        if root is not None:
            utils_init = root / "utils" / "__init__.py"
            if utils_init.is_file():
                spec = importlib.util.spec_from_file_location(
                    "utils",
                    str(utils_init),
                    submodule_search_locations=[str(root / "utils")],
                )
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules["utils"] = mod
                    try:
                        spec.loader.exec_module(mod)
                    except Exception:
                        pass

    # ── Fix 2: PromptServer.instance stub ─────────────────────────────
    _ensure_promptserver_instance()


class _NoOpProxy:
    """A proxy that silently absorbs any attribute access or call.

    Used to stub out ``PromptServer.instance`` so that custom nodes which
    access ``.routes``, ``.prompt_queue``, etc. at import time don't crash.
    """

    def __getattr__(self, name: str) -> "_NoOpProxy":
        return _NoOpProxy()

    def __call__(self, *args: object, **kwargs: object) -> "_NoOpProxy":
        return _NoOpProxy()

    def __bool__(self) -> bool:
        return False


class _AutoRoutes:
    """Mock for ``aiohttp.web.RouteTableDef`` that returns no-op decorators.

    Custom nodes use ``@PromptServer.instance.routes.post("/path")`` to
    register HTTP routes at import time.  This stub returns the decorated
    function unchanged.
    """

    def _noop_decorator(self, path: str, **kw: object):  # type: ignore
        def decorator(fn):  # type: ignore
            return fn
        return decorator

    # Cover all HTTP methods custom nodes might use.
    get = post = put = delete = patch = _noop_decorator
    static = _noop_decorator

    def __iter__(self):  # type: ignore
        return iter([])


def _ensure_promptserver_instance() -> None:
    """Set ``PromptServer.instance`` to a stub if the server isn't running."""
    try:
        from server import PromptServer  # type: ignore
    except ImportError:
        return

    if getattr(PromptServer, "instance", None) is not None:
        return  # Server is running – nothing to do.

    # Build a lightweight stub instance.
    class _StubInstance:
        routes = _AutoRoutes()
        prompt_queue = None
        client_session = None
        number = 0

        @staticmethod
        def add_on_prompt_handler(handler):  # type: ignore
            pass

        @staticmethod
        def send_progress_text(*args, **kwargs):  # type: ignore
            pass

        @staticmethod
        def send_sync(*args, **kwargs):  # type: ignore
            pass

        def __getattr__(self, name: str):  # type: ignore
            return _NoOpProxy()

    PromptServer.instance = _StubInstance()  # type: ignore


def _ensure_extra_nodes_loaded() -> None:
    """Lazily call ComfyUI's ``init_extra_nodes()`` if it hasn't run yet.

    ComfyUI's server calls ``init_extra_nodes()`` at startup which populates
    ``NODE_CLASS_MAPPINGS`` with nodes from ``comfy_extras/``,
    ``comfy_api_nodes/``, and ``custom_nodes/``.  When autoflow runs outside
    the server (standalone script / library usage), only the ~64 core nodes
    are present.  This helper detects that situation and performs the init.
    """
    global _EXTRA_NODES_INIT_DONE
    if _EXTRA_NODES_INIT_DONE:
        return

    try:
        from nodes import NODE_CLASS_MAPPINGS, init_extra_nodes  # type: ignore
    except ImportError:
        _EXTRA_NODES_INIT_DONE = True
        return

    # If the server already loaded extras, skip.
    if len(NODE_CLASS_MAPPINGS) >= _CORE_NODES_THRESHOLD:
        _EXTRA_NODES_INIT_DONE = True
        return

    import asyncio
    import logging

    # Fix import-shadowing so that `from server import PromptServer` works.
    _fix_comfyui_imports()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # We're inside an active event loop (e.g. Jupyter, server context).
        # Cannot use asyncio.run(); warn and skip.
        logging.warning(
            "autoflow: cannot load ComfyUI extra nodes from within a running "
            "event loop. NODE_CLASS_MAPPINGS may be incomplete (%d entries). "
            "Consider calling init_extra_nodes() before using NodeInfo('modules').",
            len(NODE_CLASS_MAPPINGS),
        )
        _EXTRA_NODES_INIT_DONE = True
        return

    logging.info(
        "autoflow: NODE_CLASS_MAPPINGS has only %d entries; "
        "loading extra/custom nodes via init_extra_nodes()...",
        len(NODE_CLASS_MAPPINGS),
    )
    try:
        # Suppress background threads spawned at import time by custom nodes
        # (e.g. ComfyUI-Manager's registry fetch).  These serve no purpose
        # when we're only importing nodes to inspect their class mappings.
        import threading

        _real_thread_start = threading.Thread.start

        def _suppressed_start(self_thread: threading.Thread) -> None:  # type: ignore
            logging.debug(
                "autoflow: suppressed background thread %r during init_extra_nodes()",
                self_thread.name,
            )

        threading.Thread.start = _suppressed_start  # type: ignore
        try:
            asyncio.run(init_extra_nodes())
        finally:
            threading.Thread.start = _real_thread_start  # type: ignore
    except Exception as exc:
        logging.warning("autoflow: init_extra_nodes() failed: %s", exc)
    finally:
        _EXTRA_NODES_INIT_DONE = True


def node_info_from_comfyui_modules() -> Dict[str, Any]:
    try:
        import comfy.samplers  # noqa: F401
        import comfy.sd  # noqa: F401
        from nodes import NODE_CLASS_MAPPINGS  # type: ignore
    except ImportError as e:
        raise NodeInfoError(
            "ComfyUI modules not found. Please run this script from the ComfyUI directory "
            "or use --server-url to fetch node information via API, "
            "or use --node-info-path to load from a saved file."
        ) from e

    _ensure_extra_nodes_loaded()

    out: Dict[str, Any] = {}
    for class_type, cls in NODE_CLASS_MAPPINGS.items():
        if not isinstance(class_type, str):
            continue
        info: Dict[str, Any] = {}

        inputs_def = getattr(cls, "INPUT_TYPES", None)
        if callable(inputs_def):
            try:
                inputs_def = inputs_def()
            except Exception:
                inputs_def = None
        if isinstance(inputs_def, dict):
            inputs: Dict[str, Any] = {}
            for section in ("required", "optional", "hidden"):
                section_inputs = inputs_def.get(section)
                if isinstance(section_inputs, dict):
                    inputs[section] = {k: _as_jsonable(v) for k, v in section_inputs.items()}
            if inputs:
                info["input"] = inputs

        return_types = getattr(cls, "RETURN_TYPES", None)
        if callable(return_types):
            try:
                return_types = return_types()
            except Exception:
                return_types = None
        if return_types is not None:
            info["output"] = _as_jsonable(return_types)

        output_names = getattr(cls, "RETURN_NAMES", None)
        if output_names is not None:
            info["output_name"] = _as_jsonable(output_names)

        output_is_list = getattr(cls, "OUTPUT_IS_LIST", None)
        if output_is_list is not None:
            info["output_is_list"] = _as_jsonable(output_is_list)

        output_tooltips = getattr(cls, "OUTPUT_TOOLTIPS", None)
        if output_tooltips is not None:
            info["output_tooltips"] = _as_jsonable(output_tooltips)

        info["name"] = class_type
        info["display_name"] = getattr(cls, "DISPLAY_NAME", class_type)
        info["description"] = getattr(cls, "DESCRIPTION", "")
        info["python_module"] = getattr(cls, "__module__", "nodes")
        info["category"] = getattr(cls, "CATEGORY", "")
        info["output_node"] = bool(getattr(cls, "OUTPUT_NODE", False))

        out[class_type] = info

    return out


def resolve_node_info(
    node_info: Optional[Union[Dict[str, Any], str, Path]],
    server_url: Optional[str],
    timeout: int,
    *,
    allow_env: bool = True,
    require_source: bool = False,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    oi, use_api, _origin = resolve_node_info_with_origin(
        node_info,
        server_url,
        timeout,
        allow_env=allow_env,
        require_source=require_source,
    )
    return oi, use_api


def resolve_node_info_with_origin(
    node_info: Optional[Union[Dict[str, Any], str, Path]],
    server_url: Optional[str],
    timeout: int,
    *,
    allow_env: bool = True,
    require_source: bool = False,
) -> Tuple[Optional[Dict[str, Any]], bool, NodeInfoOrigin]:
    """
    Resolve node_info and also return origin metadata.

    This is the same resolution logic used by `resolve_node_info`, but it returns an
    `NodeInfoOrigin` describing where the node_info came from.
    """
    if node_info is not None:
        if isinstance(node_info, Mapping):
            # Preserve origin if caller provided a richer node_info object.
            origin = getattr(node_info, "origin", None) or getattr(node_info, "_autoflow_origin", None)
            if isinstance(origin, NodeInfoOrigin):
                # Preserve dict subclasses (e.g. models.NodeInfo) so callers can keep metadata.
                if isinstance(node_info, dict):
                    return node_info, True, origin
                return dict(node_info), True, origin
            if isinstance(node_info, dict):
                return node_info, True, NodeInfoOrigin(requested="dict", resolved="dict")
            return dict(node_info), True, NodeInfoOrigin(requested="dict", resolved="dict")
        if isinstance(node_info, (str, Path)):
            obj_s = str(node_info).strip()
            obj_str = obj_s.lower()
            if obj_str in ("modules", "from_comfyui_modules", "comfyui_modules"):
                root = _detect_comfyui_root_from_imports()
                return (
                    node_info_from_comfyui_modules(),
                    True,
                    NodeInfoOrigin(requested=obj_s, resolved="modules", modules_root=str(root) if root else None),
                )
            if obj_str in ("fetch", "server"):
                # If a file literally named "fetch"/"server" exists, prefer it.
                # (This avoids surprising behavior for explicit node_info="fetch".)
                try:
                    p = Path(obj_s)
                    if p.exists():
                        return load_node_info_from_file(p), True, NodeInfoOrigin(requested=obj_s, resolved="file")
                except Exception:
                    pass

                effective = server_url
                if not effective and allow_env:
                    env = os.environ.get("AUTOFLOW_COMFYUI_SERVER_URL")
                    if isinstance(env, str) and env.strip():
                        effective = env.strip()

                if obj_str == "server":
                    if not effective:
                        raise WorkflowConverterError(
                            "node_info='server' requires server_url or AUTOFLOW_COMFYUI_SERVER_URL."
                        )
                    return (
                        fetch_node_info(effective, timeout),
                        True,
                        NodeInfoOrigin(requested=obj_s, resolved="server", effective_server_url=effective),
                    )

                # fetch: try server_url/env; otherwise fall back to modules
                if effective:
                    return (
                        fetch_node_info(effective, timeout),
                        True,
                        NodeInfoOrigin(
                            requested=obj_s,
                            resolved="server",
                            effective_server_url=effective,
                            note="fetch->server",
                        ),
                    )
                return (
                    node_info_from_comfyui_modules(),
                    True,
                    NodeInfoOrigin(
                        requested=obj_s,
                        resolved="modules",
                        modules_root=str(_detect_comfyui_root_from_imports() or "") or None,
                        note="fetch->modules",
                    ),
                )
            if _is_http_url(node_info):
                url = str(node_info)
                return fetch_node_info_from_url(url, timeout=timeout), True, NodeInfoOrigin(requested=url, resolved="url")
            p = str(node_info)
            return load_node_info_from_file(node_info), True, NodeInfoOrigin(requested=p, resolved="file")
        raise WorkflowConverterError("node_info must be a dictionary, file path, or URL")

    if allow_env:
        src = os.environ.get(ENV_NODE_INFO_SOURCE, "").strip()
        if src:
            src_l = src.lower()
            if src_l == "fetch":
                effective = server_url or os.environ.get("AUTOFLOW_COMFYUI_SERVER_URL")
                if effective:
                    oi = fetch_node_info(effective, timeout)
                    _NODE_INFO_SOURCE_CACHE["fetch"] = oi
                    return oi, True, NodeInfoOrigin(
                        requested=src, resolved="server", via_env=True, effective_server_url=effective, note="env:fetch->server"
                    )
                oi = node_info_from_comfyui_modules()
                _NODE_INFO_SOURCE_CACHE["modules"] = oi
                root = _detect_comfyui_root_from_imports()
                return oi, True, NodeInfoOrigin(
                    requested=src, resolved="modules", via_env=True, modules_root=str(root) if root else None, note="env:fetch->modules"
                )
            if src_l == "modules":
                cached = _NODE_INFO_SOURCE_CACHE.get("modules")
                if cached is not None:
                    root = _detect_comfyui_root_from_imports()
                    return cached, True, NodeInfoOrigin(
                        requested=src, resolved="modules", via_env=True, modules_root=str(root) if root else None, note="env:modules(cached)"
                    )
                oi = node_info_from_comfyui_modules()
                _NODE_INFO_SOURCE_CACHE["modules"] = oi
                root = _detect_comfyui_root_from_imports()
                return oi, True, NodeInfoOrigin(
                    requested=src, resolved="modules", via_env=True, modules_root=str(root) if root else None, note="env:modules"
                )
            if src_l == "server":
                effective = server_url or os.environ.get("AUTOFLOW_COMFYUI_SERVER_URL")
                if not effective:
                    raise WorkflowConverterError(
                        "AUTOFLOW_NODE_INFO_SOURCE=server requires server_url or AUTOFLOW_COMFYUI_SERVER_URL."
                    )
                oi = fetch_node_info(effective, timeout)
                _NODE_INFO_SOURCE_CACHE["server"] = oi
                return oi, True, NodeInfoOrigin(
                    requested=src, resolved="server", via_env=True, effective_server_url=effective, note="env:server"
                )
            if _is_http_url(src):
                return fetch_node_info_from_url(src, timeout=timeout), True, NodeInfoOrigin(requested=src, resolved="url", via_env=True)
            return load_node_info_from_file(src), True, NodeInfoOrigin(requested=src, resolved="file", via_env=True)

    if server_url:
        return fetch_node_info(server_url, timeout), True, NodeInfoOrigin(
            requested="server_url", resolved="server", effective_server_url=server_url, note="server_url fallback"
        )

    # Final fallback: check AUTOFLOW_COMFYUI_SERVER_URL even without explicit source
    if allow_env:
        env_server = os.environ.get("AUTOFLOW_COMFYUI_SERVER_URL", "").strip()
        if env_server:
            try:
                oi = fetch_node_info(env_server, timeout)
                _NODE_INFO_SOURCE_CACHE["server"] = oi
                return oi, True, NodeInfoOrigin(
                    requested="auto", resolved="server", via_env=True,
                    effective_server_url=env_server, note="auto:AUTOFLOW_COMFYUI_SERVER_URL fallback"
                )
            except Exception:
                pass  # server unreachable, fall through

    if require_source:
        raise WorkflowConverterError(
            "Missing node_info source. Set AUTOFLOW_NODE_INFO_SOURCE or pass node_info explicitly."
        )

    return None, False, NodeInfoOrigin(note="empty")


def normalize_node_info(
    node_info: Optional[Union[Dict[str, Any], str, Path, Any]],
    *,
    server_url: Optional[str] = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT_S,
    allow_env: bool = False,
    require_source: bool = False,
) -> Optional[Dict[str, Any]]:
    if node_info is None:
        if not allow_env and server_url is None:
            if require_source:
                raise WorkflowConverterError(
                    "Missing node_info source. Set AUTOFLOW_NODE_INFO_SOURCE or pass node_info explicitly."
                )
            return None
        oi, _use_api = resolve_node_info(
            None,
            server_url,
            timeout,
            allow_env=allow_env,
            require_source=require_source,
        )
        return oi
    oi, _use_api = resolve_node_info(
        node_info,
        server_url,
        timeout,
        allow_env=allow_env,
        require_source=require_source,
    )
    return oi


# ---------------------------------------------------------------------------
# Widget alignment (schema-guided)
# ---------------------------------------------------------------------------


def get_widget_input_names(class_type: str, node_info: Optional[Dict[str, Any]] = None, use_api: bool = False) -> List[str]:
    if use_api:
        if node_info is None:
            raise NodeInfoError("node_info must be provided when using API mode")
        node_info = node_info.get(class_type)
        if node_info is None:
            raise NodeInfoError(f"Node class '{class_type}' not found in node_info")
        inputs_def = node_info.get("input", {})
        if not isinstance(inputs_def, dict):
            raise NodeInfoError(f"Invalid input definition for node '{class_type}'")

        widget_names = []
        for section in ["required", "optional"]:
            section_inputs = inputs_def.get(section, {})
            if not isinstance(section_inputs, dict):
                continue
            for name, spec in section_inputs.items():
                if not isinstance(spec, list):
                    continue
                l = len(spec)
                if l == 0:
                    continue
                if l == 1 and isinstance(spec[0], str):
                    continue
                if l == 2 and isinstance(spec[0], str) and isinstance(spec[1], dict):
                    opts = spec[1]
                    if not opts or (len(opts) == 1 and "tooltip" in opts):
                        continue
                widget_names.append(name)
        return widget_names

    try:
        import comfy.samplers  # noqa: F401
        import comfy.sd  # noqa: F401
        from nodes import NODE_CLASS_MAPPINGS  # type: ignore
    except ImportError as e:
        raise NodeInfoError(
            "ComfyUI modules not found. Please run this script from the ComfyUI directory "
            "or use --server-url to fetch node information via API, "
            "or use --node-info-path to load from a saved file."
        ) from e

    _ensure_extra_nodes_loaded()

    if class_type not in NODE_CLASS_MAPPINGS:
        raise NodeInfoError(f"Node class '{class_type}' not found in NODE_CLASS_MAPPINGS")

    cls = NODE_CLASS_MAPPINGS[class_type]
    if not hasattr(cls, "INPUT_TYPES"):
        return []
    inputs_def = cls.INPUT_TYPES() if callable(cls.INPUT_TYPES) else cls.INPUT_TYPES
    if not isinstance(inputs_def, dict):
        return []

    widget_names = []
    for section in ["required", "optional"]:
        section_inputs = inputs_def.get(section, {})
        if not isinstance(section_inputs, dict):
            continue
        for name, spec in section_inputs.items():
            if not isinstance(spec, tuple):
                continue
            l = len(spec)
            if l == 0:
                continue
            if l == 1 and isinstance(spec[0], str):
                continue
            if l == 2 and isinstance(spec[0], str) and isinstance(spec[1], dict):
                opts = spec[1]
                if len(opts) == 1 and "tooltip" in opts:
                    continue
            widget_names.append(name)
    return widget_names


def _widget_spec_for_name(class_type: str, name: str, node_info: Optional[Dict[str, Any]]) -> Any:
    if not isinstance(node_info, dict):
        return None
    node_info = node_info.get(class_type)
    if not isinstance(node_info, dict):
        return None
    inputs_def = node_info.get("input")
    if not isinstance(inputs_def, dict):
        return None
    for sec in ("required", "optional"):
        sec_inputs = inputs_def.get(sec)
        if isinstance(sec_inputs, dict) and name in sec_inputs:
            return sec_inputs.get(name)
    return None


def _spec_default(spec: Any) -> Any:
    if isinstance(spec, list) and len(spec) >= 2 and isinstance(spec[1], dict):
        return spec[1].get("default")
    return None


def _is_int_like(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    if isinstance(v, float):
        return v.is_integer()
    return False


def _is_number_like(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    return isinstance(v, (int, float))


def _fits_widget_spec(value: Any, spec: Any) -> bool:
    if not isinstance(spec, list) or not spec:
        return True
    head = spec[0]
    if isinstance(head, (list, tuple)):
        try:
            return value in head
        except Exception:
            return False
    if isinstance(head, str):
        t = head.upper()
        if t == "INT":
            return _is_int_like(value)
        if t == "FLOAT":
            return _is_number_like(value)
        if t == "BOOLEAN":
            return isinstance(value, bool) or value in (0, 1)
        if t == "STRING":
            return isinstance(value, str)
        return True
    return True


def align_widgets_values(
    class_type: str,
    widgets_values: List[Any],
    widget_names: List[str],
    *,
    node_info: Optional[Dict[str, Any]] = None,
    size_guard: int = 2000,
) -> List[Any]:
    n = len(widget_names)
    m = len(widgets_values)
    if n == 0:
        return []

    specs = []
    defaults = []
    for name in widget_names:
        spec = _widget_spec_for_name(class_type, name, node_info)
        specs.append(spec)
        defaults.append(_spec_default(spec))

    if n * m > int(size_guard):
        out = list(widgets_values[:n])
        while len(out) < n:
            out.append(defaults[len(out)])
        return out

    skip_penalty = 1
    missing_penalty = 2
    match_score = 2
    mismatch_penalty = 6

    dp = [[-10**9] * (m + 1) for _ in range(n + 1)]
    back: List[List[Optional[Tuple[int, int, str]]]] = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] - skip_penalty
        back[0][j] = (0, j - 1, "skip")

    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] - missing_penalty
        back[i][0] = (i - 1, 0, "missing")
        for j in range(1, m + 1):
            best = dp[i][j]
            bestb = back[i][j]

            v_skip = dp[i][j - 1] - skip_penalty
            if v_skip > best:
                best = v_skip
                bestb = (i, j - 1, "skip")

            v_miss = dp[i - 1][j] - missing_penalty
            if v_miss > best:
                best = v_miss
                bestb = (i - 1, j, "missing")

            val = widgets_values[j - 1]
            fits = _fits_widget_spec(val, specs[i - 1])
            v_match = dp[i - 1][j - 1] + (match_score if fits else -mismatch_penalty)
            if v_match > best:
                best = v_match
                bestb = (i - 1, j - 1, "match")

            dp[i][j] = best
            back[i][j] = bestb

    aligned: List[Any] = [None] * n
    i, j = n, m
    while i > 0 or j > 0:
        b = back[i][j]
        if b is None:
            break
        pi, pj, action = b
        if action == "match":
            aligned[i - 1] = widgets_values[j - 1]
        elif action == "missing":
            aligned[i - 1] = defaults[i - 1]
        i, j = pi, pj

    for k in range(n):
        if aligned[k] is None:
            aligned[k] = defaults[k]
    return aligned


# ---------------------------------------------------------------------------
# Conversion implementation
# ---------------------------------------------------------------------------


def resolve_bypassed_links(
    link_id: int, link_map: Dict[int, List[Any]], node_map: Dict[str, Dict[str, Any]], current_type: str
) -> Tuple[int, int]:
    if link_id not in link_map:
        raise WorkflowConverterError(f"Link {link_id} not found in link map")
    link = link_map[link_id]
    origin_id = link[1]
    origin_slot = link[2]

    visited_nodes = set()
    while True:
        parent = node_map.get(str(origin_id))
        if not parent or parent.get("mode") != 4:
            break
        if origin_id in visited_nodes:
            break
        visited_nodes.add(origin_id)
        parent_inputs = parent.get("inputs", [])
        found = False
        for _p_slot, p_inp in enumerate(parent_inputs):
            if p_inp.get("type") == current_type:
                p_link_id = p_inp.get("link")
                if p_link_id is not None:
                    if p_link_id not in link_map:
                        break
                    p_link = link_map[p_link_id]
                    origin_id = p_link[1]
                    origin_slot = p_link[2]
                    found = True
                    break
        if not found:
            break
    return origin_id, origin_slot


def workflow_to_api_format_with_errors(
    workflow_data: Dict[str, Any],
    node_info: Optional[Dict[str, Any]] = None,
    use_api: bool = DEFAULT_USE_API,
    include_meta: bool = DEFAULT_INCLUDE_META,
) -> ConversionResult:
    errors: List[ConversionError] = []
    warnings: List[ConversionError] = []

    try:
        validate_workflow_data(workflow_data)
    except WorkflowConverterError as e:
        return ConversionResult(
            success=False,
            data=None,
            errors=[ConversionError(category=ErrorCategory.VALIDATION, severity=ErrorSeverity.CRITICAL, message=str(e))],
            warnings=[],
            processed_nodes=0,
            skipped_nodes=0,
            total_nodes=0,
        )

    workflow_data = flatten_subgraphs(workflow_data)

    nodes_list = workflow_data["nodes"]
    links_list = workflow_data["links"]
    total_nodes = len(nodes_list)

    link_map: Dict[int, List[Any]] = {int(link[0]): link for link in links_list if isinstance(link, list) and link}
    node_map: Dict[str, Dict[str, Any]] = {str(node["id"]): node for node in nodes_list if isinstance(node, dict)}

    output: Dict[str, Any] = {}
    processed_nodes = 0
    skipped_nodes = 0

    def _resolve_link_value(link_id: int, current_type: str) -> Any:
        seen_links = set()
        cur_link = int(link_id)
        while True:
            if cur_link in seen_links:
                raise WorkflowConverterError(f"Cycle detected while resolving link {link_id}")
            seen_links.add(cur_link)
            origin_id, origin_slot = resolve_bypassed_links(cur_link, link_map, node_map, current_type)
            origin_node = node_map.get(str(origin_id))
            if not origin_node:
                return [str(origin_id), origin_slot]

            origin_type = origin_node.get("type")
            if origin_type == "Reroute":
                upstream = None
                for inp in (origin_node.get("inputs", []) or []):
                    if inp.get("link") is not None:
                        upstream = inp.get("link")
                        break
                if upstream is None:
                    return [str(origin_id), origin_slot]
                cur_link = int(upstream)
                continue

            if origin_type == "PrimitiveNode":
                wv = origin_node.get("widgets_values") or []
                return wv[0] if wv else None

            if origin_type == "Note":
                return None

            return [str(origin_id), origin_slot]

    for node in nodes_list:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id", "unknown")
        node_id_str = str(node_id)
        class_type = node.get("type", "unknown")

        try:
            # Skip muted (mode=2) and bypassed (mode=4) nodes — matches ComfyUI behavior.
            node_mode = node.get("mode", 0)
            if node_mode in (2, 4):
                skipped_nodes += 1
                continue

            # Node inclusion is driven by node_info (schema), not hardcoded node names.
            # If node_info is present, any node type not in node_info is skipped (UI-only, unknown, etc).
            if isinstance(node_info, dict) and node_info and class_type not in node_info:
                skipped_nodes += 1
                warnings.append(
                    ConversionError(
                        category=ErrorCategory.NODE_PROCESSING,
                        severity=ErrorSeverity.WARNING,
                        message=f"Skipping node type not found in node_info: {class_type}",
                        node_id=node_id_str,
                        details={"class_type": class_type},
                    )
                )
                continue

            inputs: Dict[str, Any] = {}

            widget_names = get_widget_input_names(str(class_type), node_info, use_api)
            widgets_values = node.get("widgets_values", []) or []
            if use_api:
                widgets_values = align_widgets_values(str(class_type), list(widgets_values), widget_names, node_info=node_info)

            for i in range(min(len(widget_names), len(widgets_values))):
                inputs[widget_names[i]] = widgets_values[i]

            for inp in (node.get("inputs") or []):
                if not isinstance(inp, dict):
                    continue
                name = inp.get("name")
                link_id = inp.get("link")
                in_type = inp.get("type", "")
                if not isinstance(name, str) or not name:
                    continue
                if link_id is None:
                    continue
                try:
                    inputs[name] = _resolve_link_value(int(link_id), str(in_type))
                except Exception as e:
                    warnings.append(
                        ConversionError(
                            category=ErrorCategory.NODE_PROCESSING,
                            severity=ErrorSeverity.WARNING,
                            message=f"Failed to resolve link for input {name}: {e}",
                            node_id=node_id_str,
                            details={"class_type": class_type, "input": name},
                        )
                    )

            api_node: Dict[str, Any] = {"class_type": class_type, "inputs": inputs}
            if include_meta:
                meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
                if meta:
                    api_node["_meta"] = meta

            output[node_id_str] = api_node
            processed_nodes += 1

        except NodeInfoError as e:
            skipped_nodes += 1
            warnings.append(
                ConversionError(
                    category=ErrorCategory.NODE_PROCESSING,
                    severity=ErrorSeverity.WARNING,
                    message=str(e),
                    node_id=node_id_str,
                    details={"class_type": class_type},
                )
            )
        except WorkflowConverterError as e:
            errors.append(
                ConversionError(
                    category=ErrorCategory.NODE_PROCESSING,
                    severity=ErrorSeverity.ERROR,
                    message=str(e),
                    node_id=node_id_str,
                    details={"class_type": class_type},
                )
            )
        except Exception as e:
            errors.append(
                ConversionError(
                    category=ErrorCategory.NODE_PROCESSING,
                    severity=ErrorSeverity.ERROR,
                    message=f"Unexpected error processing node: {e}",
                    node_id=node_id_str,
                    details={"class_type": class_type},
                )
            )

    success = bool(output) and not any(e.severity == ErrorSeverity.CRITICAL for e in errors)
    return ConversionResult(
        success=success,
        data=output if success else output,
        errors=errors,
        warnings=warnings,
        processed_nodes=processed_nodes,
        skipped_nodes=skipped_nodes,
        total_nodes=total_nodes,
    )


def workflow_to_api_format(
    workflow_data: Dict[str, Any],
    node_info: Optional[Dict[str, Any]] = None,
    use_api: bool = DEFAULT_USE_API,
    include_meta: bool = DEFAULT_INCLUDE_META,
) -> Dict[str, Any]:
    r = workflow_to_api_format_with_errors(workflow_data, node_info=node_info, use_api=use_api, include_meta=include_meta)
    if not r.success and r.errors:
        raise WorkflowConverterError(r.errors[0].message)
    return r.data if isinstance(r.data, dict) else {}


def _extract_workflow_extra(workflow_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    extra = workflow_dict.get("extra")
    return copy.deepcopy(extra) if isinstance(extra, dict) else None


def _apply_convert_mapping(workflow_dict: Dict[str, Any], api_prompt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _extract_workflow_extra(workflow_dict)


# ---------------------------------------------------------------------------
# Flow extra -> ApiFlow node patching (opt-in)
# ---------------------------------------------------------------------------


def _deep_merge(dst: Any, src: Any, *, mode: str) -> Any:
    """
    Deep-merge `src` into `dst`.

    mode:
      - "merge": dicts merge recursively; non-dict collisions overwrite with src
      - "add":   dicts merge recursively; existing keys are preserved (no overwrite)
    """
    if not isinstance(dst, dict) or not isinstance(src, dict):
        return src if mode == "merge" else dst

    for k, v in src.items():
        if k in dst:
            if isinstance(dst.get(k), dict) and isinstance(v, dict):
                _deep_merge(dst[k], v, mode=mode)  # type: ignore[index]
            else:
                if mode == "merge":
                    dst[k] = v
                # mode == "add": keep existing
        else:
            dst[k] = v
    return dst


def _autoflow_nodes_patch_spec(workflow_extra: Optional[Dict[str, Any]]) -> Dict[str, List[Any]]:
    """
    Return a dict of node_id -> [directive, ...] (best-effort), in application order.

    Primary:
      extra.autoflow.meta.nodes
    Legacy alias (optional):
      extra.autoflow.nodes
    """
    if not isinstance(workflow_extra, dict):
        return {}

    out: Dict[str, List[Any]] = {}

    def _add_nodes(nodes_dict: Any) -> None:
        if not isinstance(nodes_dict, dict):
            return
        for nid, directive in nodes_dict.items():
            out.setdefault(str(nid), []).append(directive)

    # 1) Generic workflow metadata path (commonly used by other tools):
    #    extra.meta.nodes
    meta0 = workflow_extra.get("meta")
    if isinstance(meta0, dict):
        nodes0 = meta0.get("nodes")
        _add_nodes(nodes0)

    # 2) autoflow namespace (wins over generic extra.meta.nodes)
    f2a = workflow_extra.get("autoflow")
    if not isinstance(f2a, dict):
        return out

    meta = f2a.get("meta")
    if isinstance(meta, dict):
        nodes = meta.get("nodes")
        _add_nodes(nodes)

    legacy_nodes = f2a.get("nodes")
    if isinstance(legacy_nodes, dict):
        # Apply legacy after meta.nodes so legacy can override if needed.
        _add_nodes(legacy_nodes)

    return out


def _apply_patch_ops(dst: Dict[str, Any], patch: Dict[str, Any], *, default_mode: str) -> None:
    """
    Apply a patch dict onto dst with per-key operators.

    Key prefixes (operators):
      - "+k": add-only (do not overwrite existing key)
      - "*k": overwrite/merge (force overwrite even if default_mode is add)
      - "&k": deep-merge if both sides are dicts, else overwrite
      - "-k" or "!k": delete key k from dst (value ignored)

    Notes:
      - Operators are applied at the current dict level; nested dicts can also use operators.
      - For non-dict collisions, overwrite means dst[k]=value; add-only means keep existing.
    """
    if not patch:
        return

    def _parse_key(raw: Any) -> Tuple[str, Optional[str]]:
        k = str(raw)
        if not k:
            return k, None
        op = k[0]
        if op in ("+", "*", "&", "-", "!") and len(k) > 1:
            return k[1:], op
        return k, None

    for raw_k, v in patch.items():
        k, op = _parse_key(raw_k)
        if not k:
            continue

        if op in ("-", "!"):
            dst.pop(k, None)
            continue

        # Determine per-key mode override
        mode = default_mode
        if op == "+":
            mode = "add"
        elif op == "*":
            mode = "merge"
        elif op == "&":
            mode = "merge"

        if k not in dst:
            dst[k] = v
            continue

        if mode == "add":
            # add-only: allow recursive add into dicts, but don't overwrite scalars/lists.
            if isinstance(dst.get(k), dict) and isinstance(v, dict):
                _apply_patch_ops(dst[k], v, default_mode="add")  # type: ignore[arg-type]
            continue

        # mode == "merge"
        if isinstance(dst.get(k), dict) and isinstance(v, dict):
            _apply_patch_ops(dst[k], v, default_mode="merge")  # type: ignore[arg-type]
        else:
            dst[k] = v


def _apply_autoflow_node_patches(
    api_data: Dict[str, Any],
    workflow_extra: Optional[Dict[str, Any]],
    *,
    warn: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> None:
    """
    Apply per-node patch directives from workflow_extra into the API payload.

    Directive forms:
      - shorthand: { ... }  -> treated as {"mode":"merge","data":{...}}
      - explicit:  {"mode": "...", "data": {...}}

    Supported modes:
      - merge (default): deep-merge with overwrite on collisions
      - add:             deep-merge add-only (no overwrite)
      - replace:         replace entire node dict with `data`
    """
    spec = _autoflow_nodes_patch_spec(workflow_extra)
    if not spec:
        return

    def _norm_mode(m: Any) -> str:
        s = str(m or "").strip().lower()
        if s in ("", "merge", "append", "update"):
            return "merge"
        if s in ("add", "keep", "preserve"):
            return "add"
        if s in ("replace", "overwrite"):
            return "replace"
        return "merge"

    for nid, directives in spec.items():
        if nid not in api_data or not isinstance(api_data.get(nid), dict):
            if warn is not None:
                warn(
                    f"autoflow meta patch references node_id not in ApiFlow: {nid}",
                    {"node_id": nid},
                )
            continue

        for directive in directives:
            mode = "merge"
            data = directive
            if isinstance(directive, dict) and ("mode" in directive or "data" in directive):
                mode = _norm_mode(directive.get("mode"))
                data = directive.get("data")

            mode = _norm_mode(mode)

            if mode == "replace":
                if isinstance(data, dict):
                    api_data[nid] = data
                else:
                    if warn is not None:
                        warn(
                            f"autoflow meta patch for node {nid} ignored (replace mode requires dict data)",
                            {"node_id": nid, "mode": mode},
                        )
                continue

            if not isinstance(data, dict):
                if warn is not None:
                    warn(
                        f"autoflow meta patch for node {nid} ignored (data must be a dict)",
                        {"node_id": nid, "mode": mode},
                    )
                continue

            # Apply with per-key operators (and deep merge semantics).
            _apply_patch_ops(api_data[nid], data, default_mode=mode)  # type: ignore[arg-type]


def _wrap_apiflow(api_data: Dict[str, Any], *, node_info: Optional[Dict[str, Any]], use_api: bool, workflow_meta: Any):
    # Avoid import cycles during refactor: prefer models, fall back to api.
    try:
        from .models import ApiFlow  # type: ignore
    except Exception:
        from .api import ApiFlow  # type: ignore
    return ApiFlow(api_data, node_info=node_info, use_api=use_api, workflow_meta=workflow_meta)


def convert_workflow_with_errors(
    workflow_data: Union[Dict[str, Any], str, Path],
    node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
    server_url: Optional[str] = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT_S,
    include_meta: bool = DEFAULT_INCLUDE_META,
    output_file: Optional[Union[str, Path]] = None,
    convert_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
    disable_autoflow_meta: bool = False,
    apply_autoflow_meta: Optional[bool] = None,  # backwards-compat alias (disable wins)
) -> ConversionResult:
    try:
        workflow_dict = normalize_workflow_input(workflow_data)

        effective_server_url = normalize_server_url(
            server_url,
            allow_env=node_info is None,
            allow_none=True,
        )

        node_info_dict, use_api = resolve_node_info(
            node_info,
            effective_server_url,
            timeout,
            allow_env=True,
            require_source=True,
        )
        api_data = workflow_to_api_format_with_errors(workflow_dict, node_info_dict, use_api, include_meta)

        enable_meta = True if apply_autoflow_meta is None else bool(apply_autoflow_meta)
        if disable_autoflow_meta:
            enable_meta = False

        if enable_meta and isinstance(api_data.data, dict):
            warns = list(api_data.warnings)

            def _warn(msg: str, details: Dict[str, Any]) -> None:
                warns.append(
                    ConversionError(
                        category=ErrorCategory.CONVERSION,
                        severity=ErrorSeverity.WARNING,
                        message=msg,
                        node_id=str(details.get("node_id")) if isinstance(details.get("node_id"), str) else None,
                        details=details,
                    )
                )

            _apply_autoflow_node_patches(api_data.data, _extract_workflow_extra(workflow_dict), warn=_warn)
            api_data = ConversionResult(
                success=api_data.success,
                data=api_data.data,
                errors=api_data.errors,
                warnings=warns,
                processed_nodes=api_data.processed_nodes,
                skipped_nodes=api_data.skipped_nodes,
                total_nodes=api_data.total_nodes,
            )

        # Save raw dict if requested and successful
        if output_file is not None and isinstance(api_data.data, dict):
            try:
                save_workflow_to_file(api_data.data, output_file)
            except Exception as e:
                errs = list(api_data.errors)
                errs.append(
                    ConversionError(
                        category=ErrorCategory.IO,
                        severity=ErrorSeverity.ERROR,
                        message=f"Failed to save output file: {e}",
                        details={"output_file": str(output_file)},
                    )
                )
                return ConversionResult(
                    success=api_data.success,
                    data=api_data.data,
                    errors=errs,
                    warnings=api_data.warnings,
                    processed_nodes=api_data.processed_nodes,
                    skipped_nodes=api_data.skipped_nodes,
                    total_nodes=api_data.total_nodes,
                )

        return api_data
    except WorkflowConverterError as e:
        return ConversionResult(
            success=False,
            data=None,
            errors=[ConversionError(category=ErrorCategory.IO, severity=ErrorSeverity.CRITICAL, message=str(e))],
            warnings=[],
            processed_nodes=0,
            skipped_nodes=0,
            total_nodes=0,
        )
    except Exception as e:
        return ConversionResult(
            success=False,
            data=None,
            errors=[ConversionError(category=ErrorCategory.CONVERSION, severity=ErrorSeverity.CRITICAL, message=str(e))],
            warnings=[],
            processed_nodes=0,
            skipped_nodes=0,
            total_nodes=0,
        )


def convert_workflow(
    workflow_data: Union[Dict[str, Any], str, Path],
    node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
    server_url: Optional[str] = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT_S,
    include_meta: bool = DEFAULT_INCLUDE_META,
    output_file: Optional[Union[str, Path]] = None,
    convert_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
    disable_autoflow_meta: bool = False,
    apply_autoflow_meta: Optional[bool] = None,  # backwards-compat alias (disable wins)
):
    workflow_dict = normalize_workflow_input(workflow_data)

    effective_server_url = normalize_server_url(
        server_url,
        allow_env=node_info is None,
        allow_none=True,
    )

    node_info_dict, use_api = resolve_node_info(
        node_info,
        effective_server_url,
        timeout,
        allow_env=True,
        require_source=True,
    )
    api_data = workflow_to_api_format(workflow_dict, node_info_dict, use_api, include_meta)
    workflow_meta = _apply_convert_mapping(workflow_dict, api_data)

    enable_meta = True if apply_autoflow_meta is None else bool(apply_autoflow_meta)
    if disable_autoflow_meta:
        enable_meta = False

    if enable_meta:
        _apply_autoflow_node_patches(api_data, workflow_meta, warn=None)

    if output_file is not None:
        save_workflow_to_file(api_data, output_file)

    wf = _wrap_apiflow(api_data, node_info=node_info_dict, use_api=use_api, workflow_meta=workflow_meta)
    if convert_callbacks is not None:
        try:
            from .map import api_mapping
            wf = _wrap_apiflow(
                api_mapping(wf, convert_callbacks, in_place=False),
                node_info=wf.node_info,
                use_api=wf.use_api,
                workflow_meta=wf.workflow_meta,
            )
        except Exception:
            pass
    return wf


def convert(
    workflow_data: Union[Dict[str, Any], str, Path],
    node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
    server_url: Optional[str] = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT_S,
    include_meta: bool = DEFAULT_INCLUDE_META,
    output_path: Optional[Union[str, Path]] = None,
    convert_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
    disable_autoflow_meta: bool = False,
    apply_autoflow_meta: Optional[bool] = None,  # backwards-compat alias (disable wins)
):
    wf = convert_workflow(
        workflow_data=workflow_data,
        node_info=node_info,
        server_url=server_url,
        timeout=timeout,
        include_meta=include_meta,
        output_file=None,
        convert_callbacks=convert_callbacks,
        disable_autoflow_meta=disable_autoflow_meta,
        apply_autoflow_meta=apply_autoflow_meta,
    )
    if output_path is not None:
        wf.save(output_path)
    return wf


def convert_with_errors(
    workflow_data: Union[Dict[str, Any], str, Path],
    node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
    server_url: Optional[str] = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT_S,
    include_meta: bool = DEFAULT_INCLUDE_META,
    output_path: Optional[Union[str, Path]] = None,
    convert_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
    disable_autoflow_meta: bool = False,
    apply_autoflow_meta: Optional[bool] = None,  # backwards-compat alias (disable wins)
)-> ConvertResult:
    r = convert_workflow_with_errors(
        workflow_data=workflow_data,
        node_info=node_info,
        server_url=server_url,
        timeout=timeout,
        include_meta=include_meta,
        output_file=None,
        convert_callbacks=convert_callbacks,
        disable_autoflow_meta=disable_autoflow_meta,
        apply_autoflow_meta=apply_autoflow_meta,
    )
    data = None
    if isinstance(r.data, dict):
        try:
            data = _wrap_apiflow(r.data, node_info=None, use_api=True, workflow_meta=None)
        except Exception:
            data = r.data
    out = ConvertResult(
        ok=r.success,
        data=data,
        errors=r.errors,
        warnings=r.warnings,
        processed_nodes=r.processed_nodes,
        skipped_nodes=r.skipped_nodes,
        total_nodes=r.total_nodes,
    )
    if output_path is not None and out.data is not None:
        try:
            out.data.save(output_path)
        except Exception:
            pass
    return out


