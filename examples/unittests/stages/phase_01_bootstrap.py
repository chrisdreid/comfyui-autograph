"""Phase 1 — Bootstrap: import, version, API symbols, model-layer env switch, error handling.

Merged from: stage_00_bootstrap, stage_25_model_layer, stage_19_error_handling
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Phase 1: Bootstrap"


# ---------------------------------------------------------------------------
# Helpers (from stage_25)
# ---------------------------------------------------------------------------

def _env_with_repo_root(extra: dict) -> dict:
    env = dict(os.environ)
    pp = env.get("PYTHONPATH", "")
    parts = [p for p in pp.split(os.pathsep) if p]
    if str(_REPO_ROOT) not in parts:
        parts.insert(0, str(_REPO_ROOT))
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env.update(extra)
    return env


def _run_code(code: str, env_extra: dict) -> str:
    out = subprocess.check_output(
        [sys.executable, "-c", code],
        env=_env_with_repo_root(env_extra),
        stderr=subprocess.STDOUT,
    )
    return out.decode("utf-8", errors="replace").strip()


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    # -----------------------------------------------------------------------
    # 1.1 – 1.5  Import / version / symbols / bundled assets  (was stage 0)
    # -----------------------------------------------------------------------

    def t_1_1():
        import autoflow  # noqa: F401
        return {"input": "import autoflow", "output": f"module: {autoflow.__file__}", "result": "OK"}
    _run_test(collector, stage, "1.1", "import autoflow", t_1_1)

    def t_1_2():
        import autoflow
        v = autoflow.__version__
        assert isinstance(v, str) and len(v) > 0, f"Bad version: {v!r}"
        parts = v.split(".")
        assert len(parts) >= 2, f"Version has fewer than 2 parts: {v}"
        return {"input": "autoflow.__version__", "output": v, "result": f"✓ semver {'.'.join(parts)}"}
    _run_test(collector, stage, "1.2", "autoflow.__version__ valid", t_1_2)

    def t_1_3():
        import autoflow
        expected = [
            "Flow", "ApiFlow", "Workflow", "NodeInfo",
            "convert", "convert_with_errors",
            "api_mapping", "map_strings", "map_paths", "force_recompute",
            "WsEvent", "ProgressPrinter", "WidgetValue",
            "ConvertResult", "SubmissionResult", "ImagesResult", "ImageResult",
        ]
        missing = [s for s in expected if not hasattr(autoflow, s)]
        assert not missing, f"Missing public API symbols: {missing}"
        return {
            "input": f"{len(expected)} expected symbols",
            "output": ", ".join(expected),
            "result": f"✓ all {len(expected)} found",
        }
    _run_test(collector, stage, "1.3", "All public API symbols exist", t_1_3)

    def t_1_4():
        from autoflow import Flow
        assert _BUNDLED_WORKFLOW.exists(), f"Bundled workflow not found: {_BUNDLED_WORKFLOW}"
        f = Flow.load(str(_BUNDLED_WORKFLOW))
        assert f is not None, "Flow.load returned None"
        return {
            "input": str(_BUNDLED_WORKFLOW.name),
            "output": f"Flow ({type(f).__name__})",
            "result": "✓ loaded",
        }
    _run_test(collector, stage, "1.4", "Bundled workflow.json loads", t_1_4)

    def t_1_5():
        from autoflow import NodeInfo
        ni = NodeInfo(BUILTIN_NODE_INFO)
        assert ni is not None, "NodeInfo returned None"
        types = ["KSampler", "CLIPTextEncode", "CheckpointLoaderSimple",
                 "EmptyLatentImage", "VAEDecode", "SaveImage"]
        for ct in types:
            assert ct in BUILTIN_NODE_INFO, f"Missing node class: {ct}"
        return {
            "input": f"BUILTIN_NODE_INFO ({len(BUILTIN_NODE_INFO)} types)",
            "output": ", ".join(types),
            "result": f"✓ all {len(types)} present",
        }
    _run_test(collector, stage, "1.5", "Built-in node_info loads", t_1_5)

    # -----------------------------------------------------------------------
    # 1.6 – 1.9  Model layer env switch  (was stage 25)
    # -----------------------------------------------------------------------

    def t_1_6():
        code = "from autoflow import Flow; import inspect; print(Flow.__module__)"
        mod = _run_code(code, {"AUTOFLOW_MODEL_LAYER": ""})
        assert mod == "autoflow.flowtree", f"Default module = {mod!r}"
        return {
            "input": "AUTOFLOW_MODEL_LAYER='' → Flow.__module__",
            "output": mod,
            "result": "✓ default is flowtree",
        }
    _run_test(collector, stage, "1.6", "Default model layer is flowtree", t_1_6)

    def t_1_7():
        code = "from autoflow import Flow; print(Flow.__module__)"
        mod = _run_code(code, {"AUTOFLOW_MODEL_LAYER": "models"})
        assert mod == "autoflow.models", f"models module = {mod!r}"
        return {
            "input": "AUTOFLOW_MODEL_LAYER='models'",
            "output": mod,
            "result": "✓ models layer active",
        }
    _run_test(collector, stage, "1.7", "AUTOFLOW_MODEL_LAYER=models", t_1_7)

    def t_1_8():
        code = "from autoflow import Flow; print(Flow.__module__)"
        mod = _run_code(code, {"AUTOFLOW_MODEL_LAYER": "flowtree"})
        assert mod == "autoflow.flowtree", f"flowtree module = {mod!r}"
        return {
            "input": "AUTOFLOW_MODEL_LAYER='flowtree'",
            "output": mod,
            "result": "✓ flowtree explicit",
        }
    _run_test(collector, stage, "1.8", "AUTOFLOW_MODEL_LAYER=flowtree", t_1_8)

    def t_1_9():
        code = "import autoflow"
        try:
            _run_code(code, {"AUTOFLOW_MODEL_LAYER": "nope"})
            assert False, "Should have raised CalledProcessError"
        except subprocess.CalledProcessError as e:
            output = e.output.decode("utf-8", errors="replace")
            assert "AUTOFLOW_MODEL_LAYER must be" in output
            return {
                "input": "AUTOFLOW_MODEL_LAYER='nope'",
                "output": "CalledProcessError raised",
                "result": "✓ fails fast with message",
            }
    _run_test(collector, stage, "1.9", "Invalid model layer fails fast", t_1_9)

    # -----------------------------------------------------------------------
    # 1.10 – 1.14  Error handling  (was stage 19)
    # -----------------------------------------------------------------------

    from autoflow import convert_with_errors, Flow

    def t_1_10():
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
    _run_test(collector, stage, "1.10", "convert_with_errors: invalid workflow → ok=False", t_1_10)

    def t_1_11():
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
    _run_test(collector, stage, "1.11", "convert_with_errors: unknown node type", t_1_11)

    def t_1_12():
        f = Flow.load(str(_BUNDLED_WORKFLOW))
        result = convert_with_errors(f, node_info=BUILTIN_NODE_INFO)
        assert result.ok is True, f"Valid workflow should succeed, got ok={result.ok}"
        assert result.data is not None, "result.data is None for valid workflow"
        return {"input": "valid workflow", "output": f"ok={result.ok}, data={type(result.data).__name__}", "result": "✓ success"}
    _run_test(collector, stage, "1.12", "convert_with_errors: valid workflow → ok=True", t_1_12)

    def t_1_13():
        f = Flow.load(str(_BUNDLED_WORKFLOW))
        result = convert_with_errors(f, node_info=BUILTIN_NODE_INFO)
        assert hasattr(result, "errors"), "No .errors attribute"
        errs = result.errors or []
        assert isinstance(errs, list), f"errors is {type(errs)}"
        return {"input": "valid workflow errors", "output": f"{len(errs)} errors", "result": "✓ errors is list"}
    _run_test(collector, stage, "1.13", "result.errors is a list", t_1_13)

    def t_1_14():
        try:
            f = Flow({})
            result = convert_with_errors(f, node_info=BUILTIN_NODE_INFO)
            return {"input": "empty Flow({})", "output": f"ok={result.ok}", "result": "✓ no crash"}
        except Exception as e:
            ename = type(e).__name__
            return {"input": "empty Flow({})", "output": f"{ename}: {str(e)[:60]}", "result": f"✓ raises {ename}"}
    _run_test(collector, stage, "1.14", "convert_with_errors: empty Flow", t_1_14)

    _print_stage_summary(collector, stage)
