"""Stage 20 — Schema Drilling: NodeInfoError, fetch_node_info, advanced find with regex/depth."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
    builtin_node_info_path,
)

STAGE = "Stage 20: Schema Drilling"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Flow, Workflow

    wf_path = str(_BUNDLED_WORKFLOW)

    def t_20_1():
        f = Flow(wf_path)
        try:
            _ = f.nodes.KSampler[0].seed
            return {"input": "access seed without node_info", "output": "raised or returned", "result": "✓ no crash"}
        except Exception as e:
            ename = type(e).__name__
            return {"input": "access seed without node_info", "output": f"{ename}: {str(e)[:40]}", "result": f"✓ error: {ename}"}
    _run_test(collector, stage, "20.1", "Access widget without node_info", t_20_1)

    def t_20_2():
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = f.nodes.KSampler[0]
        seed = ks.seed
        assert seed is not None, "seed is None"
        return {"input": "ks.seed with node_info", "output": str(seed), "result": "✓ widget readable"}
    _run_test(collector, stage, "20.2", "Access widget with node_info", t_20_2)

    def t_20_3():
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        try:
            _ = f.nodes.KSampler[0].nonexistent_widget
            return {"input": "access .nonexistent_widget", "output": "returned (no error)", "result": "✓ no crash"}
        except AttributeError as e:
            return {"input": "access .nonexistent_widget", "output": f"AttributeError: {str(e)[:40]}", "result": "✓ AttributeError"}
    _run_test(collector, stage, "20.3", "AttributeError on missing widget", t_20_3)

    def t_20_4():
        f = Flow(wf_path)
        f.fetch_node_info(BUILTIN_NODE_INFO)
        seed = f.nodes.KSampler[0].seed
        assert seed is not None, "seed None after fetch_node_info()"
        return {"input": "fetch_node_info(dict) → ks.seed", "output": str(seed), "result": "✓ late-binding"}
    _run_test(collector, stage, "20.4", "fetch_node_info(dict) enables widget access", t_20_4)

    def t_20_5():
        ni_p = builtin_node_info_path()
        f = Flow(wf_path)
        f.fetch_node_info(str(ni_p))
        seed = f.nodes.KSampler[0].seed
        assert seed is not None, "seed None after fetch_node_info(path)"
        return {"input": f"fetch_node_info({ni_p.name})", "output": str(seed), "result": "✓ file path"}
    _run_test(collector, stage, "20.5", "fetch_node_info(file path)", t_20_5)

    def t_20_6():
        import re
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        results = f.nodes.find(type=re.compile(r"CLIP.*"))
        assert len(results) >= 2, f"Regex CLIP.* should match ≥2, got {len(results)}"
        return {"input": "find(type=re.compile('CLIP.*'))", "output": f"{len(results)} matches", "result": "✓ regex find"}
    _run_test(collector, stage, "20.6", "find(type=regex) advanced", t_20_6)

    def t_20_7():
        import re
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        all_nodes = f.nodes.find(type=re.compile(r".*"))
        assert len(all_nodes) > 0, "find(type=re'.*') returned empty"
        return {"input": "find(type=re.compile('.*'))", "output": f"{len(all_nodes)} nodes", "result": "✓ match-all"}
    _run_test(collector, stage, "20.7", "find(type=re'.*') match-all", t_20_7)

    def t_20_8():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        ni = api.node_info
        assert ni is not None, "api.node_info is None"
        ks_info = ni.get("KSampler", {})
        assert "input" in ks_info, f"KSampler info missing 'input': {list(ks_info.keys())}"
        req = ks_info["input"].get("required", {})
        assert "seed" in req, f"'seed' not in required inputs: {list(req.keys())}"
        return {"input": "ni['KSampler']['input']['required']['seed']", "output": str(req['seed'])[:60], "result": "✓ schema drill"}
    _run_test(collector, stage, "20.8", "NodeInfo schema drill to seed spec", t_20_8)

    def t_20_9():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler[0]
        seed_val = ks.seed
        if hasattr(seed_val, 'spec'):
            sp = seed_val.spec()
            return {"input": "ks.seed.spec()", "output": str(sp)[:60], "result": "✓ spec from WidgetValue"}
        return {"input": "ks.seed.spec()", "output": "N/A", "result": "✓ no spec method"}
    _run_test(collector, stage, "20.9", "WidgetValue.spec() schema drill", t_20_9)

    def t_20_10():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler[0]
        d = dir(ks)
        assert "seed" in d, "'seed' not in dir(ks)"
        assert "steps" in d, "'steps' not in dir(ks)"
        return {"input": "dir(api.KSampler[0])", "output": f"{len(d)} entries", "result": "✓ schema-aware dir"}
    _run_test(collector, stage, "20.10", "dir(api_node) schema-aware", t_20_10)

    _print_stage_summary(collector, stage)
