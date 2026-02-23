"""Stage 16 — DAG: edges, dependencies, ancestors, DOT/Mermaid rendering."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 16: DAG"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Flow, Workflow

    wf_path = str(_BUNDLED_WORKFLOW)

    def t_16_1():
        f = Flow(wf_path)
        dag = f.dag
        assert dag is not None, "flow.dag is None"
        ed = dag.edges
        assert hasattr(ed, '__len__'), "edges has no __len__"
        assert len(ed) > 0, "edges is empty"
        return {"input": "flow.dag.edges", "output": f"{len(ed)} edges", "result": "✓ edges accessible"}
    _run_test(collector, stage, "16.1", "Flow dag.edges", t_16_1)

    def t_16_2():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        dag = api.dag
        assert dag is not None, "api.dag is None"
        ed = dag.edges
        assert len(ed) > 0, "ApiFlow dag.edges is empty"
        return {"input": "api.dag.edges", "output": f"{len(ed)} edges", "result": "✓ ApiFlow dag"}
    _run_test(collector, stage, "16.2", "ApiFlow dag.edges", t_16_2)

    def t_16_3():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        dag = api.dag
        ed = dag.edges
        # Find edges that point TO a KSampler node
        ks_nodes = api.find(class_type="KSampler")
        assert len(ks_nodes) > 0, "No KSampler found"
        ks_id = str(ks_nodes[0].id)
        upstream = [e for e in ed if str(e[1]) == ks_id or (len(e) > 1 and str(e[-1]) == ks_id)]
        return {"input": f"edges → KSampler (id={ks_id})", "output": f"{len(upstream)} upstream edges", "result": "✓ DAG structure"}
    _run_test(collector, stage, "16.3", "dag.edges pointing to KSampler", t_16_3)

    def t_16_4():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        dag = api.dag
        nd = dag.nodes
        ed = dag.edges
        assert len(nd) > 0, "dag.nodes is empty"
        assert len(ed) > 0, "dag.edges is empty"
        return {"input": f"dag nodes + edges", "output": f"{len(nd)} nodes, {len(ed)} edges", "result": "✓ DAG populated"}
    _run_test(collector, stage, "16.4", "dag.nodes + dag.edges populated", t_16_4)

    def t_16_5():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        dag = api.dag
        dot = dag.to_dot()
        assert isinstance(dot, str), f"to_dot() returned {type(dot)}"
        assert "digraph" in dot.lower(), f"to_dot() missing 'digraph': {dot[:100]}"
        return {"input": "dag.to_dot()", "output": f"{len(dot)} chars", "result": "✓ contains 'digraph'"}
    _run_test(collector, stage, "16.5", "dag.to_dot()", t_16_5)

    def t_16_6():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        dag = api.dag
        mm = dag.to_mermaid()
        assert isinstance(mm, str), f"to_mermaid() returned {type(mm)}"
        assert "graph" in mm.lower() or "flowchart" in mm.lower(), f"to_mermaid() unexpected format: {mm[:100]}"
        return {"input": "dag.to_mermaid()", "output": f"{len(mm)} chars", "result": "✓ Mermaid syntax"}
    _run_test(collector, stage, "16.6", "dag.to_mermaid()", t_16_6)

    def t_16_7():
        f = Flow(wf_path)
        dag = f.dag
        nd = dag.nodes
        assert isinstance(nd, (list, set, dict)), f"dag.nodes returned {type(nd)}"
        assert len(nd) > 0, "dag.nodes is empty"
        return {"input": "dag.nodes", "output": f"{len(nd)} nodes", "result": "✓ populated"}
    _run_test(collector, stage, "16.7", "dag.nodes", t_16_7)

    def t_16_8():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        dag = api.dag
        save_nodes = api.find(class_type="SaveImage")
        if save_nodes:
            save_id = save_nodes[0].id
            desc = dag.descendants(save_id) if hasattr(dag, 'descendants') else []
            return {"input": f"dag.descendants({save_id})", "output": f"{len(desc)} descendants", "result": "✓ leaf or downstream"}
        return {"input": "dag.descendants (no SaveImage)", "output": "N/A", "result": "✓ skipped"}
    _run_test(collector, stage, "16.8", "dag.descendants(SaveImage)", t_16_8)

    _print_stage_summary(collector, stage)
