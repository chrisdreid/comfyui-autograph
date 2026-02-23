"""Stage 15 — ApiFlow Auto-Detect: dict/JSON/path inputs, auto_convert, load."""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 15: ApiFlow Auto-Detect"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import ApiFlow

    wf_path = str(_BUNDLED_WORKFLOW)
    with open(wf_path, "r", encoding="utf-8") as fh:
        wf_str = fh.read()
    wf_dict = json.loads(wf_str)

    def t_15_1():
        api = ApiFlow(wf_dict, node_info=BUILTIN_NODE_INFO)
        assert isinstance(api, ApiFlow), f"ApiFlow(dict) returned {type(api)}"
        return {"input": f"ApiFlow(dict, {len(wf_dict)} keys)", "output": f"ApiFlow len={len(api)}", "result": "✓ dict input"}
    _run_test(collector, stage, "15.1", "ApiFlow(dict) → ApiFlow", t_15_1)

    def t_15_2():
        api = ApiFlow(wf_str, node_info=BUILTIN_NODE_INFO)
        assert isinstance(api, ApiFlow), f"ApiFlow(JSON str) returned {type(api)}"
        return {"input": f"ApiFlow(str, {len(wf_str)} chars)", "output": f"ApiFlow len={len(api)}", "result": "✓ JSON string"}
    _run_test(collector, stage, "15.2", "ApiFlow(JSON string) → ApiFlow", t_15_2)

    def t_15_3():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        assert isinstance(api, ApiFlow), f"ApiFlow(path) returned {type(api)}"
        return {"input": f"ApiFlow({Path(wf_path).name})", "output": f"ApiFlow len={len(api)}", "result": "✓ path input"}
    _run_test(collector, stage, "15.3", "ApiFlow(path) → ApiFlow", t_15_3)

    def t_15_4():
        from autoflow import Workflow
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
            assert len(w) >= 1, "No DeprecationWarning emitted"
            assert issubclass(w[0].category, DeprecationWarning), f"Wrong warning type: {w[0].category}"
            assert isinstance(api, ApiFlow), f"Workflow(path) did not return ApiFlow: {type(api)}"
        return {"input": "Workflow(path)", "output": f"ApiFlow + DeprecationWarning", "result": "✓ deprecated alias works"}
    _run_test(collector, stage, "15.4", "Workflow() emits DeprecationWarning", t_15_4)



    _print_stage_summary(collector, stage)
