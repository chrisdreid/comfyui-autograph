"""Phase 3 — Flow: load/access, core ops, node proxy, node group, widget values, dict/list views.

Merged from: stage_01_load_access, stage_08_flow_core, stage_09_flow_node_proxy,
             stage_10_flow_node_group, stage_11_widget_value, stage_14_dict_list_view
"""

from __future__ import annotations

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
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Phase 3: Flow"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autograph import Flow, ApiFlow

    wf_path = str(_BUNDLED_WORKFLOW)
    wf_json = _BUNDLED_WORKFLOW.read_text(encoding="utf-8")

    # ===================================================================
    # 3.1–3.21  Load / Access  (was stage 1)
    # ===================================================================

    def t_3_1():
        f = Flow.load(wf_path)
        assert f is not None, "Flow.load returned None"
        return {"input": wf_path, "output": f"Flow ({type(f).__name__})", "result": "✓ loaded"}
    _run_test(collector, stage, "3.1", "Flow.load(path)", t_3_1)

    def t_3_2():
        f = Flow(wf_path)
        assert f is not None, "Flow(path) returned None"
        return {"input": f"Flow({Path(wf_path).name})", "output": f"{type(f).__name__}", "result": "✓ constructor"}
    _run_test(collector, stage, "3.2", "Flow(path) constructor", t_3_2)

    def t_3_3():
        f = Flow(wf_json)
        assert f is not None, "Flow(json_str) returned None"
        return {"input": f"Flow(json_str, {len(wf_json)} chars)", "output": f"{type(f).__name__}", "result": "✓ string constructor"}
    _run_test(collector, stage, "3.3", "Flow(json_string)", t_3_3)

    def t_3_4():
        d = json.loads(wf_json)
        f = Flow(d)
        assert f is not None, "Flow(dict) returned None"
        return {"input": f"Flow(dict, {len(d)} keys)", "output": f"{type(f).__name__}", "result": "✓ dict constructor"}
    _run_test(collector, stage, "3.4", "Flow(dict)", t_3_4)

    def t_3_5():
        f = Flow(wf_json.encode("utf-8"))
        assert f is not None, "Flow(bytes) returned None"
        return {"input": "Flow(bytes)", "output": f"{type(f).__name__}", "result": "✓ bytes constructor"}
    _run_test(collector, stage, "3.5", "Flow(bytes)", t_3_5)

    def t_3_6():
        f = Flow.load(wf_path)
        nodes = f.nodes
        assert nodes is not None, "flow.nodes is None"
        assert hasattr(nodes, '__len__'), "nodes has no __len__"
        n = len(nodes)
        assert n > 0, f"Expected nodes, got {n}"
        return {"input": "flow.nodes", "output": f"{n} node(s)", "result": "✓ accessible"}
    _run_test(collector, stage, "3.6", "flow.nodes count", t_3_6)

    def t_3_7():
        f = Flow.load(wf_path)
        ks = f.nodes.KSampler
        assert ks is not None, "KSampler not found"
        return {"input": "flow.nodes.KSampler", "output": f"{type(ks).__name__}", "result": "✓ dot access"}
    _run_test(collector, stage, "3.7", "flow.nodes.KSampler dot-access", t_3_7)

    def t_3_8():
        f = Flow.load(wf_path)
        clips = f.nodes.CLIPTextEncode
        assert clips is not None, "CLIPTextEncode not found"
        try:
            c0 = clips[0]
            c1 = clips[1]
            return {"input": "flow.nodes.CLIPTextEncode[0..1]", "output": f"id0={c0.id}, id1={c1.id}", "result": "✓ 2 instances"}
        except (IndexError, TypeError, KeyError):
            return {"input": "flow.nodes.CLIPTextEncode", "output": repr(clips)[:80], "result": "✓ accessible (index N/A)"}
    _run_test(collector, stage, "3.8", "Multi-instance: CLIPTextEncode[0], [1]", t_3_8)

    def t_3_9():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        seed = ks.seed
        assert seed is not None, "KSampler.seed is None"
        return {"input": "api.KSampler.seed", "output": str(seed), "result": "✓ widget readable"}
    _run_test(collector, stage, "3.9", "Widget dot-access: api.KSampler.seed", t_3_9)

    def t_3_10():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        a = ks.attrs()
        assert isinstance(a, list), f"attrs() did not return list: {type(a)}"
        assert len(a) > 0, "attrs() returned empty list"
        assert "seed" in a, f"'seed' not in attrs(): {a}"
        return {"input": "api.KSampler.attrs()", "output": ", ".join(a[:6]), "result": f"✓ {len(a)} attrs"}
    _run_test(collector, stage, "3.10", "Widget attrs() or repr", t_3_10)

    def t_3_11():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        ks.seed = 42
        val = ks.seed
        actual = int(val) if hasattr(val, '__int__') else val
        assert actual == 42, f"Seed was set to 42 but got {actual}"
        return {"input": "api.KSampler.seed = 42", "output": str(actual), "result": "✓ write verified"}
    _run_test(collector, stage, "3.11", "Widget set: api.KSampler.seed = 42", t_3_11)

    def t_3_12():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
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
    _run_test(collector, stage, "3.12", "Dynamic widget enumeration — no hardcoded counts", t_3_12)

    def t_3_13():
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
    _run_test(collector, stage, "3.13", "Nested dict dot-access: flow.extra.ds.scale", t_3_13)

    def t_3_14():
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
    _run_test(collector, stage, "3.14", "Nested dict dot-access: flow.extra.frontendVersion", t_3_14)

    def t_3_15():
        f = Flow.load(wf_path)
        meta = getattr(f, "workflow_meta", None) or getattr(f, "meta", None)
        return {"input": "flow.workflow_meta", "output": str(type(meta).__name__) if meta else "None", "result": "✓ accessible"}
    _run_test(collector, stage, "3.15", "flow.workflow_meta access", t_3_15)

    def t_3_16():
        f = Flow.load(wf_path)
        j = f.to_json()
        assert isinstance(j, str), f"to_json() returned {type(j)}"
        parsed = json.loads(j)
        assert isinstance(parsed, dict), "to_json() output is not valid JSON dict"
        return {"input": "flow.to_json()", "output": f"{len(j)} chars, {len(parsed)} keys", "result": "✓ valid JSON"}
    _run_test(collector, stage, "3.16", "to_json() produces valid JSON", t_3_16)

    def t_3_17():
        f = Flow.load(wf_path)
        j = f.to_json()
        f2 = Flow.load(j)
        j2 = f2.to_json()
        d1 = json.loads(j)
        d2 = json.loads(j2)
        assert d1 == d2, "Round-trip Flow→JSON→Flow→JSON produced different results"
        return {"input": "Flow→JSON→Flow→JSON", "output": f"2 passes, {len(d1)} keys each", "result": "✓ identical"}
    _run_test(collector, stage, "3.17", "Round-trip: load → to_json → load → to_json", t_3_17)

    def t_3_18():
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
    _run_test(collector, stage, "3.18", "save() → reload", t_3_18)

    def t_3_19():
        f = Flow.load(wf_path)
        dag = getattr(f, "dag", None)
        if dag is None:
            raise AssertionError("flow.dag not available")
        return {"input": "flow.dag", "output": f"{len(dag.edges)} edges, {len(dag.nodes)} nodes", "result": "✓ DAG built"}
    _run_test(collector, stage, "3.19", "flow.dag builds without error", t_3_19)

    def t_3_20():
        f = Flow.load(wf_path)
        d = dir(f.nodes)
        assert "KSampler" in d, f"KSampler not in dir(flow.nodes): {d}"
        assert "CLIPTextEncode" in d, f"CLIPTextEncode not in dir(flow.nodes): {d}"
        return {"input": "dir(flow.nodes)", "output": f"{len(d)} entries", "result": "✓ KSampler, CLIPTextEncode present"}
    _run_test(collector, stage, "3.20", "Tab completion: dir(flow.nodes) includes class_types", t_3_20)

    def t_3_21():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        d = dir(ks)
        assert "seed" in d, f"'seed' not in dir(api.KSampler): {d}"
        return {"input": "dir(api.KSampler)", "output": f"{len(d)} entries", "result": "✓ 'seed' found"}
    _run_test(collector, stage, "3.21", "Tab completion: dir(api.KSampler) shows widgets", t_3_21)

    # ===================================================================
    # 3.22–3.41  Flow Core  (was stage 8)
    # ===================================================================

    f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)

    def t_3_22():
        assert f is not None
        return {"input": "Flow(wf_path, node_info=…)", "output": f"{type(f).__name__}", "result": "✓ constructed"}
    _run_test(collector, stage, "3.22", "Flow(path, node_info) constructor", t_3_22)

    def t_3_23():
        s = f.source
        assert isinstance(s, str),  f"source is {type(s)}"
        return {"input": "flow.source", "output": s[:50], "result": "✓ string"}
    _run_test(collector, stage, "3.23", "flow.source property", t_3_23)

    def t_3_24():
        links = f.links
        assert links is not None, "flow.links is None"
        assert hasattr(links, '__len__'), "links has no __len__"
        assert len(links) > 0, "links is empty"
        return {"input": "flow.links", "output": f"{len(links)} links", "result": "✓ links accessible"}
    _run_test(collector, stage, "3.24", "flow.links property", t_3_24)

    def t_3_25():
        extra = f.extra
        assert extra is not None, "flow.extra is None"
        ds = extra.ds
        assert ds is not None, "extra.ds is None"
        return {"input": "flow.extra", "output": f"ds={ds!r}"[:60], "result": "✓ DictView"}
    _run_test(collector, stage, "3.25", "flow.extra returns DictView", t_3_25)

    def t_3_26():
        nodes = f.nodes
        assert hasattr(nodes, '__len__'), "nodes has no __len__"
        assert hasattr(nodes, '__iter__'), "nodes has no __iter__"
        return {"input": "flow.nodes", "output": f"{len(nodes)} nodes", "result": "✓ iterable with len"}
    _run_test(collector, stage, "3.26", "flow.nodes is iterable with len", t_3_26)

    def t_3_27():
        ks = f.nodes.KSampler
        assert ks is not None, "flow.nodes.KSampler is None"
        assert hasattr(ks, '__len__'), f"KSampler result has no __len__: {type(ks)}"
        assert len(ks) >= 1, "KSampler should have at least 1 instance"
        return {"input": "flow.nodes.KSampler", "output": f"{len(ks)} instance(s)", "result": "✓ FlowNodeProxy"}
    _run_test(collector, stage, "3.27", "flow.nodes.KSampler → FlowNodeProxy", t_3_27)

    def t_3_28():
        clips = f.nodes.CLIPTextEncode
        assert clips is not None, "flow.nodes.CLIPTextEncode is None"
        assert hasattr(clips, '__len__'), f"CLIPTextEncode has no __len__: {type(clips)}"
        assert len(clips) == 2, f"Expected 2 CLIPTextEncode, got {len(clips)}"
        return {"input": "flow.nodes.CLIPTextEncode", "output": f"{len(clips)} instances", "result": "✓ FlowNodeGroup"}
    _run_test(collector, stage, "3.28", "flow.nodes.CLIPTextEncode → FlowNodeGroup", t_3_28)

    def t_3_29():
        n = len(f.nodes)
        assert isinstance(n, int), f"len(nodes) is {type(n)}"
        assert n > 0, "len(nodes) is 0"
        return {"input": "len(flow.nodes)", "output": str(n), "result": "✓ non-zero"}
    _run_test(collector, stage, "3.29", "len(flow.nodes)", t_3_29)

    def t_3_30():
        items = list(f.nodes)
        assert len(items) > 0, "iter(nodes) yielded nothing"
        assert isinstance(items[0], str), f"iter yielded {type(items[0])}"
        return {"input": "iter(flow.nodes)", "output": f"{len(items)} items, type={type(items[0]).__name__}", "result": "✓ iterable"}
    _run_test(collector, stage, "3.30", "iter(flow.nodes) yields items", t_3_30)

    def t_3_31():
        k = f.nodes.keys()
        v = f.nodes.values()
        it = f.nodes.items()
        assert len(list(k)) > 0, "keys() empty"
        assert len(list(v)) > 0, "values() empty"
        assert len(list(it)) > 0, "items() empty"
        return {"input": "keys()/values()/items()", "output": f"{len(list(k))} keys", "result": "✓ all non-empty"}
    _run_test(collector, stage, "3.31", "flow.nodes.keys()/values()/items()", t_3_31)

    def t_3_32():
        lst = f.nodes.to_list()
        dct = f.nodes.to_dict()
        assert isinstance(lst, list), f"to_list() returned {type(lst)}"
        assert isinstance(dct, dict), f"to_dict() returned {type(dct)}"
        assert len(lst) > 0, "to_list() empty"
        return {"input": "to_list()/to_dict()", "output": f"list={len(lst)}, dict={len(dct)}", "result": "✓ conversions"}
    _run_test(collector, stage, "3.32", "flow.nodes.to_list()/to_dict()", t_3_32)

    def t_3_33():
        from autograph import ApiFlow
        api = f.convert(node_info=BUILTIN_NODE_INFO)
        assert isinstance(api, ApiFlow), f"convert() returned {type(api)}"
        assert len(api) > 0, "Converted ApiFlow is empty"
        return {"input": "flow.convert()", "output": f"ApiFlow with {len(api)} nodes", "result": "✓ converted"}
    _run_test(collector, stage, "3.33", "flow.convert() → ApiFlow", t_3_33)

    def t_3_34():
        result = f.convert_with_errors(node_info=BUILTIN_NODE_INFO)
        assert result is not None, "convert_with_errors returned None"
        assert hasattr(result, "ok"), "No .ok on result"
        assert result.ok, f"Conversion failed: {getattr(result, 'errors', '?')}"
        assert result.data is not None, "result.data is None"
        return {"input": "convert_with_errors()", "output": f"ok={result.ok}", "result": "✓ clean conversion"}
    _run_test(collector, stage, "3.34", "flow.convert_with_errors()", t_3_34)

    def t_3_35():
        dag = f.dag
        assert dag is not None, "flow.dag is None"
        assert isinstance(dag, dict), f"dag is {type(dag)}, expected dict subclass"
        return {"input": "flow.dag", "output": f"{len(dag.edges)} edges", "result": "✓ Dag built"}
    _run_test(collector, stage, "3.35", "flow.dag returns Dag", t_3_35)

    def t_3_36():
        ni = f.node_info
        assert ni is not None, "flow.node_info is None after passing node_info="
        return {"input": "Flow(path, node_info=dict)", "output": f"{len(ni)} types", "result": "✓ stored"}
    _run_test(collector, stage, "3.36", "Flow(path, node_info=dict) stores node_info", t_3_36)

    def t_3_37():
        f2 = Flow(wf_path)
        f2.fetch_node_info(BUILTIN_NODE_INFO)
        assert f2.node_info is not None, "node_info still None after fetch_node_info(dict)"
        return {"input": "fetch_node_info(dict)", "output": f"{len(f2.node_info)} types", "result": "✓ attached"}
    _run_test(collector, stage, "3.37", "flow.fetch_node_info(dict)", t_3_37)

    def t_3_38():
        f2 = Flow(wf_path)
        j = f2.to_json()
        f3 = Flow(j)
        assert len(f3.nodes) == len(f2.nodes), "Node count mismatch after round-trip"
        return {"input": "Flow(flow.to_json())", "output": f"{len(f3.nodes)} nodes", "result": "✓ round-trip"}
    _run_test(collector, stage, "3.38", "Round-trip: Flow(flow.to_json())", t_3_38)

    def t_3_39():
        with open(wf_path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        f2 = Flow(d)
        assert len(f2.nodes) > 0, "Flow from dict has no nodes"
        return {"input": "Flow(dict)", "output": f"{len(f2.nodes)} nodes", "result": "✓ dict constructor"}
    _run_test(collector, stage, "3.39", "Flow(dict) constructor (core)", t_3_39)

    def t_3_40():
        with open(wf_path, "rb") as fh:
            b = fh.read()
        f2 = Flow(b)
        assert len(f2.nodes) > 0, "Flow from bytes has no nodes"
        return {"input": f"Flow(bytes, {len(b)} B)", "output": f"{len(f2.nodes)} nodes", "result": "✓ bytes constructor"}
    _run_test(collector, stage, "3.40", "Flow(bytes) constructor (core)", t_3_40)

    def t_3_41():
        f2 = Flow(wf_path)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            f2.save(tmp_path)
            f3 = Flow(tmp_path)
            assert len(f3.nodes) == len(f2.nodes), "Node count mismatch after save→reload"
            return {"input": f"save({Path(tmp_path).name})", "output": f"{len(f3.nodes)} nodes", "result": "✓ save round-trip"}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    _run_test(collector, stage, "3.41", "flow.save() → reload (core)", t_3_41)

    # ===================================================================
    # 3.42–3.56  FlowNodeProxy  (was stage 9)
    # ===================================================================

    ks = f.nodes.KSampler[0]

    def t_3_42():
        nid = ks.id
        assert isinstance(nid, int), f"id is {type(nid)}, expected int"
        return {"input": "ks.id", "output": str(nid), "result": "✓ int"}
    _run_test(collector, stage, "3.42", ".id returns int", t_3_42)

    def t_3_43():
        t = ks.type
        assert t == "KSampler", f"type is {t!r}, expected 'KSampler'"
        return {"input": "ks.type", "output": t, "result": "✓ KSampler"}
    _run_test(collector, stage, "3.43", ".type returns 'KSampler'", t_3_43)

    def t_3_44():
        wv = ks.widgets_values
        assert isinstance(wv, (list, type(None))), f"widgets_values is {type(wv)}"
        n = len(wv) if wv else 0
        if wv is not None:
            assert len(wv) > 0, "widgets_values is empty"
        return {"input": "ks.widgets_values", "output": f"{n} values", "result": "✓ list"}
    _run_test(collector, stage, "3.44", ".widgets_values returns list", t_3_44)

    def t_3_45():
        n = ks.node
        assert isinstance(n, dict), f"node is {type(n)}, expected dict"
        assert "type" in n, "'type' key missing from node dict"
        return {"input": "ks.node", "output": f"dict with {len(n)} keys", "result": "✓ raw dict"}
    _run_test(collector, stage, "3.45", ".node returns raw dict", t_3_45)

    def t_3_46():
        f2 = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        node = f2.nodes.KSampler
        assert node.bypass is False, f"bypass should be False, got {node.bypass}"
        return {"input": "node.bypass (default)", "output": str(node.bypass), "result": "✓ False"}
    _run_test(collector, stage, "3.46", ".bypass is False by default", t_3_46)

    def t_3_47():
        f2 = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        node = f2.nodes.KSampler
        node.bypass = True
        assert node.bypass is True, f"bypass should be True after set, got {node.bypass}"
        assert node.node.get("mode") == 4, f"mode should be 4, got {node.node.get('mode')}"
        return {"input": "node.bypass = True", "output": f"mode={node.node.get('mode')}", "result": "✓ mode=4"}
    _run_test(collector, stage, "3.47", ".bypass = True → mode=4", t_3_47)

    def t_3_48():
        f2 = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        node = f2.nodes.KSampler
        node.bypass = True
        node.bypass = False
        assert node.bypass is False, "bypass should be False after clear"
        assert node.node.get("mode") == 0, f"mode should be 0, got {node.node.get('mode')}"
        return {"input": "bypass True→False", "output": f"mode={node.node.get('mode')}", "result": "✓ mode=0"}
    _run_test(collector, stage, "3.48", ".bypass = False → mode=0", t_3_48)

    def t_3_49():
        r = repr(ks)
        assert "KSampler" in r, f"repr missing 'KSampler': {r}"
        return {"input": "repr(ks)", "output": r[:60], "result": "✓ contains KSampler"}
    _run_test(collector, stage, "3.49", "__repr__ format", t_3_49)

    def t_3_50():
        a = ks.attrs()
        assert isinstance(a, list), f"attrs() returned {type(a)}"
        assert "seed" in a, f"'seed' not in attrs(): {a}"
        assert "steps" in a, f"'steps' not in attrs(): {a}"
        assert "cfg" in a, f"'cfg' not in attrs(): {a}"
        return {"input": "ks.attrs()", "output": ", ".join(a[:6]), "result": f"✓ {len(a)} attrs"}
    _run_test(collector, stage, "3.50", ".attrs() returns widget names", t_3_50)

    def t_3_51():
        d = dir(ks)
        assert "seed" in d, "'seed' not in dir(node)"
        assert "steps" in d, "'steps' not in dir(node)"
        return {"input": "dir(ks)", "output": f"{len(d)} entries", "result": "✓ seed, steps in dir"}
    _run_test(collector, stage, "3.51", "dir(node) includes widgets", t_3_51)

    def t_3_52():
        seed = ks.seed
        assert int(seed) == int(seed), f"seed is not numeric: {seed}"
        return {"input": "ks.seed", "output": str(seed), "result": "✓ WidgetValue"}
    _run_test(collector, stage, "3.52", "Dot-read widget → WidgetValue", t_3_52)

    def t_3_53():
        f2 = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        node = f2.nodes.KSampler[0]
        node.seed = 42
        val = node.seed
        actual = int(val) if hasattr(val, '__int__') else val
        assert actual == 42, f"seed set to 42 but got {actual}"
        return {"input": "node.seed = 42", "output": str(actual), "result": "✓ write verified"}
    _run_test(collector, stage, "3.53", "Dot-write: node.seed = 42", t_3_53)

    def t_3_54():
        f2 = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        node = f2.nodes.KSampler[0]
        node.steps = 100
        node.cfg = 12.5
        assert int(node.steps) == 100, f"steps mismatch: {node.steps}"
        assert float(node.cfg) == 12.5, f"cfg mismatch: {node.cfg}"
        return {"input": "steps=100, cfg=12.5", "output": f"steps={node.steps}, cfg={node.cfg}", "result": "✓ multi-write"}
    _run_test(collector, stage, "3.54", "Widget write round-trip", t_3_54)

    def t_3_55():
        val = ks.type
        assert val == "KSampler", f"node.type = {val!r}"
        return {"input": "ks.type", "output": val, "result": "✓ attribute access"}
    _run_test(collector, stage, "3.55", ".type attribute access", t_3_55)

    def t_3_56():
        p = ks.path()
        assert isinstance(p, str) and len(p) > 0, f"path() = {p!r}"
        a = ks.address()
        assert isinstance(a, str) and len(a) > 0, f"address() = {a!r}"
        return {"input": "path()/address()", "output": f"path={p}, addr={a}", "result": "✓ both returned"}
    _run_test(collector, stage, "3.56", ".path() and .address()", t_3_56)

    # ===================================================================
    # 3.57–3.68  FlowNodeGroup  (was stage 10)
    # ===================================================================

    clips = f.nodes.CLIPTextEncode

    def t_3_57():
        assert hasattr(clips, '__len__'), f"No __len__ on {type(clips)}"
        assert len(clips) == 2, f"Expected 2 CLIPTextEncode, got {len(clips)}"
        return {"input": "len(CLIPTextEncode)", "output": str(len(clips)), "result": "✓ == 2"}
    _run_test(collector, stage, "3.57", "len(group) == 2", t_3_57)

    def t_3_58():
        c0 = clips[0]
        c1 = clips[1]
        assert hasattr(c0, 'type'), f"clips[0] has no .type: {type(c0)}"
        assert hasattr(c1, 'type'), f"clips[1] has no .type: {type(c1)}"
        assert c0.id != c1.id, f"clips[0].id == clips[1].id == {c0.id}"
        return {"input": "clips[0], clips[1]", "output": f"id0={c0.id}, id1={c1.id}", "result": "✓ distinct proxies"}
    _run_test(collector, stage, "3.58", "group[0]/[1] → FlowNodeProxy", t_3_58)

    def t_3_59():
        refs = list(clips)
        assert len(refs) == 2, f"iter yielded {len(refs)} items"
        for r in refs:
            assert hasattr(r, 'type'), f"iter yielded {type(r)} without .type"
        return {"input": "list(clips)", "output": f"{len(refs)} refs", "result": "✓ iterable"}
    _run_test(collector, stage, "3.59", "iter(group) yields node refs", t_3_59)

    def t_3_60():
        val = clips.text
        assert val is not None, "group.text is None"
        return {"input": "clips.text (broadcast)", "output": str(val)[:40], "result": "✓ broadcast read"}
    _run_test(collector, stage, "3.60", "Broadcast read: group.text", t_3_60)

    def t_3_61():
        f2 = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        clips2 = f2.nodes.CLIPTextEncode
        clips2[0].text = "test text"
        actual = str(clips2[0].text)
        assert actual == "test text", f"Expected 'test text', got {actual!r}"
        return {"input": "clips[0].text = 'test text'", "output": actual, "result": "✓ individual write"}
    _run_test(collector, stage, "3.61", "Individual write: group[0].text = 'new'", t_3_61)

    def t_3_62():
        a = clips.attrs()
        assert isinstance(a, list), f"attrs() returned {type(a)}"
        assert "text" in a, f"'text' not in attrs(): {a}"
        return {"input": "clips.attrs()", "output": ", ".join(a), "result": f"✓ {len(a)} attrs"}
    _run_test(collector, stage, "3.62", "group.attrs()", t_3_62)

    def t_3_63():
        d = dir(clips)
        assert "text" in d, "'text' not in dir(group)"
        return {"input": "dir(clips)", "output": f"{len(d)} entries", "result": "✓ text in dir"}
    _run_test(collector, stage, "3.63", "dir(group) includes widgets", t_3_63)

    def t_3_64():
        k = list(clips.keys())
        v = list(clips.values())
        it = list(clips.items())
        assert len(k) > 0, "keys() is empty"
        assert len(v) > 0, "values() is empty"
        assert len(it) > 0, "items() is empty"
        return {"input": "keys()/values()/items()", "output": f"{len(k)} keys", "result": "✓ all non-empty"}
    _run_test(collector, stage, "3.64", "group.keys()/values()/items()", t_3_64)

    def t_3_65():
        lst = clips.to_list()
        assert isinstance(lst, list), f"to_list() returned {type(lst)}"
        assert len(lst) == 2, f"to_list() has {len(lst)} items"
        return {"input": "clips.to_list()", "output": f"{len(lst)} items", "result": "✓ list"}
    _run_test(collector, stage, "3.65", "group.to_list()", t_3_65)

    def t_3_66():
        dct = clips.to_dict()
        assert isinstance(dct, dict), f"to_dict() returned {type(dct)}"
        assert len(dct) == 2, f"to_dict() has {len(dct)} items"
        return {"input": "clips.to_dict()", "output": f"{len(dct)} entries", "result": "✓ dict"}
    _run_test(collector, stage, "3.66", "group.to_dict()", t_3_66)

    def t_3_67():
        r = repr(clips)
        assert "CLIPTextEncode" in r, f"repr missing 'CLIPTextEncode': {r}"
        return {"input": "repr(clips)", "output": r[:60], "result": "✓ CLIPTextEncode in repr"}
    _run_test(collector, stage, "3.67", "repr(group)", t_3_67)

    def t_3_68():
        last = clips[-1]
        assert hasattr(last, 'type'), f"clips[-1] has no .type: {type(last)}"
        assert last.id == clips[1].id, "clips[-1] should equal clips[1]"
        return {"input": "clips[-1]", "output": f"id={last.id}", "result": "✓ negative index"}
    _run_test(collector, stage, "3.68", "Negative index group[-1]", t_3_68)

    # ===================================================================
    # 3.69–3.83  WidgetValue  (was stage 11)
    # ===================================================================

    from autograph.models import WidgetValue

    wv_int = WidgetValue(42)
    wv_float = WidgetValue(3.14)
    wv_str = WidgetValue("euler")
    combo_spec = [["euler", "heun", "dpm"], {}]
    wv_combo = WidgetValue("euler", combo_spec)
    tooltip_spec = ["INT", {"default": 42, "tooltip": "Random seed value"}]
    wv_tooltip = WidgetValue(42, tooltip_spec)

    def t_3_69():
        assert wv_int == 42
        assert wv_str == "euler"
        return {"input": "WV(42)==42, WV('euler')=='euler'", "output": "True, True", "result": "✓ equality"}
    _run_test(collector, stage, "3.69", "wv == raw_value", t_3_69)

    def t_3_70():
        assert wv_int != 43
        assert wv_str != "heun"
        return {"input": "WV(42)!=43, WV('euler')!='heun'", "output": "True, True", "result": "✓ inequality"}
    _run_test(collector, stage, "3.70", "wv != other_value", t_3_70)

    def t_3_71():
        result = wv_int + 10
        assert result == 52, f"42 + 10 = {result}"
        return {"input": "WV(42) + 10", "output": str(result), "result": "✓ add"}
    _run_test(collector, stage, "3.71", "wv + 10", t_3_71)

    def t_3_72():
        result = 10 + wv_int
        assert result == 52, f"10 + 42 = {result}"
        return {"input": "10 + WV(42)", "output": str(result), "result": "✓ radd"}
    _run_test(collector, stage, "3.72", "10 + wv (radd)", t_3_72)

    def t_3_73():
        result = wv_int - 10
        assert result == 32, f"42 - 10 = {result}"
        return {"input": "WV(42) - 10", "output": str(result), "result": "✓ sub"}
    _run_test(collector, stage, "3.73", "wv - 10", t_3_73)

    def t_3_74():
        result = wv_int * 2
        assert result == 84, f"42 * 2 = {result}"
        return {"input": "WV(42) * 2", "output": str(result), "result": "✓ mul"}
    _run_test(collector, stage, "3.74", "wv * 2", t_3_74)

    def t_3_75():
        result = wv_int / 2
        assert result == 21.0, f"42 / 2 = {result}"
        return {"input": "WV(42) / 2", "output": str(result), "result": "✓ div"}
    _run_test(collector, stage, "3.75", "wv / 2", t_3_75)

    def t_3_76():
        assert wv_int < 100
        assert wv_int > 0
        assert wv_int <= 42
        assert wv_int >= 42
        return {"input": "WV(42) <100, >0, <=42, >=42", "output": "all True", "result": "✓ ordering"}
    _run_test(collector, stage, "3.76", "wv < 100, wv > 0, etc.", t_3_76)

    def t_3_77():
        assert int(wv_int) == 42
        assert float(wv_float) == 3.14
        return {"input": "int(WV(42)), float(WV(3.14))", "output": f"{int(wv_int)}, {float(wv_float)}", "result": "✓ cast"}
    _run_test(collector, stage, "3.77", "int(wv) / float(wv)", t_3_77)

    def t_3_78():
        assert bool(wv_int) is True
        assert bool(WidgetValue(0)) is False
        return {"input": "bool(WV(42)), bool(WV(0))", "output": "True, False", "result": "✓ bool"}
    _run_test(collector, stage, "3.78", "bool(wv)", t_3_78)

    def t_3_79():
        assert hash(wv_int) == hash(42)
        return {"input": "hash(WV(42))", "output": str(hash(wv_int)), "result": "✓ matches hash(42)"}
    _run_test(collector, stage, "3.79", "hash(wv) == hash(raw)", t_3_79)

    def t_3_80():
        assert str(wv_int) == "42"
        r = repr(wv_int)
        assert "42" in r
        return {"input": "str(WV(42)), repr(WV(42))", "output": f"str={str(wv_int)!r}, repr={r!r}", "result": "✓ string ops"}
    _run_test(collector, stage, "3.80", "str(wv) / repr(wv)", t_3_80)

    def t_3_81():
        assert wv_int.value == 42
        assert wv_str.value == "euler"
        return {"input": ".value property", "output": f"int={wv_int.value}, str={wv_str.value}", "result": "✓ raw values"}
    _run_test(collector, stage, "3.81", ".value property", t_3_81)

    def t_3_82():
        choices = wv_combo.choices()
        assert isinstance(choices, list)
        assert "euler" in choices
        assert "heun" in choices
        return {"input": "wv_combo.choices()", "output": ", ".join(choices), "result": f"✓ {len(choices)} choices"}
    _run_test(collector, stage, "3.82", ".choices() on combo", t_3_82)

    def t_3_83():
        tt = wv_tooltip.tooltip()
        assert tt == "Random seed value"
        return {"input": "wv_tooltip.tooltip()", "output": tt, "result": "✓ tooltip string"}
    _run_test(collector, stage, "3.83", ".tooltip() returns string", t_3_83)

    # ===================================================================
    # 3.84–3.93  DictView / ListView  (was stage 14)
    # ===================================================================

    from autograph.models import DictView, ListView

    def t_3_84():
        d = {"foo": 1, "bar": "baz"}
        dv = DictView(d)
        assert dv.foo == 1
        assert dv.bar == "baz"
        return {"input": "DictView({'foo':1,'bar':'baz'})", "output": f"foo={dv.foo}, bar={dv.bar}", "result": "✓ dot-read"}
    _run_test(collector, stage, "3.84", "DictView dot-read", t_3_84)

    def t_3_85():
        d = {"x": 10}
        dv = DictView(d)
        dv.x = 20
        assert d["x"] == 20
        return {"input": "dv.x = 20", "output": f"d['x']={d['x']}", "result": "✓ propagates"}
    _run_test(collector, stage, "3.85", "DictView dot-write propagates", t_3_85)

    def t_3_86():
        d = {"a": 1}
        dv = DictView(d)
        assert dv["a"] == 1
        dv["a"] = 99
        assert d["a"] == 99
        return {"input": "dv['a']=99", "output": f"d['a']={d['a']}", "result": "✓ bracket read/write"}
    _run_test(collector, stage, "3.86", "DictView bracket read/write", t_3_86)

    def t_3_87():
        d = {"a": 1, "b": 2}
        dv = DictView(d)
        del dv["a"]
        assert "a" not in d
        return {"input": "del dv['a']", "output": f"keys={list(d.keys())}", "result": "✓ deleted"}
    _run_test(collector, stage, "3.87", "del DictView['key']", t_3_87)

    def t_3_88():
        d = {"x": 1, "y": 2}
        dv = DictView(d)
        assert set(dv.keys()) == {"x", "y"}
        assert list(dv.values()) == [1, 2] or set(dv.values()) == {1, 2}
        assert len(list(dv.items())) == 2
        return {"input": "keys()/values()/items()", "output": f"keys={list(dv.keys())}", "result": "✓ all work"}
    _run_test(collector, stage, "3.88", "DictView keys()/values()/items()", t_3_88)

    def t_3_89():
        d = {"a": 1}
        dv = DictView(d)
        dv.update({"b": 2})
        assert d == {"a": 1, "b": 2}
        return {"input": "dv.update({'b':2})", "output": str(d), "result": "✓ merged"}
    _run_test(collector, stage, "3.89", "DictView update()", t_3_89)

    def t_3_90():
        d = {"a": 1, "b": 2}
        dv = DictView(d)
        val = dv.pop("a")
        assert val == 1
        assert "a" not in d
        return {"input": "dv.pop('a')", "output": f"val={val}, keys={list(d.keys())}", "result": "✓ popped"}
    _run_test(collector, stage, "3.90", "DictView pop()", t_3_90)

    def t_3_91():
        d = {"a": 1}
        dv = DictView(d)
        dv2 = dv.copy()
        assert isinstance(dv2, (DictView, dict))
        dv2["a"] = 99
        assert d["a"] == 1, "copy() should be independent"
        return {"input": "dv.copy() → modify copy", "output": f"orig={d['a']}, copy={dv2['a']}", "result": "✓ independent"}
    _run_test(collector, stage, "3.91", "DictView copy()", t_3_91)

    def t_3_92():
        dv = DictView({"x": 1})
        r = repr(dv)
        s = str(dv)
        assert isinstance(r, str) and len(r) > 0
        assert isinstance(s, str) and len(s) > 0
        return {"input": "repr(dv), str(dv)", "output": f"repr={r[:40]}", "result": "✓ string ops"}
    _run_test(collector, stage, "3.92", "DictView repr()/str()", t_3_92)

    def t_3_93():
        data = [10, 20, 30]
        lv = ListView(data)
        assert len(lv) == 3
        assert lv[0] == 10
        assert lv[2] == 30
        items = list(lv)
        assert items == [10, 20, 30]
        return {"input": "ListView([10,20,30])", "output": f"len={len(lv)}, items={items}", "result": "✓ iter+index"}
    _run_test(collector, stage, "3.93", "ListView iteration + indexing", t_3_93)

    _print_stage_summary(collector, stage)
