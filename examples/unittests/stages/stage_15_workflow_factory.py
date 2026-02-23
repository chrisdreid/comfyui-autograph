"""Stage 15 — Workflow Factory: dict/JSON/path inputs, auto_convert, load."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 15: Workflow Factory"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Workflow, ApiFlow

    wf_path = str(_BUNDLED_WORKFLOW)
    with open(wf_path, "r", encoding="utf-8") as fh:
        wf_str = fh.read()
    wf_dict = json.loads(wf_str)

    def t_15_1():
        api = Workflow(wf_dict, node_info=BUILTIN_NODE_INFO)
        assert isinstance(api, ApiFlow), f"Workflow(dict) returned {type(api)}"
        return {"input": f"Workflow(dict, {len(wf_dict)} keys)", "output": f"ApiFlow len={len(api)}", "result": "✓ dict input"}
    _run_test(collector, stage, "15.1", "Workflow(dict) → ApiFlow", t_15_1)

    def t_15_2():
        api = Workflow(wf_str, node_info=BUILTIN_NODE_INFO)
        assert isinstance(api, ApiFlow), f"Workflow(JSON str) returned {type(api)}"
        return {"input": f"Workflow(str, {len(wf_str)} chars)", "output": f"ApiFlow len={len(api)}", "result": "✓ JSON string"}
    _run_test(collector, stage, "15.2", "Workflow(JSON string) → ApiFlow", t_15_2)

    def t_15_3():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        assert isinstance(api, ApiFlow), f"Workflow(path) returned {type(api)}"
        return {"input": f"Workflow({Path(wf_path).name})", "output": f"ApiFlow len={len(api)}", "result": "✓ path input"}
    _run_test(collector, stage, "15.3", "Workflow(path) → ApiFlow", t_15_3)



    _print_stage_summary(collector, stage)
