"""autoflow.models

Legacy-parity model layer (dict/list subclasses + drilling helpers).

This module intentionally mirrors the behavior of the reference implementation in
`autoflow/api-legacy.py`, while delegating non-model concerns to the split modules:
- defaults: env/default constants
- pngmeta: png/json/path heuristics
- net: explicit server URL resolution
- convert: conversion core + schema helpers
- results: submit + output helpers
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .defaults import (
    DEFAULT_FETCH_IMAGES,
    DEFAULT_FIND_DEEP,
    DEFAULT_FIND_MAX_DEPTH,
    DEFAULT_HTTP_TIMEOUT_S,
    DEFAULT_INCLUDE_META,
    DEFAULT_JSON_ENSURE_ASCII,
    DEFAULT_JSON_INDENT,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_SUBGRAPH_MAX_DEPTH,
    DEFAULT_SUBMIT_CLIENT_ID,
    DEFAULT_SUBMIT_WAIT,
    DEFAULT_USE_API,
    DEFAULT_WAIT_TIMEOUT_S,
)
from .pngmeta import (
    extract_png_comfyui_metadata,
    is_png_bytes,
    is_png_path,
    looks_like_json,
    looks_like_path,
    parse_png_metadata_from_bytes,
)

# Conversion/schema helpers (stdlib-first; direct-mode imports are explicit and opt-in).
from .convert import (  # noqa: F401
    ConvertResult,
    ConversionError,
    ConversionResult,
    ErrorCategory,
    ErrorSeverity,
    NodeInfoError,
    WorkflowConverterError,
    align_widgets_values,
    convert,
    convert_workflow_with_errors,
    fetch_node_info,
    fetch_node_info_from_url,
    get_widget_input_names,
    load_node_info_from_file,
    node_info_from_comfyui_modules,
    resolve_node_info,
)


# ---------------------------------------------------------------------------
# Find/drilling helper functions (legacy-parity)
# ---------------------------------------------------------------------------


def _collect_key_hits(obj: Any, keys: set, *, depth: int) -> Dict[str, List[Any]]:
    """
    Traverse dicts + lists up to `depth` levels and collect values for matching keys.

    - depth=0: only the root object is examined (no recursion)
    - depth=1: recurse into one level of children, etc.
    """
    want = {str(k) for k in keys}
    hits: Dict[str, List[Any]] = {k: [] for k in want}
    stack: List[Tuple[Any, int]] = [(obj, 0)]
    seen: set = set()
    max_depth = max(0, int(depth))

    while stack:
        cur, lvl = stack.pop()
        try:
            cid = id(cur)
            if cid in seen:
                continue
            seen.add(cid)
        except Exception:
            pass

        if isinstance(cur, dict):
            for k, v in cur.items():
                ks = str(k)
                if ks in hits:
                    hits[ks].append(v)
                if lvl < max_depth and isinstance(v, (dict, list)):
                    stack.append((v, lvl + 1))
        elif isinstance(cur, list):
            if lvl < max_depth:
                for v in cur:
                    if isinstance(v, (dict, list)):
                        stack.append((v, lvl + 1))

    return hits


def _flow_widget_map(node: Dict[str, Any], flow: Any, *, _cache: Dict[str, List[str]]) -> Dict[str, Any]:
    """
    Best-effort: resolve Flow node widgets_values into a {widget_name: value} dict using flow.node_info.
    """
    node_info = getattr(flow, "node_info", None)
    if not isinstance(node_info, dict):
        return {}
    class_type = node.get("type")
    if not isinstance(class_type, str) or not class_type:
        return {}
    widget_names = _cache.get(class_type)
    if widget_names is None:
        widget_names = get_widget_input_names(class_type, node_info=node_info, use_api=True)
        _cache[class_type] = widget_names
    wv0 = node.get("widgets_values", []) or []
    wv = align_widgets_values(class_type, list(wv0), widget_names, node_info=node_info)
    out: Dict[str, Any] = {}
    for i in range(min(len(widget_names), len(wv))):
        out[widget_names[i]] = wv[i]
    return out


def _is_regex(x: Any) -> bool:
    # Duck-type re.Pattern across Python versions
    return hasattr(x, "search") and hasattr(x, "pattern")


def _match_expected(expected: Any, candidate: Any) -> bool:
    """
    Match a candidate value against an expected filter.

    - expected == "*": existence-only (handled by caller)
    - expected is re.Pattern: regex search against str(candidate)
    - else: equality
    """
    if _is_regex(expected):
        try:
            return expected.search(str(candidate)) is not None  # type: ignore[union-attr]
        except Exception:
            return False
    return candidate == expected


def _match_str_filter(expected: Any, candidate: Any, *, case_insensitive: bool = True) -> bool:
    """
    Match a string-like candidate against an expected filter:
      - expected is None: always match
      - expected is str: equality (case-insensitive by default)
      - expected is re.Pattern: regex search against candidate (stringified)
    """
    if expected is None:
        return True
    if not isinstance(candidate, str):
        return False
    if _is_regex(expected):
        try:
            return expected.search(candidate) is not None  # type: ignore[union-attr]
        except Exception:
            return False
    if isinstance(expected, str):
        if case_insensitive:
            return candidate.lower() == expected.lower()
        return candidate == expected
    return False


# ---------------------------------------------------------------------------
# Workspace subgraph traversal (legacy-parity)
# ---------------------------------------------------------------------------


from .convert import _get_subgraph_defs  # single source of truth


def _iter_flow_nodes_with_paths(
    workflow_data: Dict[str, Any],
    *,
    deep: bool = DEFAULT_FIND_DEEP,
    max_depth: int = DEFAULT_FIND_MAX_DEPTH,
) -> Iterable[Tuple[Dict[str, Any], str]]:
    """
    Iterate workspace nodes, optionally recursing through subgraph instances.

    Yields:
        (node_dict, comfy_path)
    """
    nodes = workflow_data.get("nodes")
    if not isinstance(nodes, list):
        return []

    sub_defs = _get_subgraph_defs(workflow_data)

    def _walk(nodes_list: List[Any], chain_ids: List[str], sg_stack: set, depth: int):
        for n in nodes_list:
            if not isinstance(n, dict):
                continue
            nid = n.get("id")
            if nid is None:
                continue
            nid_s = str(nid)
            path = ":".join(chain_ids + [nid_s]) if chain_ids else nid_s
            yield n, path

            if not deep:
                continue
            if depth >= max_depth:
                continue
            ntype = n.get("type")
            if not isinstance(ntype, str):
                continue
            if ntype not in sub_defs:
                continue
            if ntype in sg_stack:
                continue
            sg = sub_defs[ntype]
            sg_nodes = sg.get("nodes")
            if not isinstance(sg_nodes, list):
                continue
            yield from _walk(sg_nodes, chain_ids + [nid_s], sg_stack | {ntype}, depth + 1)

    return list(_walk(nodes, [], set(), 0))


# ---------------------------------------------------------------------------
# Dict/list views + proxies (legacy-parity)
# ---------------------------------------------------------------------------


class _DictMixin:
    """
    Mixin providing dict-like interface for classes with _get_data() method.
    Subclasses must implement _get_data() -> dict.
    """

    __slots__ = ("_autoflow_addr",)

    def path(self) -> str:
        addr = getattr(self, "_autoflow_addr", None)
        if isinstance(addr, str) and addr:
            return addr
        try:
            v = getattr(self, "id")
            if v is not None:
                return str(v)
        except Exception:
            pass
        return ""

    def address(self) -> str:
        return self.path()

    def _get_data(self) -> Dict[str, Any]:
        raise NotImplementedError

    def keys(self):
        return self._get_data().keys()

    def values(self):
        return self._get_data().values()

    def items(self):
        return self._get_data().items()

    def __iter__(self):
        return iter(self._get_data())

    def __len__(self):
        return len(self._get_data())

    def __contains__(self, key):
        return key in self._get_data()

    def get(self, key, default=None):
        return self._get_data().get(key, default)

    def __getitem__(self, key):
        return self._get_data()[key]

    def to_dict(self) -> Dict[str, Any]:
        return self._get_data()


class DictView(_DictMixin):
    """Dict proxy with attribute drilling; modifications propagate to the original dict."""

    __slots__ = ("_data",)

    def __init__(self, data: Dict[str, Any]):
        object.__setattr__(self, "_data", data)

    def _get_data(self) -> Dict[str, Any]:
        return object.__getattribute__(self, "_data")

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        data = self._get_data()
        try:
            val = data[name]
            if isinstance(val, dict) and not isinstance(val, DictView):
                return DictView(val)
            if isinstance(val, list) and not isinstance(val, ListView):
                return ListView(val)
            return val
        except KeyError:
            raise AttributeError(f"No key '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self._get_data()[name] = value

    def __getitem__(self, key):
        val = self._get_data()[key]
        if isinstance(val, dict) and not isinstance(val, DictView):
            return DictView(val)
        if isinstance(val, list) and not isinstance(val, ListView):
            return ListView(val)
        return val

    def __setitem__(self, key, value):
        self._get_data()[key] = value

    def __delitem__(self, key):
        del self._get_data()[key]

    def __repr__(self) -> str:
        return repr(self._get_data())

    def __str__(self) -> str:
        return str(self._get_data())

    def get(self, key, default=None):
        val = self._get_data().get(key, default)
        if isinstance(val, dict) and not isinstance(val, DictView):
            return DictView(val)
        if isinstance(val, list) and not isinstance(val, ListView):
            return ListView(val)
        return val

    def update(self, *args, **kwargs):
        self._get_data().update(*args, **kwargs)

    def pop(self, key, *args):
        return self._get_data().pop(key, *args)

    def setdefault(self, key, default=None):
        return self._get_data().setdefault(key, default)

    def copy(self) -> Dict[str, Any]:
        return dict(self._get_data())


class ListView:
    """List proxy with 1-item list-of-dicts attribute drilling; modifications propagate."""

    __slots__ = ("_data", "_autoflow_addr")

    def __init__(self, data: List[Any]):
        object.__setattr__(self, "_data", data)

    def _get_data(self) -> List[Any]:
        return object.__getattribute__(self, "_data")

    def path(self) -> str:
        addr = getattr(self, "_autoflow_addr", None)
        return addr if isinstance(addr, str) else ""

    def address(self) -> str:
        return self.path()

    def __len__(self):
        return len(self._get_data())

    def __iter__(self):
        return iter(self._get_data())

    def __getitem__(self, key):
        val = self._get_data()[key]
        if isinstance(val, dict) and not isinstance(val, DictView):
            return DictView(val)
        if isinstance(val, list) and not isinstance(val, ListView):
            return ListView(val)
        return val

    def __setitem__(self, key, value) -> None:
        self._get_data()[key] = value

    def append(self, value: Any) -> None:
        self._get_data().append(value)

    def extend(self, it) -> None:
        self._get_data().extend(it)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        data = self._get_data()
        if len(data) != 1:
            raise AttributeError(f"List has {len(data)} items; use indexing first (e.g. x[0].{name})")
        first = data[0]
        if isinstance(first, dict):
            return getattr(DictView(first), name)
        raise AttributeError(f"List item is {type(first).__name__}; cannot access attribute '{name}'")

    def __repr__(self) -> str:
        return repr(self._get_data())

    def __str__(self) -> str:
        return str(self._get_data())


class NodeProxy(_DictMixin):
    """Wrap a single API node for attribute-style input access."""

    __slots__ = ("_node", "_node_id", "_parent")

    def __init__(self, node: Dict[str, Any], node_id: str, parent: Dict[str, Any]):
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_node_id", node_id)
        object.__setattr__(self, "_parent", parent)

    def _get_data(self) -> Dict[str, Any]:
        return object.__getattribute__(self, "_node")

    @property
    def id(self) -> str:
        return object.__getattribute__(self, "_node_id")

    @property
    def class_type(self) -> str:
        return self._get_data().get("class_type", "")

    @property
    def inputs(self) -> DictView:
        return DictView(self._get_data().get("inputs", {}))

    @property
    def node(self) -> Dict[str, Any]:
        return self._get_data()

    @property
    def meta(self) -> DictView:
        m = self._get_data().get("_meta", {})
        return DictView(m if isinstance(m, dict) else {})

    @property
    def _meta(self) -> DictView:
        return self.meta

    def __getattr__(self, name: str) -> Any:
        node = self._get_data()
        inputs = node.get("inputs", {})
        if isinstance(inputs, dict) and name in inputs:
            val = inputs[name]
            # Wrap in WidgetValue when node_info is available
            parent = object.__getattribute__(self, "_parent")
            ni = getattr(parent, "node_info", None)
            if ni is not None:
                ct = node.get("class_type", "")
                spec = _get_input_spec(ct, name, ni)
                if spec is not None:
                    return WidgetValue(val, spec)
            return val
        if name in node:
            return node[name]
        raise AttributeError(f"Node {self.id!r} ({self.class_type}) has no input '{name}'")

    def attrs(self) -> List[str]:
        node = self._get_data()
        keys: set = set()
        if isinstance(node, dict):
            keys |= {str(k) for k in node.keys()}
            inputs = node.get("inputs")
            if isinstance(inputs, dict):
                keys |= {str(k) for k in inputs.keys()}
        return sorted(keys)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_node", "_node_id", "_parent"):
            object.__setattr__(self, name, value)
        elif name == "_meta":
            self._get_data()["_meta"] = value
        else:
            self._get_data().setdefault("inputs", {})[name] = value

    def __repr__(self) -> str:
        return f"<NodeProxy id={self.id!r} class_type={self.class_type!r}>"


class NodeGroup:
    """Group of API nodes of the same class_type."""

    __slots__ = ("_nodes", "_parent")

    def __init__(self, nodes: List[Tuple[str, Dict[str, Any]]], parent: Dict[str, Any]):
        object.__setattr__(self, "_nodes", nodes)
        object.__setattr__(self, "_parent", parent)

    def __getitem__(self, key) -> NodeProxy:
        nodes = object.__getattribute__(self, "_nodes")
        parent = object.__getattribute__(self, "_parent")
        if isinstance(key, int):
            if key < 0 or key >= len(nodes):
                raise IndexError(f"Node index {key} out of range (have {len(nodes)})")
            node_id, node = nodes[key]
            return NodeProxy(node, node_id, parent)
        if isinstance(key, str):
            for node_id, node in nodes:
                if node_id == key:
                    return node  # raw dict for dict() compatibility
            raise KeyError(key)
        raise TypeError(f"Node index must be int or str, not {type(key).__name__}")

    def __len__(self) -> int:
        return len(object.__getattribute__(self, "_nodes"))

    def __iter__(self):
        nodes = object.__getattribute__(self, "_nodes")
        parent = object.__getattribute__(self, "_parent")
        for node_id, node in nodes:
            yield NodeProxy(node, node_id, parent)

    def __getattr__(self, name: str) -> Any:
        nodes = object.__getattribute__(self, "_nodes")
        parent = object.__getattribute__(self, "_parent")
        if not nodes:
            raise AttributeError("No nodes in group")
        node_id, node = nodes[0]
        return getattr(NodeProxy(node, node_id, parent), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_nodes", "_parent"):
            object.__setattr__(self, name, value)
        else:
            nodes = object.__getattribute__(self, "_nodes")
            parent = object.__getattribute__(self, "_parent")
            if not nodes:
                raise AttributeError("No nodes in group")
            node_id, node = nodes[0]
            NodeProxy(node, node_id, parent).__setattr__(name, value)

    def __repr__(self) -> str:
        nodes = object.__getattribute__(self, "_nodes")
        if nodes:
            class_type = nodes[0][1].get("class_type", "?")
            return f"<NodeGroup class_type={class_type!r} count={len(nodes)}>"
        return "<NodeGroup empty>"

    def attrs(self) -> List[str]:
        nodes = object.__getattribute__(self, "_nodes")
        parent = object.__getattribute__(self, "_parent")
        if not nodes:
            return []
        node_id, node = nodes[0]
        return NodeProxy(node, node_id, parent).attrs()

    def keys(self):
        nodes = object.__getattribute__(self, "_nodes")
        return (node_id for node_id, _ in nodes)

    def values(self):
        nodes = object.__getattribute__(self, "_nodes")
        return (node for _, node in nodes)

    def items(self):
        nodes = object.__getattribute__(self, "_nodes")
        return ((node_id, node) for node_id, node in nodes)

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        nodes = object.__getattribute__(self, "_nodes")
        return {node_id: node for node_id, node in nodes}

    def to_list(self) -> List[Dict[str, Any]]:
        nodes = object.__getattribute__(self, "_nodes")
        return [node for _, node in nodes]


# ---------------------------------------------------------------------------
# WidgetValue — transparent wrapper with .choices() / .tooltip()
# ---------------------------------------------------------------------------


def _get_input_spec(class_type: str, input_name: str, node_info: Optional[Dict[str, Any]]) -> Optional[list]:
    """Return the raw node_info spec (list) for a given input_name, or None."""
    if not isinstance(node_info, dict):
        return None
    ni = node_info.get(class_type)
    if not isinstance(ni, dict):
        return None
    inputs_def = ni.get("input", {})
    if not isinstance(inputs_def, dict):
        return None
    for section in ("required", "optional"):
        section_inputs = inputs_def.get(section, {})
        if isinstance(section_inputs, dict) and input_name in section_inputs:
            spec = section_inputs[input_name]
            return spec if isinstance(spec, list) else None
    return None


# ---------------------------------------------------------------------------
# Spec-format extractors (extend these lists to support new node-info formats)
# ---------------------------------------------------------------------------

def _choices_from_combo(spec: list) -> Optional[List[str]]:
    """New format: ["COMBO", {"options": [...]}]"""
    if (len(spec) >= 2
            and spec[0] == "COMBO"
            and isinstance(spec[1], dict)
            and "options" in spec[1]):
        return list(spec[1]["options"])
    return None


def _choices_from_legacy_list(spec: list) -> Optional[List[str]]:
    """Legacy format: [["choice1", "choice2", ...], {...}]"""
    if isinstance(spec[0], list):
        return list(spec[0])
    return None


# Add new choice extractors here as ComfyUI introduces new formats.
_CHOICES_EXTRACTORS: List[Callable[[list], Optional[List[str]]]] = [
    _choices_from_combo,
    _choices_from_legacy_list,
]


def _tooltip_from_spec_dict(spec: list) -> Optional[str]:
    """Both formats store tooltip in spec[1] dict: ["TYPE", {"tooltip": "..."}]"""
    if len(spec) >= 2 and isinstance(spec[1], dict):
        tt = spec[1].get("tooltip")
        return tt if isinstance(tt, str) else None
    return None


# Add new tooltip extractors here as ComfyUI introduces new formats.
_TOOLTIP_EXTRACTORS: List[Callable[[list], Optional[str]]] = [
    _tooltip_from_spec_dict,
]


class WidgetValue:
    """Transparent wrapper around a widget value, adding `.choices()` and `.tooltip()`.

    Comparison, hashing, and string conversion delegate to the underlying value,
    so ``node.seed == 200`` still works as expected.
    """

    __slots__ = ("_value", "_spec", "_node_ref", "_attr_name")

    def __init__(self, value: Any, spec: Optional[list] = None):
        object.__setattr__(self, "_value", value)
        object.__setattr__(self, "_spec", spec)
        object.__setattr__(self, "_node_ref", None)
        object.__setattr__(self, "_attr_name", None)

    # --- transparent delegation ---
    def __repr__(self) -> str:
        return repr(self._value)

    def __str__(self) -> str:
        return str(self._value)

    def __eq__(self, other: Any) -> bool:
        o = other._value if isinstance(other, WidgetValue) else other
        return self._value == o

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return hash(self._value)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __int__(self) -> int:
        return int(self._value)

    def __float__(self) -> float:
        return float(self._value)

    def __add__(self, other: Any) -> Any:
        o = other._value if isinstance(other, WidgetValue) else other
        return self._value + o

    def __radd__(self, other: Any) -> Any:
        o = other._value if isinstance(other, WidgetValue) else other
        return o + self._value

    def __mul__(self, other: Any) -> Any:
        o = other._value if isinstance(other, WidgetValue) else other
        return self._value * o

    def __rmul__(self, other: Any) -> Any:
        return self.__mul__(other)

    def __sub__(self, other: Any) -> Any:
        o = other._value if isinstance(other, WidgetValue) else other
        return self._value - o

    def __rsub__(self, other: Any) -> Any:
        o = other._value if isinstance(other, WidgetValue) else other
        return o - self._value

    def __truediv__(self, other: Any) -> Any:
        o = other._value if isinstance(other, WidgetValue) else other
        return self._value / o

    def __lt__(self, other: Any) -> bool:
        o = other._value if isinstance(other, WidgetValue) else other
        return self._value < o

    def __le__(self, other: Any) -> bool:
        o = other._value if isinstance(other, WidgetValue) else other
        return self._value <= o

    def __gt__(self, other: Any) -> bool:
        o = other._value if isinstance(other, WidgetValue) else other
        return self._value > o

    def __ge__(self, other: Any) -> bool:
        o = other._value if isinstance(other, WidgetValue) else other
        return self._value >= o

    # --- value access ---
    @property
    def value(self) -> Any:
        """The raw underlying value."""
        return self._value

    # --- introspection ---
    def choices(self) -> Optional[List[str]]:
        """Return the list of valid choices if this is a combo widget, else None."""
        spec = self._spec
        if not isinstance(spec, list) or len(spec) == 0:
            return None
        for extractor in _CHOICES_EXTRACTORS:
            result = extractor(spec)
            if result is not None:
                return result
        return None

    def tooltip(self) -> Optional[str]:
        """Return the tooltip string for this widget, if one exists."""
        spec = self._spec
        if not isinstance(spec, list) or len(spec) < 2:
            return None
        for extractor in _TOOLTIP_EXTRACTORS:
            result = extractor(spec)
            if result is not None:
                return result
        return None

    def spec(self) -> Optional[list]:
        """Return the raw node_info spec for this input."""
        return self._spec

    def to_input(self) -> None:
        """Promote this attr to a connectable input slot."""
        nr = self._node_ref
        name = self._attr_name
        if nr is None or name is None:
            raise RuntimeError("WidgetValue has no node reference — cannot promote.")
        nr.to_input(name)

    def to_attr(self) -> None:
        """Demote the corresponding input slot back to attr-only."""
        nr = self._node_ref
        name = self._attr_name
        if nr is None or name is None:
            raise RuntimeError("WidgetValue has no node reference — cannot demote.")
        nr.to_attr(name)

    def __dir__(self) -> List[str]:
        return ["value", "choices", "tooltip", "spec", "to_input", "to_attr"]


class FlowNodeProxy(_DictMixin):
    """Wrap a single workspace node for attribute-style access (schema-aware widgets)."""

    __slots__ = ("_node", "_index", "_parent")

    def __init__(self, node: Dict[str, Any], index: int, parent: "Flow"):
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_index", index)
        object.__setattr__(self, "_parent", parent)

    def _get_data(self) -> Dict[str, Any]:
        return object.__getattribute__(self, "_node")

    @property
    def id(self) -> int:
        return self._get_data().get("id")

    @property
    def type(self) -> str:
        return self._get_data().get("type", "")

    @property
    def widgets_values(self) -> List[Any]:
        return self._get_data().get("widgets_values", [])

    @property
    def node(self) -> Dict[str, Any]:
        return self._get_data()

    @property
    def bypass(self) -> bool:
        """True when the node is bypassed (LiteGraph mode 4)."""
        return self._get_data().get("mode", 0) == 4

    @bypass.setter
    def bypass(self, value: bool) -> None:
        """Set bypass state: True → mode 4 (bypassed), False → mode 0 (normal)."""
        self._get_data()["mode"] = 4 if value else 0

    def __getattr__(self, name: str) -> Any:
        node = self._get_data()
        parent = object.__getattribute__(self, "_parent")
        node_info = getattr(parent, "node_info", None)

        # When node_info is available, widget names take priority over raw
        # node-dict keys to avoid collisions (e.g. LiteGraph's "mode" vs
        # a PorterDuffImageComposite "mode" widget).
        if node_info is not None:
            try:
                widget_names = get_widget_input_names(self.type, node_info=node_info, use_api=True)
            except NodeInfoError:
                widget_names = []

            if name in widget_names:
                wv = align_widgets_values(self.type, list(self.widgets_values or []), widget_names, node_info=node_info)
                widget_map = {k: wv[i] for i, k in enumerate(widget_names) if i < len(wv)}
                if name in widget_map:
                    val = widget_map[name]
                    if isinstance(val, dict) and not isinstance(val, DictView):
                        return DictView(val)
                    if isinstance(val, list) and not isinstance(val, ListView):
                        return ListView(val)
                    spec = _get_input_spec(self.type, name, node_info)
                    return WidgetValue(val, spec)

        # Fall back to raw node-dict keys (id, type, pos, size, flags, etc.)
        if name in node:
            val = node[name]
            if isinstance(val, dict) and not isinstance(val, DictView):
                return DictView(val)
            if isinstance(val, list) and not isinstance(val, ListView):
                return ListView(val)
            return val

        # No node_info at all — raise a helpful error
        if node_info is None:
            raise NodeInfoError(
                f"Cannot drill widget '{name}' on Flow node {self.id} ({self.type}). "
                f"This Flow has no node_info. Attach one via Flow(..., node_info=...), "
                f"or call flow.fetch_node_info(...)."
            )

        raise AttributeError(
            f"Node {self.id} ({self.type}) has no attribute or widget '{name}'. "
            f"Available widgets: {', '.join(widget_names) if widget_names else '(none)'}"
        )

    def __dir__(self) -> List[str]:
        base = set(super().__dir__())
        try:
            node = self._get_data()
            if isinstance(node, dict):
                base.update(str(k) for k in node.keys())
        except Exception:
            pass
        try:
            parent = object.__getattribute__(self, "_parent")
            node_info = getattr(parent, "node_info", None)
            if node_info is not None:
                widget_names = get_widget_input_names(self.type, node_info=node_info, use_api=True)
                base.update(widget_names)
        except Exception:
            pass
        return sorted(base)

    def attrs(self) -> List[str]:
        node = self._get_data()
        keys: set = set()
        if isinstance(node, dict):
            keys |= {str(k) for k in node.keys()}
        try:
            parent = object.__getattribute__(self, "_parent")
            node_info = getattr(parent, "node_info", None)
            if isinstance(node_info, dict):
                keys |= set(get_widget_input_names(self.type, node_info=node_info, use_api=True))
        except Exception:
            pass
        return sorted(keys)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_node", "_index", "_parent"):
            object.__setattr__(self, name, value)
            return

        # Route property descriptors (e.g. bypass) through the normal
        # descriptor protocol so the @bypass.setter fires.
        prop = getattr(type(self), name, None)
        if isinstance(prop, property) and prop.fset is not None:
            prop.fset(self, value)
            return

        node = self._get_data()
        parent = object.__getattribute__(self, "_parent")
        node_info = getattr(parent, "node_info", None)
        if isinstance(node_info, dict):
            try:
                widget_names = get_widget_input_names(self.type, node_info=node_info, use_api=True)
            except Exception:
                widget_names = []
            if widget_names and name in widget_names:
                wv0 = node.get("widgets_values")
                wv0_list = wv0 if isinstance(wv0, list) else []
                # Use alignment to find the correct position of this widget
                # in the original array, then update in-place to preserve
                # values unknown to node_info (e.g. control_after_generate).
                aligned = align_widgets_values(self.type, list(wv0_list), widget_names, node_info=node_info)
                target_idx = widget_names.index(name)
                old_val = aligned[target_idx] if target_idx < len(aligned) else None
                # Find the position of old_val in the original array
                # by tracing the alignment mapping
                updated = False
                if wv0_list:
                    # Build a forward map: for each widget_name[i], which
                    # original index does it correspond to?
                    remaining = list(range(len(wv0_list)))
                    idx_map: dict = {}
                    for wi, wn in enumerate(widget_names):
                        wval = aligned[wi] if wi < len(aligned) else None
                        for ri, oi in enumerate(remaining):
                            if oi < len(wv0_list) and wv0_list[oi] == wval:
                                idx_map[wi] = oi
                                remaining.pop(ri)
                                break
                    if target_idx in idx_map:
                        wv0_list[idx_map[target_idx]] = value
                        node["widgets_values"] = wv0_list
                        updated = True
                if not updated:
                    # Fallback: replace with aligned (for newly created nodes
                    # where widgets_values may be empty or mismatched)
                    aligned[target_idx] = value
                    node["widgets_values"] = aligned
                return

        node[name] = value

    def __repr__(self) -> str:
        return f"<FlowNodeProxy id={self.id} type={self.type!r}>"


class FlowNodeGroup:
    """Group of workspace nodes of the same type."""

    __slots__ = ("_nodes", "_parent")

    def __init__(self, nodes: List[Tuple[int, Dict[str, Any]]], parent: "Flow"):
        object.__setattr__(self, "_nodes", nodes)
        object.__setattr__(self, "_parent", parent)

    def __getitem__(self, key):
        nodes = object.__getattribute__(self, "_nodes")
        parent = object.__getattribute__(self, "_parent")
        if isinstance(key, int):
            if 0 <= key < len(nodes):
                list_idx, node = nodes[key]
                return FlowNodeProxy(node, list_idx, parent)
            for list_idx, node in nodes:
                if node.get("id") == key:
                    return node
            raise KeyError(key)
        raise TypeError(f"Node index must be int, not {type(key).__name__}")

    def __len__(self) -> int:
        return len(object.__getattribute__(self, "_nodes"))

    def __iter__(self):
        nodes = object.__getattribute__(self, "_nodes")
        parent = object.__getattribute__(self, "_parent")
        for list_idx, node in nodes:
            yield FlowNodeProxy(node, list_idx, parent)

    def __getattr__(self, name: str) -> Any:
        nodes = object.__getattribute__(self, "_nodes")
        parent = object.__getattribute__(self, "_parent")
        if not nodes:
            raise AttributeError("No nodes in group")
        list_idx, node = nodes[0]
        return getattr(FlowNodeProxy(node, list_idx, parent), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_nodes", "_parent"):
            object.__setattr__(self, name, value)
            return
        nodes = object.__getattribute__(self, "_nodes")
        parent = object.__getattribute__(self, "_parent")
        if not nodes:
            raise AttributeError("No nodes in group")
        list_idx, node = nodes[0]
        FlowNodeProxy(node, list_idx, parent).__setattr__(name, value)

    def __repr__(self) -> str:
        nodes = object.__getattribute__(self, "_nodes")
        if nodes:
            node_type = nodes[0][1].get("type", "?")
            return f"<FlowNodeGroup type={node_type!r} count={len(nodes)}>"
        return "<FlowNodeGroup empty>"

    def __dir__(self) -> List[str]:
        base = set(super().__dir__())
        try:
            nodes = object.__getattribute__(self, "_nodes")
            parent = object.__getattribute__(self, "_parent")
            if nodes:
                list_idx, node = nodes[0]
                p = FlowNodeProxy(node, list_idx, parent)
                base.update(p.__dir__())
        except Exception:
            pass
        return sorted(base)

    def attrs(self) -> List[str]:
        nodes = object.__getattribute__(self, "_nodes")
        parent = object.__getattribute__(self, "_parent")
        if not nodes:
            return []
        list_idx, node = nodes[0]
        return FlowNodeProxy(node, list_idx, parent).attrs()

    def keys(self):
        nodes = object.__getattribute__(self, "_nodes")
        return (node.get("id", idx) for idx, node in nodes)

    def values(self):
        nodes = object.__getattribute__(self, "_nodes")
        return (node for _, node in nodes)

    def items(self):
        nodes = object.__getattribute__(self, "_nodes")
        return ((node.get("id", idx), node) for idx, node in nodes)

    def to_list(self) -> List[Dict[str, Any]]:
        nodes = object.__getattribute__(self, "_nodes")
        return [node for _, node in nodes]

    def to_dict(self) -> Dict[int, Dict[str, Any]]:
        nodes = object.__getattribute__(self, "_nodes")
        return {node.get("id", idx): node for idx, node in nodes}


class FlowNodesView:
    """View of Flow.nodes with OOP access patterns."""

    __slots__ = ("_flow",)

    def __init__(self, flow: "Flow"):
        object.__setattr__(self, "_flow", flow)

    def __getattr__(self, name: str) -> FlowNodeGroup:
        if name.startswith("_"):
            raise AttributeError(name)
        flow = object.__getattribute__(self, "_flow")
        nodes_list = flow.get("nodes", [])
        matches = [(i, n) for i, n in enumerate(nodes_list) if isinstance(n, dict) and n.get("type", "").lower() == name.lower()]
        if matches:
            return FlowNodeGroup(matches, flow)
        raise AttributeError(f"No nodes with type '{name}'")

    def __dir__(self) -> List[str]:
        base = set(super().__dir__())
        try:
            flow = object.__getattribute__(self, "_flow")
            nodes_list = flow.get("nodes", []) if isinstance(flow, dict) else []
            for n in nodes_list:
                if isinstance(n, dict):
                    t = n.get("type")
                    if isinstance(t, str) and t:
                        base.add(t)
        except Exception:
            pass
        return sorted(base)

    def find(
        self,
        *,
        type: Optional[Any] = None,  # noqa: A002
        title: Optional[Any] = None,
        node_id: Optional[int] = None,
        deep: bool = DEFAULT_FIND_DEEP,
        depth: Optional[int] = None,
        max_depth: int = DEFAULT_FIND_MAX_DEPTH,  # backwards-compat alias
        operator: str = "and",
        operator_mode: Optional[str] = None,
        **attrs: Any,
    ) -> List[FlowNodeProxy]:
        flow = object.__getattribute__(self, "_flow")
        op = (operator_mode or operator or "and").lower().strip()
        if op not in ("and", "or"):
            raise ValueError("operator must be 'and' or 'or'")
        eff_depth = int(max_depth if depth is None else depth)
        eff_depth = max(0, eff_depth)

        want: Dict[str, Any] = {str(k): v for k, v in attrs.items()}
        want_keys = set(want.keys())
        widget_cache: Dict[str, List[str]] = {}
        out: List[FlowNodeProxy] = []

        for node, path in _iter_flow_nodes_with_paths(flow, deep=deep, max_depth=eff_depth):
            if not isinstance(node, dict):
                continue
            if node_id is not None and node.get("id") != node_id:
                continue
            if type is not None:
                t = node.get("type")
                if not _match_str_filter(type, t, case_insensitive=True):
                    continue
            if title is not None:
                t1 = node.get("title")
                t2 = None
                props = node.get("properties")
                if isinstance(props, dict):
                    t2 = props.get("Node name for S&R")
                match = t1 if isinstance(t1, str) else (t2 if isinstance(t2, str) else None)
                if not _match_str_filter(title, match, case_insensitive=True):
                    continue

            if want:
                roots: List[Any] = [node]
                try:
                    wmap = _flow_widget_map(node, flow, _cache=widget_cache)
                except Exception:
                    wmap = {}
                if wmap:
                    roots.append(wmap)

                found: Dict[str, List[Any]] = {k: [] for k in want_keys}
                for r in roots:
                    hits = _collect_key_hits(r, want_keys, depth=eff_depth)
                    for k, vals in hits.items():
                        if vals:
                            found[k].extend(vals)

                def _key_ok(k: str, expected: Any) -> bool:
                    vals = found.get(k) or []
                    if expected == "*":
                        return bool(vals)
                    for v in vals:
                        if _match_expected(expected, v):
                            return True
                    return False

                if op == "and":
                    if not all(_key_ok(k, want[k]) for k in want_keys):
                        continue
                else:
                    if not any(_key_ok(k, want[k]) for k in want_keys):
                        continue

            p = FlowNodeProxy(node, 0, flow)
            object.__setattr__(p, "_autoflow_addr", path)
            out.append(p)

        return out

    def __iter__(self):
        flow = object.__getattribute__(self, "_flow")
        nodes_list = flow.get("nodes", [])
        for i, node in enumerate(nodes_list):
            if isinstance(node, dict):
                yield FlowNodeProxy(node, i, flow)

    def __len__(self) -> int:
        flow = object.__getattribute__(self, "_flow")
        return len(flow.get("nodes", []))

    def __getitem__(self, key) -> FlowNodeProxy:
        flow = object.__getattribute__(self, "_flow")
        nodes_list = flow.get("nodes", [])
        if isinstance(key, int):
            if 0 <= key < len(nodes_list):
                return FlowNodeProxy(nodes_list[key], key, flow)
            for i, node in enumerate(nodes_list):
                if isinstance(node, dict) and node.get("id") == key:
                    return node  # raw dict for dict() compatibility
            raise KeyError(key)
        raise TypeError(f"Index must be int, not {type(key).__name__}")

    def __repr__(self) -> str:
        flow = object.__getattribute__(self, "_flow")
        return f"<FlowNodesView count={len(flow.get('nodes', []))}>"

    def keys(self):
        flow = object.__getattribute__(self, "_flow")
        nodes_list = flow.get("nodes", [])
        return (node.get("id", i) for i, node in enumerate(nodes_list) if isinstance(node, dict))

    def values(self):
        flow = object.__getattribute__(self, "_flow")
        return (node for node in flow.get("nodes", []) if isinstance(node, dict))

    def items(self):
        flow = object.__getattribute__(self, "_flow")
        nodes_list = flow.get("nodes", [])
        return ((node.get("id", i), node) for i, node in enumerate(nodes_list) if isinstance(node, dict))

    def to_list(self) -> List[Dict[str, Any]]:
        flow = object.__getattribute__(self, "_flow")
        return list(flow.get("nodes", []))

    def to_dict(self) -> Dict[int, Dict[str, Any]]:
        flow = object.__getattribute__(self, "_flow")
        nodes_list = flow.get("nodes", [])
        return {node.get("id", i): node for i, node in enumerate(nodes_list) if isinstance(node, dict)}


# ---------------------------------------------------------------------------
# Top-level dict subclasses (legacy-parity)
# ---------------------------------------------------------------------------


class ApiFlow(dict):
    """API payload dict subclass with ergonomic helpers."""

    def __init__(
        self,
        x: Optional[Union[str, Path, bytes, Dict[str, Any]]] = None,
        *args,
        map_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        in_place: bool = False,
        node_info: Optional[Dict[str, Any]] = None,
        use_api: Optional[bool] = None,
        workflow_meta: Optional[Dict[str, Any]] = None,
        # Conversion params (used when auto-detecting workspace format).
        auto_convert: bool = True,
        server_url: Optional[str] = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        include_meta: bool = DEFAULT_INCLUDE_META,
        convert_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        **kwargs,
    ):
        src: Optional[str] = None
        if x is not None and not args and not kwargs:
            data: Any
            if isinstance(x, dict):
                data = x
                src = "dict"
            elif isinstance(x, (bytes, bytearray)):
                b = bytes(x)
                if is_png_bytes(b):
                    meta = parse_png_metadata_from_bytes(b)
                    if "prompt" not in meta:
                        raise ValueError("PNG bytes have no embedded 'prompt' (API payload) metadata")
                    data = meta["prompt"]
                    src = "png-bytes"
                else:
                    data = json.loads(b.decode("utf-8"))
                    src = "json-bytes"
            elif isinstance(x, (str, Path)):
                if is_png_path(x):
                    meta = extract_png_comfyui_metadata(x)
                    if "prompt" not in meta:
                        raise ValueError(f"PNG file has no embedded 'prompt' (API payload) metadata: {x}")
                    data = meta["prompt"]
                    try:
                        src = f"png:{Path(x).expanduser().resolve()}"
                    except Exception:
                        src = f"png:{x}"
                elif isinstance(x, Path) and x.exists():
                    data = json.loads(x.read_text(encoding="utf-8"))
                    try:
                        src = f"file:{x.expanduser().resolve()}"
                    except Exception:
                        src = f"file:{x}"
                elif isinstance(x, str):
                    if looks_like_json(x):
                        data = json.loads(x)
                        src = "json-string"
                    elif Path(x).exists():
                        data = json.loads(Path(x).read_text(encoding="utf-8"))
                        try:
                            src = f"file:{Path(x).expanduser().resolve()}"
                        except Exception:
                            src = f"file:{x}"
                    else:
                        if looks_like_path(x):
                            raise FileNotFoundError(f"Workflow file not found: {x}")
                        data = json.loads(x)
                        src = "json-string"
                else:
                    data = json.loads(Path(x).read_text(encoding="utf-8"))
                    try:
                        src = f"file:{Path(x).expanduser().resolve()}"
                    except Exception:
                        src = f"file:{x}"
            else:
                raise TypeError("x must be a dict, path (JSON/PNG), bytes, or JSON string")

            if not isinstance(data, dict):
                raise ValueError("API payload must be a dict at top level")

            # ── Auto-detect workspace format and convert ──────────────
            if _is_workspace_data(data):
                flow = Flow(data, node_info=node_info, server_url=server_url, timeout=timeout)
                if auto_convert:
                    converted = flow.convert(
                        node_info=node_info,
                        server_url=server_url,
                        timeout=timeout,
                        include_meta=include_meta,
                        convert_callbacks=convert_callbacks,
                        map_callbacks=map_callbacks,
                    )
                    # Steal the converted data and metadata.
                    super().__init__(converted)
                    if isinstance(src, str) and src:
                        object.__setattr__(self, "_autoflow_source", f"converted_from({src})")
                    self.node_info = converted.node_info
                    self.use_api = converted.use_api if converted.use_api is not None else use_api
                    self.workflow_meta = converted.workflow_meta if converted.workflow_meta is not None else workflow_meta
                    return
                else:
                    raise ValueError(
                        "Data is a ComfyUI workspace workflow (has 'nodes'/'links'), "
                        "not an API payload. Use Flow() to load workspace files, "
                        "or pass auto_convert=True (default) to convert automatically."
                    )

            for k, v in data.items():
                if not isinstance(v, dict):
                    raise ValueError(f"API payload node {k!r} must be a dict")
                if "class_type" not in v or "inputs" not in v:
                    raise ValueError(f"API payload node {k!r} missing 'class_type' or 'inputs'")
            super().__init__(data)
        else:
            super().__init__(x if x is not None else {}, *args, **kwargs)

        if isinstance(src, str) and src:
            object.__setattr__(self, "_autoflow_source", src)

        if node_info is not None and not isinstance(node_info, dict):
            from .convert import resolve_node_info_with_origin

            oi_dict, _use_api, origin = resolve_node_info_with_origin(node_info, None, DEFAULT_HTTP_TIMEOUT_S, allow_env=True)
            oi_obj = NodeInfo(oi_dict or {})
            setattr(oi_obj, "_autoflow_origin", origin)
            # Cache a stable string source for easy introspection.
            s = oi_obj.source
            if isinstance(s, str) and s:
                setattr(oi_obj, "_autoflow_source", s)
            node_info = oi_obj
        elif node_info is not None and isinstance(node_info, dict) and not isinstance(node_info, NodeInfo):
            # Wrap plain dicts so callers can do `api.node_info.source`.
            oi_obj = NodeInfo(node_info)
            if not getattr(oi_obj, "_autoflow_source", None):
                setattr(oi_obj, "_autoflow_source", "dict")
            node_info = oi_obj

        self.node_info = node_info
        self.use_api = use_api
        self.workflow_meta = workflow_meta

        if map_callbacks is not None:
            from .map import api_mapping

            mapped = api_mapping(self, map_callbacks, node_info=node_info, in_place=in_place)
            if mapped is not self:
                self.clear()
                self.update(mapped)

    def copy(self) -> "ApiFlow":  # noqa: A003
        return ApiFlow(dict(self), node_info=self.node_info, use_api=self.use_api, workflow_meta=self.workflow_meta)

    @property
    def source(self) -> Optional[str]:
        return getattr(self, "_autoflow_source", None)

    @property
    def node_info_origin(self):
        oi = getattr(self, "node_info", None)
        return getattr(oi, "origin", None) or getattr(oi, "_autoflow_origin", None)

    @property
    def dag(self):
        """
        Best-effort dependency graph for this ApiFlow.

        Returns a `Dag` dict-subclass with helper methods like:
        - `.edges`, `.deps(node_id)`, `.ancestors(node_id)`
        - `.to_dot()`, `.to_mermaid()`
        """
        cache = getattr(self, "_autoflow_dag_cache", None)
        if cache is not None:
            return cache
        from .dag import build_api_dag

        d = build_api_dag(dict(self))
        object.__setattr__(self, "_autoflow_dag_cache", d)
        return d

    def __getattr__(self, name: str) -> NodeGroup:
        if name.startswith("_"):
            raise AttributeError(name)
        matches = [
            (nid, n)
            for nid, n in self.items()
            if isinstance(n, dict) and n.get("class_type", "").lower() == name.lower()
        ]
        if matches:
            return NodeGroup(matches, self)
        raise AttributeError(f"No nodes with class_type '{name}'")

    def find(
        self,
        *,
        class_type: Optional[Any] = None,
        title: Optional[Any] = None,
        node_id: Optional[Union[str, int]] = None,
        has_input: Optional[str] = None,
        depth: Optional[int] = None,
        max_depth: int = DEFAULT_FIND_MAX_DEPTH,
        operator: str = "and",
        operator_mode: Optional[str] = None,
        **attrs: Any,
    ) -> List[NodeProxy]:
        out: List[NodeProxy] = []
        op = (operator_mode or operator or "and").lower().strip()
        if op not in ("and", "or"):
            raise ValueError("operator must be 'and' or 'or'")
        eff_depth = int(max_depth if depth is None else depth)
        eff_depth = max(0, eff_depth)
        want: Dict[str, Any] = {str(k): v for k, v in attrs.items()}
        want_keys = set(want.keys())
        want_id = str(node_id) if node_id is not None else None

        for nid, node in self.items():
            if not isinstance(node, dict):
                continue
            nid_s = str(nid)
            if want_id is not None and nid_s != want_id:
                continue
            if class_type is not None:
                ct = node.get("class_type")
                if not _match_str_filter(class_type, ct, case_insensitive=True):
                    continue
            if title is not None:
                t = node.get("_meta", {}).get("title") if isinstance(node.get("_meta"), dict) else None
                if not _match_str_filter(title, t, case_insensitive=True):
                    continue
            if has_input is not None:
                inputs = node.get("inputs")
                if not (isinstance(inputs, dict) and has_input in inputs):
                    continue

            if want:
                hits = _collect_key_hits(node, want_keys, depth=eff_depth)

                def _key_ok(k: str, expected: Any) -> bool:
                    vals = hits.get(k) or []
                    if expected == "*":
                        return bool(vals)
                    for v in vals:
                        if _match_expected(expected, v):
                            return True
                    return False

                if op == "and":
                    if not all(_key_ok(k, want[k]) for k in want_keys):
                        continue
                else:
                    if not any(_key_ok(k, want[k]) for k in want_keys):
                        continue

            p = NodeProxy(node, nid_s, self)
            object.__setattr__(p, "_autoflow_addr", nid_s)
            out.append(p)

        return out

    def __getitem__(self, key):
        if isinstance(key, str) and "/" in key:
            return self._path_get(key)
        return super().__getitem__(str(key) if isinstance(key, int) else key)

    def __setitem__(self, key, value):
        if isinstance(key, str) and "/" in key:
            return self._path_set(key, value)
        return super().__setitem__(str(key) if isinstance(key, int) else key, value)

    def _path_get(self, path: str) -> Any:
        parts = path.split("/")
        if not parts:
            raise KeyError(path)
        first = parts[0]
        rest = parts[1:]

        if first in self:
            node = self.get(first)
            if not rest:
                return DictView(node) if isinstance(node, dict) else node
            result = self._navigate_node(node, first, rest)
            return DictView(result) if isinstance(result, dict) else result

        matches = [(nid, n) for nid, n in self.items() if isinstance(n, dict) and n.get("class_type", "").lower() == first.lower()]
        if not matches:
            raise KeyError(f"No node with id or class_type '{first}'")

        idx = 0
        if rest and rest[0].isdigit():
            idx = int(rest[0])
            rest = rest[1:]
        if idx >= len(matches):
            raise KeyError(f"Index {idx} out of range for class_type '{first}' (have {len(matches)})")

        node_id, node = matches[idx]
        if not rest:
            return DictView(node) if isinstance(node, dict) else node
        result = self._navigate_node(node, node_id, rest)
        return DictView(result) if isinstance(result, dict) else result

    def _navigate_node(self, node: Dict[str, Any], node_id: str, path_parts: List[str]) -> Any:
        if not path_parts:
            return node
        key = path_parts[0]
        rest = path_parts[1:]

        inputs = node.get("inputs", {})
        if isinstance(inputs, dict) and key in inputs:
            val = inputs[key]
            if not rest:
                return val
            if isinstance(val, dict):
                return self._dict_navigate(val, rest)
            raise KeyError(f"Cannot navigate into non-dict value at '{key}'")

        if key in node:
            val = node[key]
            if not rest:
                return val
            if isinstance(val, dict):
                return self._dict_navigate(val, rest)
            raise KeyError(f"Cannot navigate into non-dict value at '{key}'")

        raise KeyError(f"Node {node_id!r} has no input or key '{key}'")

    def _dict_navigate(self, d: Dict[str, Any], path_parts: List[str]) -> Any:
        for part in path_parts:
            if not isinstance(d, dict):
                raise KeyError(f"Cannot navigate into non-dict at '{part}'")
            if part not in d:
                raise KeyError(f"Key '{part}' not found")
            d = d[part]
        return d

    def _path_set(self, path: str, value: Any) -> None:
        parts = path.split("/")
        if len(parts) < 2:
            raise KeyError(f"Path must have at least 2 parts: '{path}'")
        first = parts[0]
        rest = parts[1:]

        if first in self:
            node = self.get(first)
            self._set_in_node(node, first, rest, value)
            return

        matches = [(nid, n) for nid, n in self.items() if isinstance(n, dict) and n.get("class_type", "").lower() == first.lower()]
        if not matches:
            raise KeyError(f"No node with id or class_type '{first}'")

        idx = 0
        if rest and rest[0].isdigit():
            idx = int(rest[0])
            rest = rest[1:]
        if idx >= len(matches):
            raise KeyError(f"Index {idx} out of range for class_type '{first}' (have {len(matches)})")

        node_id, node = matches[idx]
        self._set_in_node(node, node_id, rest, value)

    def _set_in_node(self, node: Dict[str, Any], node_id: str, path_parts: List[str], value: Any) -> None:
        if not path_parts:
            raise KeyError("Cannot replace entire node via path syntax")
        key = path_parts[0]
        rest = path_parts[1:]

        if not rest:
            node.setdefault("inputs", {})[key] = value
            return

        inputs = node.get("inputs", {})
        if isinstance(inputs, dict) and key in inputs and isinstance(inputs[key], dict):
            self._dict_set(inputs[key], rest, value)
        elif key in node and isinstance(node[key], dict):
            self._dict_set(node[key], rest, value)
        else:
            raise KeyError(f"Cannot navigate into '{key}' on node {node_id!r}")

    def _dict_set(self, d: Dict[str, Any], path_parts: List[str], value: Any) -> None:
        for part in path_parts[:-1]:
            if part not in d:
                d[part] = {}
            d = d[part]
            if not isinstance(d, dict):
                raise KeyError(f"Cannot navigate into non-dict at '{part}'")
        d[path_parts[-1]] = value

    @classmethod
    def load(
        cls,
        x: Union[str, Path, bytes, Dict[str, Any]],
        *,
        map_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        in_place: bool = False,
        node_info: Optional[Dict[str, Any]] = None,
        use_api: Optional[bool] = None,
        workflow_meta: Optional[Dict[str, Any]] = None,
    ) -> "ApiFlow":
        return cls(
            x,
            map_callbacks=map_callbacks,
            in_place=in_place,
            node_info=node_info,
            use_api=use_api,
            workflow_meta=workflow_meta,
        )

    def to_json(self, indent: int = DEFAULT_JSON_INDENT, ensure_ascii: bool = DEFAULT_JSON_ENSURE_ASCII) -> str:
        return json.dumps(self, indent=indent, ensure_ascii=ensure_ascii) + "\n"

    def save(self, output_path: Union[str, Path], indent: int = DEFAULT_JSON_INDENT, ensure_ascii: bool = DEFAULT_JSON_ENSURE_ASCII) -> Path:
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.to_json(indent=indent, ensure_ascii=ensure_ascii), encoding="utf-8")
        return out_path

    def submit(
        self,
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
    ):
        from .results import _submit_impl

        return _submit_impl(
            self,
            server_url=server_url,
            client_id=client_id,
            extra=extra,
            timeout=timeout,
            wait=wait,
            poll_interval=poll_interval,
            wait_timeout=wait_timeout,
            poll_queue=poll_queue,
            queue_poll_interval=queue_poll_interval,
            fetch_outputs=fetch_outputs,
            output_path=output_path,
            include_bytes=include_bytes,
            on_event=on_event,
        )


class Flow(dict):
    """Strict workspace workflow.json container (dict subclass)."""

    def __init__(
        self,
        x: Optional[Union[str, Path, bytes, Dict[str, Any]]] = None,
        *args,
        workflow_meta: Optional[Dict[str, Any]] = None,
        node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
        server_url: Optional[str] = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        fetch_oi: bool = False,
        **kwargs,
    ):
        src: Optional[str] = None
        if x is not None and not args and not kwargs:
            data: Any
            if isinstance(x, dict):
                data = x
                src = "dict"
            elif isinstance(x, (bytes, bytearray)):
                b = bytes(x)
                if is_png_bytes(b):
                    meta = parse_png_metadata_from_bytes(b)
                    if "workflow" not in meta:
                        raise ValueError("PNG bytes have no embedded 'workflow' metadata")
                    data = meta["workflow"]
                    src = "png-bytes"
                else:
                    data = json.loads(b.decode("utf-8"))
                    src = "json-bytes"
            elif isinstance(x, (str, Path)):
                if is_png_path(x):
                    meta = extract_png_comfyui_metadata(x)
                    if "workflow" not in meta:
                        raise ValueError(f"PNG file has no embedded 'workflow' metadata: {x}")
                    data = meta["workflow"]
                    try:
                        src = f"png:{Path(x).expanduser().resolve()}"
                    except Exception:
                        src = f"png:{x}"
                elif isinstance(x, Path) and x.exists():
                    data = json.loads(x.read_text(encoding="utf-8"))
                    try:
                        src = f"file:{x.expanduser().resolve()}"
                    except Exception:
                        src = f"file:{x}"
                elif isinstance(x, str):
                    if looks_like_json(x):
                        data = json.loads(x)
                        src = "json-string"
                    elif Path(x).exists():
                        data = json.loads(Path(x).read_text(encoding="utf-8"))
                        try:
                            src = f"file:{Path(x).expanduser().resolve()}"
                        except Exception:
                            src = f"file:{x}"
                    else:
                        if looks_like_path(x):
                            raise FileNotFoundError(f"API payload file not found: {x}")
                        data = json.loads(x)
                        src = "json-string"
                else:
                    data = json.loads(Path(x).read_text(encoding="utf-8"))
                    try:
                        src = f"file:{Path(x).expanduser().resolve()}"
                    except Exception:
                        src = f"file:{x}"
            else:
                raise TypeError("x must be a dict, path (JSON/PNG), bytes, or JSON string")

            if not isinstance(data, dict):
                raise ValueError("workflow.json must be a dict at top level")
            if not isinstance(data.get("nodes"), list) or not isinstance(data.get("links"), list):
                raise ValueError("Not a ComfyUI workspace workflow.json (expected 'nodes' and 'links' lists)")
            if "last_node_id" not in data or "last_link_id" not in data:
                raise ValueError("Not a ComfyUI workspace workflow.json (missing 'last_node_id'/'last_link_id')")

            super().__init__(data)
            if isinstance(src, str) and src:
                object.__setattr__(self, "_autoflow_source", src)
            inferred = data.get("extra") if isinstance(data.get("extra"), dict) else None
            self.workflow_meta = copy.deepcopy(inferred) if inferred is not None else None
            if workflow_meta is not None:
                self.workflow_meta = workflow_meta

            if node_info is not None:
                from .convert import resolve_node_info_with_origin

                oi_dict, _use_api, origin = resolve_node_info_with_origin(node_info, None, timeout, allow_env=True)
                oi_obj = NodeInfo(oi_dict or {})
                setattr(oi_obj, "_autoflow_origin", origin)
                s = oi_obj.source
                if isinstance(s, str) and s:
                    setattr(oi_obj, "_autoflow_source", s)
                self.node_info = oi_obj
            elif fetch_oi:
                effective = server_url or os.environ.get("AUTOFLOW_COMFYUI_SERVER_URL")
                if not effective:
                    raise ValueError("fetch_oi=True requires server_url= (or env AUTOFLOW_COMFYUI_SERVER_URL).")
                oi_obj = NodeInfo(fetch_node_info(effective, timeout=timeout))
                from .origin import NodeInfoOrigin

                setattr(oi_obj, "_autoflow_origin", NodeInfoOrigin(requested="fetch_oi", resolved="server", effective_server_url=effective))
                setattr(oi_obj, "_autoflow_source", f"server:{effective}")
                self.node_info = oi_obj
            else:
                self.node_info = None
            return

        super().__init__(x if x is not None else {}, *args, **kwargs)
        if isinstance(src, str) and src:
            object.__setattr__(self, "_autoflow_source", src)
        self.workflow_meta = workflow_meta
        if node_info is not None:
            from .convert import resolve_node_info_with_origin

            oi_dict, _use_api, origin = resolve_node_info_with_origin(node_info, None, timeout, allow_env=True)
            oi_obj = NodeInfo(oi_dict or {})
            setattr(oi_obj, "_autoflow_origin", origin)
            s = oi_obj.source
            if isinstance(s, str) and s:
                setattr(oi_obj, "_autoflow_source", s)
            self.node_info = oi_obj
        elif fetch_oi:
            effective = server_url or os.environ.get("AUTOFLOW_COMFYUI_SERVER_URL")
            if not effective:
                raise ValueError("fetch_oi=True requires server_url= (or env AUTOFLOW_COMFYUI_SERVER_URL).")
            oi_obj = NodeInfo(fetch_node_info(effective, timeout=timeout))
            from .origin import NodeInfoOrigin

            setattr(oi_obj, "_autoflow_origin", NodeInfoOrigin(requested="fetch_oi", resolved="server", effective_server_url=effective))
            setattr(oi_obj, "_autoflow_source", f"server:{effective}")
            self.node_info = oi_obj
        else:
            self.node_info = None

    @classmethod
    def load(cls, x: Union[str, Path, bytes, Dict[str, Any]]) -> "Flow":
        return cls(x)

    @classmethod
    def _from_raw(
        cls,
        data: Dict[str, Any],
        *,
        node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
    ) -> "Flow":
        """Construct a Flow from a raw dict, bypassing normal validation.

        Used by Flow.create() to build empty flows.  The caller is responsible
        for ensuring the dict has the required structure.
        """
        inst = dict.__new__(cls)
        dict.__init__(inst, data)
        inst.workflow_meta = data.get("extra") if isinstance(data.get("extra"), dict) else None
        object.__setattr__(inst, "_autoflow_source", "created")
        if node_info is not None:
            from .convert import resolve_node_info_with_origin

            oi_dict, _use_api, origin = resolve_node_info_with_origin(node_info, None, timeout, allow_env=True)
            oi_obj = NodeInfo(oi_dict or {})
            setattr(oi_obj, "_autoflow_origin", origin)
            s = oi_obj.source
            if isinstance(s, str) and s:
                setattr(oi_obj, "_autoflow_source", s)
            inst.node_info = oi_obj
        else:
            inst.node_info = None
        return inst

    def to_json(self, indent: int = DEFAULT_JSON_INDENT, ensure_ascii: bool = DEFAULT_JSON_ENSURE_ASCII) -> str:
        return json.dumps(self, indent=indent, ensure_ascii=ensure_ascii) + "\n"

    def save(self, output_path: Union[str, Path], indent: int = DEFAULT_JSON_INDENT, ensure_ascii: bool = DEFAULT_JSON_ENSURE_ASCII) -> Path:
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.to_json(indent=indent, ensure_ascii=ensure_ascii), encoding="utf-8")
        return out_path

    @property
    def nodes(self) -> FlowNodesView:
        return FlowNodesView(self)

    @property
    def node_info_origin(self):
        oi = getattr(self, "node_info", None)
        return getattr(oi, "origin", None) or getattr(oi, "_autoflow_origin", None)

    @property
    def source(self) -> Optional[str]:
        return getattr(self, "_autoflow_source", None)

    @property
    def dag(self):
        """
        Best-effort dependency graph for this workspace Flow (uses `links` table).

        Returns a `Dag` dict-subclass with helper methods like:
        - `.edges`, `.deps(node_id)`, `.ancestors(node_id)`
        - `.to_dot()`, `.to_mermaid()`
        """
        cache = getattr(self, "_autoflow_dag_cache", None)
        if cache is not None:
            return cache
        from .dag import build_flow_dag

        d = build_flow_dag(dict(self), node_info=getattr(self, "node_info", None))
        object.__setattr__(self, "_autoflow_dag_cache", d)
        return d

    def find(
        self,
        *,
        type: Optional[str] = None,  # noqa: A002
        title: Optional[str] = None,
        node_id: Optional[int] = None,
        deep: bool = DEFAULT_FIND_DEEP,
        depth: Optional[int] = None,
        max_depth: int = DEFAULT_FIND_MAX_DEPTH,
        operator: str = "and",
        operator_mode: Optional[str] = None,
        **attrs: Any,
    ) -> List[FlowNodeProxy]:
        return self.nodes.find(
            type=type,
            title=title,
            node_id=node_id,
            deep=deep,
            depth=depth,
            max_depth=max_depth,
            operator=operator,
            operator_mode=operator_mode,
            **attrs,
        )

    @property
    def links(self) -> List[Any]:
        return self.get("links", [])

    @property
    def extra(self) -> DictView:
        return DictView(self.get("extra", {}))

    def convert(
        self,
        node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
        server_url: Optional[str] = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        include_meta: bool = DEFAULT_INCLUDE_META,
        *,
        convert_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        map_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        disable_autoflow_meta: bool = False,
        apply_autoflow_meta: Optional[bool] = None,  # backwards-compat alias (disable wins)
    ) -> ApiFlow:
        # Prefer attached node_info (with source metadata) when caller doesn't override.
        oi_for_convert = node_info if node_info is not None else getattr(self, "node_info", None)
        if oi_for_convert is not None and not isinstance(oi_for_convert, NodeInfo):
            # Wrap plain dicts so callers can introspect `api.node_info.source`.
            if isinstance(oi_for_convert, dict):
                oi_obj = NodeInfo(oi_for_convert)
                if not getattr(oi_obj, "_autoflow_source", None):
                    setattr(oi_obj, "_autoflow_source", "dict")
                oi_for_convert = oi_obj
            else:
                from .convert import resolve_node_info_with_origin

                oi_dict, _use_api, origin = resolve_node_info_with_origin(
                    oi_for_convert,
                    server_url,
                    timeout,
                    allow_env=True,
                    require_source=True,
                )
                oi_obj = NodeInfo(oi_dict or {})
                setattr(oi_obj, "_autoflow_origin", origin)
                s = oi_obj.source
                if isinstance(s, str) and s:
                    setattr(oi_obj, "_autoflow_source", s)
                oi_for_convert = oi_obj

        api = convert(
            self,
            node_info=oi_for_convert,
            server_url=server_url,
            timeout=timeout,
            include_meta=include_meta,
            convert_callbacks=convert_callbacks,
            disable_autoflow_meta=disable_autoflow_meta,
            apply_autoflow_meta=apply_autoflow_meta,
        )
        parent_src = getattr(self, "_autoflow_source", None)
        if isinstance(parent_src, str) and parent_src:
            try:
                object.__setattr__(api, "_autoflow_source", f"converted_from({parent_src})")
            except Exception:
                pass
        if map_callbacks is not None:
            from .map import api_mapping

            mapped = api_mapping(api, map_callbacks, in_place=False)
            if isinstance(mapped, ApiFlow):
                if isinstance(parent_src, str) and parent_src:
                    try:
                        object.__setattr__(mapped, "_autoflow_source", f"converted_from({parent_src})")
                    except Exception:
                        pass
                return mapped
            out = ApiFlow(mapped, node_info=api.node_info, use_api=api.use_api, workflow_meta=api.workflow_meta)
            if isinstance(parent_src, str) and parent_src:
                try:
                    object.__setattr__(out, "_autoflow_source", f"converted_from({parent_src})")
                except Exception:
                    pass
            return out
        return api

    def fetch_node_info(
        self,
        value: Optional[Union[Dict[str, Any], str, Path, bytes, "NodeInfo"]] = None,
        *,
        server_url: Optional[str] = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
    ) -> Dict[str, Any]:
        if value is not None:
            from .convert import resolve_node_info_with_origin

            oi_dict, _use_api, origin = resolve_node_info_with_origin(value, None, timeout, allow_env=True)
            oi_obj = NodeInfo(oi_dict or {})
            setattr(oi_obj, "_autoflow_origin", origin)
            s = oi_obj.source
            if isinstance(s, str) and s:
                setattr(oi_obj, "_autoflow_source", s)
            self.node_info = oi_obj
            return oi_obj

        effective = server_url or os.environ.get("AUTOFLOW_COMFYUI_SERVER_URL")
        if not effective:
            raise ValueError(
                "Missing server_url. Pass server_url= or set AUTOFLOW_COMFYUI_SERVER_URL, "
                "or pass a value to fetch_node_info(value=...) to load without a server."
            )
        oi_obj = NodeInfo(fetch_node_info(effective, timeout=timeout))
        from .origin import NodeInfoOrigin

        setattr(oi_obj, "_autoflow_origin", NodeInfoOrigin(requested="fetch_node_info", resolved="server", effective_server_url=effective))
        setattr(oi_obj, "_autoflow_source", f"server:{effective}")
        self.node_info = oi_obj
        return oi_obj

    def convert_with_errors(
        self,
        node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
        server_url: Optional[str] = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        include_meta: bool = DEFAULT_INCLUDE_META,
        output_path: Optional[Union[str, Path]] = None,
        *,
        convert_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        map_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        disable_autoflow_meta: bool = False,
        apply_autoflow_meta: Optional[bool] = None,  # backwards-compat alias (disable wins)
    ) -> ConvertResult:
        r = convert_workflow_with_errors(
            workflow_data=self,
            node_info=node_info,
            server_url=server_url,
            timeout=timeout,
            include_meta=include_meta,
            output_file=None,
            convert_callbacks=convert_callbacks,
            disable_autoflow_meta=disable_autoflow_meta,
            apply_autoflow_meta=apply_autoflow_meta,
        )
        data = r.data if isinstance(r.data, ApiFlow) or r.data is None else ApiFlow(r.data)
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
            out.data.save(output_path)
        if map_callbacks is not None and out.data is not None:
            from .map import api_mapping

            mapped = api_mapping(out.data, map_callbacks, in_place=False)
            out.data = mapped if isinstance(mapped, ApiFlow) else ApiFlow(mapped, node_info=out.data.node_info, use_api=out.data.use_api, workflow_meta=out.data.workflow_meta)  # type: ignore[attr-defined]
        return out

    def submit(
        self,
        server_url: Optional[str] = None,
        *,
        node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        include_meta: bool = DEFAULT_INCLUDE_META,
        convert_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        map_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        client_id: str = DEFAULT_SUBMIT_CLIENT_ID,
        extra: Optional[Dict[str, Any]] = None,
        wait: bool = DEFAULT_SUBMIT_WAIT,
        poll_interval: float = DEFAULT_POLL_INTERVAL_S,
        wait_timeout: int = DEFAULT_WAIT_TIMEOUT_S,
        poll_queue: Optional[bool] = None,
        queue_poll_interval: Optional[float] = None,
        fetch_outputs: bool = DEFAULT_FETCH_IMAGES,
        output_path: Optional[Union[str, Path]] = None,
        include_bytes: bool = False,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        api = self.convert(
            node_info=node_info,
            server_url=server_url,
            timeout=timeout,
            include_meta=include_meta,
            convert_callbacks=convert_callbacks,
            map_callbacks=map_callbacks,
        )
        return api.submit(
            server_url=server_url,
            client_id=client_id,
            extra=extra,
            timeout=timeout,
            wait=wait,
            poll_interval=poll_interval,
            wait_timeout=wait_timeout,
            poll_queue=poll_queue,
            queue_poll_interval=queue_poll_interval,
            fetch_outputs=fetch_outputs,
            output_path=output_path,
            include_bytes=include_bytes,
            on_event=on_event,
        )


def _is_workspace_data(data: Dict[str, Any]) -> bool:
    """Return True if *data* looks like a ComfyUI workspace workflow.json."""
    return (
        "last_node_id" in data
        and "last_link_id" in data
        and isinstance(data.get("nodes"), list)
        and isinstance(data.get("links"), list)
    )


class Workflow:
    """Deprecated – use ``ApiFlow`` directly.

    ``ApiFlow`` now auto-detects workspace-format JSON and converts it
    automatically.  ``Workflow`` is kept as a thin compatibility alias
    and will be removed in a future release.
    """

    def __new__(
        cls,
        x: Optional[Union[str, Path, bytes, Dict[str, Any]]] = None,
        *args,
        auto_convert: bool = True,
        node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
        server_url: Optional[str] = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        include_meta: bool = DEFAULT_INCLUDE_META,
        convert_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        map_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        in_place: bool = False,
        use_api: Optional[bool] = None,
        workflow_meta: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> "ApiFlow":
        import warnings
        warnings.warn(
            "Workflow() is deprecated — use ApiFlow() instead. "
            "ApiFlow now auto-detects workspace files and converts them automatically.",
            DeprecationWarning,
            stacklevel=2,
        )
        if args or kwargs:
            raise TypeError("Workflow accepts a single positional arg (x) plus keyword args")

        return ApiFlow(
            x,
            auto_convert=auto_convert,
            node_info=node_info,
            server_url=server_url,
            timeout=timeout,
            include_meta=include_meta,
            convert_callbacks=convert_callbacks,
            map_callbacks=map_callbacks,
            in_place=in_place,
            use_api=use_api,
            workflow_meta=workflow_meta,
        )

    @classmethod
    def load(
        cls,
        x: Union[str, Path, bytes, Dict[str, Any]],
        *,
        auto_convert: bool = True,
        node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
        server_url: Optional[str] = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        include_meta: bool = DEFAULT_INCLUDE_META,
        convert_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        map_callbacks: Optional[Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]]] = None,
        in_place: bool = False,
        use_api: Optional[bool] = None,
        workflow_meta: Optional[Dict[str, Any]] = None,
    ) -> Union["Flow", "ApiFlow"]:
        return cls(
            x,
            auto_convert=auto_convert,
            node_info=node_info,
            server_url=server_url,
            timeout=timeout,
            include_meta=include_meta,
            convert_callbacks=convert_callbacks,
            map_callbacks=map_callbacks,
            in_place=in_place,
            use_api=use_api,
            workflow_meta=workflow_meta,
        )


class NodeInfo(dict):
    """node_info dict subclass with drilling and find helpers."""

    @property
    def source(self) -> Optional[str]:
        """
        Short string describing where this node_info came from.

        Examples: "modules", "server:http://localhost:8188", "file:/abs/node_info.json",
        "json-string", "json-bytes", "dict", "env:modules", ...
        """
        v = getattr(self, "_autoflow_source", None)
        if isinstance(v, str) and v:
            return v
        # Back-compat: derive a best-effort source from origin metadata if present.
        o = getattr(self, "_autoflow_origin", None)
        try:
            req = getattr(o, "requested", None)
            res = getattr(o, "resolved", None)
            via_env = bool(getattr(o, "via_env", False))
            eff = getattr(o, "effective_server_url", None)
            base: Optional[str] = None
            if res == "modules":
                mroot = getattr(o, "modules_root", None)
                if isinstance(mroot, str) and mroot:
                    base = f"modules:{mroot}"
                else:
                    base = "modules"
            elif res == "server":
                base = f"server:{eff}" if isinstance(eff, str) and eff else "server"
            elif res == "file":
                if isinstance(req, str) and req:
                    try:
                        base = f"file:{Path(req).expanduser().resolve()}"
                    except Exception:
                        base = f"file:{req}"
                else:
                    base = "file"
            elif res == "url":
                base = f"url:{req}" if isinstance(req, str) and req else "url"
            elif res == "dict":
                base = "dict"
            if via_env and base:
                return f"env:{base}"
            return base
        except Exception:
            return None

    @property
    def origin(self):
        """
        Best-effort origin metadata (if available).

        When NodeInfo is created via autoflow's resolvers/fetch helpers, this is set to an
        `NodeInfoOrigin` instance. For manually constructed dicts, this may be missing/None.
        """
        return getattr(self, "_autoflow_origin", None)

    @classmethod
    def from_comfyui_modules(cls) -> "NodeInfo":
        from .origin import NodeInfoOrigin
        from .convert import _detect_comfyui_root_from_imports

        oi = cls(node_info_from_comfyui_modules())
        root = _detect_comfyui_root_from_imports()
        setattr(oi, "_autoflow_origin", NodeInfoOrigin(requested="modules", resolved="modules", modules_root=str(root) if root else None))
        setattr(oi, "_autoflow_source", f"modules:{root}" if root else "modules")
        return oi

    @classmethod
    def fetch(
        cls,
        server_url: Optional[str] = None,
        *,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        output_path: Optional[Union[str, Path]] = None,
    ) -> "NodeInfo":
        from .net import resolve_comfy_server_url
        from .origin import NodeInfoOrigin

        effective_url = resolve_comfy_server_url(server_url)
        data = fetch_node_info(effective_url, timeout=timeout)
        oi = cls(data)
        setattr(oi, "_autoflow_origin", NodeInfoOrigin(requested=server_url or "server_url", resolved="server", effective_server_url=effective_url))
        setattr(oi, "_autoflow_source", f"server:{effective_url}")
        if output_path is not None:
            oi.save(output_path)
        return oi

    @classmethod
    def load(cls, x: Union[str, Path, bytes, Dict[str, Any]]) -> "NodeInfo":
        from .origin import NodeInfoOrigin

        data: Any
        origin: Optional[NodeInfoOrigin] = None
        source: Optional[str] = None
        if isinstance(x, dict):
            data = x
            origin = NodeInfoOrigin(requested="dict", resolved="dict")
            source = "dict"
        elif isinstance(x, (bytes, bytearray)):
            data = json.loads(bytes(x).decode("utf-8"))
            origin = NodeInfoOrigin(requested="bytes", resolved="dict")
            source = "json-bytes"
        elif isinstance(x, Path):
            data = json.loads(x.read_text(encoding="utf-8"))
            origin = NodeInfoOrigin(requested=str(x), resolved="file")
            try:
                source = f"file:{x.expanduser().resolve()}"
            except Exception:
                source = f"file:{x}"
        elif isinstance(x, str):
            if looks_like_json(x):
                data = json.loads(x)
                origin = NodeInfoOrigin(requested="json", resolved="dict")
                source = "json-string"
            elif Path(x).exists():
                data = json.loads(Path(x).read_text(encoding="utf-8"))
                origin = NodeInfoOrigin(requested=x, resolved="file")
                try:
                    source = f"file:{Path(x).expanduser().resolve()}"
                except Exception:
                    source = f"file:{x}"
            else:
                data = json.loads(x)
                origin = NodeInfoOrigin(requested="json", resolved="dict")
                source = "json-string"
        else:
            raise TypeError("x must be a dict, bytes, Path, or str (file path or JSON string)")

        if not isinstance(data, dict):
            raise ValueError("node_info must be a dict at top level")
        oi = cls(data)
        if origin is not None:
            setattr(oi, "_autoflow_origin", origin)
        if isinstance(source, str) and source:
            setattr(oi, "_autoflow_source", source)
        return oi

    def to_json(self, indent: int = DEFAULT_JSON_INDENT, ensure_ascii: bool = DEFAULT_JSON_ENSURE_ASCII) -> str:
        return json.dumps(self, indent=indent, ensure_ascii=ensure_ascii) + "\n"

    def save(self, output_path: Union[str, Path], indent: int = DEFAULT_JSON_INDENT, ensure_ascii: bool = DEFAULT_JSON_ENSURE_ASCII) -> Path:
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.to_json(indent=indent, ensure_ascii=ensure_ascii), encoding="utf-8")
        return out_path

    def find(
        self,
        q: Optional[str] = None,
        *,
        class_type: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> List[DictView]:
        out: List[DictView] = []
        q2 = q.lower() if isinstance(q, str) and q else None
        ct2 = class_type.lower() if isinstance(class_type, str) and class_type else None
        dn2 = display_name.lower() if isinstance(display_name, str) and display_name else None

        for k, v in self.items():
            if not isinstance(k, str) or not isinstance(v, dict):
                continue
            k_l = k.lower()
            disp = v.get("display_name")
            disp_l = disp.lower() if isinstance(disp, str) else None

            if ct2 is not None and k_l != ct2:
                continue
            if dn2 is not None and disp_l != dn2:
                continue
            if q2 is not None:
                if q2 not in k_l and (disp_l is None or q2 not in disp_l):
                    continue

            dv = DictView(v)
            object.__setattr__(dv, "_autoflow_addr", k)
            out.append(dv)
        return out

    def __getitem__(self, key):
        if isinstance(key, str) and "/" in key:
            parts = key.split("/")
            if not parts:
                raise KeyError(key)

            # Important: avoid self.__getitem__ recursion for intermediate parts, otherwise we may
            # wrap dicts into DictView and then fail isinstance(d, dict) checks during traversal.
            d: Any = super().__getitem__(parts[0])
            for part in parts[1:]:
                if not isinstance(d, dict):
                    raise KeyError(f"Cannot navigate into non-dict at '{part}'")
                if part not in d:
                    raise KeyError(f"Key '{part}' not found")
                d = d[part]
            if isinstance(d, dict):
                return DictView(d)
            return d
        val = super().__getitem__(key)
        if isinstance(val, dict):
            return DictView(val)
        return val

    def __getattr__(self, name: str) -> DictView:
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            val = self[name]
            if isinstance(val, dict):
                return DictView(val)
            return val
        except KeyError:
            raise AttributeError(f"No class_type '{name}'")


__all__ = [
    "DictView",
    "ListView",
    "NodeProxy",
    "NodeGroup",
    "FlowNodeProxy",
    "FlowNodeGroup",
    "FlowNodesView",
    "ApiFlow",
    "Flow",
    "Workflow",
    "NodeInfo",
    # re-export error/result types for convenience (legacy surface)
    "WorkflowConverterError",
    "NodeInfoError",
    "ErrorSeverity",
    "ErrorCategory",
    "ConversionError",
    "ConversionResult",
    "ConvertResult",
]


