"""Stage 0 — Bootstrap: import, version, API symbols, bundled workflow, node_info."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is importable
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 0: Bootstrap"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    # 0.1 Import autoflow
    def t_0_1():
        import autoflow  # noqa: F401
        return {"input": "import autoflow", "output": f"module: {autoflow.__file__}", "result": "OK"}
    _run_test(collector, stage, "0.1", "import autoflow", t_0_1)

    # 0.2 Version is valid string
    def t_0_2():
        import autoflow
        v = autoflow.__version__
        assert isinstance(v, str) and len(v) > 0, f"Bad version: {v!r}"
        parts = v.split(".")
        assert len(parts) >= 2, f"Version has fewer than 2 parts: {v}"
        return {"input": "autoflow.__version__", "output": v, "result": f"✓ semver {'.'.join(parts)}"}
    _run_test(collector, stage, "0.2", "autoflow.__version__ valid", t_0_2)

    # 0.3 All public API symbols
    def t_0_3():
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
    _run_test(collector, stage, "0.3", "All public API symbols exist", t_0_3)

    # 0.4 Bundled workflow loads
    def t_0_4():
        from autoflow import Flow
        assert _BUNDLED_WORKFLOW.exists(), f"Bundled workflow not found: {_BUNDLED_WORKFLOW}"
        f = Flow.load(str(_BUNDLED_WORKFLOW))
        assert f is not None, "Flow.load returned None"
        return {
            "input": str(_BUNDLED_WORKFLOW.name),
            "output": f"Flow ({type(f).__name__})",
            "result": "✓ loaded",
        }
    _run_test(collector, stage, "0.4", "Bundled workflow.json loads", t_0_4)

    # 0.5 Built-in node_info loads
    def t_0_5():
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
    _run_test(collector, stage, "0.5", "Built-in node_info loads", t_0_5)

    _print_stage_summary(collector, stage)
