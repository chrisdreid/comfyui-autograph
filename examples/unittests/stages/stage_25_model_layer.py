"""Stage 25 — Model Layer: env switch tests (subprocess-based) for AUTOFLOW_MODEL_LAYER."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import ResultCollector, _run_test, _print_stage_summary  # noqa: E402

STAGE = "Stage 25: Model Layer"


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


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    def t_25_1():
        code = "from autoflow import Flow; import inspect; print(Flow.__module__)"
        mod = _run_code(code, {"AUTOFLOW_MODEL_LAYER": ""})
        assert mod == "autoflow.flowtree", f"Default module = {mod!r}"
        return {
            "input": "AUTOFLOW_MODEL_LAYER='' → Flow.__module__",
            "output": mod,
            "result": "✓ default is flowtree",
        }
    _run_test(collector, stage, "25.1", "Default model layer is flowtree", t_25_1)

    def t_25_2():
        code = "from autoflow import Flow; print(Flow.__module__)"
        mod = _run_code(code, {"AUTOFLOW_MODEL_LAYER": "models"})
        assert mod == "autoflow.models", f"models module = {mod!r}"
        return {
            "input": "AUTOFLOW_MODEL_LAYER='models'",
            "output": mod,
            "result": "✓ models layer active",
        }
    _run_test(collector, stage, "25.2", "AUTOFLOW_MODEL_LAYER=models", t_25_2)

    def t_25_3():
        code = "from autoflow import Flow; print(Flow.__module__)"
        mod = _run_code(code, {"AUTOFLOW_MODEL_LAYER": "flowtree"})
        assert mod == "autoflow.flowtree", f"flowtree module = {mod!r}"
        return {
            "input": "AUTOFLOW_MODEL_LAYER='flowtree'",
            "output": mod,
            "result": "✓ flowtree explicit",
        }
    _run_test(collector, stage, "25.3", "AUTOFLOW_MODEL_LAYER=flowtree", t_25_3)

    def t_25_4():
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
    _run_test(collector, stage, "25.4", "Invalid model layer fails fast", t_25_4)

    _print_stage_summary(collector, stage)
