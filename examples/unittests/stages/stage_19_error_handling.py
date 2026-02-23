"""Stage 19 — Error Handling: convert_with_errors, invalid workflows, partial success."""

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

STAGE = "Stage 19: Error Handling"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import convert_with_errors, Flow

    def t_19_1():
        invalid = {"last_node_id": 1, "last_link_id": 0, "nodes": [
            {"id": 1, "type": "CompletelyFakeNode", "pos": [0, 0], "size": [200, 100],
             "inputs": [], "outputs": [], "widgets_values": [], "properties": {}}
        ], "links": [], "groups": [], "config": {}, "extra": {}, "version": 0.4}
        f = Flow(invalid)
        result = convert_with_errors(f, node_info=BUILTIN_NODE_INFO)
        assert hasattr(result, "ok"), "No .ok attribute"
        assert result.ok is False, f"Expected ok=False for invalid workflow, got {result.ok}"
        errs = len(result.errors) if result.errors else 0
        return {"input": "invalid workflow with CompletelyFakeNode", "output": f"ok={result.ok}, errors={errs}", "result": "✓ failure detected"}
    _run_test(collector, stage, "19.1", "convert_with_errors: invalid workflow → ok=False", t_19_1)

    def t_19_2():
        unknown_ni = {"KSampler": BUILTIN_NODE_INFO.get("KSampler", {"input": {}})}
        wf = {"last_node_id": 2, "last_link_id": 0, "nodes": [
            {"id": 1, "type": "KSampler", "pos": [0, 0], "size": [200, 100],
             "inputs": [], "outputs": [], "widgets_values": [200, "randomize", 20, 8.0, "euler", "normal", 1.0],
             "properties": {}},
            {"id": 2, "type": "UnknownNodeXYZ", "pos": [300, 0], "size": [200, 100],
             "inputs": [], "outputs": [], "widgets_values": [], "properties": {}},
        ], "links": [], "groups": [], "config": {}, "extra": {}, "version": 0.4}
        f = Flow(wf)
        result = convert_with_errors(f, node_info=unknown_ni)
        errs = result.errors if result.errors else []
        err_types = [e.get("type", "") if isinstance(e, dict) else str(e) for e in errs]
        return {"input": "KSampler + UnknownNodeXYZ", "output": f"ok={result.ok}, errors={len(errs)}", "result": f"✓ partial: {err_types[:2]}"}
    _run_test(collector, stage, "19.2", "convert_with_errors: unknown node type", t_19_2)

    def t_19_3():
        f = Flow.load(str(_BUNDLED_WORKFLOW))
        result = convert_with_errors(f, node_info=BUILTIN_NODE_INFO)
        assert result.ok is True, f"Valid workflow should succeed, got ok={result.ok}"
        assert result.data is not None, "result.data is None for valid workflow"
        return {"input": "valid workflow", "output": f"ok={result.ok}, data={type(result.data).__name__}", "result": "✓ success"}
    _run_test(collector, stage, "19.3", "convert_with_errors: valid workflow → ok=True", t_19_3)

    def t_19_4():
        f = Flow.load(str(_BUNDLED_WORKFLOW))
        result = convert_with_errors(f, node_info=BUILTIN_NODE_INFO)
        assert hasattr(result, "errors"), "No .errors attribute"
        errs = result.errors or []
        assert isinstance(errs, list), f"errors is {type(errs)}"
        return {"input": "valid workflow errors", "output": f"{len(errs)} errors", "result": "✓ errors is list"}
    _run_test(collector, stage, "19.4", "result.errors is a list", t_19_4)

    def t_19_5():
        try:
            f = Flow({})
            result = convert_with_errors(f, node_info=BUILTIN_NODE_INFO)
            return {"input": "empty Flow({})", "output": f"ok={result.ok}", "result": "✓ no crash"}
        except Exception as e:
            ename = type(e).__name__
            return {"input": "empty Flow({})", "output": f"{ename}: {str(e)[:60]}", "result": f"✓ raises {ename}"}
    _run_test(collector, stage, "19.5", "convert_with_errors: empty Flow", t_19_5)

    _print_stage_summary(collector, stage)
