"""Stage 17 — Map Helpers: map_strings, map_paths, force_recompute, api_mapping, meta."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 17: Map Helpers"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Workflow, map_strings, map_paths, force_recompute, api_mapping

    wf_path = str(_BUNDLED_WORKFLOW)

    # --- map_strings ---
    def t_17_1():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = copy.deepcopy(dict(api.unwrap()))
        spec = {"replacements": {"literal": {"Default": "MAPPED"}}}
        result = map_strings(raw, spec)
        j = json.dumps(result)
        assert "MAPPED" in j, "Literal replacement not found"
        return {"input": "map_strings literal 'Default'→'MAPPED'", "output": "MAPPED found", "result": "✓ literal"}
    _run_test(collector, stage, "17.1", "map_strings literal replacement", t_17_1)

    def t_17_2():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = copy.deepcopy(dict(api.unwrap()))
        spec = {"replacements": {"regex": {"output_\\d+": "gen_img"}}}
        result = map_strings(raw, spec)
        j = json.dumps(result)
        if "output_" in json.dumps(raw):
            assert "gen_img" in j, "Regex replacement not applied"
            return {"input": "map_strings regex 'output_\\d+'→'gen_img'", "output": "gen_img found", "result": "✓ regex"}
        return {"input": "map_strings regex (no match in source)", "output": "no match", "result": "✓ no-op"}
    _run_test(collector, stage, "17.2", "map_strings regex replacement", t_17_2)

    def t_17_3():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = copy.deepcopy(dict(api.unwrap()))
        os.environ["_AF_TEST_MAP"] = "env_expanded"
        try:
            spec = {"replacements": {"literal": {"Default": "$_AF_TEST_MAP"}}, "expand_env": True}
            result = map_strings(raw, spec)
            j = json.dumps(result)
            assert "env_expanded" in j, "Env expansion not applied"
            return {"input": "$_AF_TEST_MAP → env_expanded", "output": "env_expanded found", "result": "✓ env expansion"}
        finally:
            os.environ.pop("_AF_TEST_MAP", None)
    _run_test(collector, stage, "17.3", "map_strings env expansion", t_17_3)

    def t_17_4():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = copy.deepcopy(dict(api.unwrap()))
        spec = {"replacements": {"file": "/tmp/_af_test_rules.txt"}}
        Path("/tmp/_af_test_rules.txt").write_text("Default=FILE_MAPPED\n", encoding="utf-8")
        try:
            result = map_strings(raw, spec)
            j = json.dumps(result)
            if "FILE_MAPPED" in j:
                return {"input": "map_strings file rules", "output": "FILE_MAPPED found", "result": "✓ file rules"}
            return {"input": "map_strings file rules", "output": "no match in source", "result": "✓ rules parsed"}
        finally:
            Path("/tmp/_af_test_rules.txt").unlink(missing_ok=True)
    _run_test(collector, stage, "17.4", "map_strings file rules", t_17_4)

    # --- map_paths ---
    def t_17_5():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = copy.deepcopy(dict(api.unwrap()))
        spec = {"replacements": {"literal": {"/old/path": "/new/path"}}}
        result = map_paths(raw, spec)
        assert isinstance(result, dict), f"map_paths returned {type(result)}"
        return {"input": "map_paths(spec={literal: /old→/new})", "output": type(result).__name__, "result": "✓ path mapping"}
    _run_test(collector, stage, "17.5", "map_paths(flow, spec)", t_17_5)

    # --- force_recompute ---
    def t_17_6():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        result = force_recompute(api)
        assert result is not None, "force_recompute returned None"
        return {"input": "force_recompute(api)", "output": type(result).__name__, "result": "✓ cache-bust"}
    _run_test(collector, stage, "17.6", "force_recompute()", t_17_6)

    # --- api_mapping callback ---
    def t_17_7():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        contexts = []
        def cb(ctx):
            contexts.append(ctx)
            return None
        api_mapping(api, cb, node_info=BUILTIN_NODE_INFO)
        assert len(contexts) > 0, "Callback never called"
        return {"input": f"api_mapping(cb)", "output": f"{len(contexts)} calls", "result": "✓ callback fired"}
    _run_test(collector, stage, "17.7", "api_mapping callback invocations", t_17_7)

    def t_17_8():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        ctx_keys = set()
        def cb(ctx):
            ctx_keys.update(ctx.keys())
            return None
        api_mapping(api, cb, node_info=BUILTIN_NODE_INFO)
        expected = {"node_id", "class_type", "param", "value"}
        missing = expected - ctx_keys
        assert not missing, f"Missing context keys: {missing}"
        return {"input": "api_mapping context keys", "output": ", ".join(sorted(ctx_keys)), "result": f"✓ {len(ctx_keys)} keys"}
    _run_test(collector, stage, "17.8", "api_mapping context has full keys", t_17_8)

    def t_17_9():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        def cb(ctx):
            if ctx.get("param") == "seed":
                return 12345
            return None
        result = api_mapping(api, cb, node_info=BUILTIN_NODE_INFO)
        for nid, node in result.items():
            if isinstance(node, dict) and node.get("class_type") == "KSampler":
                assert node["inputs"]["seed"] == 12345, f"Seed = {node['inputs']['seed']}"
                return {"input": "cb: seed→12345", "output": f"seed={node['inputs']['seed']}", "result": "✓ overwrite"}
        return {"input": "cb: seed→12345", "output": "no KSampler", "result": "✓ ran"}
    _run_test(collector, stage, "17.9", "api_mapping typed overwrite", t_17_9)

    # --- verify api_mapping doesn't crash with empty callback ---
    def t_17_10():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        result = api_mapping(api, lambda ctx: None, node_info=BUILTIN_NODE_INFO)
        assert isinstance(result, dict), f"api_mapping returned {type(result)}"
        return {"input": "api_mapping(lambda ctx: None)", "output": type(result).__name__, "result": "✓ no-op callback"}
    _run_test(collector, stage, "17.10", "api_mapping with no-op callback", t_17_10)

    _print_stage_summary(collector, stage)
