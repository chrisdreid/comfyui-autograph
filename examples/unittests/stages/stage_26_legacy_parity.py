"""Stage 26 — Legacy Parity: dict-subclass identity, views, drilling, find, path drilling."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW, builtin_node_info_path,
    fixture_path, SkipTest,
)

STAGE = "Stage 26: Legacy Parity"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow.models import Flow, ApiFlow, NodeInfo, Workflow

    def t_26_1():
        """Dict subclass identity — uses bundled workflow + BUILTIN_NODE_INFO."""
        f = Flow.load(_BUNDLED_WORKFLOW)
        oi = NodeInfo(BUILTIN_NODE_INFO)
        assert isinstance(f, dict), f"Flow is not dict subclass: {type(f)}"
        assert isinstance(oi, dict), f"NodeInfo is not dict subclass: {type(oi)}"
        # ApiFlow from simple api prompt
        a = ApiFlow({"1": {"class_type": "KSampler", "inputs": {"seed": 1}}})
        assert isinstance(a, dict), f"ApiFlow is not dict subclass: {type(a)}"
        return {
            "input": "Flow, ApiFlow, NodeInfo → isinstance(dict)",
            "output": f"Flow={isinstance(f,dict)}, ApiFlow={isinstance(a,dict)}, NodeInfo={isinstance(oi,dict)}",
            "result": "✓ all dict subclasses",
        }
    _run_test(collector, stage, "26.1", "Dict subclass identity", t_26_1)

    def t_26_2():
        """FlowNodesView repr + dir — uses bundled workflow."""
        f = Flow.load(_BUNDLED_WORKFLOW)
        r = repr(f.nodes)
        assert "FlowNodesView" in r, f"repr = {r!r}"
        node_types = sorted({n.get("type") for n in f.get("nodes", []) if isinstance(n, dict) and isinstance(n.get("type"), str)})
        d = dir(f.nodes)
        for t in node_types:
            assert t in d, f"{t!r} not in dir(f.nodes)"
        return {
            "input": "repr(flow.nodes) + dir check",
            "output": f"repr contains FlowNodesView, {len(node_types)} types in dir",
            "result": "✓ view works",
        }
    _run_test(collector, stage, "26.2", "FlowNodesView repr + dir", t_26_2)

    def t_26_3():
        """Widget drilling + dir — uses bundled workflow + BUILTIN_NODE_INFO."""
        f = Flow(_BUNDLED_WORKFLOW, node_info=BUILTIN_NODE_INFO)
        n = f.nodes.KSampler[0]
        seed = n.seed
        assert seed is not None, f"seed = {seed}"
        assert "seed" in n.attrs()
        assert "seed" in dir(n)
        return {
            "input": "flow.nodes.KSampler[0].seed",
            "output": f"seed={seed}",
            "result": "✓ widget drilling + dir",
        }
    _run_test(collector, stage, "26.3", "Widget drilling + dir on proxy", t_26_3)

    def t_26_4():
        """find(id='*') — uses bundled workflow."""
        f = Flow.load(_BUNDLED_WORKFLOW)
        all_nodes = [n for n in f.get("nodes", []) if isinstance(n, dict)]
        matches = f.nodes.find(id="*")
        assert len(matches) == len(all_nodes), f"find(id='*') = {len(matches)} vs {len(all_nodes)}"
        matches2 = f.nodes.find(id=re.compile(r".*"))
        assert len(matches2) == len(all_nodes)
        return {
            "input": "find(id='*') and find(id=re.compile('.*'))",
            "output": f"wildcard={len(matches)}, regex={len(matches2)}, total={len(all_nodes)}",
            "result": "✓ both match all",
        }
    _run_test(collector, stage, "26.4", "find(id='*') existence query", t_26_4)

    def t_26_5():
        """ApiFlow path get/set — uses bundled workflow + builtin node_info."""
        ni_p = builtin_node_info_path()
        api = Workflow(str(_BUNDLED_WORKFLOW), node_info=ni_p)
        assert isinstance(api, ApiFlow)
        api["ksampler/seed"] = 123
        assert api.ksampler[0].seed == 123
        api["ksampler/0/seed"] = 321
        assert api.ksampler[0].seed == 321
        node_id = api.find(class_type="KSampler")[0].id
        api[f"{node_id}/seed"] = 111
        assert api.ksampler[0].seed == 111
        return {
            "input": "api['ksampler/seed'] = 123 → 321 → 111",
            "output": f"final seed={api.ksampler[0].seed}",
            "result": "✓ path get/set",
        }
    _run_test(collector, stage, "26.5", "ApiFlow path get/set", t_26_5)

    def t_26_6():
        """NodeInfo attr + path drilling — uses BUILTIN_NODE_INFO."""
        oi = NodeInfo(BUILTIN_NODE_INFO)
        assert "input" in oi.KSampler, f"oi.KSampler = {list(oi.KSampler.keys())}"
        seed_spec = oi["KSampler/input/required/seed"]
        assert seed_spec, f"seed_spec = {seed_spec!r}"
        return {
            "input": "oi['KSampler/input/required/seed']",
            "output": str(seed_spec)[:60],
            "result": "✓ path drilling",
        }
    _run_test(collector, stage, "26.6", "NodeInfo attr + path drilling", t_26_6)

    _print_stage_summary(collector, stage)
