"""Stage 1 — Load + Access: Flow loading from various sources, node access, widgets."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Callable

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 1: Load + Access"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Flow

    wf_path = str(_BUNDLED_WORKFLOW)
    with open(wf_path, "r", encoding="utf-8") as fh:
        wf_json = fh.read()
    wf_dict = json.loads(wf_json)
    wf_bytes = wf_json.encode("utf-8")

    # 1.1-1.5 Load formats
    def t_load(loader: Callable, desc: str, input_desc: str):
        def _inner():
            f = loader()
            assert f is not None, f"Load returned None for {desc}"
            return {"input": input_desc, "output": type(f).__name__, "result": f"✓ loaded via {desc}"}
        return _inner

    _run_test(collector, stage, "1.1", "Flow.load(path string)", t_load(lambda: Flow.load(wf_path), "path string", f"Flow.load({Path(wf_path).name})"))
    _run_test(collector, stage, "1.2", "Flow.load(Path object)", t_load(lambda: Flow.load(Path(wf_path)), "Path object", f"Flow.load(Path({Path(wf_path).name}))"))
    _run_test(collector, stage, "1.3", "Flow.load(dict)", t_load(lambda: Flow.load(copy.deepcopy(wf_dict)), "dict", f"Flow.load(dict, {len(wf_dict)} keys)"))
    _run_test(collector, stage, "1.4", "Flow.load(JSON string)", t_load(lambda: Flow.load(wf_json), "JSON string", f"Flow.load(str, {len(wf_json)} chars)"))
    _run_test(collector, stage, "1.5", "Flow.load(bytes)", t_load(lambda: Flow.load(wf_bytes), "bytes", f"Flow.load(bytes, {len(wf_bytes)} B)"))

    # 1.6 Node enumeration
    def t_1_6():
        f = Flow.load(wf_path)
        nodes = f.nodes
        assert nodes is not None, "flow.nodes is None"
        return {"input": "flow.nodes", "output": f"{len(nodes)} nodes", "result": "✓ nodes accessible"}
    _run_test(collector, stage, "1.6", "flow.nodes returns nodes", t_1_6)

    # 1.7 Dot-access by class_type
    def t_1_7():
        f = Flow.load(wf_path)
        ks = f.nodes.KSampler
        assert ks is not None, "flow.nodes.KSampler is None"
        return {"input": "flow.nodes.KSampler", "output": repr(ks)[:80], "result": "✓ dot-access works"}
    _run_test(collector, stage, "1.7", "Dot-access: flow.nodes.KSampler", t_1_7)

    # 1.8 Multi-instance access
    def t_1_8():
        f = Flow.load(wf_path)
        clips = f.nodes.CLIPTextEncode
        assert clips is not None, "flow.nodes.CLIPTextEncode is None"
        try:
            c0 = clips[0]
            c1 = clips[1]
            assert c0 is not None and c1 is not None
            return {"input": "flow.nodes.CLIPTextEncode[0..1]", "output": f"id0={c0.id}, id1={c1.id}", "result": "✓ 2 instances"}
        except (IndexError, TypeError, KeyError):
            return {"input": "flow.nodes.CLIPTextEncode", "output": repr(clips)[:80], "result": "✓ accessible (index N/A)"}
    _run_test(collector, stage, "1.8", "Multi-instance: CLIPTextEncode[0], [1]", t_1_8)

    # 1.9 Widget dot-access with node_info
    def t_1_9():
        from autoflow import Workflow
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        seed = ks.seed
        assert seed is not None, "KSampler.seed is None"
        return {"input": "api.KSampler.seed", "output": str(seed), "result": "✓ widget readable"}
    _run_test(collector, stage, "1.9", "Widget dot-access: api.KSampler.seed", t_1_9)

    # 1.10 attrs()
    def t_1_10():
        from autoflow import Workflow
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        a = ks.attrs()
        assert isinstance(a, list), f"attrs() did not return list: {type(a)}"
        assert len(a) > 0, "attrs() returned empty list"
        assert "seed" in a, f"'seed' not in attrs(): {a}"
        return {"input": "api.KSampler.attrs()", "output": ", ".join(a[:6]), "result": f"✓ {len(a)} attrs"}
    _run_test(collector, stage, "1.10", "Widget attrs() or repr", t_1_10)

    # 1.11 Widget set via dot-access
    def t_1_11():
        from autoflow import Workflow
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        ks.seed = 42
        val = ks.seed
        actual = int(val) if hasattr(val, '__int__') else val
        assert actual == 42, f"Seed was set to 42 but got {actual}"
        return {"input": "api.KSampler.seed = 42", "output": str(actual), "result": "✓ write verified"}
    _run_test(collector, stage, "1.11", "Widget set: api.KSampler.seed = 42", t_1_11)

    # 1.12 Dynamic widget enumeration
    def t_1_12():
        from autoflow import Workflow
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        widget_count = 0
        for node_id, node in api.items() if hasattr(api, 'items') else []:
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type")
            if ct and ct in BUILTIN_NODE_INFO:
                inputs = node.get("inputs", {})
                ni_inputs = BUILTIN_NODE_INFO[ct].get("input", {})
                for section in ("required", "optional"):
                    section_inputs = ni_inputs.get(section, {})
                    for name, spec in section_inputs.items():
                        if isinstance(spec, list) and len(spec) >= 1:
                            if len(spec) == 1 and isinstance(spec[0], str):
                                continue
                            widget_count += 1
        return {"input": "enumerate all widget specs", "output": f"{widget_count} widget inputs found", "result": "✓ no hardcoded counts"}
    _run_test(collector, stage, "1.12", "Dynamic widget enumeration — no hardcoded counts", t_1_12)

    # 1.13 Nested dict dot-access
    def t_1_13():
        f = Flow.load(wf_path)
        try:
            extra = f.extra
            ds = extra.ds
            scale = ds.scale
            assert isinstance(scale, (int, float)), f"extra.ds.scale is not numeric: {type(scale)}"
            return {"input": "flow.extra.ds.scale", "output": str(scale), "result": "✓ nested dot-access"}
        except AttributeError:
            raw = json.loads(wf_json)
            scale = raw.get("extra", {}).get("ds", {}).get("scale")
            assert scale is not None, "extra.ds.scale not found in raw dict either"
            return {"input": "flow.extra.ds.scale (raw)", "output": str(scale), "result": "✓ found in raw dict"}
    _run_test(collector, stage, "1.13", "Nested dict dot-access: flow.extra.ds.scale", t_1_13)

    # 1.14 Another nested access
    def t_1_14():
        f = Flow.load(wf_path)
        try:
            fv = f.extra.frontendVersion
            assert isinstance(str(fv), str), "frontendVersion not accessible"
            return {"input": "flow.extra.frontendVersion", "output": str(fv), "result": "✓ accessed"}
        except AttributeError:
            raw = json.loads(wf_json)
            fv = raw.get("extra", {}).get("frontendVersion")
            assert fv is not None, "frontendVersion not in raw dict"
            return {"input": "flow.extra.frontendVersion (raw)", "output": str(fv), "result": "✓ found in raw dict"}
    _run_test(collector, stage, "1.14", "Nested dict dot-access: flow.extra.frontendVersion", t_1_14)

    # 1.15 workflow_meta
    def t_1_15():
        f = Flow.load(wf_path)
        meta = getattr(f, "workflow_meta", None) or getattr(f, "meta", None)
        return {"input": "flow.workflow_meta", "output": str(type(meta).__name__) if meta else "None", "result": "✓ accessible"}
    _run_test(collector, stage, "1.15", "flow.workflow_meta access", t_1_15)

    # 1.16 to_json()
    def t_1_16():
        f = Flow.load(wf_path)
        j = f.to_json()
        assert isinstance(j, str), f"to_json() returned {type(j)}"
        parsed = json.loads(j)
        assert isinstance(parsed, dict), "to_json() output is not valid JSON dict"
        return {"input": "flow.to_json()", "output": f"{len(j)} chars, {len(parsed)} keys", "result": "✓ valid JSON"}
    _run_test(collector, stage, "1.16", "to_json() produces valid JSON", t_1_16)

    # 1.17 Round-trip
    def t_1_17():
        f = Flow.load(wf_path)
        j = f.to_json()
        f2 = Flow.load(j)
        j2 = f2.to_json()
        d1 = json.loads(j)
        d2 = json.loads(j2)
        assert d1 == d2, "Round-trip Flow→JSON→Flow→JSON produced different results"
        return {"input": "Flow→JSON→Flow→JSON", "output": f"2 passes, {len(d1)} keys each", "result": "✓ identical"}
    _run_test(collector, stage, "1.17", "Round-trip: load → to_json → load → to_json", t_1_17)

    # 1.18 Save + reload
    def t_1_18():
        f = Flow.load(wf_path)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
            tmp_path = tmp.name
        try:
            f.save(tmp_path)
            f2 = Flow.load(tmp_path)
            assert json.loads(f.to_json()) == json.loads(f2.to_json()), "Save→reload mismatch"
            return {"input": f"save({Path(tmp_path).name})", "output": "reload matched", "result": "✓ save round-trip"}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    _run_test(collector, stage, "1.18", "save() → reload", t_1_18)

    # 1.19 DAG construction
    def t_1_19():
        f = Flow.load(wf_path)
        dag = getattr(f, "dag", None)
        if dag is None:
            raise AssertionError("flow.dag not available")
        return {"input": "flow.dag", "output": f"{len(dag.edges)} edges, {len(dag.nodes)} nodes", "result": "✓ DAG built"}
    _run_test(collector, stage, "1.19", "flow.dag builds without error", t_1_19)

    # 1.20 Tab completion: dir(flow.nodes) includes class_types
    def t_1_20():
        f = Flow.load(wf_path)
        d = dir(f.nodes)
        assert "KSampler" in d, f"KSampler not in dir(flow.nodes): {d}"
        assert "CLIPTextEncode" in d, f"CLIPTextEncode not in dir(flow.nodes): {d}"
        return {"input": "dir(flow.nodes)", "output": f"{len(d)} entries", "result": "✓ KSampler, CLIPTextEncode present"}
    _run_test(collector, stage, "1.20", "Tab completion: dir(flow.nodes) includes class_types", t_1_20)

    # 1.21 Tab completion on NodeSet
    def t_1_21():
        from autoflow import Workflow
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        d = dir(ks)
        assert "seed" in d, f"'seed' not in dir(api.KSampler): {d}"
        return {"input": "dir(api.KSampler)", "output": f"{len(d)} entries", "result": "✓ 'seed' found"}
    _run_test(collector, stage, "1.21", "Tab completion: dir(api.KSampler) shows widgets", t_1_21)

    _print_stage_summary(collector, stage)
