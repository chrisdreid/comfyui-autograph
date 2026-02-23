"""Stage 4 — Mapping: map_strings, force_recompute, api_mapping callbacks."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 4: Mapping"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Workflow, api_mapping, map_strings, force_recompute

    wf_path = str(_BUNDLED_WORKFLOW)

    def t_4_1():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = copy.deepcopy(dict(api.unwrap()))
        spec = {
            "replacements": {
                "literal": {"Default": "REPLACED_PREFIX"}
            }
        }
        result = map_strings(raw, spec)
        j = json.dumps(result)
        assert "REPLACED_PREFIX" in j, f"Literal string replacement not found in output: {j[:400]}"
        return {"input": "map_strings('Default'→'REPLACED_PREFIX')", "output": "REPLACED_PREFIX found", "result": "✓ literal replacement"}
    _run_test(collector, stage, "4.1", "map_strings() literal replacement", t_4_1)

    def t_4_5():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        result = force_recompute(api)
        assert result is not None, "force_recompute returned None"
        return {"input": "force_recompute(api)", "output": type(result).__name__, "result": "✓ cache-bust applied"}
    _run_test(collector, stage, "4.5", "force_recompute()", t_4_5)

    def t_4_7():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        contexts_received: List[Dict] = []
        def cb(ctx):
            contexts_received.append(ctx)
            return None
        api_mapping(api, cb, node_info=BUILTIN_NODE_INFO)
        assert len(contexts_received) > 0, "api_mapping callback was never called"
        ctx0 = contexts_received[0]
        expected_keys = {"node_id", "class_type", "param", "value"}
        actual_keys = set(ctx0.keys())
        missing = expected_keys - actual_keys
        assert not missing, f"Callback context missing keys: {missing}. Got: {actual_keys}"
        return {"input": f"api_mapping(cb) → {len(contexts_received)} calls", "output": f"keys: {', '.join(sorted(actual_keys))}", "result": "✓ full context"}
    _run_test(collector, stage, "4.7", "api_mapping callback receives full context", t_4_7)

    def t_4_8():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        def cb(ctx):
            if ctx.get("param") == "seed":
                return 999999
            return None
        result = api_mapping(api, cb, node_info=BUILTIN_NODE_INFO)
        for nid, node in result.items():
            if isinstance(node, dict) and node.get("class_type") == "KSampler":
                assert node["inputs"]["seed"] == 999999, f"Seed overwrite failed: {node['inputs'].get('seed')}"
                return {"input": "cb: seed→999999", "output": f"KSampler.seed={node['inputs']['seed']}", "result": "✓ typed overwrite"}
        return {"input": "cb: seed→999999", "output": "no KSampler found", "result": "✓ callback ran"}
    _run_test(collector, stage, "4.8", "api_mapping typed overwrite (return value)", t_4_8)

    _print_stage_summary(collector, stage)
