"""Tool implementations for the autograph MCP server.

Each function takes the server configuration (and, where it works on a
workflow, the session store) and returns a JSON-friendly dict. The MCP layer
in ``server.py`` only declares the tools and passes arguments through, so
everything here can be called and tested without the ``mcp`` package.

How the pieces fit:

* ``graph.Catalog`` is the server's node list, fetched once and searched many
  times. Sessions get it attached, so the builder knows every node type.
* A workflow lives in a session (``workflow_id``) as a workspace graph. Graphs
  with subgraphs are flattened when they are loaded, so every node has one id
  and it is the id ComfyUI uses in its errors and results.
* Inputs are read and written by name in ComfyUI's API notation: a value, or a
  link ``[node_id, output_index]``.
"""

from __future__ import annotations

import difflib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import warnings as _warnings
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import autograph
from autograph.net import comfy_url, http_json, upload_file as _upload_file
from autograph.results import SubmissionResult

from . import errors as _errors
from . import graft as _graft
from . import graph as _graph
from . import library as _library
from .config import McpConfig
from .images import build_image_response
from .session import SessionStore, WorkflowSession


WorkflowInput = Union[str, Dict[str, Any]]
Progress = Callable[[float, Optional[float], str], None]

_NODE_INFO_CACHE_PATH = Path.home() / ".comfyui-autograph" / "node_info_cache.json"

CATALOG_TTL_S = 300.0           # the node list is read again after this long
POLL_INTERVAL_S = 1.0
NODE_MODES = {"active": 0, "muted": 2, "mute": 2, "bypassed": 4, "bypass": 4}

_catalogs: Dict[str, Tuple[float, _graph.Catalog]] = {}
_templates: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_lock = threading.RLock()


# ---------------------------------------------------------------------------
# The server's node list
# ---------------------------------------------------------------------------


def get_catalog(
    config: McpConfig,
    server_url: Optional[str] = None,
    *,
    refresh: bool = False,
    required: bool = True,
) -> Optional[_graph.Catalog]:
    """The node types of the ComfyUI server, fetched once and kept for a few minutes.

    Falls back to the last node list saved on disk when the server cannot be
    reached (``catalog.from_cache``), so a workflow can still be built and
    checked offline. Raises when there is neither, unless ``required=False``.
    """
    base = config.resolve_server_url(server_url)
    with _lock:
        hit = _catalogs.get(base)
        if hit is not None and not refresh and time.time() - hit[0] < CATALOG_TTL_S and not hit[1].from_cache:
            return hit[1]
    try:
        from autograph.convert import fetch_node_info

        data = _graph.plain(fetch_node_info(base, timeout=max(int(config.timeout_s), 30)))
        catalog = _graph.Catalog(data, server_url=base)
        try:
            _NODE_INFO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _NODE_INFO_CACHE_PATH.write_text(
                json.dumps({"fetched_at": time.time(), "server_url": base, "data": catalog.node_info})
            )
        except Exception:
            pass
    except Exception as fetch_exc:
        if hit is not None:
            return hit[1]
        try:
            cached = json.loads(_NODE_INFO_CACHE_PATH.read_text())
            catalog = _graph.Catalog(cached.get("data", cached), server_url=base, from_cache=True)
        except Exception:
            if not required:
                return None
            raise RuntimeError(
                f"Cannot read the node list from ComfyUI at {base} ({type(fetch_exc).__name__}: {fetch_exc}) "
                "and there is no saved copy. Start ComfyUI, or set AUTOGRAPH_COMFYUI_SERVER_URL to where it runs; "
                "comfyui_status shows what is reachable."
            ) from fetch_exc
    with _lock:
        _catalogs[base] = (time.time(), catalog)
    return catalog


def use_node_info(config: McpConfig, node_info: Dict[str, Any], server_url: Optional[str] = None) -> _graph.Catalog:
    """Work from a given node list (an ``/object_info`` dict) instead of asking the server: for tests and offline use."""
    base = config.resolve_server_url(server_url)
    catalog = _graph.Catalog(_graph.plain(node_info), server_url=base)
    with _lock:
        _catalogs[base] = (time.time() + 10 ** 9, catalog)        # never goes stale
    return catalog


def forget_catalog(config: McpConfig, server_url: Optional[str] = None) -> None:
    """Have the node list read again next time (after an upload, the file lists in it are out of date)."""
    with _lock:
        _catalogs.pop(config.resolve_server_url(server_url), None)


def _catalog_age(config: McpConfig, server_url: Optional[str] = None) -> float:
    with _lock:
        hit = _catalogs.get(config.resolve_server_url(server_url))
    return time.time() - hit[0] if hit else 1e9


# ---------------------------------------------------------------------------
# Workflows and sessions
# ---------------------------------------------------------------------------


def _raw(flow: "autograph.Flow") -> Dict[str, Any]:
    """The workspace dict a Flow edits."""
    return flow._flow if hasattr(flow, "_flow") else flow  # type: ignore[return-value]


def _read_source(source: Any) -> Tuple[Any, Optional[str]]:
    """(workflow data or a path autograph can open, the file it came from)."""
    if isinstance(source, dict):
        return source, None
    if isinstance(source, (bytes, bytearray)):
        return json.loads(bytes(source).decode("utf-8")), None
    if isinstance(source, Path):
        source = str(source)
    if not isinstance(source, str):
        raise TypeError(f"A workflow is a file path, a JSON string or an object, not {type(source).__name__}.")
    text = source.strip()
    if text.startswith("{"):
        return json.loads(text), None
    path = Path(text).expanduser()
    if path.is_file():
        if path.suffix.lower() == ".json":
            return json.loads(path.read_text(encoding="utf-8")), str(path.resolve())
        return path, str(path.resolve())            # e.g. a PNG with the workflow embedded
    raise FileNotFoundError(
        f"{source!r} is neither JSON nor an existing file. Give a file path, the workflow JSON, "
        "or use load_template / load_local_workflow for a named workflow."
    )


def _open_flow(
    data: Any,
    catalog: Optional[_graph.Catalog],
) -> Tuple["autograph.Flow", Dict[str, Any]]:
    """A workspace Flow from workspace or API-format data, plus notes on what was done to it."""
    notes: Dict[str, Any] = {}
    kwargs: Dict[str, Any] = {"node_info": catalog.node_info} if catalog is not None else {}
    if _graph.looks_like_api(data):
        if catalog is None:
            raise RuntimeError("An API-format workflow can only be loaded when the server's node list is available.")
        flow, id_map, problems = _graph.build_flow(data, catalog)
        notes["format"] = "api"
        changed = {old: new for old, new in id_map.items() if old != new}
        if changed:
            notes["node_ids"] = changed
            notes["node_ids_note"] = "Node ids were renumbered; this maps the given ids to the ids in the session."
        if problems:
            notes["problems"] = problems
        notes["auto_layout"] = True
        return flow, notes
    if isinstance(data, dict):
        if not isinstance(data.get("nodes"), list):
            raise ValueError(
                "Not a ComfyUI workflow: expected a workspace graph (with \"nodes\" and \"links\") or an "
                "API-format graph ({\"1\": {\"class_type\": ..., \"inputs\": {...}}, ...})."
            )
        subgraphs = ((data.get("definitions") or {}).get("subgraphs") or []) if isinstance(data.get("definitions"), dict) else []
        if subgraphs:
            from autograph.convert import flatten_subgraphs

            data = flatten_subgraphs(json.loads(json.dumps(data, default=str)))
            used = {str(n.get("type")) for n in data.get("nodes") or [] if isinstance(n, dict)}
            left = [d for d in (data.get("definitions") or {}).get("subgraphs") or []
                    if isinstance(d, dict) and str(d.get("id")) in used]
            if left:
                data["definitions"]["subgraphs"] = left
            else:
                data.get("definitions", {}).pop("subgraphs", None)
            notes["subgraphs_flattened"] = len(subgraphs) - len(left)
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        flow = autograph.Flow(data, **kwargs)
    if notes.get("subgraphs_flattened"):
        try:
            flow.auto_layout()       # the nodes that were inside subgraphs had positions of their own
        except Exception:
            pass
    return flow, notes


def _attach_node_info(flow: "autograph.Flow", catalog: Optional[_graph.Catalog]) -> None:
    if catalog is not None and getattr(flow, "node_info", None) is None:
        try:
            flow.fetch_node_info(catalog.node_info)
        except Exception:
            pass


def _resolve(
    config: McpConfig,
    store: SessionStore,
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
    *,
    server_url: Optional[str] = None,
    need_catalog: bool = False,
) -> Tuple["autograph.Flow", Optional[WorkflowSession], Optional[_graph.Catalog]]:
    """The flow a tool works on: a session's, or one opened from an inline workflow."""
    catalog = get_catalog(config, server_url, required=need_catalog)
    if workflow_id:
        session = store.get(workflow_id)
        _attach_node_info(session.flow, catalog)
        return session.flow, session, catalog
    if workflow is None:
        raise ValueError("Give `workflow_id` (a session from create_workflow / load_workflow) or `workflow`.")
    data, _path = _read_source(workflow)
    flow, _notes = _open_flow(data, catalog)
    return flow, None, catalog


def _new_session(
    store: SessionStore,
    flow: "autograph.Flow",
    notes: Dict[str, Any],
    *,
    source_path: Optional[str] = None,
    label: Optional[str] = None,
) -> WorkflowSession:
    session = store.create(flow=flow, source_path=source_path, label=label)
    if notes.get("auto_layout"):
        session.metadata["auto_layout"] = True
    return session


def _touch(store: SessionStore, session: Optional[WorkflowSession]) -> None:
    """After an edit: tidy a graph built here, and write the session's checkpoint."""
    if session is None:
        return
    if session.metadata.get("auto_layout"):
        try:
            session.flow.auto_layout()
        except Exception:
            pass
    store.touch(session.id)


def _counts(flow: "autograph.Flow") -> Dict[str, Any]:
    raw = _raw(flow)
    return {"node_count": len(raw.get("nodes") or []), "link_count": len(raw.get("links") or [])}


def _session_info(session: WorkflowSession, notes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"workflow_id": session.id}
    if session.label:
        out["label"] = session.label
    if session.source_path:
        out["source_path"] = session.source_path
    out.update(_counts(session.flow))
    for key in ("format", "subgraphs_flattened", "node_ids", "node_ids_note", "problems"):
        if notes and notes.get(key):
            out[key] = notes[key]
    return out


def _node(flow: "autograph.Flow", node_id: Any) -> Dict[str, Any]:
    want = str(node_id).strip()
    nodes = [n for n in _raw(flow).get("nodes") or [] if isinstance(n, dict)]
    for node in nodes:
        if str(node.get("id")) == want:
            return node
    ids = ", ".join(str(n.get("id")) for n in nodes[:40]) or "none"
    raise LookupError(f"No node with id {node_id!r} in this workflow. Node ids: {ids}. inspect_workflow lists them.")


def _ref(flow: "autograph.Flow", node_id: Any) -> Any:
    _node(flow, node_id)
    return flow.nodes.by_id(str(node_id).strip())


def _class_type(node: Dict[str, Any]) -> str:
    return str(node.get("type") or node.get("class_type") or "")


def _view(flow: "autograph.Flow", catalog: Optional[_graph.Catalog], node_ids: Optional[List[Any]] = None,
          brief: bool = False) -> List[Dict[str, Any]]:
    return _graph.summarize(_graph.plain(_raw(flow)), catalog, node_ids, brief)


def _place(flow: "autograph.Flow", node: Dict[str, Any]) -> None:
    """Put a node added to a loaded graph to the right of what is there, not on top of it."""
    others = [n for n in _raw(flow).get("nodes") or [] if isinstance(n, dict) and n is not node]
    spots = [n.get("pos") for n in others if isinstance(n.get("pos"), (list, tuple)) and len(n["pos"]) >= 2]
    try:
        if spots:
            node["pos"] = [max(float(p[0]) for p in spots) + 420.0, min(float(p[1]) for p in spots)]
    except (TypeError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Checking a workflow
# ---------------------------------------------------------------------------


_SKIPPED_TYPE = re.compile(r"Skipping node type not found in node_info:\s*(.+)$")


def _convert_issue(entry: Any, kind: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"code": f"convert_{kind}", "message": str(getattr(entry, "message", entry))}
    node_id = getattr(entry, "node_id", None)
    if node_id is not None:
        out["node_id"] = str(node_id)
    return out


def _check(
    config: McpConfig,
    flow: "autograph.Flow",
    catalog: Optional[_graph.Catalog],
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert a workflow and check the result. Returns errors, warnings and the API-format graph."""
    kwargs: Dict[str, Any] = {"node_info": catalog.node_info} if catalog is not None else {}
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        result = flow.convert_with_errors(**kwargs)
    errors = [_convert_issue(e, "error") for e in getattr(result, "errors", None) or []]
    warnings = []
    for entry in getattr(result, "warnings", None) or []:
        message = str(getattr(entry, "message", ""))
        if "not among the options" in message:
            continue                                  # reported below, with suggestions
        skipped = _SKIPPED_TYPE.match(message)
        if skipped:
            # The converter leaves out node types the server does not have. Notes are not part of
            # what runs; anything else is a missing custom node.
            class_type = skipped.group(1).strip()
            if class_type not in _graph.EDITOR_ONLY:
                issue = {"code": "unknown_node_type", "class_type": class_type,
                         "message": f"Node {getattr(entry, 'node_id', '?')}: the server has no node type {class_type!r}."}
                if getattr(entry, "node_id", None) is not None:
                    issue["node_id"] = str(entry.node_id)
                close = catalog.suggest(class_type) if catalog is not None else []
                if close:
                    issue["closest"] = close
                issue["hint"] = _graph.unknown_type_hint(close)
                errors.append(issue)
            continue
        warnings.append(_convert_issue(entry, "warning"))
    api = _graph.plain(result.data) if getattr(result, "data", None) is not None else None
    if isinstance(api, dict):
        api = {k: v for k, v in api.items() if isinstance(v, dict) and "class_type" in v}
    if api is not None and catalog is not None:
        found = _graph.validate(api, catalog)
        stale = any(i["code"] in ("value_not_in_list", "no_options") for i in found["errors"])
        if stale and not catalog.from_cache and _catalog_age(config, server_url) > 3.0:
            # A file may have been added since the node list was read.
            fresh = get_catalog(config, server_url, refresh=True, required=False)
            if fresh is not None:
                catalog = fresh
                found = _graph.validate(api, catalog)
        if any(e["code"] == "unknown_node_type" for e in errors):
            # The converter left those nodes out, so "no output node" may only mean the output was one of them.
            found["errors"] = [e for e in found["errors"] if e["code"] != "no_output_node"]
        errors += found["errors"]
        warnings += found["warnings"]
    elif api is not None:
        warnings.append({"code": "not_checked", "message": "The server's node list is not available, so node "
                         "types, links and values were not checked."})
    if api is not None and not api and not errors:
        errors.append({"code": "empty_workflow", "message": "The workflow has no nodes to run."})
    return {"ok": not errors, "errors": errors, "warnings": warnings, "api": api,
            "node_count": len(api) if api is not None else 0}


# ===========================================================================
# Server / discovery
# ===========================================================================


def comfyui_status(config: McpConfig, server_url: Optional[str] = None, refresh: bool = False) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    out: Dict[str, Any] = {"server_url": base, "reachable": False}
    try:
        stats = http_json(comfy_url(base, "/system_stats"), payload=None, timeout=config.timeout_s, method="GET")
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["hint"] = ("Start ComfyUI, or point AUTOGRAPH_COMFYUI_SERVER_URL (or the server_url argument) at it. "
                       "Workflows can still be built and checked against the last saved node list.")
        return out
    out["reachable"] = True
    system = stats.get("system") if isinstance(stats.get("system"), dict) else {}
    for key in ("comfyui_version", "python_version", "os"):
        if system.get(key):
            out[key] = str(system[key]).split(" ")[0] if key == "python_version" else system[key]
    for key in ("ram_total", "ram_free"):
        if isinstance(system.get(key), (int, float)):
            out[f"{key}_gb"] = round(system[key] / 2 ** 30, 1)
    devices = []
    for device in stats.get("devices") or []:
        if not isinstance(device, dict):
            continue
        row = {"name": device.get("name"), "type": device.get("type")}
        for key in ("vram_total", "vram_free"):
            if isinstance(device.get(key), (int, float)):
                row[f"{key}_gb"] = round(device[key] / 2 ** 30, 1)
        devices.append(row)
    if devices:
        out["devices"] = devices
    try:
        queue = http_json(comfy_url(base, "/queue"), payload=None, timeout=config.timeout_s, method="GET")
        out["queue"] = {"running": len(queue.get("queue_running") or []), "pending": len(queue.get("queue_pending") or [])}
    except Exception:
        pass
    try:
        catalog = get_catalog(config, base, refresh=refresh)
        out["node_types"] = len(catalog)
        if catalog.from_cache:
            out["node_list"] = "saved copy (the server did not answer)"
    except Exception as exc:
        out["node_list_error"] = str(exc)
    return out


def list_node_types(
    config: McpConfig,
    query: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 20,
    server_url: Optional[str] = None,
    input_type: Optional[str] = None,
    output_type: Optional[str] = None,
    offset: int = 0,
    include_deprecated: bool = False,
) -> Dict[str, Any]:
    catalog = get_catalog(config, server_url)
    out = catalog.search(query, category=category, input_type=input_type, output_type=output_type,
                         limit=limit, offset=offset, include_deprecated=include_deprecated)
    if catalog.from_cache:
        out["from_cache"] = True
    if not out["total"]:
        close = catalog.suggest(query) if query else []
        if close:
            out["closest"] = close
        out["hint"] = "Nothing matched every filter. Try fewer or other words, or search by input_type / output_type."
    return out


def describe_node_type(
    config: McpConfig,
    class_type: Union[str, List[str]],
    server_url: Optional[str] = None,
    max_options: int = _graph.MAX_OPTIONS,
) -> Dict[str, Any]:
    catalog = get_catalog(config, server_url)
    if isinstance(class_type, (list, tuple)):
        found, missing = [], []
        for name in class_type:
            try:
                found.append(catalog.describe(str(name), max_options))
            except LookupError as exc:
                missing.append({"class_type": name, "error": str(exc).strip("'\""), "closest": catalog.suggest(str(name))})
        out: Dict[str, Any] = {"node_types": found}
        if missing:
            out["unknown"] = missing
        return out
    out = catalog.describe(str(class_type), max_options)
    if catalog.from_cache:
        out["from_cache"] = True
    return out


def list_input_options(
    config: McpConfig,
    class_type: str,
    input_name: str,
    query: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    catalog = get_catalog(config, server_url, refresh=_catalog_age(config, server_url) > 30.0)
    return catalog.options(class_type, input_name, query, limit, offset)


def list_models(
    config: McpConfig,
    folder: Optional[str] = None,
    server_url: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)

    def _get(path: str) -> List[Any]:
        data = http_json(comfy_url(base, path), payload=None, timeout=config.timeout_s, method="GET")
        items = data.get("raw") if isinstance(data, dict) else data
        return list(items) if isinstance(items, list) else []

    if not folder:
        return {"folders": _get("/models"),
                "hint": "Give `folder` to list what is installed in one, e.g. list_models(folder=\"checkpoints\")."}
    try:
        names = [str(n) for n in _get(f"/models/{urllib.parse.quote(folder)}")]
    except urllib.error.HTTPError as exc:
        folders = _get("/models")
        raise LookupError(f"No model folder {folder!r} (HTTP {exc.code}). Folders: {', '.join(map(str, folders))}") from exc
    wanted = str(query).lower() if query else None
    hits = [n for n in names if wanted is None or wanted in n.lower()]
    out: Dict[str, Any] = {"folder": folder, "total": len(hits), "models": hits[:max(1, int(limit))]}
    if not names:
        out["hint"] = (f"No models are installed in {folder!r}. A workflow that needs one cannot run until the "
                       "file is placed in ComfyUI's models folder.")
    return out


# ===========================================================================
# Templates served by ComfyUI
# ===========================================================================


def _template_index(config: McpConfig, base: str) -> List[Dict[str, Any]]:
    with _lock:
        hit = _templates.get(base)
        if hit is not None and time.time() - hit[0] < 600:
            return hit[1]
    try:
        data = http_json(comfy_url(base, "/templates/index.json"), payload=None,
                         timeout=max(int(config.timeout_s), 30), method="GET")
    except Exception as exc:
        raise RuntimeError(
            f"ComfyUI at {base} does not serve workflow templates ({type(exc).__name__}: {exc}). "
            "search_local_workflows and list_workflow_sources are the alternatives."
        ) from exc
    groups = data.get("raw") if isinstance(data, dict) else data
    rows: List[Dict[str, Any]] = []
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        for template in group.get("templates") or []:
            if isinstance(template, dict) and template.get("name"):
                row = dict(template)
                row["_category"] = group.get("title") or group.get("category") or ""
                row["_type"] = group.get("type") or ""
                rows.append(row)
    with _lock:
        _templates[base] = (time.time(), rows)
    return rows


def _template_row(template: Dict[str, Any], long: bool = False) -> Dict[str, Any]:
    description = str(template.get("description") or "")
    row: Dict[str, Any] = {
        "name": template["name"],
        "title": template.get("title") or template["name"],
        "category": template.get("_category"),
        "media_type": template.get("_type") or template.get("mediaType"),
        "description": description if long or len(description) <= 220 else description[:219] + "…",
    }
    if template.get("tags"):
        row["tags"] = template["tags"]
    if template.get("models"):
        row["models"] = template["models"]
    if template.get("openSource") is False:
        row["uses_paid_api_nodes"] = True
    if isinstance(template.get("size"), (int, float)) and template["size"]:
        row["model_download_gb"] = round(template["size"] / 2 ** 30, 1)
    return row


def search_templates(
    config: McpConfig,
    query: Optional[str] = None,
    media_type: Optional[str] = None,
    limit: int = 10,
    local_only: bool = False,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    words = [w for w in re.split(r"[\s_\-]+", str(query or "").lower()) if w]
    kind = str(media_type).lower() if media_type else None
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for template in _template_index(config, base):
        if kind and kind not in (str(template.get("_type")).lower(), str(template.get("mediaType")).lower()):
            continue
        if local_only and template.get("openSource") is False:
            continue
        score = 0.0
        if words:
            title = f"{template.get('title', '')} {template['name']}".lower()
            tags = " ".join(str(t) for t in (template.get("tags") or []) + (template.get("models") or [])).lower()
            rest = f"{template.get('description', '')} {template.get('_category', '')}".lower()
            for word in words:
                if word in title:
                    score += 10
                elif word in tags:
                    score += 6
                elif word in rest:
                    score += 2
                else:
                    score = 0.0
                    break
            if score <= 0:
                continue
        usage = template.get("usage") if isinstance(template.get("usage"), (int, float)) else 0
        scored.append((score + min(usage, 10 ** 6) / 10 ** 6, template))
    scored.sort(key=lambda item: -item[0])
    page = scored[:max(1, int(limit))]
    out: Dict[str, Any] = {"total": len(scored), "count": len(page),
                           "templates": [_template_row(t) for _score, t in page]}
    if not scored:
        out["hint"] = "No template matched. Try fewer words or another media_type (image, video, audio, 3d, llm)."
    return out


def load_template(
    config: McpConfig,
    store: SessionStore,
    name: str,
    label: Optional[str] = None,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    index = _template_index(config, base)
    entry = next((t for t in index if t["name"] == name), None)
    if entry is None:
        lowered = {t["name"].lower(): t for t in index}
        entry = lowered.get(str(name).lower()) or next(
            (t for t in index if str(t.get("title", "")).lower() == str(name).lower()), None)
    if entry is None:
        close = difflib.get_close_matches(str(name), [t["name"] for t in index], n=5, cutoff=0.4)
        raise LookupError(f"No template named {name!r}." + (f" Closest: {', '.join(close)}." if close else "")
                       + " search_templates finds them.")
    data = http_json(comfy_url(base, f"/templates/{urllib.parse.quote(entry['name'])}.json"), payload=None,
                     timeout=max(int(config.timeout_s), 30), method="GET")
    catalog = get_catalog(config, base, required=False)
    flow, notes = _open_flow(data, catalog)
    session = _new_session(store, flow, notes, label=label or str(entry.get("title") or entry["name"]))
    out = _session_info(session, notes)
    out["template"] = _template_row(entry, long=True)
    io = entry.get("io") if isinstance(entry.get("io"), dict) else {}
    for side in ("inputs", "outputs"):
        rows = [{"node_id": str(i.get("nodeId")), "class_type": i.get("nodeType"), "file": i.get("file"),
                 "media_type": i.get("mediaType")} for i in io.get(side) or [] if isinstance(i, dict)]
        if rows:
            out[side] = [{k: v for k, v in row.items() if v is not None} for row in rows]
    check = _check(config, flow, catalog, base)
    out["ready_to_run"] = check["ok"]
    if check["errors"]:
        out["problems"] = check["errors"][:12]
        if len(check["errors"]) > 12:
            out["more_problems"] = len(check["errors"]) - 12
        out["hint"] = ("These must be fixed before it can run (validate_workflow lists all of them). Missing model "
                       "files have to be installed in ComfyUI; missing node types need their custom node.")
    return out


# ===========================================================================
# Reading a workflow
# ===========================================================================


def inspect_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
    brief: bool = False,
    node_ids: Optional[List[Any]] = None,
    class_type: Optional[str] = None,
) -> Dict[str, Any]:
    flow, session, catalog = _resolve(config, store, workflow, workflow_id)
    nodes = _view(flow, catalog, node_ids, brief)
    if class_type:
        wanted = str(class_type).lower()
        nodes = [n for n in nodes if wanted in str(n.get("class_type", "")).lower()]
    out: Dict[str, Any] = {}
    if session is not None:
        out["workflow_id"] = session.id
        if session.label:
            out["label"] = session.label
    out.update(_counts(flow))
    if catalog is not None:
        out["output_nodes"] = [n["id"] for n in _view(flow, catalog, None, True)
                               if catalog.is_output_node(n["class_type"])]
    else:
        out["note"] = "The server's node list is not available: inputs are shown without names for unknown nodes."
    out["nodes"] = nodes
    return out


def convert_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
) -> Dict[str, Any]:
    flow, session, catalog = _resolve(config, store, workflow, workflow_id)
    check = _check(config, flow, catalog)
    return {
        "workflow_id": session.id if session else None,
        "ok": check["ok"],
        "errors": check["errors"],
        "warnings": check["warnings"],
        "total_nodes": check["node_count"],
        "api_workflow": check["api"],
    }


def validate_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    flow, session, catalog = _resolve(config, store, workflow, workflow_id, server_url=server_url)
    check = _check(config, flow, catalog, server_url)
    out: Dict[str, Any] = {
        "workflow_id": session.id if session else None,
        "ok": check["ok"],
        "errors": check["errors"],
        "warnings": check["warnings"],
        "total_nodes": check["node_count"],
    }
    if check["ok"]:
        out["next"] = "run_workflow"
    return out


def get_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    format: str = "workspace",
) -> Dict[str, Any]:
    flow, session, catalog = _resolve(config, store, None, workflow_id)
    if str(format).lower() == "api":
        check = _check(config, flow, catalog)
        out: Dict[str, Any] = {"workflow_id": workflow_id, "format": "api", "workflow": check["api"]}
        if check["errors"]:
            out["errors"] = check["errors"]
        return out
    return {"workflow_id": workflow_id, "format": "workspace", "workflow": json.loads(flow.to_json())}


# ===========================================================================
# Building and editing
# ===========================================================================


def load_workflow(
    config: McpConfig,
    store: SessionStore,
    source: Union[str, Dict[str, Any]],
    label: Optional[str] = None,
) -> Dict[str, Any]:
    """Load a workflow (file path, JSON, or object; workspace or API format) into a new session."""
    data, path = _read_source(source)
    catalog = get_catalog(config, required=False)
    flow, notes = _open_flow(data, catalog)
    session = _new_session(store, flow, notes, source_path=path, label=label)
    out = _session_info(session, notes)
    if catalog is None:
        out["note"] = "ComfyUI is not reachable and no node list is saved: the workflow can be read, not checked or edited by name."
    return out


def create_workflow(
    config: McpConfig,
    store: SessionStore,
    starter: Optional[str] = None,
    label: Optional[str] = None,
    nodes: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Start a workflow: empty, from a library starter, or built from an API-format graph in one call."""
    catalog = get_catalog(config, required=nodes is not None)
    if starter:
        entry = _library.load_by_name(starter)
        data, path = _read_source(str(entry.path))
        flow, notes = _open_flow(data, catalog)
        session = _new_session(store, flow, notes, source_path=path, label=label or entry.title)
        out = _session_info(session, notes)
        out["starter"] = entry.name
        return out
    if nodes is not None:
        if isinstance(nodes, str):
            nodes = json.loads(nodes)
        if not _graph.looks_like_api(nodes):
            raise ValueError(
                "`nodes` is an API-format graph: {\"1\": {\"class_type\": \"CheckpointLoaderSimple\", \"inputs\": "
                "{\"ckpt_name\": \"model.safetensors\"}}, \"2\": {\"class_type\": \"CLIPTextEncode\", \"inputs\": "
                "{\"text\": \"a cat\", \"clip\": [\"1\", 1]}}}. A link is [node_id, output_index]."
            )
        flow, notes = _open_flow(nodes, catalog)
        session = _new_session(store, flow, notes, label=label)
        out = _session_info(session, notes)
        check = _check(config, flow, catalog)
        out["valid"] = check["ok"]
        if check["errors"]:
            out["errors"] = check["errors"]
        if check["warnings"]:
            out["warnings"] = check["warnings"]
        return out
    kwargs: Dict[str, Any] = {"node_info": catalog.node_info} if catalog is not None else {}
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        flow = autograph.Flow.create(**kwargs)
    session = _new_session(store, flow, {"auto_layout": True}, label=label)
    out = _session_info(session)
    if catalog is None:
        out["note"] = ("ComfyUI is not reachable and no node list is saved, so nodes cannot be added yet. "
                       "comfyui_status shows what is reachable.")
    return out


def _split_output(value: Any) -> Tuple[str, Any]:
    """A link given as [node_id, index_or_name], "node_id" or {"node_id": ..., "output": ...}."""
    if isinstance(value, dict):
        return str(value.get("node_id", value.get("from_node", ""))), value.get("output", value.get("from_output"))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return str(value[0]), value[1]
    raise ValueError(f"A link is [node_id, output_index], not {value!r}.")


def _is_link_value(value: Any) -> bool:
    if _graph.is_link(value):
        return True
    return (isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], (str, int))
            and not isinstance(value[0], bool) and isinstance(value[1], str))


def _outputs_of(node: Dict[str, Any], catalog: Optional[_graph.Catalog]) -> List[Dict[str, Any]]:
    class_type = _class_type(node)
    if catalog is not None and class_type in catalog:
        return catalog.outputs(class_type)
    return [{"index": i, "name": str(o.get("name")), "type": str(o.get("type"))}
            for i, o in enumerate(node.get("outputs") or []) if isinstance(o, dict)]


def _input_names(node: Dict[str, Any], catalog: Optional[_graph.Catalog]) -> List[str]:
    class_type = _class_type(node)
    if catalog is not None and class_type in catalog:
        return [f"{s['name']}:{s['type']}" for s in catalog.inputs(class_type, 0)]
    return [f"{s.get('name')}:{s.get('type')}" for s in node.get("inputs") or [] if isinstance(s, dict)]


def _connect(
    flow: "autograph.Flow",
    catalog: Optional[_graph.Catalog],
    from_node: Any,
    to_node: Any,
    to_input: str,
    from_output: Any = None,
) -> Dict[str, Any]:
    """Link an output to an input after checking that the two exist and their types fit."""
    src, dst = _node(flow, from_node), _node(flow, to_node)
    src_type, dst_type = _class_type(src), _class_type(dst)
    if str(src.get("id")) == str(dst.get("id")):
        raise ValueError("A node cannot be connected to itself.")
    outputs = _outputs_of(src, catalog)
    if not outputs:
        raise ValueError(f"Node {from_node} ({src_type}) has no outputs.")
    wanted_type = "*"
    if catalog is not None and dst_type in catalog:
        spec = catalog.input_spec(dst_type, to_input)
        if spec is None:
            names = _input_names(dst, catalog)
            close = difflib.get_close_matches(str(to_input), [n.split(":")[0] for n in names], n=3, cutoff=0.5)
            raise LookupError(f"{dst_type} (node {to_node}) has no input {to_input!r}."
                           + (f" Did you mean {', '.join(close)}?" if close else "") + f" Inputs: {', '.join(names)}")
        if spec["kind"] == "connection_group":
            raise ValueError(f"'{to_input}' on {dst_type} is a group of inputs; connect to a member: {spec.get('members')}")
        wanted_type = spec["type"]
    else:
        slot = next((s for s in dst.get("inputs") or [] if isinstance(s, dict) and s.get("name") == to_input), None)
        if slot is not None:
            wanted_type = str(slot.get("type") or "*")
    listing = ", ".join(f"{o['index']}: {o['name']} ({o['type']})" for o in outputs)
    chosen: Optional[Dict[str, Any]] = None
    if isinstance(from_output, bool):
        raise ValueError("from_output is an output index or name.")
    if isinstance(from_output, int) or (isinstance(from_output, str) and from_output.strip().lstrip("-").isdigit()):
        index = int(from_output)
        if index < 0 or index >= len(outputs):
            raise IndexError(f"Node {from_node} ({src_type}) has no output {index}. Outputs: {listing}")
        chosen = outputs[index]
    elif isinstance(from_output, str) and from_output.strip():
        key = from_output.strip().lower()
        named = [o for o in outputs if o["name"].lower() == key] or [o for o in outputs if str(o["type"]).lower() == key]
        if not named:
            raise LookupError(f"Node {from_node} ({src_type}) has no output named {from_output!r}. Outputs: {listing}")
        fitting = [o for o in named if _graph.types_connect(o["type"], wanted_type)]
        chosen = (fitting or named)[0]
    else:
        fitting = [o for o in outputs if _graph.types_connect(o["type"], wanted_type)]
        exact = [o for o in fitting if str(o["type"]).upper() == str(wanted_type).upper()]
        if len(exact) == 1 or (exact and len({o["name"] for o in exact}) == 1):
            chosen = exact[0]
        elif len(fitting) == 1:
            chosen = fitting[0]
        elif not fitting:
            raise TypeError(f"No output of node {from_node} ({src_type}) fits {dst_type}.{to_input} ({wanted_type}). "
                            f"Outputs: {listing}")
        else:
            raise ValueError(f"Several outputs of node {from_node} ({src_type}) fit {dst_type}.{to_input}; "
                             f"give from_output. Outputs: {listing}")
    if not _graph.types_connect(chosen["type"], wanted_type):
        fits_here = [o for o in outputs if _graph.types_connect(o["type"], wanted_type)]
        takers = []
        if catalog is not None and dst_type in catalog:
            takers = [s["name"] for s in catalog.inputs(dst_type, 0)
                      if s["kind"] == "connection" and _graph.types_connect(chosen["type"], s["type"])]
        hint = ""
        if fits_here:
            hint += f" Outputs of node {from_node} that fit: " + ", ".join(f"{o['index']} ({o['name']})" for o in fits_here) + "."
        if takers:
            hint += f" Inputs of node {to_node} that take {chosen['type']}: {', '.join(takers)}."
        if not hint:
            hint = f" Find a node that gives {wanted_type} with list_node_types(output_type=\"{wanted_type}\")."
        raise TypeError(f"Types do not fit: {src_type}.{chosen['name']} gives {chosen['type']}, "
                        f"{dst_type}.{to_input} takes {wanted_type}.{hint}")
    replaced = None
    for slot in dst.get("inputs") or []:
        if isinstance(slot, dict) and slot.get("name") == to_input and slot.get("link") is not None:
            replaced = True
    _ref(flow, to_node).connect(to_input, _ref(flow, from_node), chosen["index"])
    out: Dict[str, Any] = {
        "from": f"{src.get('id')}.{chosen['name']}", "to": f"{dst.get('id')}.{to_input}",
        "link": [str(src.get("id")), chosen["index"]], "type": chosen["type"],
    }
    if replaced:
        out["replaced_previous_link"] = True
    return out


def _named_values(node: Dict[str, Any], catalog: _graph.Catalog) -> Dict[str, Any]:
    return _graph._widget_values(_graph.plain(node), catalog.node_info)


def _set_value(
    flow: "autograph.Flow",
    catalog: Optional[_graph.Catalog],
    node_id: Any,
    name: str,
    value: Any,
) -> Dict[str, Any]:
    """Set one input of one node: a widget value, a link, or None to disconnect.

    Returns ``{"input", "ok", ...}``; a refused change says why and what would work.
    """
    node = _node(flow, node_id)
    class_type = _class_type(node)
    row: Dict[str, Any] = {"node_id": str(node.get("id")), "input": name}
    try:
        if _is_link_value(value):
            src_id, output = _split_output(value)
            row.update(_connect(flow, catalog, src_id, node_id, name, output))
            row["ok"] = True
            return row
        if catalog is None or class_type not in catalog:
            row.update(ok=False, error=(
                "The server's node list is not available, so inputs cannot be set by name." if catalog is None
                else f"The server has no node type {class_type!r}, so its inputs are not known."))
            return row
        current = _named_values(node, catalog)
        spec = catalog.input_spec(class_type, name, current)
        if spec is None:
            names = [s["name"] for s in catalog.inputs(class_type, 0)]
            for parent in catalog.inputs(class_type, 0):
                chosen = (parent.get("inputs_per_option") or {}).get(str(current.get(parent["name"])))
                names += [s["name"] for s in chosen or []]
            row.update(ok=False, error=f"{class_type} has no input {name!r}"
                       + (f" while '{name.split('.')[0]}' is {current.get(name.split('.')[0])!r}." if "." in name else "."),
                       closest=difflib.get_close_matches(str(name), names, n=3, cutoff=0.5), inputs=names)
            if "." in name:
                row["hint"] = (f"Set '{name.split('.')[0]}' first; describe_node_type shows the inputs each "
                               "choice has.")
            return row
        if value is None:
            if spec["kind"] == "widget" and not any(
                    isinstance(s, dict) and s.get("name") == name and s.get("link") is not None
                    for s in node.get("inputs") or []):
                row.update(ok=False, error=f"'{name}' is a widget; give it a value.")
                return row
            _ref(flow, node_id).disconnect(name)
            row.update(ok=True, disconnected=True)
            return row
        if spec["kind"] != "widget":
            primitive = _graph.primitive_for(spec, value, catalog) if spec.get("accepts_value") else None
            if primitive is None:
                row.update(
                    ok=False,
                    error=f"'{name}' on {class_type} takes a {spec['type']} connection, not a value.",
                    hint=f"Give a link [node_id, output_index] from a node that gives {spec['type']} "
                         f"(list_node_types(output_type=\"{spec['type']}\")).",
                )
                return row
            source = flow.add_node(primitive, value=value)
            _place(flow, source._data_ref())
            _ref(flow, node_id).connect(name, source, 0)
            row.update(ok=True, value=value, via_node=str(source.addr),
                       note=f"'{name}' only takes a connection, so the value is held by a new {primitive} node.")
            return row
        fixed, problem = _graph.check_value(spec, value)
        if problem is not None and problem["code"] in ("wrong_type", "out_of_range"):
            row.update(ok=False, error=problem["message"])
            row.update({k: v for k, v in problem.items() if k in ("min", "max")})
            return row
        if "inputs_per_option" in spec and problem is not None:
            row.update(ok=False, error=problem["message"], options=problem.get("options"))
            return row
        if "inputs_per_option" in spec:
            _switch_option(flow, catalog, node, name, fixed)
        else:
            setattr(_ref(flow, node_id), name, fixed)
        row.update(ok=True, value=fixed)
        if problem is not None:
            row["warning"] = problem["message"]
            for key in ("closest", "hint"):
                if problem.get(key):
                    row[key] = problem[key]
        if any(isinstance(s, dict) and s.get("name") == name and s.get("link") is not None
               for s in node.get("inputs") or []):
            row["note"] = f"'{name}' is linked, and the link wins. Pass null for it to use the value."
        return row
    except (LookupError, ValueError, TypeError) as exc:
        row.update(ok=False, error=str(exc).strip("'\""))
        return row


def _switch_option(flow: "autograph.Flow", catalog: _graph.Catalog, node: Dict[str, Any], name: str, key: Any) -> None:
    """Choose another option of a dropdown that has inputs of its own: those inputs get the new option's defaults."""
    class_type = _class_type(node)
    values = {k: v for k, v in _named_values(node, catalog).items() if not k.startswith(name + ".")}
    values[name] = key
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        scratch = autograph.Flow.create(node_info=catalog.node_info)
        scratch.add_node(class_type, **values)
    node["widgets_values"] = _graph.plain(_raw(scratch)["nodes"][0].get("widgets_values"))


def _node_result(flow: "autograph.Flow", catalog: Optional[_graph.Catalog], node_id: Any) -> Dict[str, Any]:
    rows = _view(flow, catalog, [node_id])
    return rows[0] if rows else {"id": str(node_id)}


def add_node(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    class_type: str,
    inputs: Optional[Dict[str, Any]] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a node. ``inputs`` holds widget values and links (``[node_id, output_index]``) by input name."""
    flow, session, catalog = _resolve(config, store, None, workflow_id, need_catalog=True)
    assert catalog is not None and session is not None
    real = catalog.resolve(class_type)
    given = dict(inputs or {})
    specs = {s["name"]: s for s in catalog.inputs(real, 0)}
    values: Dict[str, Any] = {}
    later: Dict[str, Any] = {}
    problems: List[str] = []
    for name, value in given.items():
        spec = catalog.input_spec(real, name, given)
        if spec is None:
            close = difflib.get_close_matches(str(name), list(specs), n=3, cutoff=0.5)
            problems.append(f"no input {name!r}" + (f" (did you mean {', '.join(close)}?)" if close else ""))
            continue
        if value is None:
            continue
        if _is_link_value(value):
            src_id, _output = _split_output(value)
            _node(flow, src_id)          # must exist before anything is added
            later[name] = value
            continue
        if spec["kind"] != "widget":
            if spec.get("accepts_value") and _graph.primitive_for(spec, value, catalog):
                later[name] = value
            else:
                problems.append(f"'{name}' takes a {spec['type']} connection, not a value: give a link "
                                "[node_id, output_index]")
            continue
        fixed, problem = _graph.check_value(spec, value)
        if problem is not None and (problem["code"] in ("wrong_type", "out_of_range") or "inputs_per_option" in spec):
            problems.append(problem["message"])
            continue
        if "." in name and "inputs_per_option" not in spec:
            later[name] = fixed          # an input of a dropdown's option: set once the node exists
        else:
            values[name] = fixed
    if problems:
        names = ", ".join(f"{s['name']}:{s['type']}" for s in specs.values())
        raise ValueError(f"{real} was not added: " + "; ".join(p.rstrip(".") for p in problems)
                         + f". Inputs of {real}: {names}")
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        ref = flow.add_node(real, **values)
    node = ref._data_ref()
    node_id = str(ref.addr)
    if title:
        node["title"] = title
    if not session.metadata.get("auto_layout"):
        _place(flow, node)
    results = [_set_value(flow, catalog, node_id, name, value) for name, value in later.items()]
    failed = [r for r in results if not r.get("ok")]
    if failed:
        flow.remove_node(int(node_id) if node_id.isdigit() else node_id)
        _raw(flow)["last_node_id"] = max([n.get("id") for n in _raw(flow).get("nodes") or []
                                          if isinstance(n, dict) and isinstance(n.get("id"), int)] or [0])
        _touch(store, session)
        raise ValueError(f"{real} was not added: " + "; ".join(
            (str(r.get("error")) if str(r.get("error", "")).startswith(("'", r["input"])) else f"{r['input']}: {r.get('error')}")
            + (f" {r['hint']}" if r.get("hint") else "") for r in failed))
    _touch(store, session)
    out: Dict[str, Any] = {"workflow_id": workflow_id, "node_id": node_id}
    out.update(_node_result(flow, catalog, node_id))
    out.pop("id", None)
    notes = [str(w.message) for w in caught] + [r["warning"] for r in results if r.get("warning")]
    for name, value in values.items():
        _fixed, problem = _graph.check_value(specs.get(name) or {"name": name}, value)
        if problem is not None:
            notes.append(problem["message"] + (f" Closest: {', '.join(problem['closest'])}." if problem.get("closest") else ""))
    if notes:
        out["warnings"] = notes
    out.update(_counts(flow))
    return out


def connect_nodes(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    from_node: str,
    to_node: str,
    to_input: str,
    from_output: Optional[Union[str, int]] = None,
) -> Dict[str, Any]:
    flow, session, catalog = _resolve(config, store, None, workflow_id)
    out = {"workflow_id": workflow_id, "ok": True}
    out.update(_connect(flow, catalog, from_node, to_node, to_input, from_output))
    _touch(store, session)
    still = _node_result(flow, catalog, to_node).get("missing")
    if still:
        out["still_missing_on_target"] = still
    return out


def disconnect_input(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    node_id: str,
    input_name: str,
) -> Dict[str, Any]:
    flow, session, catalog = _resolve(config, store, None, workflow_id)
    node = _node(flow, node_id)
    slot = next((s for s in node.get("inputs") or [] if isinstance(s, dict) and s.get("name") == input_name), None)
    if slot is None or slot.get("link") is None:
        linked = [s.get("name") for s in node.get("inputs") or [] if isinstance(s, dict) and s.get("link") is not None]
        return {"workflow_id": workflow_id, "ok": False,
                "error": f"Input {input_name!r} of node {node_id} is not connected.", "connected_inputs": linked}
    _ref(flow, node_id).disconnect(input_name)
    _touch(store, session)
    return {"workflow_id": workflow_id, "node_id": str(node_id), "input_name": input_name, "ok": True, **_counts(flow)}


def remove_node(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    node_id: Union[str, List[str]],
) -> Dict[str, Any]:
    flow, session, catalog = _resolve(config, store, None, workflow_id)
    ids = node_id if isinstance(node_id, (list, tuple)) else [node_id]
    before = len(_raw(flow).get("links") or [])
    removed, unplugged = [], []
    for one in ids:
        node = _node(flow, one)
        real_id = node.get("id")
        for other in _view(flow, catalog, None, True):
            for name, value in (other.get("inputs") or {}).items():
                if _graph.is_link(value) and str(value[0]) == str(real_id):
                    unplugged.append(f"{other['id']}.{name}")
        flow.remove_node(real_id if isinstance(real_id, int) else node)
        removed.append(str(real_id))
    _touch(store, session)
    out: Dict[str, Any] = {"workflow_id": workflow_id, "removed": removed,
                           "links_dropped": before - len(_raw(flow).get("links") or []), **_counts(flow)}
    if unplugged:
        out["inputs_left_unconnected"] = unplugged
    return out


def set_workflow_values(
    config: McpConfig,
    store: SessionStore,
    updates: List[Dict[str, Any]],
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Change inputs of existing nodes: values, links, or null to disconnect."""
    if isinstance(updates, dict):
        updates = [updates]
    if not isinstance(updates, list):
        raise TypeError("updates is a list of {node_id | class_type | title, inputs: {name: value}}.")
    flow, session, catalog = _resolve(config, store, workflow, workflow_id)
    applied: List[Dict[str, Any]] = []
    nodes = [n for n in _raw(flow).get("nodes") or [] if isinstance(n, dict)]
    for patch in updates:
        if not isinstance(patch, dict):
            continue
        inputs = patch.get("inputs") if isinstance(patch.get("inputs"), dict) else {}
        if patch.get("node_id") is not None:
            try:
                targets = [_node(flow, patch["node_id"])]
            except LookupError as exc:
                applied.append({"node_id": str(patch["node_id"]), "ok": False, "error": str(exc).strip("'\"")})
                continue
        else:
            wanted_type = str(patch.get("class_type") or "").lower()
            wanted_title = patch.get("title")
            if not wanted_type and not wanted_title:
                applied.append({"ok": False, "error": "An update names its node(s) with node_id, class_type or title."})
                continue
            targets = [n for n in nodes
                       if (not wanted_type or _class_type(n).lower() == wanted_type)
                       and (not wanted_title or str(n.get("title") or "") == wanted_title)]
            if not targets:
                applied.append({"ok": False, "class_type": patch.get("class_type"), "title": wanted_title,
                                "error": "No node matches. inspect_workflow lists the nodes."})
                continue
        for node in targets:
            node_id = node.get("id")
            if isinstance(patch.get("set_title"), str):
                node["title"] = patch["set_title"]
                applied.append({"node_id": str(node_id), "title": patch["set_title"], "ok": True})
            for name, value in inputs.items():
                applied.append(_set_value(flow, catalog, node_id, str(name), value))
            if not inputs and not isinstance(patch.get("set_title"), str):
                applied.append({"node_id": str(node_id), "ok": False, "error": "The update has no `inputs`."})
    failed = sum(1 for row in applied if not row.get("ok"))
    out: Dict[str, Any] = {"ok": failed == 0, "applied": applied}
    if failed:
        out["failed"] = failed
    if session is not None:
        _touch(store, session)
        out = {"workflow_id": session.id, **out}
    else:
        out["workflow"] = json.loads(flow.to_json())
    return out


def set_node_mode(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    node_ids: Union[str, List[str]],
    mode: str = "active",
) -> Dict[str, Any]:
    """Switch nodes on or off: ``active``, ``bypassed`` (data passes through) or ``muted`` (does not run)."""
    key = str(mode).strip().lower()
    if key not in NODE_MODES:
        raise ValueError("mode is one of: active, bypassed, muted.")
    flow, session, _catalog = _resolve(config, store, None, workflow_id)
    ids = node_ids if isinstance(node_ids, (list, tuple)) else [node_ids]
    changed = []
    for node_id in ids:
        node = _node(flow, node_id)
        node["mode"] = NODE_MODES[key]
        changed.append(str(node.get("id")))
    _touch(store, session)
    return {"workflow_id": workflow_id, "mode": {"mute": "muted", "bypass": "bypassed"}.get(key, key), "node_ids": changed}


def merge_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    fragment: Union[str, Dict[str, Any]],
    auto_connect: bool = True,
) -> Dict[str, Any]:
    """Merge a workspace-format fragment into the session, wiring what can only go one way."""
    flow, session, catalog = _resolve(config, store, None, workflow_id)
    data, _path = _read_source(fragment)
    if _graph.looks_like_api(data):
        if catalog is None:
            raise RuntimeError("An API-format fragment can only be merged when the server's node list is available.")
        built, _ids, _problems = _graph.build_flow(data, catalog)
        data = json.loads(built.to_json())
    report = _graft.merge_into_flow(flow, data, auto_connect=auto_connect)
    _touch(store, session)
    return {"workflow_id": workflow_id, **report, **_counts(flow)}


def save_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    path: Optional[str] = None,
    format: str = "workspace",
) -> Dict[str, Any]:
    flow, session, catalog = _resolve(config, store, None, workflow_id)
    if str(format).lower() == "api":
        if not path:
            raise ValueError("Give `path` to save the API-format workflow.")
        check = _check(config, flow, catalog)
        if check["api"] is None:
            return {"workflow_id": workflow_id, "ok": False, "errors": check["errors"]}
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(check["api"], indent=2), encoding="utf-8")
        return {"workflow_id": workflow_id, "saved_to": str(target), "format": "api"}
    _touch(store, session)
    target = store.save(workflow_id, path=path)
    return {"workflow_id": workflow_id, "saved_to": str(target), "format": "workspace",
            "note": "Opens in ComfyUI (drag the file onto the editor, or Workflow > Open)."}


def list_sessions(config: McpConfig, store: SessionStore) -> Dict[str, Any]:
    return {"sessions": store.list()}


def close_session(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    delete_checkpoint: bool = False,
) -> Dict[str, Any]:
    return store.close(workflow_id, delete_checkpoint=delete_checkpoint)


# ===========================================================================
# Library / online sources
# ===========================================================================


def list_workflow_sources(config: McpConfig) -> Dict[str, Any]:
    return {"sources": list(_library.ONLINE_SOURCES)}


def search_local_workflows(
    config: McpConfig,
    query: Optional[str] = None,
    tags: Optional[List[str]] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    entries = _library.search(query=query, tags=tags, limit=limit)
    return {
        "library_dirs": [str(p) for p in _library.library_dirs()],
        "count": len(entries),
        "results": [e.to_dict() for e in entries],
    }


def load_local_workflow(
    config: McpConfig,
    store: SessionStore,
    name: str,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    entry = _library.load_by_name(name)
    data, path = _read_source(str(entry.path))
    catalog = get_catalog(config, required=False)
    flow, notes = _open_flow(data, catalog)
    session = _new_session(store, flow, notes, source_path=path, label=label or entry.title)
    out = _session_info(session, notes)
    out["library_entry"] = entry.to_dict()
    return out


# ===========================================================================
# Running
# ===========================================================================


def _history_entry(config: McpConfig, base: str, prompt_id: str) -> Optional[Dict[str, Any]]:
    data = http_json(comfy_url(base, f"/history/{urllib.parse.quote(str(prompt_id))}"), payload=None,
                     timeout=config.timeout_s, method="GET")
    entry = data.get(prompt_id) if isinstance(data, dict) else None
    return entry if isinstance(entry, dict) else None


def _queue(config: McpConfig, base: str) -> Tuple[List[str], List[str]]:
    """(ids running, ids waiting in order)."""
    data = http_json(comfy_url(base, "/queue"), payload=None, timeout=config.timeout_s, method="GET")

    def ids(rows: Any) -> List[Tuple[Any, str]]:
        return [(row[0], str(row[1])) for row in rows or [] if isinstance(row, (list, tuple)) and len(row) > 1]

    running = [pid for _n, pid in ids(data.get("queue_running"))]
    pending = [pid for _n, pid in sorted(ids(data.get("queue_pending")), key=lambda item: item[0])]
    return running, pending


def _output_rows(entry: Optional[Dict[str, Any]], prompt_id: str) -> List[Dict[str, Any]]:
    """The files and texts a finished job produced."""
    rows: List[Dict[str, Any]] = []
    outputs = entry.get("outputs") if isinstance(entry, dict) else None
    if not isinstance(outputs, dict):
        return rows
    for node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        for kind, items in node_out.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("filename"):
                    folder = str(item.get("subfolder") or "")
                    name = f"{folder}/{item['filename']}" if folder else str(item["filename"])
                    row = {"node_id": str(node_id), "kind": kind, "filename": item["filename"],
                           "subfolder": folder, "type": item.get("type", ""),
                           "resource_uri": f"comfyui://outputs/{prompt_id}/{item['filename']}"}
                    if item.get("type") in ("output", "temp", "input"):
                        # What a loader node (LoadImage, LoadAudio, ...) takes to read this file back.
                        row["as_input"] = name if item["type"] == "input" else f"{name} [{item['type']}]"
                    rows.append(row)
                elif isinstance(item, str) and kind == "text":
                    rows.append({"node_id": str(node_id), "kind": "text",
                                 "text": item if len(item) <= 4000 else item[:4000] + "…"})
    return rows


def _execution_errors(entry: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for error in _errors.parse_history_errors(entry):
        details = error.pop("details", None) or {}
        trace = details.get("traceback")
        if isinstance(trace, list) and trace:
            error["traceback_tail"] = "".join(str(line) for line in trace[-4:]).strip()[-1200:]
        out.append({k: v for k, v in error.items() if v not in (None, "")})
    return out


def _job_status(config: McpConfig, base: str, prompt_id: str) -> Dict[str, Any]:
    """Where a job is: pending / running / completed / failed / cancelled / unknown."""
    out: Dict[str, Any] = {"prompt_id": prompt_id}
    entry = _history_entry(config, base, prompt_id)
    if entry is not None:
        status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
        messages = [m for m in status.get("messages") or [] if isinstance(m, (list, tuple)) and len(m) > 1]
        kinds = [m[0] for m in messages]
        stamps = {m[0]: m[1].get("timestamp") for m in messages if isinstance(m[1], dict)}
        if "execution_interrupted" in kinds:
            out["status"] = "cancelled"
        elif status.get("status_str") == "error" or "execution_error" in kinds:
            out["status"] = "failed"
            out["errors"] = _execution_errors(entry)
        else:
            out["status"] = "completed"
        start, end = stamps.get("execution_start"), None
        for kind in ("execution_success", "execution_error", "execution_interrupted"):
            end = stamps.get(kind) or end
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            out["duration_s"] = round((end - start) / 1000.0, 1)
        cached = next((m[1].get("nodes") for m in messages if m[0] == "execution_cached" and isinstance(m[1], dict)), None)
        if cached:
            out["cached_nodes"] = len(cached)
        rows = _output_rows(entry, prompt_id)
        if rows:
            out["outputs"] = rows
        elif out["status"] == "completed":
            out["note"] = "Finished without new outputs (every node was cached, or no output node produced a file)."
        return out
    running, pending = _queue(config, base)
    if prompt_id in running:
        out["status"] = "running"
    elif prompt_id in pending:
        out["status"] = "pending"
        out["queue_position"] = pending.index(prompt_id) + 1 + len(running)
    else:
        out["status"] = "unknown"
        out["hint"] = "Not in the queue and not in the history: the id is wrong, or the history was cleared."
    return out


def _wait(
    config: McpConfig,
    base: str,
    prompt_id: str,
    timeout: float,
    on_progress: Optional[Progress] = None,
) -> Dict[str, Any]:
    """Poll until the job is done or ``timeout`` seconds have passed."""
    started = time.time()
    unknown = 0
    while True:
        status = _job_status(config, base, prompt_id)
        elapsed = time.time() - started
        state = status["status"]
        if state in ("completed", "failed", "cancelled"):
            return status
        if state == "unknown":
            unknown += 1                  # between leaving the queue and reaching the history there is a short gap
            if unknown >= 5:
                return status
        else:
            unknown = 0
        if on_progress is not None:
            try:
                message = (f"waiting in the queue (position {status.get('queue_position', '?')})"
                           if state == "pending" else f"running, {int(elapsed)} s")
                on_progress(min(elapsed, timeout), float(timeout), message)
            except Exception:
                pass
        if elapsed >= timeout:
            status["timed_out"] = True
            status["waited_s"] = round(elapsed, 1)
            status["hint"] = (f"Still {state}. wait_for_job(prompt_id=\"{prompt_id}\") keeps waiting, "
                              "get_job_status checks once, cancel_job stops it.")
            return status
        time.sleep(POLL_INTERVAL_S if elapsed > 3 else 0.4)


def _node_errors(resp: Any) -> List[Dict[str, Any]]:
    """Per-node problems ComfyUI reports while still queueing the rest of a prompt."""
    out: List[Dict[str, Any]] = []
    node_errors = resp.get("node_errors") if isinstance(resp, dict) else None
    if isinstance(node_errors, dict):
        for node_id, info in node_errors.items():
            if not isinstance(info, dict):
                continue
            for error in info.get("errors") or []:
                if isinstance(error, dict):
                    out.append({"node_id": str(node_id), "class_type": info.get("class_type"),
                                "error_type": error.get("type"), "message": error.get("message"),
                                "details": error.get("details")})
    return out


def _with_hints(server_errors: List[Dict[str, Any]], found: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """ComfyUI's errors, each with what the pre-flight found for the same node and problem (closest, candidates, hint)."""
    out = []
    for error in server_errors:
        row = {k: v for k, v in error.items() if v not in (None, "")}
        code = _graph.SERVER_CODES.get(str(error.get("error_type")))
        details = str(error.get("details") or "")
        for issue in found:
            if str(issue.get("node_id")) != str(error.get("node_id")) or issue.get("code") != code:
                continue
            if issue.get("input") and details and str(issue["input"]) not in details:
                continue
            for key in ("input", "closest", "candidates", "hint", "min", "max"):
                if issue.get(key) not in (None, [], ""):
                    row.setdefault(key, issue[key])
            break
        out.append(row)
    return out


def _submit(
    config: McpConfig,
    flow: "autograph.Flow",
    catalog: Optional[_graph.Catalog],
    base: str,
    skip_validation: bool,
) -> Tuple[Optional[SubmissionResult], Dict[str, Any]]:
    """Check and queue a workflow. Returns (submission or None, what to report).

    Problems in the graph itself (unknown node types, unconnected or mismatched
    inputs, no output) stop it here. Problems with a value are ComfyUI's to
    decide, since a node may check its own inputs: the workflow is queued, and
    if ComfyUI refuses it, its errors come back with what was found here.
    """
    report: Dict[str, Any] = {}
    found: List[Dict[str, Any]] = []
    if not skip_validation:
        check = _check(config, flow, catalog, base)
        found = check["errors"]
        if check["warnings"]:
            report["warnings"] = check["warnings"]
        if any(issue["code"] not in _graph.VALUE_CODES for issue in found):
            report.update(ok=False, stage="validation", errors=found,
                          hint="Nothing was queued. Fix these and run again; skip_validation=true queues it anyway.")
            return None, report
    try:
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")
            submission = flow.submit(server_url=base, wait=False, fetch_outputs=False, timeout=config.timeout_s)
    except Exception as exc:
        report.update(ok=False, stage="submit", errors=_with_hints(_errors.parse_prompt_error(exc), found),
                      hint="ComfyUI refused the workflow; nothing was queued.")
        return None, report
    partial = _node_errors(submission)
    if partial:
        report["node_errors"] = _with_hints(partial, found)
        report["note"] = "ComfyUI refused these nodes and queued the rest: the outputs that depend on them will be missing."
    elif found:
        report.setdefault("warnings", []).extend(found)       # ComfyUI accepted what looked wrong here
    return submission, report


def run_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
    wait: bool = True,
    fetch_outputs: bool = True,
    save_to: Optional[str] = None,
    inline_images: bool = True,
    max_inline: Optional[int] = None,
    server_url: Optional[str] = None,
    wait_timeout: Optional[float] = None,
    skip_validation: bool = False,
    on_progress: Optional[Progress] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    flow, session, catalog = _resolve(config, store, workflow, workflow_id, server_url=server_url)
    submission, payload = _submit(config, flow, catalog, base, skip_validation)
    payload["workflow_id"] = session.id if session else None
    payload["server_url"] = base
    if submission is None:
        return payload
    prompt_id = str(submission.prompt_id)
    payload["prompt_id"] = prompt_id
    if not wait:
        payload.update(ok=True, status="queued",
                       hint=f"wait_for_job(prompt_id=\"{prompt_id}\") waits for it and returns the outputs.")
        return payload
    timeout = float(wait_timeout) if wait_timeout else float(getattr(config, "wait_timeout_s", 120))
    status = _wait(config, base, prompt_id, timeout, on_progress)
    return _finish(config, base, payload, status, fetch_outputs=fetch_outputs, save_to=save_to,
                   inline_images=inline_images, max_inline=max_inline)


def _finish(
    config: McpConfig,
    base: str,
    payload: Dict[str, Any],
    status: Dict[str, Any],
    *,
    fetch_outputs: bool,
    save_to: Optional[str],
    inline_images: bool,
    max_inline: Optional[int],
) -> Dict[str, Any]:
    """The result of a job that was waited for: its state, and its outputs when it finished."""
    prompt_id = status["prompt_id"]
    payload.update({k: v for k, v in status.items() if k != "outputs"})
    state = status["status"]
    if state == "completed":
        payload["ok"] = True
    elif state in ("failed", "cancelled", "unknown"):
        payload["ok"] = False
    if state != "completed":
        if status.get("outputs"):
            payload["outputs"] = status["outputs"]       # what the job produced before it stopped
        return payload
    files = [row for row in status.get("outputs") or [] if row.get("filename")]
    texts = [row for row in status.get("outputs") or [] if row.get("kind") == "text"]
    if texts:
        payload["texts"] = texts
    if not fetch_outputs or not files:
        payload["outputs"] = files
        return payload
    try:
        submission = SubmissionResult.from_prompt_id(prompt_id, server_url=base)
        got = _materialize_submission(config, submission, save_to=save_to, inline_images=inline_images,
                                      max_inline=max_inline, do_fetch=True)
        by_name = {row["filename"]: row for row in files}
        for entry in got.get("outputs") or []:
            extra = by_name.get(entry.get("filename")) or {}
            for key in ("node_id", "as_input"):
                if extra.get(key):
                    entry[key] = extra[key]
        payload["outputs"] = got.get("outputs")
        payload["save_path"] = got.get("save_path")
        payload["content_blocks"] = got.get("content_blocks")
    except Exception as exc:
        payload["outputs"] = files
        payload["fetch_error"] = f"{type(exc).__name__}: {exc}"
        payload["hint"] = f"The job finished; fetch_outputs(prompt_id=\"{prompt_id}\") downloads the files."
    return payload


def queue_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
    server_url: Optional[str] = None,
    skip_validation: bool = False,
) -> Dict[str, Any]:
    return run_workflow(config, store, workflow=workflow, workflow_id=workflow_id, wait=False,
                        server_url=server_url, skip_validation=skip_validation)


def get_job_status(
    config: McpConfig,
    prompt_id: Optional[str] = None,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    if prompt_id:
        return _job_status(config, base, str(prompt_id))
    running, pending = _queue(config, base)
    return {"running": running, "pending": pending,
            "recent": get_history(config, limit=5, server_url=base)["jobs"]}


def wait_for_job(
    config: McpConfig,
    prompt_id: str,
    timeout: Optional[float] = None,
    fetch_outputs: bool = True,
    save_to: Optional[str] = None,
    inline_images: bool = True,
    max_inline: Optional[int] = None,
    server_url: Optional[str] = None,
    on_progress: Optional[Progress] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    limit = float(timeout) if timeout else float(getattr(config, "wait_timeout_s", 120))
    status = _wait(config, base, str(prompt_id), limit, on_progress)
    return _finish(config, base, {"server_url": base}, status, fetch_outputs=fetch_outputs, save_to=save_to,
                   inline_images=inline_images, max_inline=max_inline)


def cancel_job(
    config: McpConfig,
    prompt_id: str,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    prompt_id = str(prompt_id)
    running, pending = _queue(config, base)
    if prompt_id in pending:
        http_json(comfy_url(base, "/queue"), payload={"delete": [prompt_id]}, timeout=config.timeout_s, method="POST")
        return {"prompt_id": prompt_id, "ok": True, "was": "pending", "action": "removed from the queue"}
    if prompt_id in running:
        http_json(comfy_url(base, "/interrupt"), payload={"prompt_id": prompt_id}, timeout=config.timeout_s, method="POST")
        return {"prompt_id": prompt_id, "ok": True, "was": "running", "action": "interrupted"}
    status = _job_status(config, base, prompt_id)
    return {"prompt_id": prompt_id, "ok": False, "status": status["status"],
            "error": "The job is not in the queue, so there is nothing to cancel."}


def get_history(
    config: McpConfig,
    prompt_id: Optional[str] = None,
    limit: int = 10,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    if prompt_id:
        return _job_status(config, base, str(prompt_id))
    data = http_json(comfy_url(base, f"/history?max_items={max(1, int(limit))}"), payload=None,
                     timeout=config.timeout_s, method="GET")
    jobs = []
    for pid, entry in reversed(list(data.items() if isinstance(data, dict) else [])):
        if not isinstance(entry, dict):
            continue
        status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
        kinds = [m[0] for m in status.get("messages") or [] if isinstance(m, (list, tuple)) and m]
        state = ("cancelled" if "execution_interrupted" in kinds
                 else "failed" if status.get("status_str") == "error" else "completed")
        rows = _output_rows(entry, str(pid))
        jobs.append({"prompt_id": str(pid), "status": state, "outputs": len(rows),
                     "files": [r["filename"] for r in rows if r.get("filename")][:4]})
    return {"count": len(jobs), "jobs": jobs,
            "hint": "get_job_status(prompt_id) has the details of one; fetch_outputs downloads its files."}


def interrupt(config: McpConfig, server_url: Optional[str] = None) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    try:
        http_json(comfy_url(base, "/interrupt"), payload={}, timeout=config.timeout_s, method="POST")
        return {"server_url": base, "ok": True}
    except urllib.error.HTTPError as exc:
        return {"server_url": base, "ok": False, "error": f"HTTP {exc.code}: {exc.reason}"}


def free_memory(
    config: McpConfig,
    unload_models: bool = True,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    http_json(comfy_url(base, "/free"), payload={"unload_models": bool(unload_models), "free_memory": True},
              timeout=config.timeout_s, method="POST")
    out: Dict[str, Any] = {"ok": True, "unloaded_models": bool(unload_models)}
    try:
        stats = http_json(comfy_url(base, "/system_stats"), payload=None, timeout=config.timeout_s, method="GET")
        for device in stats.get("devices") or []:
            if isinstance(device, dict) and isinstance(device.get("vram_free"), (int, float)):
                out.setdefault("vram_free_gb", {})[str(device.get("name"))] = round(device["vram_free"] / 2 ** 30, 1)
    except Exception:
        pass
    return out


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def get_logs(config: McpConfig, lines: int = 60, query: Optional[str] = None,
             server_url: Optional[str] = None) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    try:
        data = http_json(comfy_url(base, "/internal/logs"), payload=None, timeout=config.timeout_s, method="GET")
    except Exception as exc:
        raise RuntimeError(f"This ComfyUI does not serve its log ({type(exc).__name__}: {exc}).") from exc
    text = data.get("raw") if isinstance(data, dict) else data
    rows = [_ANSI.sub("", line) for line in str(text or "").splitlines() if line.strip()]
    if query:
        rows = [line for line in rows if str(query).lower() in line.lower()]
    tail = rows[-max(1, int(lines)):]
    return {"lines": len(tail), "log": "\n".join(tail)}


# ===========================================================================
# Files / outputs
# ===========================================================================


def upload_file(
    config: McpConfig,
    local_path: str,
    accept: Optional[Union[str, List[str]]] = None,
    subfolder: Optional[str] = None,
    overwrite: bool = False,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    result = _upload_file(
        local_path,
        server_url=base,
        subfolder=subfolder,
        overwrite=overwrite,
        accept=accept,
        timeout=config.timeout_s,
    )
    items = [dict(it) for it in result] if isinstance(result, list) else [dict(result)]
    for item in items:
        name = item.get("name") or item.get("filename")
        if name:
            folder = str(item.get("subfolder") or "")
            item["as_input"] = f"{folder}/{name}" if folder else str(name)
    forget_catalog(config, base)         # loader nodes now list the new file
    return {"server_url": base, "uploaded": items,
            "hint": "Use `as_input` as the value of a loader node's file input (LoadImage.image, LoadAudio.audio, ...)."}


def fetch_outputs(
    config: McpConfig,
    prompt_id: str,
    save_to: Optional[str] = None,
    kinds: Optional[Union[str, List[str]]] = None,
    inline_images: bool = True,
    max_inline: Optional[int] = None,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    submission = SubmissionResult.from_prompt_id(prompt_id, server_url=base)
    return _materialize_submission(
        config,
        submission,
        save_to=save_to,
        inline_images=inline_images,
        max_inline=max_inline,
        do_fetch=True,
        output_types=kinds,
    )


def list_outputs(
    config: McpConfig,
    prompt_id: str,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    entry = _history_entry(config, base, str(prompt_id))
    out: Dict[str, Any] = {"prompt_id": prompt_id, "outputs": _output_rows(entry, str(prompt_id))}
    if entry is None:
        out["hint"] = "No such job in the history yet. get_job_status(prompt_id) says whether it is still running."
    return out


# ===========================================================================
# Internals
# ===========================================================================


def _materialize_submission(
    config: McpConfig,
    submission: SubmissionResult,
    *,
    save_to: Optional[str],
    inline_images: bool,
    max_inline: Optional[int],
    do_fetch: bool,
    output_types: Optional[Union[str, List[str]]] = None,
) -> Dict[str, Any]:
    base = submission.server_url
    out: Dict[str, Any] = {
        "prompt_id": submission.prompt_id,
        "server_url": base,
    }
    if not do_fetch:
        return out

    output_path = save_to or config.output_path
    files = submission.fetch_files(
        output_types=output_types,
        timeout=config.timeout_s,
        wait=True,
        output_path=output_path,
        include_bytes=bool(inline_images),
        overwrite=True,
    )

    cap_count = max_inline if isinstance(max_inline, int) else config.max_inline_images
    blocks, summary = build_image_response(
        files,
        prompt_id=submission.prompt_id,
        inline=bool(inline_images),
        max_inline_count=cap_count,
        max_inline_bytes=config.max_inline_image_bytes,
    )

    out["outputs"] = summary
    out["save_path"] = output_path
    out["content_blocks"] = blocks
    return out
