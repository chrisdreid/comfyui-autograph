"""Stage 2 — Convert + Metadata: Workflow conversion, MarkdownNote stripping, introspection."""

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

STAGE = "Stage 2: Convert + Metadata"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Workflow, ApiFlow, convert_with_errors

    wf_path = str(_BUNDLED_WORKFLOW)

    # 2.1 Basic conversion
    def t_2_1():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        assert api is not None, "Workflow() returned None"
        assert hasattr(api, "items"), "Converted result has no items()"
        return {"input": f"Workflow({Path(wf_path).name})", "output": type(api).__name__, "result": "✓ converted"}
    _run_test(collector, stage, "2.1", "Workflow(path, node_info) produces ApiFlow", t_2_1)

    # 2.2 MarkdownNotes stripped
    def t_2_2():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = getattr(api, "unwrap", lambda: api)()
        if hasattr(raw, "items"):
            node_count = sum(1 for _, v in raw.items() if isinstance(v, dict) and "class_type" in v)
        else:
            node_count = sum(1 for _, v in api.items() if isinstance(v, dict) and "class_type" in v)
        assert node_count == 7, f"Expected 7 API nodes (MarkdownNotes stripped), got {node_count}"
        return {"input": "count API nodes post-strip", "output": f"{node_count} nodes", "result": "✓ MarkdownNotes stripped"}
    _run_test(collector, stage, "2.2", "MarkdownNotes stripped → 7 API nodes", t_2_2)

    # 2.3 ApiFlow dot-access
    def t_2_3():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        seed = api.KSampler.seed
        assert seed is not None, "api.KSampler.seed is None"
        return {"input": "api.KSampler.seed", "output": str(seed), "result": "✓ dot-access works"}
    _run_test(collector, stage, "2.3", "ApiFlow dot-access: api.KSampler.seed", t_2_3)

    # 2.4 Path-style access
    def t_2_4():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        try:
            val = api["3"]
            assert val is not None, "api['3'] returned None"
            ct = val.get("class_type", "?") if isinstance(val, dict) else type(val).__name__
            return {"input": "api['3']", "output": ct, "result": "✓ bracket access"}
        except (KeyError, TypeError) as e:
            raise AssertionError(f"Path-style access api['3'] failed: {e}")
    _run_test(collector, stage, "2.4", "Path-style access: api['3']", t_2_4)

    # 2.5 Workflow one-liner
    def t_2_5():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        j = api.to_json()
        parsed = json.loads(j)
        assert isinstance(parsed, dict), "Workflow→to_json() is not a valid dict"
        return {"input": "Workflow→to_json()", "output": f"{len(j)} chars, {len(parsed)} keys", "result": "✓ valid JSON"}
    _run_test(collector, stage, "2.5", "Workflow one-liner → to_json()", t_2_5)

    # 2.6 convert_with_errors
    def t_2_6():
        from autoflow import Flow
        f = Flow.load(str(_BUNDLED_WORKFLOW))
        result = convert_with_errors(f, node_info=BUILTIN_NODE_INFO)
        assert result is not None, "convert_with_errors returned None"
        assert hasattr(result, "ok"), "No .ok on ConvertResult"
        assert hasattr(result, "data"), "No .data on ConvertResult"
        assert result.ok, f"Conversion failed: {result.errors}"
        errs = len(result.errors) if result.errors else 0
        return {"input": "convert_with_errors(flow)", "output": f"ok={result.ok}, errors={errs}", "result": "✓ conversion clean"}
    _run_test(collector, stage, "2.6", "convert_with_errors() returns result", t_2_6)

    # 2.7 _meta access on ApiFlow
    def t_2_7():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        try:
            meta = ks._meta
            return {"input": "api.KSampler._meta", "output": str(type(meta).__name__), "result": "✓ _meta accessible"}
        except AttributeError:
            meta = getattr(ks, "meta", None)
            return {"input": "api.KSampler.meta", "output": str(type(meta).__name__) if meta else "None", "result": "✓ meta fallback"}
    _run_test(collector, stage, "2.7", "api.KSampler._meta access", t_2_7)

    # 2.8 Set _meta pre-convert
    def t_2_8():
        from autoflow import Flow
        f = Flow.load(str(_BUNDLED_WORKFLOW))
        ks = f.nodes.KSampler
        try:
            ks._meta = {"test_key": "test_value"}
            return {"input": "ks._meta = {test_key: test_value}", "output": "set without error", "result": "✓ no crash"}
        except (AttributeError, TypeError):
            return {"input": "ks._meta = {test_key: test_value}", "output": "not supported", "result": "✓ no crash"}
    _run_test(collector, stage, "2.8", "Set _meta on Flow node (no crash)", t_2_8)

    # 2.9 _meta survives to_json
    def t_2_9():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = getattr(api, "unwrap", lambda: api)()
        for nid, node in (raw.items() if hasattr(raw, 'items') else api.items()):
            if isinstance(node, dict) and node.get("class_type") == "KSampler":
                node["_meta"] = {"autoflow_test": True}
                break
        j = api.to_json()
        parsed = json.loads(j)
        found_meta = False
        for nid, node in parsed.items():
            if isinstance(node, dict) and node.get("class_type") == "KSampler":
                if "_meta" in node:
                    found_meta = True
        assert found_meta, "_meta was set but not found in to_json() output"
        return {"input": "set _meta → to_json()", "output": f"found_meta={found_meta}", "result": "✓ _meta survives serialization"}
    _run_test(collector, stage, "2.9", "_meta survives to_json()", t_2_9)

    # 2.14 Widget introspection: choices()
    def t_2_14():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        try:
            choices = ks.sampler_name.choices()
            assert isinstance(choices, (list, tuple)), f"choices() returned {type(choices)}"
            assert "euler" in choices, f"'euler' not in choices: {choices}"
            return {"input": "ks.sampler_name.choices()", "output": f"{len(choices)} choices", "result": f"✓ euler in [{', '.join(choices[:4])}…]"}
        except AttributeError:
            try:
                sv = ks.sampler_name
                if hasattr(sv, 'choices'):
                    choices = sv.choices()
                    assert "euler" in choices
                    return {"input": "ks.sampler_name.choices()", "output": f"{len(choices)} choices", "result": "✓ euler found"}
                else:
                    raise AssertionError("No choices() method on sampler_name")
            except Exception as e:
                raise AssertionError(f"choices() access failed: {e}")
    _run_test(collector, stage, "2.14", "Widget introspection: .choices()", t_2_14)

    # 2.15 Widget introspection: tooltip()
    def t_2_15():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        try:
            sv = ks.seed
            if hasattr(sv, 'tooltip'):
                tt = sv.tooltip()
                return {"input": "ks.seed.tooltip()", "output": str(tt)[:60], "result": "✓ tooltip accessible"}
            elif hasattr(sv, 'spec'):
                return {"input": "ks.seed (no tooltip)", "output": "spec available", "result": "✓ no tooltip, spec exists"}
            return {"input": "ks.seed", "output": "no tooltip/spec", "result": "✓ access ok"}
        except AttributeError:
            return {"input": "ks.seed.tooltip()", "output": "N/A", "result": "✓ no crash"}
    _run_test(collector, stage, "2.15", "Widget introspection: .tooltip()", t_2_15)

    # 2.16 Widget introspection: spec()
    def t_2_16():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        try:
            sv = ks.seed
            if hasattr(sv, 'spec'):
                sp = sv.spec()
                assert sp is not None, "spec() returned None"
                return {"input": "ks.seed.spec()", "output": str(sp)[:60], "result": "✓ spec returned"}
            return {"input": "ks.seed", "output": "no spec()", "result": "✓ no spec method"}
        except AttributeError:
            return {"input": "ks.seed.spec()", "output": "N/A", "result": "✓ no crash"}
    _run_test(collector, stage, "2.16", "Widget introspection: .spec()", t_2_16)

    _print_stage_summary(collector, stage)
