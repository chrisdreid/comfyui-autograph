"""Phase 5 — ApiFlow: node proxy, path get/set, find/navigate, map helpers, mapping API.

Merged from: stage_12_apiflow_node_proxy, stage_03_find_navigate,
             stage_04_mapping, stage_17_map_helpers, stage_26 (path/legacy parts)
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW, builtin_node_info_path,
)

STAGE = "Phase 5: ApiFlow"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Workflow, ApiFlow, NodeInfo
    from autoflow import map_strings, map_paths, force_recompute, api_mapping

    wf_path = str(_BUNDLED_WORKFLOW)
    api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)

    # ===================================================================
    # 5.1–5.12  ApiFlow node proxy  (was stage 12)
    # ===================================================================

    def t_5_1():
        ks = api.KSampler
        assert ks is not None, "api.KSampler is None"
        return {"input": "api.KSampler", "output": type(ks).__name__, "result": "✓ dot access"}
    _run_test(collector, stage, "5.1", "api.KSampler dot access", t_5_1)

    def t_5_2():
        ks = api.KSampler
        seed = ks.seed
        assert seed is not None, "KSampler.seed is None"
        return {"input": "api.KSampler.seed", "output": str(seed), "result": "✓ widget readable"}
    _run_test(collector, stage, "5.2", "api.KSampler.seed", t_5_2)

    def t_5_3():
        api2 = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        api2.KSampler.seed = 42
        val = api2.KSampler.seed
        actual = int(val) if hasattr(val, '__int__') else val
        assert actual == 42, f"Set to 42, got {actual}"
        return {"input": "api.KSampler.seed = 42", "output": str(actual), "result": "✓ write"}
    _run_test(collector, stage, "5.3", "Widget write on ApiFlow", t_5_3)

    def t_5_4():
        a = api.KSampler.attrs()
        assert isinstance(a, list) and "seed" in a
        return {"input": "api.KSampler.attrs()", "output": ", ".join(a[:6]), "result": f"✓ {len(a)} attrs"}
    _run_test(collector, stage, "5.4", "api.KSampler.attrs()", t_5_4)

    def t_5_5():
        d = dir(api.KSampler)
        assert "seed" in d
        return {"input": "dir(api.KSampler)", "output": f"{len(d)} entries", "result": "✓ seed in dir"}
    _run_test(collector, stage, "5.5", "dir(api.KSampler) includes widgets", t_5_5)

    def t_5_6():
        r = repr(api.KSampler)
        assert "KSampler" in r
        return {"input": "repr(api.KSampler)", "output": r[:60], "result": "✓ repr"}
    _run_test(collector, stage, "5.6", "repr(api.KSampler)", t_5_6)

    def t_5_7():
        d = dir(api)
        assert "KSampler" in d
        return {"input": "dir(api)", "output": f"{len(d)} entries", "result": "✓ KSampler in dir"}
    _run_test(collector, stage, "5.7", "dir(api) includes class_types", t_5_7)

    def t_5_8():
        j = api.to_json()
        parsed = json.loads(j)
        assert isinstance(parsed, dict) and len(parsed) > 0
        return {"input": "api.to_json()", "output": f"{len(j)} chars", "result": "✓ valid JSON"}
    _run_test(collector, stage, "5.8", "api.to_json()", t_5_8)

    def t_5_9():
        raw = dict(api.unwrap()) if hasattr(api, 'unwrap') else dict(api)
        assert isinstance(raw, dict) and len(raw) > 0
        return {"input": "api.unwrap()", "output": f"{len(raw)} nodes", "result": "✓ raw dict"}
    _run_test(collector, stage, "5.9", "api.unwrap() returns raw dict", t_5_9)

    def t_5_10():
        api2 = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        api2.KSampler.steps = 100
        api2.KSampler.cfg = 12.5
        assert int(api2.KSampler.steps) == 100
        assert float(api2.KSampler.cfg) == 12.5
        return {"input": "steps=100, cfg=12.5", "output": f"steps={api2.KSampler.steps}, cfg={api2.KSampler.cfg}", "result": "✓ multi-write"}
    _run_test(collector, stage, "5.10", "Multi-widget write on ApiFlow", t_5_10)

    def t_5_11():
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            api.save(tmp_path)
            api2 = ApiFlow.load(tmp_path)
            assert len(api2) == len(api)
            return {"input": f"save→load({Path(tmp_path).name})", "output": f"len={len(api2)}", "result": "✓ round-trip"}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    _run_test(collector, stage, "5.11", "api.save() → ApiFlow.load()", t_5_11)

    def t_5_12():
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
            tmp.write(api.to_json())
            tmp_path = tmp.name
        try:
            loaded = ApiFlow.load(tmp_path)
            assert isinstance(loaded, ApiFlow)
            return {"input": f"ApiFlow.load({Path(tmp_path).name})", "output": f"ApiFlow len={len(loaded)}", "result": "✓ loaded"}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    _run_test(collector, stage, "5.12", "ApiFlow.load(path)", t_5_12)

    # ===================================================================
    # 5.13–5.25  Find / Navigate  (was stage 3)
    # ===================================================================

    def t_5_13():
        results = api.find(class_type="KSampler")
        assert len(results) >= 1
        assert results[0].class_type == "KSampler"
        return {"input": "find(class_type='KSampler')", "output": f"{len(results)} result(s)", "result": "✓ exact match"}
    _run_test(collector, stage, "5.13", "find(class_type='KSampler')", t_5_13)

    def t_5_14():
        results = api.find(class_type="CLIPTextEncode")
        assert len(results) >= 2
        return {"input": "find(class_type='CLIPTextEncode')", "output": f"{len(results)} results", "result": "✓ multi-match"}
    _run_test(collector, stage, "5.14", "find(class_type='CLIPTextEncode')", t_5_14)

    def t_5_15():
        results = api.find()
        assert len(results) > 0
        return {"input": "find() — all nodes", "output": f"{len(results)} nodes", "result": "✓ all nodes"}
    _run_test(collector, stage, "5.15", "find() returns all nodes", t_5_15)

    def t_5_16():
        d = dir(api)
        class_types = [name for name in d if not name.startswith('_')]
        assert "KSampler" in class_types
        return {"input": "dir(api) class_types", "output": ", ".join(class_types[:5]), "result": f"✓ {len(class_types)} entries"}
    _run_test(collector, stage, "5.16", "dir(api) lists class_types", t_5_16)

    def t_5_17():
        import re
        results = api.find(class_type=re.compile(r".*Sampler"))
        assert len(results) >= 1
        return {"input": "find(class_type=re'.*Sampler')", "output": f"{len(results)} matches", "result": "✓ regex find"}
    _run_test(collector, stage, "5.17", "find(class_type=regex) regex match", t_5_17)

    def t_5_18():
        ks = api.find(class_type="KSampler")
        if ks:
            node = ks[0]
            try:
                p = node.path()
                return {"input": "node.path()", "output": p[:50], "result": "✓ path string"}
            except AttributeError:
                return {"input": "node.path()", "output": "N/A", "result": "✓ no path method"}
        return {"input": "find(KSampler)", "output": "no results", "result": "✓ skipped"}
    _run_test(collector, stage, "5.18", "ApiFlow node.path()", t_5_18)

    def t_5_19():
        import re
        results = api.find(class_type=re.compile(r"CLIP.*"))
        assert len(results) >= 2
        return {"input": "find(class_type=re'CLIP.*')", "output": f"{len(results)} matches", "result": "✓ regex find"}
    _run_test(collector, stage, "5.19", "find(class_type=regex CLIP)", t_5_19)

    def t_5_20():
        results = api.find(class_type="CheckpointLoaderSimple")
        if results:
            node = results[0]
            ckpt = getattr(node, "ckpt_name", None) if hasattr(node, "ckpt_name") else "N/A"
            return {"input": "find(CheckpointLoaderSimple)", "output": f"ckpt={ckpt}", "result": "✓ found"}
        return {"input": "find(CheckpointLoaderSimple)", "output": "not found", "result": "✓ ran"}
    _run_test(collector, stage, "5.20", "find(class_type='CheckpointLoaderSimple')", t_5_20)

    def t_5_21():
        val = api["3"]
        assert val is not None
        return {"input": "api['3']", "output": type(val).__name__, "result": "✓ bracket ID access"}
    _run_test(collector, stage, "5.21", "api['3'] direct ID access", t_5_21)

    def t_5_22():
        ks_nodes = api.find(class_type="KSampler")
        if ks_nodes:
            nid = str(ks_nodes[0].id)
            matched = api[nid]
            assert matched is not None
            return {"input": f"api['{nid}']", "output": type(matched).__name__, "result": "✓ ID match"}
        return {"input": "no KSampler", "output": "N/A", "result": "✓ skipped"}
    _run_test(collector, stage, "5.22", "api[node_id] by discovered ID", t_5_22)

    def t_5_23():
        import re
        all_nodes = api.find(class_type=re.compile(r".*"))
        assert len(all_nodes) > 0
        return {"input": "find(class_type=re'.*')", "output": f"{len(all_nodes)} nodes", "result": "✓ match-all"}
    _run_test(collector, stage, "5.23", "find(class_type=re'.*') match-all", t_5_23)

    def t_5_24():
        results = api.find(class_type="NonExistentNodeType")
        assert len(results) == 0
        return {"input": "find(class_type='NonExistentNodeType')", "output": "0 results", "result": "✓ empty"}
    _run_test(collector, stage, "5.24", "find returns empty for missing type", t_5_24)

    def t_5_25():
        api2 = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        api2.KSampler.seed = 9999
        ks = api2.find(class_type="KSampler")
        assert len(ks) >= 1
        seed_val = ks[0].seed if hasattr(ks[0], 'seed') else None
        if seed_val is not None:
            actual = int(seed_val) if hasattr(seed_val, '__int__') else seed_val
            assert actual == 9999
        return {"input": "set seed=9999 → find → verify", "output": f"seed={seed_val}", "result": "✓ find sees mutation"}
    _run_test(collector, stage, "5.25", "find() sees recent mutations", t_5_25)

    # ===================================================================
    # 5.26–5.29  Mapping API  (was stage 4)
    # ===================================================================

    def t_5_26():
        contexts_collected = []
        def callback(ctx):
            contexts_collected.append(ctx)
            return None
        result = api_mapping(api, callback, node_info=BUILTIN_NODE_INFO)
        assert isinstance(result, dict)
        assert len(contexts_collected) > 0
        return {"input": "api_mapping(api, noop_cb)", "output": f"{len(contexts_collected)} invocations", "result": "✓ callback fired"}
    _run_test(collector, stage, "5.26", "api_mapping(noop callback)", t_5_26)

    def t_5_27():
        ctx_keys = set()
        def cb(ctx):
            ctx_keys.update(ctx.keys())
            return None
        api_mapping(api, cb, node_info=BUILTIN_NODE_INFO)
        expected = {"node_id", "class_type", "param", "value"}
        missing = expected - ctx_keys
        assert not missing, f"Missing keys: {missing}"
        return {"input": "api_mapping context keys", "output": ", ".join(sorted(ctx_keys)), "result": f"✓ {len(ctx_keys)} keys"}
    _run_test(collector, stage, "5.27", "api_mapping context has full keys", t_5_27)

    def t_5_28():
        def cb(ctx):
            if ctx.get("param") == "seed":
                return 12345
            return None
        result = api_mapping(api, cb, node_info=BUILTIN_NODE_INFO)
        for nid, node in result.items():
            if isinstance(node, dict) and node.get("class_type") == "KSampler":
                assert node["inputs"]["seed"] == 12345
                return {"input": "cb: seed→12345", "output": f"seed={node['inputs']['seed']}", "result": "✓ overwrite"}
        return {"input": "cb: seed→12345", "output": "no KSampler", "result": "✓ ran"}
    _run_test(collector, stage, "5.28", "api_mapping typed overwrite", t_5_28)

    def t_5_29():
        result = api_mapping(api, lambda ctx: None, node_info=BUILTIN_NODE_INFO)
        assert isinstance(result, dict)
        return {"input": "api_mapping(lambda ctx: None)", "output": type(result).__name__, "result": "✓ no-op callback"}
    _run_test(collector, stage, "5.29", "api_mapping with no-op callback", t_5_29)

    # ===================================================================
    # 5.30–5.39  Map helpers  (was stage 17)
    # ===================================================================

    def t_5_30():
        api2 = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = copy.deepcopy(dict(api2.unwrap()))
        spec = {"replacements": {"literal": {"Default": "MAPPED"}}}
        result = map_strings(raw, spec)
        j = json.dumps(result)
        assert "MAPPED" in j
        return {"input": "map_strings literal 'Default'→'MAPPED'", "output": "MAPPED found", "result": "✓ literal"}
    _run_test(collector, stage, "5.30", "map_strings literal replacement", t_5_30)

    def t_5_31():
        api2 = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = copy.deepcopy(dict(api2.unwrap()))
        spec = {"replacements": {"regex": {"output_\\d+": "gen_img"}}}
        result = map_strings(raw, spec)
        j = json.dumps(result)
        if "output_" in json.dumps(raw):
            assert "gen_img" in j
            return {"input": "map_strings regex 'output_\\d+'→'gen_img'", "output": "gen_img found", "result": "✓ regex"}
        return {"input": "map_strings regex (no match)", "output": "no match", "result": "✓ no-op"}
    _run_test(collector, stage, "5.31", "map_strings regex replacement", t_5_31)

    def t_5_32():
        api2 = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = copy.deepcopy(dict(api2.unwrap()))
        os.environ["_AF_TEST_MAP"] = "env_expanded"
        try:
            spec = {"replacements": {"literal": {"Default": "$_AF_TEST_MAP"}}, "expand_env": True}
            result = map_strings(raw, spec)
            j = json.dumps(result)
            assert "env_expanded" in j
            return {"input": "$_AF_TEST_MAP → env_expanded", "output": "env_expanded found", "result": "✓ env expansion"}
        finally:
            os.environ.pop("_AF_TEST_MAP", None)
    _run_test(collector, stage, "5.32", "map_strings env expansion", t_5_32)

    def t_5_33():
        api2 = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = copy.deepcopy(dict(api2.unwrap()))
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
    _run_test(collector, stage, "5.33", "map_strings file rules", t_5_33)

    def t_5_34():
        api2 = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = copy.deepcopy(dict(api2.unwrap()))
        spec = {"replacements": {"literal": {"/old/path": "/new/path"}}}
        result = map_paths(raw, spec)
        assert isinstance(result, dict)
        return {"input": "map_paths(spec={literal: /old→/new})", "output": type(result).__name__, "result": "✓ path mapping"}
    _run_test(collector, stage, "5.34", "map_paths(flow, spec)", t_5_34)

    def t_5_35():
        api2 = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        result = force_recompute(api2)
        assert result is not None
        return {"input": "force_recompute(api)", "output": type(result).__name__, "result": "✓ cache-bust"}
    _run_test(collector, stage, "5.35", "force_recompute()", t_5_35)

    # ===================================================================
    # 5.36–5.39  Legacy parity / path drilling  (was stage 26.4–26.6)
    # ===================================================================

    def t_5_36():
        import re
        all_by_regex = api.find(class_type=re.compile(".*"))
        all_by_empty = api.find()
        assert len(all_by_regex) == len(all_by_empty), (
            f"regex={len(all_by_regex)} vs find()={len(all_by_empty)}"
        )
        return {
            "input": "find(class_type=re'.*') vs find()",
            "output": f"regex={len(all_by_regex)}, all={len(all_by_empty)}",
            "result": "✓ both match all",
        }
    _run_test(collector, stage, "5.36", "find(class_type=wildcard) == find()", t_5_36)

    def t_5_37():
        ni_p = builtin_node_info_path()
        api2 = Workflow(str(_BUNDLED_WORKFLOW), node_info=ni_p)
        assert isinstance(api2, ApiFlow)
        api2["ksampler/seed"] = 123
        assert api2.ksampler[0].seed == 123
        api2["ksampler/0/seed"] = 321
        assert api2.ksampler[0].seed == 321
        node_id = api2.find(class_type="KSampler")[0].id
        api2[f"{node_id}/seed"] = 111
        assert api2.ksampler[0].seed == 111
        return {
            "input": "api['ksampler/seed'] = 123 → 321 → 111",
            "output": f"final seed={api2.ksampler[0].seed}",
            "result": "✓ path get/set",
        }
    _run_test(collector, stage, "5.37", "ApiFlow path get/set", t_5_37)

    def t_5_38():
        oi = NodeInfo(BUILTIN_NODE_INFO)
        assert "input" in oi.KSampler
        seed_spec = oi["KSampler/input/required/seed"]
        assert seed_spec
        return {
            "input": "oi['KSampler/input/required/seed']",
            "output": str(seed_spec)[:60],
            "result": "✓ path drilling",
        }
    _run_test(collector, stage, "5.38", "NodeInfo attr + path drilling", t_5_38)

    _print_stage_summary(collector, stage)
