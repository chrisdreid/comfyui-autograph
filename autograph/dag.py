"""autoflow.dag

Stdlib-only DAG helpers for Flow (workspace) and ApiFlow (API payload).

Design goals:
- tiny, ergonomic API (dict subclass + helper methods)
- safe inference: only treat a value as an upstream node ref when it matches a known node id
- stable output ordering (Python 3.7+ dict insertion order)
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple, Union


NodeId = str
Edge = Tuple[NodeId, NodeId]  # (src, dst)

class _PrettyText(str):
    """A str that renders nicely in REPLs by making repr() equal to the text itself."""

    def __repr__(self) -> str:  # pragma: no cover
        return str(self)


class _DagNodes(list):
    __slots__ = ("_dag",)

    def __init__(self, items: Iterable[NodeId], dag: "Dag"):
        super().__init__(items)
        self._dag = dag

    def toposort(self) -> List[NodeId]:
        return self._dag._toposort_nodes()


class _DagEntities(dict):
    __slots__ = ("_dag",)

    def __init__(self, items: Dict[NodeId, Dict[str, Any]], dag: "Dag"):
        super().__init__(items)
        self._dag = dag

    def toposort(self) -> Dict[NodeId, Dict[str, Any]]:
        ordered = self._dag._toposort_nodes()
        return {n: self[n] for n in ordered if n in self}


class Dag(dict):
    """Tiny dict subclass representing a directed acyclic graph (best-effort).

    Underlying structure:
      {
        "kind": "api" | "flow",
        "nodes": [<node_id>, ...],
        "edges": [[src, dst], ...],
        "labels": {node_id: {"class_type": "...", "title": "..."}}
      }
    """

    @property
    def kind(self) -> Optional[str]:
        # Backwards-compat alias (older plans used "kind": "api"/"flow").
        k = self.get("kind")
        return k if isinstance(k, str) else None

    @property
    def cls(self) -> Optional[str]:
        """Graph source class name: 'Flow' or 'ApiFlow'."""
        c = self.get("class")
        return c if isinstance(c, str) else None

    @property
    def nodes(self) -> List[NodeId]:
        n = self.get("nodes")
        base = [str(x) for x in n] if isinstance(n, list) else []
        return _DagNodes(base, self)

    @property
    def edges(self) -> List[Edge]:
        e = self.get("edges")
        out: List[Edge] = []
        if not isinstance(e, list):
            return out
        for it in e:
            if isinstance(it, (list, tuple)) and len(it) == 2:
                out.append((str(it[0]), str(it[1])))
        return out

    @property
    def labels(self) -> Dict[NodeId, Dict[str, Any]]:
        # Backwards-compat alias.
        m = self.get("labels")
        return m if isinstance(m, dict) else {}

    @property
    def entities(self) -> Dict[NodeId, Dict[str, Any]]:
        """Node metadata map keyed by node_id (e.g. class_type/title)."""
        m = self.get("entities")
        base = m if isinstance(m, dict) else {}
        return _DagEntities(base, self)

    def deps(self, node_id: Union[str, int]) -> List[NodeId]:
        """Immediate upstream deps of node_id."""
        nid = str(node_id)
        deps_set: Set[NodeId] = set()
        for src, dst in self.edges:
            if dst == nid:
                deps_set.add(src)
        return sorted(deps_set, key=self.nodes.index) if nid in self.nodes else sorted(deps_set)

    def rdeps(self, node_id: Union[str, int]) -> List[NodeId]:
        """Immediate downstream deps of node_id."""
        nid = str(node_id)
        out_set: Set[NodeId] = set()
        for src, dst in self.edges:
            if src == nid:
                out_set.add(dst)
        return sorted(out_set, key=self.nodes.index) if nid in self.nodes else sorted(out_set)

    def ancestors(self, node_id: Union[str, int]) -> List[NodeId]:
        """All upstream ancestors (transitive)."""
        nid = str(node_id)
        seen: Set[NodeId] = set()
        q = deque(self.deps(nid))
        while q:
            cur = q.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            for p in self.deps(cur):
                if p not in seen:
                    q.append(p)
        return sorted(seen, key=self.nodes.index) if nid in self.nodes else sorted(seen)

    def descendants(self, node_id: Union[str, int]) -> List[NodeId]:
        """All downstream descendants (transitive)."""
        nid = str(node_id)
        seen: Set[NodeId] = set()
        q = deque(self.rdeps(nid))
        while q:
            cur = q.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            for ch in self.rdeps(cur):
                if ch not in seen:
                    q.append(ch)
        return sorted(seen, key=self.nodes.index) if nid in self.nodes else sorted(seen)

    def _toposort_nodes(self) -> List[NodeId]:
        """Best-effort topological ordering. Falls back to original nodes order if cycles exist."""
        nodes = self.nodes
        indeg: Dict[NodeId, int] = {n: 0 for n in nodes}
        adj: Dict[NodeId, List[NodeId]] = {n: [] for n in nodes}
        for src, dst in self.edges:
            if src not in indeg or dst not in indeg:
                continue
            adj[src].append(dst)
            indeg[dst] += 1

        q = deque([n for n in nodes if indeg.get(n, 0) == 0])
        out: List[NodeId] = []
        while q:
            n = q.popleft()
            out.append(n)
            for m in adj.get(n, []):
                indeg[m] -= 1
                if indeg[m] == 0:
                    q.append(m)

        # Cycle or missing nodes: return stable nodes order
        if len(out) != len(nodes):
            return list(nodes)
        return out

    def toposort(self) -> "Dag":
        """Return a new Dag whose `nodes` ordering is topologically sorted (best-effort)."""
        return self.toposorted()

    def toposorted(self) -> "Dag":
        """Return a new Dag whose `nodes` ordering is topologically sorted (best-effort).

        This is useful for visualization:
          dag.toposorted().to_mermaid(...)
          dag.toposorted().to_dot(...)
        """
        ordered = self._toposort_nodes()
        idx = {n: i for i, n in enumerate(ordered)}
        # Reorder edges to match the new node ordering for stable rendering.
        edges_sorted = sorted(
            self.edges,
            key=lambda e: (idx.get(e[0], 10**9), idx.get(e[1], 10**9)),
        )
        # Preserve other fields and use the ordered node list.
        d = dict(self)
        d["nodes"] = list(ordered)
        d["edges"] = [[a, b] for a, b in edges_sorted]
        return Dag(d)

    def to_dot(self, *, label: str = "id") -> str:
        """Graphviz DOT string.

        label can be:
        - preset: "id" | "class_type" | "title"
        - template: contains "{" and "}", e.g. "{id} - {class_type}"
        """
        lbls = self.entities or self.labels
        lines = ["digraph comfyui {", "  rankdir=LR;"]
        for n in self.nodes:
            meta = lbls.get(n, {}) if isinstance(lbls, dict) else {}
            l = _format_node_label(label, n, meta)
            safe = l.replace('"', '\\"')
            lines.append(f'  "{n}" [label="{safe}"];')
        for src, dst in self.edges:
            lines.append(f'  "{src}" -> "{dst}";')
        lines.append("}")
        return _PrettyText("\n".join(lines))

    def to_mermaid(self, *, direction: str = "LR", label: str = "{id}: {class_type}") -> str:
        """Mermaid flowchart string.

        direction: LR|TD
        label can be:
        - preset: "id" | "class_type" | "title" | "id_class_type"
        - template: contains "{" and "}", e.g. "{id} - {class_type}"
        """
        lbls = self.entities or self.labels
        dir2 = direction if direction in ("LR", "TD") else "LR"
        lines = [f"flowchart {dir2}"]

        # Mermaid IDs can't contain ':' reliably; use a safe prefix and keep original as label.
        def _mid(n: str) -> str:
            return "n_" + "".join(ch if ch.isalnum() else "_" for ch in n)

        def _label(n: str) -> str:
            meta = lbls.get(n, {}) if isinstance(lbls, dict) else {}
            return _format_node_label(label, n, meta)

        # Emit node declarations in `self.nodes` order so topo-sorted DAGs render predictably.
        for n in self.nodes:
            ln = _label(n).replace('"', '\\"')
            lines.append(f'  {_mid(n)}["{ln}"]')

        # Then emit edges (already ordered for topo-sorted DAGs).
        for src, dst in self.edges:
            lines.append(f"  {_mid(src)} --> {_mid(dst)}")
        return _PrettyText("\n".join(lines))


def _format_node_label(label: str, node_id: str, meta: Dict[str, Any]) -> str:
    """Format node label from preset or template string."""
    # Template mode: any string with braces.
    if isinstance(label, str) and "{" in label and "}" in label:
        mapping = {
            "id": str(node_id),
            "class_type": str(meta.get("class_type", "")) if meta.get("class_type") is not None else "",
            "title": str(meta.get("title", "")) if meta.get("title") is not None else "",
        }
        try:
            out = label.format(**mapping)
            return out if out else str(node_id)
        except Exception:
            return str(node_id)

    # Preset mode
    if label == "class_type":
        return str(meta.get("class_type", node_id))
    if label == "title":
        return str(meta.get("title", node_id))
    if label in ("id_class_type", "id+class_type", "id:class_type"):
        ct = meta.get("class_type")
        if isinstance(ct, str) and ct:
            return f"{node_id}: {ct}"
        return str(node_id)
    # default: id
    return str(node_id)


def build_api_dag(api: Dict[str, Any]) -> Dag:
    """Build a DAG from an API payload (ApiFlow)."""
    nodes = [str(k) for k in api.keys()]
    node_set = set(nodes)
    edges_set: Set[Edge] = set()
    entities: Dict[NodeId, Dict[str, Any]] = {}

    for nid, node in api.items():
        nid_s = str(nid)
        if isinstance(node, dict):
            entities[nid_s] = {
                "class_type": node.get("class_type"),
                "title": (node.get("_meta") or {}).get("title") if isinstance(node.get("_meta"), dict) else None,
            }
            inputs = node.get("inputs")
            if isinstance(inputs, dict):
                for _, v in inputs.items():
                    for up in _iter_upstream_node_ids(v, node_set):
                        edges_set.add((up, nid_s))

    edges = [
        [a, b]
        for a, b in sorted(
            edges_set,
            key=lambda e: (
                nodes.index(e[0]) if e[0] in node_set else 10**9,
                nodes.index(e[1]) if e[1] in node_set else 10**9,
            ),
        )
    ]
    return Dag({"class": "ApiFlow", "nodes": nodes, "edges": edges, "entities": entities})


def build_flow_dag(flow: Dict[str, Any], *, node_info: Optional[Dict[str, Any]] = None) -> Dag:
    """Build a DAG from a workspace flow (Flow) using its link table.

    If node_info is provided, the DAG is filtered to node types present in node_info,
    matching Flow->ApiFlow conversion behavior (UI-only nodes like MarkdownNote are excluded).
    """
    nodes_list = flow.get("nodes", [])
    nodes: List[NodeId] = []
    entities: Dict[NodeId, Dict[str, Any]] = {}
    allowed_types: Optional[Set[str]] = None
    if isinstance(node_info, dict) and node_info:
        allowed_types = set(node_info.keys())
    if isinstance(nodes_list, list):
        for n in nodes_list:
            if not isinstance(n, dict):
                continue
            nid = n.get("id")
            if nid is None:
                continue
            ntype = n.get("type")
            if allowed_types is not None and isinstance(ntype, str) and ntype not in allowed_types:
                continue
            nid_s = str(nid)
            nodes.append(nid_s)
            entities[nid_s] = {"class_type": n.get("type"), "title": n.get("title")}

    node_set = set(nodes)
    edges_set: Set[Edge] = set()
    links = flow.get("links", [])
    if isinstance(links, list):
        for l in links:
            # link shape: [id, origin, origin_slot, target, target_slot, type]
            if not (isinstance(l, list) and len(l) >= 5):
                continue
            origin = l[1]
            target = l[3]
            src = str(origin)
            dst = str(target)
            if src in node_set and dst in node_set:
                edges_set.add((src, dst))

    edges = [[a, b] for a, b in sorted(edges_set, key=lambda e: (nodes.index(e[0]), nodes.index(e[1])))]
    return Dag({"class": "Flow", "nodes": nodes, "edges": edges, "entities": entities})


def _iter_upstream_node_ids(value: Any, node_set: Set[str]) -> Iterator[str]:
    """Yield upstream node ids from a nested input value."""
    # direct ref: ["4", 0]
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and str(value[0]) in node_set and isinstance(value[1], int):
            yield str(value[0])
            return
        for it in value:
            yield from _iter_upstream_node_ids(it, node_set)
        return
    if isinstance(value, dict):
        for it in value.values():
            yield from _iter_upstream_node_ids(it, node_set)
        return
    # scalars: ignore


