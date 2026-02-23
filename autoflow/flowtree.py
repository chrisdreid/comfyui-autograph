"""autoflow.flowtree

Navigation-first model wrappers (non-dict subclasses).

This is the default model layer (``AUTOFLOW_MODEL_LAYER=flowtree``).
"""

from __future__ import annotations

import json
import os
import warnings
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from functools import wraps

from . import models as _legacy
from .convert import normalize_server_url, WorkflowConverterError, resolve_node_info_with_origin
from .defaults import DEFAULT_HTTP_TIMEOUT_S
from .defaults import (
    DEFAULT_FETCH_IMAGES,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_QUEUE_POLL_INTERVAL_S,
    DEFAULT_SUBMIT_CLIENT_ID,
    DEFAULT_SUBMIT_WAIT,
    DEFAULT_WAIT_TIMEOUT_S,
)
from .defaults import DEFAULT_JSON_ENSURE_ASCII, DEFAULT_JSON_INDENT


def _wrap(v: Any, *, path: str) -> "Tree":
    return Tree(v, path=path)


class Tree:
    """
    Terminal-first tree view over any python object (dict/list/scalars), with in-place mutation.
    """

    __slots__ = ("_v", "_path")

    def __init__(self, v: Any, *, path: str = ""):
        self._v = v
        self._path = path

    @property
    def v(self) -> Any:
        return self._v

    def path(self) -> str:
        return self._path

    def unwrap(self) -> Any:
        return self._v

    def attrs(self) -> List[str]:
        if isinstance(self._v, dict):
            return sorted({str(k) for k in self._v.keys()})
        if isinstance(self._v, list):
            return [str(i) for i in range(len(self._v))]
        return []

    def ls(self) -> List[Tuple[str, str]]:
        """
        List children as (name, summary) tuples.
        """
        out: List[Tuple[str, str]] = []
        if isinstance(self._v, dict):
            for k in sorted(self._v.keys(), key=lambda x: str(x)):
                vv = self._v[k]
                out.append((str(k), _summary(vv)))
        elif isinstance(self._v, list):
            for i, vv in enumerate(self._v):
                out.append((str(i), _summary(vv)))
        return out

    def find(self, *, key: Optional[str] = None, value: Any = None, depth: int = 6) -> List["Tree"]:
        """
        Very small generic finder:
        - key only: match any dict key name (depth-first)
        - key+value: match key with expected value (regex supported if value has .search)
        """
        want_key = str(key) if key is not None else None

        def _is_re(x: Any) -> bool:
            return hasattr(x, "search") and callable(getattr(x, "search"))

        def _match(expected: Any, candidate: Any) -> bool:
            if expected == "*":
                return True
            if _is_re(expected):
                try:
                    return bool(expected.search(str(candidate)))
                except Exception:
                    return False
            return expected == candidate

        out: List[Tree] = []
        stack: List[Tuple[Any, str, int]] = [(self._v, self._path, 0)]
        seen: set[int] = set()
        while stack:
            cur, pth, lvl = stack.pop()
            try:
                cid = id(cur)
                if cid in seen:
                    continue
                seen.add(cid)
            except Exception:
                pass

            if isinstance(cur, dict):
                for k, vv in cur.items():
                    kp = f"{pth}/{k}" if pth else str(k)
                    if want_key is None or str(k) == want_key:
                        if value is None:
                            out.append(Tree(vv, path=kp))
                        elif _match(value, vv):
                            out.append(Tree(vv, path=kp))
                    if lvl < depth and isinstance(vv, (dict, list)):
                        stack.append((vv, kp, lvl + 1))
            elif isinstance(cur, list):
                for i, vv in enumerate(cur):
                    kp = f"{pth}/{i}" if pth else str(i)
                    if lvl < depth and isinstance(vv, (dict, list)):
                        stack.append((vv, kp, lvl + 1))
        return out

    def __getitem__(self, key: Any) -> "Tree":
        if isinstance(self._v, dict):
            vv = self._v[key]
            kp = f"{self._path}/{key}" if self._path else str(key)
            return _wrap(vv, path=kp)
        if isinstance(self._v, list):
            vv = self._v[int(key)]
            kp = f"{self._path}/{int(key)}" if self._path else str(int(key))
            return _wrap(vv, path=kp)
        raise TypeError("Tree is not indexable (not a dict or list)")

    def __setitem__(self, key: Any, value: Any) -> None:
        if isinstance(self._v, dict):
            self._v[key] = value
            return
        if isinstance(self._v, list):
            self._v[int(key)] = value
            return
        raise TypeError("Tree is not assignable (not a dict or list)")

    def __getattr__(self, name: str) -> "Tree":
        if name.startswith("_"):
            raise AttributeError(name)
        if isinstance(self._v, dict) and name in self._v:
            return self[name]
        raise AttributeError(name)

    def __repr__(self) -> str:
        return f"<Tree path={self._path!r} { _summary(self._v) }>"


def _summary(v: Any) -> str:
    if isinstance(v, dict):
        keys = list(v.keys())
        head = ", ".join([repr(str(k)) for k in keys[:4]])
        more = "" if len(keys) <= 4 else f", +{len(keys)-4}"
        return f"dict({len(keys)})[{head}{more}]"
    if isinstance(v, list):
        return f"list({len(v)})"
    if isinstance(v, (str, int, float, bool)) or v is None:
        s = repr(v)
        return s if len(s) <= 80 else (s[:77] + "...")
    return type(v).__name__


class _MappingWrapper(MutableMapping):
    """
    Base mapping wrapper with an attached Tree view.
    """

    _data: Dict[str, Any]

    def tree(self) -> Tree:
        return Tree(self._data, path="")

    def unwrap(self) -> Dict[str, Any]:
        return self._data

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    def to_json(self, *, indent: int = DEFAULT_JSON_INDENT, ensure_ascii: bool = DEFAULT_JSON_ENSURE_ASCII) -> str:
        # Prefer underlying implementation for exact parity (e.g. trailing newline and formatting).
        tj = getattr(self._data, "to_json", None)
        if callable(tj):
            try:
                return tj(indent=indent, ensure_ascii=ensure_ascii)
            except TypeError:
                return tj()
        return json.dumps(self._data, indent=indent, ensure_ascii=ensure_ascii) + "\n"

    def save(self, output_path: Union[str, Path], *, indent: int = DEFAULT_JSON_INDENT, ensure_ascii: bool = DEFAULT_JSON_ENSURE_ASCII) -> Path:
        sv = getattr(self._data, "save", None)
        if callable(sv):
            try:
                return sv(output_path, indent=indent, ensure_ascii=ensure_ascii)
            except TypeError:
                return sv(output_path)
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(indent=indent, ensure_ascii=ensure_ascii), encoding="utf-8")
        return p

    # MutableMapping protocol
    def __getitem__(self, k: Any) -> Any:
        return self._data[k]

    def __setitem__(self, k: Any, v: Any) -> None:
        self._data[k] = v

    def __delitem__(self, k: Any) -> None:
        del self._data[k]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class ApiFlow(_MappingWrapper):
    def __init__(self, x: Optional[Union[str, Path, bytes, Dict[str, Any], _legacy.ApiFlow]] = None, **kwargs: Any):
        if "server_url" in kwargs:
            kwargs["server_url"] = normalize_server_url(
                kwargs.get("server_url"),
                allow_env=False,
                allow_none=True,
            )
        timeout = kwargs.get("timeout", DEFAULT_HTTP_TIMEOUT_S)
        if "node_info" in kwargs:
            oi_in = kwargs.get("node_info")
            # Preserve source metadata when caller passes flowtree NodeInfo.
            if isinstance(oi_in, NodeInfo):
                kwargs["node_info"] = oi_in._oi
        else:
            # Auto-resolve from env (and keep env provenance in NodeInfo.source).
            oi_dict, _use_api, origin = resolve_node_info_with_origin(
                None,
                kwargs.get("server_url"),
                timeout,
                allow_env=True,
                require_source=False,
            )
            if oi_dict is not None:
                oi_obj = _legacy.NodeInfo(oi_dict)
                setattr(oi_obj, "_autoflow_origin", origin)
                s = getattr(oi_obj, "source", None)
                if isinstance(s, str) and s:
                    setattr(oi_obj, "_autoflow_source", s)
                kwargs["node_info"] = oi_obj
        api = x if isinstance(x, _legacy.ApiFlow) else _legacy.ApiFlow(x, **kwargs)
        self._api = api
        self._data = api  # underlying is a dict subclass

    @classmethod
    def load(cls, x: Union[str, Path, bytes, Dict[str, Any]], **kwargs: Any) -> "ApiFlow":
        return cls(x, **kwargs)

    @property
    def node_info(self):
        return getattr(self._api, "node_info", None)

    @property
    def use_api(self):
        return getattr(self._api, "use_api", None)

    @property
    def workflow_meta(self):
        return getattr(self._api, "workflow_meta", None)

    @property
    def source(self):
        return getattr(self._api, "source", None)

    def find(self, **kwargs: Any):
        return NodeSet.from_apiflow_find(self, **kwargs)

    def by_id(self, node_id: Union[str, int]) -> "NodeRef":
        nid = str(node_id)
        node = self._api.get(nid)
        if not isinstance(node, dict):
            raise KeyError(nid)
        p = _legacy.NodeProxy(node, nid, self._api)
        object.__setattr__(p, "_autoflow_addr", nid)
        return NodeRef(p, kind="api", addr=nid, group=None, index=None, dotpath=f'by_id("{nid}")', dictpath=[nid])

    def submit(self, *args: Any, **kwargs: Any):
        return self._api.submit(*args, **kwargs)

    def execute(
        self,
        *,
        client_id: str = DEFAULT_SUBMIT_CLIENT_ID,
        extra: Optional[Dict[str, Any]] = None,
        on_event: Optional[Any] = None,
        init_extra_nodes: bool = False,
        cleanup: bool = True,
    ):
        """
        Execute this ApiFlow **serverlessly** (in-process ComfyUI node execution).

        Notes:
        - This does not call the ComfyUI HTTP API. If you want to run against a server,
          use `submit(server_url=...)` instead.
        - This requires ComfyUI to be importable in the current Python environment.
        """
        from .inprocess import execute_prompt

        return execute_prompt(
            dict(self._api),
            client_id=client_id,
            extra=extra,
            on_event=on_event,
            init_extra_nodes=bool(init_extra_nodes),
            cleanup=bool(cleanup),
        )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        # NodeSet access by class_type (case-insensitive)
        want = name.lower()
        matches: List[Tuple[str, Dict[str, Any]]] = []
        for nid, n in self._api.items():
            if isinstance(n, dict) and n.get("class_type", "").lower() == want:
                matches.append((str(nid), n))
        if not matches:
            raise AttributeError(f"No nodes with class_type '{name}'")
        return NodeSet.from_apiflow_group(self, group_name=name, matches=matches)

    def __dir__(self) -> List[str]:
        base = {"find", "by_id", "submit", "execute", "save", "to_json", "to_dict",
                "node_info", "dag", "items", "keys", "values"}
        for _nid, n in self._api.items():
            if isinstance(n, dict):
                ct = n.get("class_type")
                if isinstance(ct, str) and ct:
                    base.add(ct)
        return sorted(base)

    def _as_dict(self) -> Dict[str, Any]:
        """Build {Type[i]: widget_dict} for all nodes."""
        groups: Dict[str, Any] = {}
        type_counts: Dict[str, int] = {}
        for nid, node in self._api.items():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type", f"node_{nid}")
            i = type_counts.get(ct, 0)
            type_counts[ct] = i + 1
            key = f"{ct}[{i}]"
            inputs = node.get("inputs", {})
            widgets = {k: v for k, v in inputs.items() if not isinstance(v, list)} if isinstance(inputs, dict) else {}
            groups[key] = widgets
        return groups

    def items(self) -> list:
        return list(self._as_dict().items())

    def keys(self) -> list:
        return list(self._as_dict().keys())

    def values(self) -> list:
        return list(self._as_dict().values())

    def __repr__(self) -> str:
        return f"ApiFlow({repr(self._as_dict())})"

    @property
    def dag(self):
        # Delegate to legacy models.ApiFlow implementation
        return getattr(self._api, "dag")


class Flow(_MappingWrapper):
    def __init__(self, x: Optional[Union[str, Path, bytes, Dict[str, Any], _legacy.Flow]] = None, **kwargs: Any):
        if "server_url" in kwargs:
            kwargs["server_url"] = normalize_server_url(
                kwargs.get("server_url"),
                allow_env=False,
                allow_none=True,
            )
        timeout = kwargs.get("timeout", DEFAULT_HTTP_TIMEOUT_S)
        if "node_info" in kwargs:
            oi_in = kwargs.get("node_info")
            if isinstance(oi_in, NodeInfo):
                kwargs["node_info"] = oi_in._oi
        else:
            oi_dict, _use_api, origin = resolve_node_info_with_origin(
                None,
                kwargs.get("server_url"),
                timeout,
                allow_env=True,
                require_source=False,
            )
            if oi_dict is not None:
                oi_obj = _legacy.NodeInfo(oi_dict)
                setattr(oi_obj, "_autoflow_origin", origin)
                s = getattr(oi_obj, "source", None)
                if isinstance(s, str) and s:
                    setattr(oi_obj, "_autoflow_source", s)
                kwargs["node_info"] = oi_obj
        f = x if isinstance(x, _legacy.Flow) else _legacy.Flow(x, **kwargs)
        self._flow = f
        self._data = f
        # Warn if node_info could not be resolved.
        if getattr(f, "node_info", None) is None:
            warnings.warn(
                "Flow created without node_info — widget access and tab completion will be limited.\n"
                "Options:\n"
                "  • Set AUTOFLOW_COMFYUI_SERVER_URL env var (auto-fetches)\n"
                "  • Pass node_info= to Flow()\n"
                "  • Call flow.fetch_node_info(server_url=...)\n"
                "See: docs/node-info-and-env.md",
                UserWarning, stacklevel=2,
            )

    @classmethod
    def load(cls, x: Union[str, Path, bytes, Dict[str, Any]], **kwargs: Any) -> "Flow":
        return cls(x, **kwargs)

    @property
    def nodes(self):
        return FlowTreeNodesView(self)

    @property
    def node_info(self):
        return getattr(self._flow, "node_info", None)

    @property
    def workflow_meta(self):
        return getattr(self._flow, "workflow_meta", None)

    @property
    def source(self):
        return getattr(self._flow, "source", None)

    def find(self, **kwargs: Any):
        return self.nodes.find(**kwargs)

    def fetch_node_info(self, *args: Any, **kwargs: Any):
        return self._flow.fetch_node_info(*args, **kwargs)

    def convert(self, *args: Any, **kwargs: Any) -> ApiFlow:
        kwargs.setdefault("include_meta", True)
        api = self._flow.convert(*args, **kwargs)
        return ApiFlow(api)

    def convert_with_errors(self, *args: Any, **kwargs: Any):
        return self._flow.convert_with_errors(*args, **kwargs)

    def submit(self, *args: Any, **kwargs: Any):
        return self._flow.submit(*args, **kwargs)

    def execute(
        self,
        *,
        node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT_S,
        include_meta: bool = False,
        convert_callbacks: Optional[Any] = None,
        map_callbacks: Optional[Any] = None,
        client_id: str = DEFAULT_SUBMIT_CLIENT_ID,
        extra: Optional[Dict[str, Any]] = None,
        on_event: Optional[Any] = None,
        init_extra_nodes: bool = False,
        cleanup: bool = True,
    ):
        """
        Execute this Flow **serverlessly** (in-process ComfyUI node execution).

        This converts the workspace `Flow` to an API payload and then executes the ComfyUI
        nodes in-process. If you want to run against a running ComfyUI server, use
        `submit(server_url=...)` instead.
        """
        api = self._flow.convert(
            node_info=node_info,
            server_url=None,
            timeout=timeout,
            include_meta=include_meta,
            convert_callbacks=convert_callbacks,
            map_callbacks=map_callbacks,
        )
        from .inprocess import execute_prompt

        return execute_prompt(
            dict(api),
            client_id=client_id,
            extra=extra,
            on_event=on_event,
            init_extra_nodes=bool(init_extra_nodes),
            cleanup=bool(cleanup),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._flow, name)

    def __repr__(self) -> str:
        nv = self.nodes
        links = self._flow.get("links", [])
        link_count = len(links) if isinstance(links, list) else 0
        return f"Flow(nodes={repr(nv._as_dict())}, links={link_count})"

    @property
    def dag(self):
        # Delegate to legacy models.Flow implementation
        return getattr(self._flow, "dag")


class NodeInfo(_MappingWrapper):
    def __init__(self, x: Optional[Union[str, Path, bytes, Dict[str, Any], _legacy.NodeInfo]] = None, **kwargs: Any):
        """
        Create an NodeInfo wrapper.

        Supported inputs (x or source=):
        - dict-like node_info
        - file path to node_info.json
        - URL to a JSON node_info
        - "modules" / "from_comfyui_modules" to load from local ComfyUI modules
        - "fetch" / "server" when server_url (or AUTOFLOW_COMFYUI_SERVER_URL) is available

        Default behavior (when x and source are omitted):
        - If AUTOFLOW_NODE_INFO_SOURCE (or server_url) is set, node_info is auto-resolved.
        - Otherwise, an empty NodeInfo is created (no error).
        """
        source = kwargs.pop("source", None)
        server_url = kwargs.pop("server_url", None)
        timeout = kwargs.pop("timeout", DEFAULT_HTTP_TIMEOUT_S)
        allow_env = kwargs.pop("allow_env", True)

        # Prefer explicit x over source= for backwards compatibility.
        inp = x if x is not None else source

        if isinstance(inp, _legacy.NodeInfo):
            oi = inp
        elif inp is not None:
            oi_dict, _use_api, origin = resolve_node_info_with_origin(
                inp,
                server_url,
                timeout,
                allow_env=False,
                require_source=True,
            )
            if oi_dict is None:  # pragma: no cover (resolver should raise when require_source=True)
                raise WorkflowConverterError("Missing node_info source. Pass source= or set AUTOFLOW_NODE_INFO_SOURCE.")
            oi = _legacy.NodeInfo(oi_dict)
            setattr(oi, "_autoflow_origin", origin)
            s = getattr(oi, "source", None)
            if isinstance(s, str) and s:
                setattr(oi, "_autoflow_source", s)
        else:
            oi_dict, _use_api, origin = resolve_node_info_with_origin(
                None,
                server_url,
                timeout,
                allow_env=bool(allow_env),
                require_source=False,
            )
            if oi_dict is None:
                oi = _legacy.NodeInfo({})
            else:
                oi = _legacy.NodeInfo(oi_dict)
                setattr(oi, "_autoflow_origin", origin)
                s = getattr(oi, "source", None)
                if isinstance(s, str) and s:
                    setattr(oi, "_autoflow_source", s)

        self._oi = oi
        self._data = oi

    @classmethod
    def load(cls, x: Union[str, Path, bytes, Dict[str, Any]], **kwargs: Any) -> "NodeInfo":
        return cls(x, **kwargs)

    @classmethod
    def from_comfyui_modules(cls) -> "NodeInfo":
        return cls(_legacy.NodeInfo.from_comfyui_modules())

    class _DualMethod:
        """
        Descriptor that acts like a classmethod when accessed on the class,
        and like an instance method when accessed on an instance.
        """

        def __init__(self, class_func, inst_func):
            self._class_func = class_func
            self._inst_func = inst_func
            try:
                self.__doc__ = getattr(class_func, "__doc__", None)
            except Exception:
                pass

        def __get__(self, obj, objtype=None):
            if obj is None:
                @wraps(self._class_func)
                def _bound(*args, **kwargs):
                    return self._class_func(objtype, *args, **kwargs)

                return _bound

            @wraps(self._inst_func)
            def _bound(*args, **kwargs):
                return self._inst_func(obj, *args, **kwargs)

            return _bound

    @staticmethod
    def _fetch_new(cls, *args: Any, **kwargs: Any) -> "NodeInfo":
        """Fetch node_info from server and return a new NodeInfo."""
        return cls(_legacy.NodeInfo.fetch(*args, **kwargs))

    @staticmethod
    def _fetch_inplace(self, *args: Any, **kwargs: Any) -> "NodeInfo":
        """Fetch node_info from server and update this instance in-place."""
        oi = _legacy.NodeInfo.fetch(*args, **kwargs)
        self._oi = oi
        self._data = oi
        return self

    fetch = _DualMethod(_fetch_new, _fetch_inplace)

    def find(self, *args: Any, **kwargs: Any):
        return self._oi.find(*args, **kwargs)

    @property
    def source(self):
        return getattr(self._oi, "source", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._oi, name)

    def __repr__(self) -> str:
        count = len(self._oi)
        types = sorted(self._oi.keys())[:10]
        more = f", +{count - 10} more" if count > 10 else ""
        return f"NodeInfo(count={count}, types={types!r}{more})"


class Workflow:
    """Deprecated – use ``ApiFlow`` directly.

    ``ApiFlow`` now auto-detects workspace-format JSON and converts it
    automatically.  This class is kept for backward compatibility and
    will be removed in a future release.
    """

    def __new__(cls, *args: Any, **kwargs: Any):
        import warnings
        warnings.warn(
            "Workflow() is deprecated — use ApiFlow() instead. "
            "ApiFlow now auto-detects workspace files and converts them automatically.",
            DeprecationWarning,
            stacklevel=2,
        )
        return ApiFlow(*args, **kwargs)

    @classmethod
    def load(cls, *args: Any, **kwargs: Any):
        return cls(*args, **kwargs)


__all__ = ["Tree", "Flow", "ApiFlow", "NodeInfo", "Workflow"]


# ---------------------------------------------------------------------------
# FlowTree-native navigation types
# ---------------------------------------------------------------------------


class _CallableStr(str):
    def __call__(self) -> str:
        return str(self)


class _CallableList(list):
    def __call__(self):
        return self


class NodeRef:
    """
    Wrap a single legacy proxy (FlowNodeProxy or NodeProxy) and provide path metadata.
    """

    __slots__ = ("_p", "kind", "addr", "group", "index", "path", "where", "dictpath")

    def __init__(
        self,
        proxy: Any,
        *,
        kind: str,
        addr: str,
        group: Optional[str],
        index: Optional[int],
        dotpath: str,
        dictpath: List[Any],
    ):
        object.__setattr__(self, "_p", proxy)
        self.kind = kind
        self.addr = addr
        self.group = group
        self.index = index
        # Legacy-compatible identity path (node id / address).
        self.path = _CallableStr(str(addr))
        # Human-friendly navigation path (used for printing / terminal UX).
        self.where = _CallableStr(dotpath)
        self.dictpath = _CallableList(dictpath)

    def _data_ref(self) -> Dict[str, Any]:
        """
        Return a best-effort *live* dict reference for this node.
        """
        try:
            gd = getattr(self._p, "_get_data", None)
            if callable(gd):
                d = gd()
                if isinstance(d, dict):
                    return d
        except Exception:
            pass
        if isinstance(self._p, dict):
            return self._p
        # Fallback may be a copy; prefer callers to use proxy attribute access.
        return self.unwrap()

    def unwrap(self) -> Dict[str, Any]:
        if hasattr(self._p, "to_dict"):
            return self._p.to_dict()
        return dict(self._p)

    @property
    def type(self) -> str:
        """
        Unified node type accessor:
        - Flow node: workspace `type`
        - Api node: `class_type`
        """
        try:
            if self.kind == "flow":
                return str(getattr(self._p, "type"))
            if self.kind == "api":
                return str(getattr(self._p, "class_type"))
        except Exception:
            pass
        d = self.unwrap()
        if self.kind == "api":
            return str(d.get("class_type", ""))
        return str(d.get("type", ""))

    @property
    def title(self) -> str:
        """
        Best-effort node display name/title.
        """
        d = self.unwrap()
        if self.kind == "api":
            m = d.get("_meta")
            if isinstance(m, dict) and isinstance(m.get("title"), str):
                return m.get("title") or ""
            return ""
        # workspace
        t1 = d.get("title")
        if isinstance(t1, str) and t1:
            return t1
        props = d.get("properties")
        if isinstance(props, dict):
            t2 = props.get("Node name for S&R")
            if isinstance(t2, str) and t2:
                return t2
        return ""

    def to_dict(self) -> Dict[str, Any]:
        return self.unwrap()

    def attrs(self) -> List[str]:
        if hasattr(self._p, "attrs"):
            try:
                return list(self._p.attrs())
            except Exception:
                pass
        # fallback: raw keys
        d = self.unwrap()
        return sorted({str(k) for k in d.keys()})

    def tree(self) -> Tree:
        return Tree(self.unwrap(), path=self.where)

    def __getattr__(self, name: str) -> Any:
        if name in ("_meta", "meta"):
            d = self._data_ref()
            m = d.get("_meta")
            if not isinstance(m, dict):
                m = {}
                d["_meta"] = m
            return _legacy.DictView(m)
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._p, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_p", "kind", "addr", "group", "index", "path", "where", "dictpath"):
            return object.__setattr__(self, name, value)
        if name in ("_meta", "meta"):
            self._data_ref()["_meta"] = value
            return
        return setattr(self._p, name, value)

    # mapping protocol (so dict(node) works)
    def __len__(self) -> int:
        try:
            return len(self._p)
        except Exception:
            return len(self.unwrap())

    def __iter__(self):
        try:
            return iter(self._p)
        except Exception:
            return iter(self.unwrap())

    def __getitem__(self, key):
        try:
            return self._p[key]
        except Exception:
            return self.unwrap()[key]

    def __dir__(self) -> List[str]:
        base = {"attrs", "choices", "tooltip", "spec", "tree", "to_dict", "unwrap",
                "type", "title", "where", "meta"}
        try:
            base.update(self._widget_dict().keys())
        except Exception:
            pass
        return sorted(base)

    def _widget_names(self) -> List[str]:
        """Return only user-editable widget input names (not links or raw keys)."""
        try:
            parent = object.__getattribute__(self._p, "_parent")
            ni = getattr(parent, "node_info", None)
            if ni is not None:
                return list(_legacy.get_widget_input_names(
                    self.type, node_info=ni, use_api=True
                ))
        except Exception:
            pass
        # Fallback: inputs that aren't lists (links are [node_id, slot])
        try:
            d = self.unwrap()
            inputs = d.get("inputs") if isinstance(d, dict) else None
            if isinstance(inputs, dict):
                return [k for k, v in inputs.items() if not isinstance(v, list)]
        except Exception:
            pass
        return []

    def _widget_dict(self) -> Dict[str, Any]:
        """Return a {name: value} dict of widget values, empty if no node_info."""
        try:
            names = self._widget_names()
            if not names:
                return {}
            return {n: getattr(self._p, n, None) for n in names}
        except Exception:
            return {}

    def __repr__(self) -> str:
        w = self._widget_dict()
        return repr({str(self.where): w})

    def __str__(self) -> str:
        return self.__repr__()


class NodeSet:
    """
    A set/selection of nodes.

    - assignment sets on first node
    - .set() sets on all nodes
    """

    __slots__ = ("_nodes", "_kind", "_set_path", "_set_dictpath")

    def __init__(self, nodes: List[NodeRef], *, kind: str, set_path: str, set_dictpath: List[Any]):
        self._nodes = nodes
        self._kind = kind
        self._set_path = set_path
        self._set_dictpath = set_dictpath

    def __len__(self) -> int:
        return len(self._nodes)

    def __iter__(self) -> Iterator[NodeRef]:
        return iter(self._nodes)

    def __getitem__(self, idx: int) -> NodeRef:
        return self._nodes[idx]

    def first(self) -> NodeRef:
        if not self._nodes:
            raise IndexError("NodeSet is empty")
        return self._nodes[0]

    def attrs(self, *, mode: str = "union") -> List[str]:
        if not self._nodes:
            return []
        if mode not in ("union", "intersection"):
            raise ValueError("mode must be 'union' or 'intersection'")
        sets = [set(n.attrs()) for n in self._nodes]
        if mode == "intersection":
            out = set.intersection(*sets) if sets else set()
        else:
            out = set.union(*sets) if sets else set()
        return sorted(out)

    def paths(self) -> List[str]:
        return [n.where for n in self._nodes]

    def dictpaths(self) -> List[List[Any]]:
        return [list(n.dictpath) for n in self._nodes]

    def to_list(self) -> List[NodeRef]:
        return list(self._nodes)

    def to_dict(self) -> Dict[str, NodeRef]:
        return {str(n.addr): n for n in self._nodes}

    def set(self, **kwargs: Any) -> "NodeSet":
        for n in self._nodes:
            for k, v in kwargs.items():
                setattr(n, k, v)
        return self

    def apply(self, fn) -> "NodeSet":
        for n in self._nodes:
            fn(n)
        return self

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.first(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_nodes", "_kind", "_set_path", "_set_dictpath"):
            return object.__setattr__(self, name, value)
        # assignment targets the first node only
        setattr(self.first(), name, value)

    def __dir__(self) -> List[str]:
        base = {"set", "apply", "first", "attrs", "find", "items", "keys", "values"}
        try:
            # Show only widget names (not link inputs or raw node keys)
            for n in self._nodes:
                base.update(n._widget_dict().keys())
        except Exception:
            pass
        return sorted(base)

    def __repr__(self) -> str:
        # Always show widget dicts for all nodes (consistent dict format)
        combined: Dict[str, Any] = {}
        for n in self._nodes:
            combined[str(n.where)] = n._widget_dict()
        return repr(combined)

    def __str__(self) -> str:
        return self.__repr__()

    @staticmethod
    def from_apiflow_group(api: ApiFlow, *, group_name: str, matches: List[Tuple[str, Dict[str, Any]]]) -> "NodeSet":
        nodes: List[NodeRef] = []
        for i, (nid, node) in enumerate(matches):
            p = _legacy.NodeProxy(node, nid, api._api)
            object.__setattr__(p, "_autoflow_addr", nid)
            dot = f"{group_name}[{i}]"
            nodes.append(NodeRef(p, kind="api", addr=nid, group=group_name, index=i, dotpath=dot, dictpath=[nid]))
        return NodeSet(nodes, kind="api", set_path=group_name, set_dictpath=[group_name])

    @staticmethod
    def from_apiflow_find(api: ApiFlow, **kwargs: Any) -> "NodeSet":
        out = api._api.find(**kwargs)
        nodes: List[NodeRef] = []
        for i, p in enumerate(out):
            addr = p.path()
            nodes.append(NodeRef(p, kind="api", addr=addr, group=None, index=i, dotpath=f'by_id("{addr}")', dictpath=[addr]))
        return NodeSet(nodes, kind="api", set_path="find(...)", set_dictpath=["find(...)"])

    @staticmethod
    def from_flow_find(flow: Flow, proxies: List[Any], *, set_path: str) -> "NodeSet":
        nodes: List[NodeRef] = []
        top_nodes = flow._flow.get("nodes", [])
        for i, p in enumerate(proxies):
            addr = p.path()
            dictpath: List[Any]
            dot: str
            # best-effort top-level index mapping
            idx = None
            if isinstance(top_nodes, list):
                try:
                    idx = top_nodes.index(p.node)  # type: ignore[attr-defined]
                except Exception:
                    idx = None
            if idx is not None:
                dictpath = ["nodes", idx]
                dot = f"nodes[{idx}]"
            else:
                dictpath = ["nodes_by_path", addr]
                dot = f'nodes.by_path("{addr}")'
            nodes.append(NodeRef(p, kind="flow", addr=addr, group=None, index=i, dotpath=dot, dictpath=dictpath))
        return NodeSet(nodes, kind="flow", set_path=set_path, set_dictpath=[set_path])


class FlowTreeNodesView:
    __slots__ = ("_flowtree",)

    def __init__(self, flowtree: Flow):
        self._flowtree = flowtree

    def __dir__(self) -> List[str]:
        base = {"items", "keys", "values", "to_list", "to_dict", "find", "by_path"}
        flow = self._flowtree._flow
        nodes = flow.get("nodes", [])
        if isinstance(nodes, list):
            for n in nodes:
                if isinstance(n, dict):
                    t = n.get("type")
                    if isinstance(t, str) and t:
                        base.add(t)
        return sorted(base)

    def to_list(self) -> List[NodeRef]:
        flow = self._flowtree._flow
        nodes = flow.get("nodes", [])
        if not isinstance(nodes, list):
            return []
        return [self[i] for i in range(len(nodes))]

    def _as_dict(self) -> Dict[str, Any]:
        """Build {nodes.Type[i]: widget_dict} for all nodes."""
        flow = self._flowtree._flow
        nodes = flow.get("nodes", [])
        if not isinstance(nodes, list):
            return {}
        type_counts: Dict[str, int] = {}
        out: Dict[str, Any] = {}
        for idx, n in enumerate(nodes):
            if not isinstance(n, dict):
                continue
            t = n.get("type", f"node_{idx}")
            i = type_counts.get(t, 0)
            type_counts[t] = i + 1
            key = f"nodes.{t}[{i}]"
            try:
                ref = self[idx]
                out[key] = ref._widget_dict()
            except Exception:
                out[key] = {}
        return out

    def __repr__(self) -> str:
        d = self._as_dict()
        if not d:
            return "<FlowTreeNodesView (empty)>"
        return repr(d)

    def __str__(self) -> str:
        return self.__repr__()

    def items(self) -> list:
        return list(self._as_dict().items())

    def keys(self) -> list:
        return list(self._as_dict().keys())

    def values(self) -> list:
        return list(self._as_dict().values())

    def __iter__(self):
        return iter(self._as_dict())

    def __len__(self) -> int:
        flow = self._flowtree._flow
        nodes = flow.get("nodes", [])
        return len(nodes) if isinstance(nodes, list) else 0

    def to_dict(self) -> Dict[str, NodeRef]:
        flow = self._flowtree._flow
        nodes = flow.get("nodes", [])
        if not isinstance(nodes, list):
            return {}
        out: Dict[str, NodeRef] = {}
        for i, n in enumerate(nodes):
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id", i))
            out[nid] = self[i]
        return out

    def __getattr__(self, name: str) -> NodeSet:
        if name.startswith("_"):
            raise AttributeError(name)
        flow = self._flowtree._flow
        nodes = flow.get("nodes", [])
        matches: List[Tuple[int, Dict[str, Any]]] = []
        if isinstance(nodes, list):
            for idx, n in enumerate(nodes):
                if isinstance(n, dict) and n.get("type", "").lower() == name.lower():
                    matches.append((idx, n))
        if not matches:
            raise AttributeError(f"No nodes with type '{name}'")
        refs: List[NodeRef] = []
        for i, (idx, n) in enumerate(matches):
            p = _legacy.FlowNodeProxy(n, idx, flow)
            object.__setattr__(p, "_autoflow_addr", str(n.get("id", idx)))
            refs.append(
                NodeRef(
                    p,
                    kind="flow",
                    addr=str(n.get("id", idx)),
                    group=name,
                    index=i,
                    dotpath=f"nodes.{name}[{i}]",
                    dictpath=["nodes", idx],
                )
            )
        return NodeSet(refs, kind="flow", set_path=f"nodes.{name}", set_dictpath=["nodes", name])

    def by_path(self, addr: str) -> NodeRef:
        flow = self._flowtree._flow
        for node, pth in _legacy._iter_flow_nodes_with_paths(flow, deep=True, max_depth=_legacy.DEFAULT_FIND_MAX_DEPTH):  # type: ignore[attr-defined]
            if pth == addr:
                # best-effort list index
                nodes = flow.get("nodes", [])
                idx = 0
                if isinstance(nodes, list):
                    try:
                        idx = nodes.index(node)
                    except Exception:
                        idx = 0
                proxy = _legacy.FlowNodeProxy(node, idx, flow)
                object.__setattr__(proxy, "_autoflow_addr", pth)
                return NodeRef(proxy, kind="flow", addr=pth, group=None, index=None, dotpath=f'nodes.by_path("{pth}")', dictpath=["nodes_by_path", pth])
        raise KeyError(addr)

    def find(self, **kwargs: Any) -> NodeSet:
        # Delegate to legacy find for correctness (including deep/subgraphs/widget map)
        proxies = self._flowtree._flow.nodes.find(**kwargs)
        return NodeSet.from_flow_find(self._flowtree, proxies, set_path="nodes.find(...)")

    def __getitem__(self, key: Any) -> NodeRef:
        flow = self._flowtree._flow
        nodes = flow.get("nodes", [])
        if isinstance(key, int):
            if isinstance(nodes, list) and 0 <= key < len(nodes) and isinstance(nodes[key], dict):
                n = nodes[key]
                proxy = _legacy.FlowNodeProxy(n, key, flow)
                object.__setattr__(proxy, "_autoflow_addr", str(n.get("id", key)))
                return NodeRef(proxy, kind="flow", addr=str(n.get("id", key)), group=None, index=None, dotpath=f"nodes[{key}]", dictpath=["nodes", key])
            # treat as node id lookup (top-level only)
            if isinstance(nodes, list):
                for idx, n in enumerate(nodes):
                    if isinstance(n, dict) and n.get("id") == key:
                        proxy = _legacy.FlowNodeProxy(n, idx, flow)
                        object.__setattr__(proxy, "_autoflow_addr", str(key))
                        return NodeRef(proxy, kind="flow", addr=str(key), group=None, index=None, dotpath=f"nodes[{key}]", dictpath=["nodes", idx])
            raise KeyError(key)
        if isinstance(key, str):
            # comfy path (supports subgraphs)
            return self.by_path(key)
        raise TypeError("nodes[...] expects int index/id or str comfy path")



