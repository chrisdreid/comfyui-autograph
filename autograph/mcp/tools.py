"""Tool implementations for the autograph MCP server.

Every function here is a thin wrapper over the public ``autograph`` API plus
the small set of MCP-specific modules in this package (sessions, graft, errors,
library). There is no business logic in this module — its job is to translate
MCP-friendly arg shapes into autograph calls and translate autograph results
into JSON-friendly dicts.

Tools that take a workflow accept *either* an inline ``workflow`` (path / JSON
string / dict) *or* a ``workflow_id`` from the live :class:`SessionStore`.
"""

from __future__ import annotations

import json
import time
import urllib.error
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import autograph
from autograph.net import comfy_url, http_json, upload_file as _upload_file
from autograph.results import SubmissionResult

from . import errors as _errors
from . import graft as _graft
from . import library as _library
from .config import McpConfig
from .images import build_image_response
from .session import SessionStore, WorkflowSession, resolve_flow


WorkflowInput = Union[str, Dict[str, Any]]

_NODE_INFO_CACHE_PATH = Path.home() / ".comfyui-autograph" / "node_info_cache.json"


def _fetch_node_info_with_cache(
    config: "McpConfig",
    base: str,
) -> Tuple["autograph.NodeInfo", bool]:
    """Fetch NodeInfo from the live server, falling back to a disk cache.

    Returns ``(node_info, from_cache)`` where ``from_cache`` is True when the
    server was unreachable and the result came from the local snapshot.
    """
    try:
        ni = autograph.NodeInfo.fetch(server_url=base, timeout=config.timeout_s)
        try:
            _NODE_INFO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _NODE_INFO_CACHE_PATH.write_text(
                json.dumps({"fetched_at": time.time(), "data": dict(ni)})
            )
        except Exception:
            pass
        return ni, False
    except Exception as fetch_exc:
        try:
            cached = json.loads(_NODE_INFO_CACHE_PATH.read_text())
            return autograph.NodeInfo(cached.get("data", cached)), True
        except Exception:
            raise fetch_exc


# ---------------------------------------------------------------------------
# Workflow input helpers
# ---------------------------------------------------------------------------


def _flow_from(
    store: SessionStore,
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
    *,
    server_url: Optional[str] = None,
) -> "autograph.Flow":
    if workflow_id:
        return store.get(workflow_id).flow
    if workflow is None:
        raise ValueError("Provide either `workflow_id` or `workflow`.")
    return resolve_flow(store, workflow=workflow, workflow_id=None)


def _node_summary(node: Any) -> Dict[str, Any]:
    raw = node.unwrap() if hasattr(node, "unwrap") else dict(node)
    summary: Dict[str, Any] = {
        "id": getattr(node, "addr", None) or str(raw.get("id", "")),
        "class_type": getattr(node, "type", None) or raw.get("class_type") or raw.get("type", ""),
        "title": getattr(node, "title", "") or "",
    }
    inputs = raw.get("inputs")
    if isinstance(inputs, dict):
        widgets: Dict[str, Any] = {}
        connections: Dict[str, Any] = {}
        for k, v in inputs.items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], (str, int)):
                connections[k] = {"from_node_id": str(v[0]), "from_output": v[1]}
            else:
                widgets[k] = v
        if widgets:
            summary["inputs"] = widgets
        if connections:
            summary["connections"] = connections
    elif isinstance(inputs, list):
        # Workspace-format inputs are slot dicts.
        wired: List[Dict[str, Any]] = []
        free: List[Dict[str, Any]] = []
        for inp in inputs:
            if not isinstance(inp, dict):
                continue
            entry = {"name": inp.get("name"), "type": inp.get("type", "*")}
            if inp.get("link") is not None:
                entry["link"] = inp.get("link")
                wired.append(entry)
            else:
                free.append(entry)
        if wired:
            summary["wired_inputs"] = wired
        if free:
            summary["free_inputs"] = free
        wv = raw.get("widgets_values")
        if isinstance(wv, list):
            summary["widgets_values"] = wv
    return summary


def _brief_node_summary(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Compact node summary showing input/output connectivity state."""
    node_id = str(raw.get("id", ""))
    class_type = raw.get("type") or raw.get("class_type", "")
    title = raw.get("title") or ""

    inputs_raw = raw.get("inputs")
    outputs_raw = raw.get("outputs")

    inputs: List[Dict[str, Any]] = []
    if isinstance(inputs_raw, list):
        for inp in inputs_raw:
            if not isinstance(inp, dict):
                continue
            inputs.append({
                "name": inp.get("name"),
                "type": inp.get("type", "*"),
                "connected": inp.get("link") is not None,
            })
    elif isinstance(inputs_raw, dict):
        # API format: values are either link refs [node_id, slot] or widget values
        for name, val in inputs_raw.items():
            is_link = isinstance(val, list) and len(val) == 2 and isinstance(val[0], (str, int))
            inputs.append({"name": name, "type": "*", "connected": is_link})

    outputs: List[Dict[str, Any]] = []
    if isinstance(outputs_raw, list):
        for out in outputs_raw:
            if not isinstance(out, dict):
                continue
            links = out.get("links") or []
            outputs.append({
                "name": out.get("name"),
                "type": out.get("type", "*"),
                "consumers": len(links),
            })

    summary: Dict[str, Any] = {"id": node_id, "type": class_type, "title": title}
    if inputs:
        summary["inputs"] = inputs
    if outputs:
        summary["outputs"] = outputs
    widgets = raw.get("widgets_values")
    if isinstance(widgets, list):
        summary["widgets_values"] = widgets
    return summary


def _flow_summary(flow: "autograph.Flow") -> Dict[str, Any]:
    raw = flow._flow if hasattr(flow, "_flow") else flow
    nodes_raw = raw.get("nodes", []) if isinstance(raw, dict) else []
    return {
        "node_count": len(nodes_raw),
        "link_count": len(raw.get("links", [])) if isinstance(raw, dict) else 0,
        "last_node_id": raw.get("last_node_id") if isinstance(raw, dict) else None,
        "last_link_id": raw.get("last_link_id") if isinstance(raw, dict) else None,
        "node_info_source": getattr(flow, "source", None),
    }


def _err(e: Any) -> Dict[str, Any]:
    return {
        "category": _enum_value(getattr(e, "category", None)),
        "severity": _enum_value(getattr(e, "severity", None)),
        "message": getattr(e, "message", str(e)),
        "node_id": getattr(e, "node_id", None),
        "details": getattr(e, "details", None),
    }


def _enum_value(v: Any) -> Optional[str]:
    if v is None:
        return None
    return getattr(v, "value", str(v))


# ===========================================================================
# Server / introspection
# ===========================================================================


def comfyui_status(config: McpConfig, server_url: Optional[str] = None) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    out: Dict[str, Any] = {"server_url": base, "reachable": False}
    try:
        stats = http_json(comfy_url(base, "/system_stats"), payload=None, timeout=config.timeout_s, method="GET")
        out["reachable"] = True
        out["system_stats"] = stats
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    try:
        queue = http_json(comfy_url(base, "/queue"), payload=None, timeout=config.timeout_s, method="GET")
        running = queue.get("queue_running") if isinstance(queue, dict) else None
        pending = queue.get("queue_pending") if isinstance(queue, dict) else None
        out["queue"] = {
            "running": len(running) if isinstance(running, list) else 0,
            "pending": len(pending) if isinstance(pending, list) else 0,
        }
    except Exception:
        pass
    return out


def list_node_types(
    config: McpConfig,
    query: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    ni, from_cache = _fetch_node_info_with_cache(config, base)
    matches = ni.find(q=query) if query else None
    out: List[Dict[str, Any]] = []
    if matches is None:
        for k, v in ni.items():
            if not isinstance(k, str) or not isinstance(v, dict):
                continue
            cat = v.get("category")
            if category and isinstance(cat, str) and category.lower() not in cat.lower():
                continue
            out.append(
                {
                    "class_type": k,
                    "display_name": v.get("display_name"),
                    "category": cat,
                    "output_node": bool(v.get("output_node")),
                }
            )
            if len(out) >= max(1, limit):
                break
    else:
        for view in matches:
            addr = getattr(view, "_AUTOGRAPH_addr", None)
            data = ni.get(addr) if addr else None
            if not isinstance(data, dict):
                continue
            cat = data.get("category")
            if category and isinstance(cat, str) and category.lower() not in cat.lower():
                continue
            out.append(
                {
                    "class_type": addr,
                    "display_name": data.get("display_name"),
                    "category": cat,
                    "output_node": bool(data.get("output_node")),
                }
            )
            if len(out) >= max(1, limit):
                break
    return {"server_url": base, "from_cache": from_cache, "count": len(out), "node_types": out}


def describe_node_type(
    config: McpConfig,
    class_type: str,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    ni, from_cache = _fetch_node_info_with_cache(config, base)
    if class_type in ni:
        return {"class_type": class_type, "from_cache": from_cache, "definition": dict(ni[class_type])}
    target = class_type.lower()
    for k, v in ni.items():
        if isinstance(k, str) and k.lower() == target and isinstance(v, dict):
            return {"class_type": k, "from_cache": from_cache, "definition": dict(v)}
    raise KeyError(f"Unknown class_type: {class_type!r}")


def list_models(
    config: McpConfig,
    folder: Optional[str] = None,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    path = "/models" if not folder else f"/models/{folder}"
    data = http_json(comfy_url(base, path), payload=None, timeout=config.timeout_s, method="GET")
    return {"server_url": base, "folder": folder, "data": data}


# ===========================================================================
# Workflow inspection / editing (read-only & widget patches)
# ===========================================================================


def inspect_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
    brief: bool = False,
) -> Dict[str, Any]:
    flow = _flow_from(store, workflow, workflow_id)
    raw_flow = flow._flow if hasattr(flow, "_flow") else (dict(flow) if isinstance(flow, dict) else {})
    raw_nodes = raw_flow.get("nodes", []) if isinstance(raw_flow, dict) else []

    nodes: List[Dict[str, Any]] = []
    if brief:
        for n in raw_nodes:
            if isinstance(n, dict):
                nodes.append(_brief_node_summary(n))
    else:
        try:
            for n in flow.nodes:
                nodes.append(_node_summary(n))
        except Exception:
            for n in raw_nodes:
                if isinstance(n, dict):
                    nodes.append(
                        {
                            "id": str(n.get("id", "")),
                            "class_type": n.get("type") or n.get("class_type", ""),
                            "title": n.get("title") or "",
                            "widgets_values": n.get("widgets_values"),
                        }
                    )
    return {
        "workflow_id": workflow_id,
        "brief": brief,
        **_flow_summary(flow),
        "nodes": nodes,
    }


def convert_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
) -> Dict[str, Any]:
    flow = _flow_from(store, workflow, workflow_id)
    result = flow.convert_with_errors()
    api_data = None
    if getattr(result, "data", None) is not None:
        try:
            api_data = result.data.unwrap() if hasattr(result.data, "unwrap") else dict(result.data)
        except Exception:
            api_data = result.data
    return {
        "workflow_id": workflow_id,
        "ok": bool(getattr(result, "ok", False)),
        "errors": [_err(e) for e in getattr(result, "errors", []) or []],
        "warnings": [_err(w) for w in getattr(result, "warnings", []) or []],
        "processed_nodes": getattr(result, "processed_nodes", 0),
        "skipped_nodes": getattr(result, "skipped_nodes", 0),
        "total_nodes": getattr(result, "total_nodes", 0),
        "api_workflow": api_data,
    }


def validate_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
) -> Dict[str, Any]:
    flow = _flow_from(store, workflow, workflow_id)
    result = flow.convert_with_errors()
    return {
        "workflow_id": workflow_id,
        "ok": bool(getattr(result, "ok", False)),
        "errors": [_err(e) for e in getattr(result, "errors", []) or []],
        "warnings": [_err(w) for w in getattr(result, "warnings", []) or []],
        "processed_nodes": getattr(result, "processed_nodes", 0),
        "total_nodes": getattr(result, "total_nodes", 0),
    }


def set_workflow_values(
    config: McpConfig,
    store: SessionStore,
    updates: List[Dict[str, Any]],
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
) -> Dict[str, Any]:
    flow = _flow_from(store, workflow, workflow_id)
    applied: List[Dict[str, Any]] = []
    if not isinstance(updates, list):
        raise TypeError("updates must be a list of {node_id?, class_type?, title?, inputs} dicts")

    for patch in updates:
        if not isinstance(patch, dict):
            continue
        inputs = patch.get("inputs")
        if not isinstance(inputs, dict) or not inputs:
            continue
        node_id = patch.get("node_id")
        class_type = patch.get("class_type")
        title = patch.get("title")
        for t in _resolve_targets(flow, node_id=node_id, class_type=class_type, title=title):
            for k, v in inputs.items():
                try:
                    setattr(t, k, v)
                    applied.append(
                        {
                            "node_id": getattr(t, "addr", None),
                            "class_type": getattr(t, "type", None),
                            "input": k,
                            "value": v,
                            "ok": True,
                        }
                    )
                except Exception as exc:
                    applied.append(
                        {
                            "node_id": getattr(t, "addr", None),
                            "class_type": getattr(t, "type", None),
                            "input": k,
                            "value": v,
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

    if workflow_id:
        store.touch(workflow_id)
        return {"workflow_id": workflow_id, "applied": applied}
    return {
        "applied": applied,
        "workflow": flow._flow if hasattr(flow, "_flow") else dict(flow),
    }


def _resolve_targets(
    flow: "autograph.Flow",
    *,
    node_id: Optional[Any] = None,
    class_type: Optional[str] = None,
    title: Optional[str] = None,
) -> Iterable[Any]:
    if node_id is not None:
        try:
            return [_by_id(flow, node_id)]
        except Exception:
            return []
    matches = []
    try:
        all_nodes = list(flow.nodes)
    except Exception:
        all_nodes = []
    for n in all_nodes:
        if class_type and (getattr(n, "type", "") or "").lower() != class_type.lower():
            continue
        if title and (getattr(n, "title", "") or "") != title:
            continue
        matches.append(n)
    return matches


def _by_id(flow: "autograph.Flow", node_id: Any) -> Any:
    target = str(node_id)
    for n in flow.nodes:
        if str(getattr(n, "addr", "")) == target:
            return n
    raise KeyError(f"No node with id {node_id!r}")


# ===========================================================================
# Builder API
# ===========================================================================


def load_workflow(
    config: McpConfig,
    store: SessionStore,
    source: Union[str, Dict[str, Any]],
    label: Optional[str] = None,
) -> Dict[str, Any]:
    """Load a workflow into a new session and return its workflow_id."""
    session = store.load_from(source, label=label)
    return {
        "workflow_id": session.id,
        "label": session.label,
        "source_path": session.source_path,
        "checkpoint_path": str(session.checkpoint_path) if session.checkpoint_path else None,
        **_flow_summary(session.flow),
    }


def create_workflow(
    config: McpConfig,
    store: SessionStore,
    starter: Optional[str] = None,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    """Start a new workflow. With ``starter``, seed it from the local library."""
    if starter:
        entry = _library.load_by_name(starter)
        flow = autograph.Flow(entry.path)
        session = store.create(flow=flow, source_path=str(entry.path), label=label or entry.title)
        return {
            "workflow_id": session.id,
            "starter": entry.name,
            "starter_path": str(entry.path),
            "checkpoint_path": str(session.checkpoint_path) if session.checkpoint_path else None,
            **_flow_summary(session.flow),
        }
    session = store.create(label=label)
    return {
        "workflow_id": session.id,
        "label": session.label,
        "checkpoint_path": str(session.checkpoint_path) if session.checkpoint_path else None,
        **_flow_summary(session.flow),
    }


def add_node(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    class_type: str,
    inputs: Optional[Dict[str, Any]] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a new node to the active workflow.

    ``inputs`` are widget overrides (the LLM should call ``describe_node_type`` first
    if it doesn't know what's available). Connection-only inputs are made via
    ``connect_nodes`` afterwards.
    """
    session = store.get(workflow_id)
    flow = session.flow
    overrides = dict(inputs or {})
    import warnings as _warnings
    notes: List[str] = []
    try:
        # add_node warns about input names the node type does not have
        # (typos, connection inputs); hand that back instead of losing it.
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            node_ref = flow.add_node(class_type, **overrides)
        notes = [str(w.message) for w in caught]
    except Exception as exc:
        msg = str(exc)
        if "node_info" in msg.lower() or "nodeinfo" in msg.lower():
            raise type(exc)(
                f"{msg}. The ComfyUI server must be reachable for add_node. "
                f"Alternatively, use merge_workflow with a workspace-format "
                f"fragment ({{nodes:[{{id:1,type:'{class_type}',...}}],links:[],last_node_id:1,last_link_id:0}}) "
                f"which works without a live server."
            ) from exc
        raise
    if title:
        try:
            node_ref._data_ref()["title"] = title
        except Exception:
            pass
    store.touch(workflow_id)
    result = {
        "workflow_id": workflow_id,
        "node_id": getattr(node_ref, "addr", None),
        "class_type": class_type,
        "title": title or "",
        **_flow_summary(flow),
    }
    if notes:
        result["warnings"] = notes
    return result


def connect_nodes(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    from_node: str,
    to_node: str,
    to_input: str,
    from_output: Optional[Union[str, int]] = None,
) -> Dict[str, Any]:
    """Wire ``from_node.<from_output>`` into ``to_node.<to_input>``.

    If ``from_output`` is None, autograph picks the unique matching output by type;
    raises if it's ambiguous.
    """
    session = store.get(workflow_id)
    flow = session.flow
    src = _by_id(flow, from_node)
    dst = _by_id(flow, to_node)
    dst.connect(to_input, src, from_output)
    store.touch(workflow_id)
    return {
        "workflow_id": workflow_id,
        "from": {"node_id": from_node, "from_output": from_output},
        "to": {"node_id": to_node, "to_input": to_input},
        "ok": True,
        **_flow_summary(flow),
    }


def disconnect_input(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    node_id: str,
    input_name: str,
) -> Dict[str, Any]:
    session = store.get(workflow_id)
    flow = session.flow
    node = _by_id(flow, node_id)
    if hasattr(node, "disconnect"):
        node.disconnect(input_name)
    else:
        # Fallback: clear the input slot's link directly.
        data = node._data_ref()
        for inp in data.get("inputs", []) or []:
            if inp.get("name") == input_name:
                inp["link"] = None
                break
    store.touch(workflow_id)
    return {
        "workflow_id": workflow_id,
        "node_id": node_id,
        "input_name": input_name,
        "ok": True,
        **_flow_summary(flow),
    }


def remove_node(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    node_id: str,
) -> Dict[str, Any]:
    session = store.get(workflow_id)
    flow = session.flow
    flow_data = flow._flow
    target_id = int(node_id) if str(node_id).isdigit() else None
    nodes = flow_data.get("nodes", []) or []
    new_nodes = []
    removed = False
    for n in nodes:
        nid = n.get("id") if isinstance(n, dict) else None
        if (target_id is not None and nid == target_id) or str(nid) == str(node_id):
            removed = True
            continue
        new_nodes.append(n)
    flow_data["nodes"] = new_nodes

    # Remove every link touching this node and unlink the corresponding slot refs.
    surviving_links: List[Any] = []
    dropped_link_ids: set = set()
    for ln in flow_data.get("links", []) or []:
        if not isinstance(ln, list) or len(ln) < 6:
            continue
        link_id, src, _src_slot, dst, _dst_slot, _typ = ln[:6]
        if str(src) == str(node_id) or str(dst) == str(node_id) or src == target_id or dst == target_id:
            dropped_link_ids.add(link_id)
            continue
        surviving_links.append(ln)
    flow_data["links"] = surviving_links
    if dropped_link_ids:
        for n in flow_data.get("nodes", []) or []:
            for inp in n.get("inputs", []) or []:
                if inp.get("link") in dropped_link_ids:
                    inp["link"] = None
            for outp in n.get("outputs", []) or []:
                outp["links"] = [li for li in (outp.get("links") or []) if li not in dropped_link_ids]

    store.touch(workflow_id)
    return {
        "workflow_id": workflow_id,
        "node_id": node_id,
        "removed": removed,
        "links_dropped": sorted(dropped_link_ids),
        **_flow_summary(flow),
    }


def merge_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    fragment: Union[str, Dict[str, Any]],
    auto_connect: bool = True,
) -> Dict[str, Any]:
    """Merge a workflow fragment into the active workflow with auto-stitching."""
    session = store.get(workflow_id)
    report = _graft.merge_into_flow(session.flow, fragment, auto_connect=auto_connect)
    store.touch(workflow_id)
    return {
        "workflow_id": workflow_id,
        **report,
        **_flow_summary(session.flow),
    }


def save_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    target = store.save(workflow_id, path=path)
    return {"workflow_id": workflow_id, "saved_to": str(target)}


def get_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow_id: str,
    format: str = "workspace",
) -> Dict[str, Any]:
    session = store.get(workflow_id)
    if format == "api":
        api = session.flow.convert()
        data = api._api if hasattr(api, "_api") else dict(api)
        return {"workflow_id": workflow_id, "format": "api", "workflow": data}
    raw = session.flow._flow if hasattr(session.flow, "_flow") else dict(session.flow)
    return {"workflow_id": workflow_id, "format": "workspace", "workflow": raw}


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
    session = store.load_from(entry.path, label=label or entry.title)
    return {
        "workflow_id": session.id,
        "library_entry": entry.to_dict(),
        "checkpoint_path": str(session.checkpoint_path) if session.checkpoint_path else None,
        **_flow_summary(session.flow),
    }


# ===========================================================================
# Execution
# ===========================================================================


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
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    flow = _flow_from(store, workflow, workflow_id)
    try:
        submission = flow.submit(
            server_url=base,
            wait=wait,
            fetch_outputs=False,
            timeout=config.timeout_s,
        )
    except Exception as exc:
        return {
            "ok": False,
            "workflow_id": workflow_id,
            "server_url": base,
            "errors": _errors.parse_prompt_error(exc),
        }

    try:
        payload = _materialize_submission(
            config,
            submission,
            save_to=save_to,
            inline_images=inline_images,
            max_inline=max_inline,
            do_fetch=fetch_outputs,
        )
    except Exception as fetch_exc:
        payload = {
            "prompt_id": submission.prompt_id,
            "server_url": base,
            "fetch_error": str(fetch_exc),
            "message": (
                f"Job completed but output fetch failed. "
                f"Retry with fetch_outputs(prompt_id='{submission.prompt_id}')."
            ),
        }

    payload["workflow_id"] = workflow_id
    payload["ok"] = True

    # Surface execution errors that came back through history.
    history = submission.get("history") if isinstance(submission, dict) else None
    if isinstance(history, dict):
        prompt_id = submission.prompt_id
        entry = history.get(prompt_id) if prompt_id else None
        exec_errors = _errors.parse_history_errors(entry)
        if exec_errors:
            payload["ok"] = False
            payload["errors"] = exec_errors
    return payload


def queue_workflow(
    config: McpConfig,
    store: SessionStore,
    workflow: Optional[WorkflowInput] = None,
    workflow_id: Optional[str] = None,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    flow = _flow_from(store, workflow, workflow_id)
    try:
        submission = flow.submit(server_url=base, wait=False, fetch_outputs=False, timeout=config.timeout_s)
    except Exception as exc:
        return {
            "ok": False,
            "workflow_id": workflow_id,
            "server_url": base,
            "errors": _errors.parse_prompt_error(exc),
        }
    return {
        "ok": True,
        "workflow_id": workflow_id,
        "prompt_id": submission.prompt_id,
        "server_url": submission.server_url,
    }


def get_history(
    config: McpConfig,
    prompt_id: Optional[str] = None,
    limit: int = 10,
    server_url: Optional[str] = None,
) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    if prompt_id:
        url = comfy_url(base, f"/history/{prompt_id}")
        return {"server_url": base, "prompt_id": prompt_id, "history": http_json(url, payload=None, timeout=config.timeout_s, method="GET")}
    url = comfy_url(base, f"/history?max_items={max(1, int(limit))}")
    return {"server_url": base, "limit": limit, "history": http_json(url, payload=None, timeout=config.timeout_s, method="GET")}


def interrupt(config: McpConfig, server_url: Optional[str] = None) -> Dict[str, Any]:
    base = config.resolve_server_url(server_url)
    try:
        http_json(comfy_url(base, "/interrupt"), payload={}, timeout=config.timeout_s, method="POST")
        return {"server_url": base, "ok": True}
    except urllib.error.HTTPError as exc:
        return {"server_url": base, "ok": False, "error": f"HTTP {exc.code}: {exc.reason}"}


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
    if isinstance(result, list):
        items = [dict(it) for it in result]
        return {"server_url": base, "uploaded": items}
    return {"server_url": base, "uploaded": [dict(result)]}


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
    url = comfy_url(base, f"/history/{prompt_id}")
    history = http_json(url, payload=None, timeout=config.timeout_s, method="GET")
    entry = history.get(prompt_id) if isinstance(history, dict) else None
    outputs: List[Dict[str, Any]] = []
    if isinstance(entry, dict):
        node_outputs = entry.get("outputs")
        if isinstance(node_outputs, dict):
            for node_id, node_out in node_outputs.items():
                if not isinstance(node_out, dict):
                    continue
                for kind, items in node_out.items():
                    if not isinstance(items, list):
                        continue
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        outputs.append(
                            {
                                "node_id": str(node_id),
                                "kind": kind,
                                "filename": it.get("filename"),
                                "subfolder": it.get("subfolder", ""),
                                "type": it.get("type", ""),
                                "resource_uri": (
                                    f"comfyui://outputs/{prompt_id}/{it.get('filename')}"
                                    if it.get("filename")
                                    else None
                                ),
                            }
                        )
    return {"server_url": base, "prompt_id": prompt_id, "outputs": outputs}


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
