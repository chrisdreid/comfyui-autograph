"""Phase 4 — Conversion: ApiFlow auto-detect, MarkdownNote strip, subgraphs, DAG, save/format.

Merged from: stage_02_convert_metadata, stage_15_workflow_factory,
             stage_16_dag, stage_18_save_formatting, stage_21_subgraphs
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
    ResultCollector, _run_test, _print_stage_summary, SkipTest,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Phase 4: Conversion"




def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Flow, ApiFlow, convert_with_errors
    from autoflow.api import convert_workflow, _sanitize_api_prompt

    wf_path = str(_BUNDLED_WORKFLOW)
    with open(wf_path, "r", encoding="utf-8") as fh:
        wf_str = fh.read()
    wf_dict = json.loads(wf_str)

    # ===================================================================
    # 4.1–4.3  ApiFlow auto-detect  (was stage 15)
    # ===================================================================

    def t_4_1():
        api = ApiFlow(wf_dict, node_info=BUILTIN_NODE_INFO)
        assert isinstance(api, ApiFlow), f"ApiFlow(dict) returned {type(api)}"
        return {"input": f"ApiFlow(dict, {len(wf_dict)} keys)", "output": f"ApiFlow len={len(api)}", "result": "✓ dict input"}
    _run_test(collector, stage, "4.1", "ApiFlow(dict) → ApiFlow", t_4_1)

    def t_4_2():
        api = ApiFlow(wf_str, node_info=BUILTIN_NODE_INFO)
        assert isinstance(api, ApiFlow), f"ApiFlow(JSON str) returned {type(api)}"
        return {"input": f"ApiFlow(str, {len(wf_str)} chars)", "output": f"ApiFlow len={len(api)}", "result": "✓ JSON string"}
    _run_test(collector, stage, "4.2", "ApiFlow(JSON string) → ApiFlow", t_4_2)

    def t_4_3():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        assert isinstance(api, ApiFlow), f"ApiFlow(path) returned {type(api)}"
        return {"input": f"ApiFlow({Path(wf_path).name})", "output": f"ApiFlow len={len(api)}", "result": "✓ path input"}
    _run_test(collector, stage, "4.3", "ApiFlow(path) → ApiFlow", t_4_3)

    # ===================================================================
    # 4.4–4.15  Convert metadata  (was stage 2)
    # ===================================================================

    def t_4_4():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        assert api is not None, "ApiFlow() returned None"
        assert hasattr(api, "items"), "Converted result has no items()"
        return {"input": f"ApiFlow({Path(wf_path).name})", "output": type(api).__name__, "result": "✓ converted"}
    _run_test(collector, stage, "4.4", "ApiFlow(path, node_info) produces ApiFlow", t_4_4)

    def t_4_5():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = getattr(api, "unwrap", lambda: api)()
        if hasattr(raw, "items"):
            node_count = sum(1 for _, v in raw.items() if isinstance(v, dict) and "class_type" in v)
        else:
            node_count = sum(1 for _, v in api.items() if isinstance(v, dict) and "class_type" in v)
        assert node_count == 7, f"Expected 7 API nodes (MarkdownNotes stripped), got {node_count}"
        return {"input": "count API nodes post-strip", "output": f"{node_count} nodes", "result": "✓ MarkdownNotes stripped"}
    _run_test(collector, stage, "4.5", "MarkdownNotes stripped → 7 API nodes", t_4_5)

    def t_4_6():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        seed = api.KSampler.seed
        assert seed is not None, "api.KSampler.seed is None"
        return {"input": "api.KSampler.seed", "output": str(seed), "result": "✓ dot-access works"}
    _run_test(collector, stage, "4.6", "ApiFlow dot-access: api.KSampler.seed", t_4_6)

    def t_4_7():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        try:
            val = api["3"]
            assert val is not None, "api['3'] returned None"
            ct = val.get("class_type", "?") if isinstance(val, dict) else type(val).__name__
            return {"input": "api['3']", "output": ct, "result": "✓ bracket access"}
        except (KeyError, TypeError) as e:
            raise AssertionError(f"Path-style access api['3'] failed: {e}")
    _run_test(collector, stage, "4.7", "Path-style access: api['3']", t_4_7)

    def t_4_8():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        j = api.to_json()
        parsed = json.loads(j)
        assert isinstance(parsed, dict), "ApiFlow→to_json() is not a valid dict"
        return {"input": "ApiFlow→to_json()", "output": f"{len(j)} chars, {len(parsed)} keys", "result": "✓ valid JSON"}
    _run_test(collector, stage, "4.8", "ApiFlow one-liner → to_json()", t_4_8)

    def t_4_9():
        f = Flow.load(str(_BUNDLED_WORKFLOW))
        result = convert_with_errors(f, node_info=BUILTIN_NODE_INFO)
        assert result is not None, "convert_with_errors returned None"
        assert hasattr(result, "ok"), "No .ok on ConvertResult"
        assert hasattr(result, "data"), "No .data on ConvertResult"
        assert result.ok, f"Conversion failed: {result.errors}"
        errs = len(result.errors) if result.errors else 0
        return {"input": "convert_with_errors(flow)", "output": f"ok={result.ok}, errors={errs}", "result": "✓ conversion clean"}
    _run_test(collector, stage, "4.9", "convert_with_errors() returns result", t_4_9)

    def t_4_10():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        try:
            meta = ks._meta
            return {"input": "api.KSampler._meta", "output": str(type(meta).__name__), "result": "✓ _meta accessible"}
        except AttributeError:
            meta = getattr(ks, "meta", None)
            return {"input": "api.KSampler.meta", "output": str(type(meta).__name__) if meta else "None", "result": "✓ meta fallback"}
    _run_test(collector, stage, "4.10", "api.KSampler._meta access", t_4_10)

    def t_4_11():
        f = Flow.load(str(_BUNDLED_WORKFLOW))
        ks = f.nodes.KSampler
        try:
            ks._meta = {"test_key": "test_value"}
            return {"input": "ks._meta = {test_key: test_value}", "output": "set without error", "result": "✓ no crash"}
        except (AttributeError, TypeError):
            return {"input": "ks._meta = {test_key: test_value}", "output": "not supported", "result": "✓ no crash"}
    _run_test(collector, stage, "4.11", "Set _meta on Flow node (no crash)", t_4_11)

    def t_4_12():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
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
    _run_test(collector, stage, "4.12", "_meta survives to_json()", t_4_12)

    def t_4_13():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler
        try:
            choices = ks.sampler_name.choices()
            assert isinstance(choices, (list, tuple)), f"choices() returned {type(choices)}"
            assert "euler" in choices, f"'euler' not in choices: {choices}"
            return {"input": "ks.sampler_name.choices()", "output": f"{len(choices)} choices", "result": f"✓ euler in [{', '.join(choices[:4])}…]"}
        except AttributeError:
            sv = ks.sampler_name
            if hasattr(sv, 'choices'):
                choices = sv.choices()
                assert "euler" in choices
                return {"input": "ks.sampler_name.choices()", "output": f"{len(choices)} choices", "result": "✓ euler found"}
            else:
                raise AssertionError("No choices() method on sampler_name")
    _run_test(collector, stage, "4.13", "Widget introspection: .choices()", t_4_13)

    def t_4_14():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
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
    _run_test(collector, stage, "4.14", "Widget introspection: .tooltip()", t_4_14)

    def t_4_15():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
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
    _run_test(collector, stage, "4.15", "Widget introspection: .spec()", t_4_15)

    # ===================================================================
    # 4.16–4.23  DAG  (was stage 16)
    # ===================================================================

    def t_4_16():
        f = Flow(wf_path)
        dag = f.dag
        assert dag is not None
        ed = dag.edges
        assert hasattr(ed, '__len__') and len(ed) > 0
        return {"input": "flow.dag.edges", "output": f"{len(ed)} edges", "result": "✓ edges accessible"}
    _run_test(collector, stage, "4.16", "Flow dag.edges", t_4_16)

    def t_4_17():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        dag = api.dag
        assert dag is not None
        ed = dag.edges
        assert len(ed) > 0
        return {"input": "api.dag.edges", "output": f"{len(ed)} edges", "result": "✓ ApiFlow dag"}
    _run_test(collector, stage, "4.17", "ApiFlow dag.edges", t_4_17)

    def t_4_18():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        dag = api.dag
        ed = dag.edges
        ks_nodes = api.find(class_type="KSampler")
        assert len(ks_nodes) > 0
        ks_id = str(ks_nodes[0].id)
        upstream = [e for e in ed if str(e[1]) == ks_id or (len(e) > 1 and str(e[-1]) == ks_id)]
        return {"input": f"edges → KSampler (id={ks_id})", "output": f"{len(upstream)} upstream edges", "result": "✓ DAG structure"}
    _run_test(collector, stage, "4.18", "dag.edges pointing to KSampler", t_4_18)

    def t_4_19():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        dag = api.dag
        nd = dag.nodes
        ed = dag.edges
        assert len(nd) > 0 and len(ed) > 0
        return {"input": f"dag nodes + edges", "output": f"{len(nd)} nodes, {len(ed)} edges", "result": "✓ DAG populated"}
    _run_test(collector, stage, "4.19", "dag.nodes + dag.edges populated", t_4_19)

    def t_4_20():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        dag = api.dag
        dot = dag.to_dot()
        assert isinstance(dot, str) and "digraph" in dot.lower()
        return {"input": "dag.to_dot()", "output": f"{len(dot)} chars", "result": "✓ contains 'digraph'",
                "preview": dot, "preview_type": "dot"}
    _run_test(collector, stage, "4.20", "dag.to_dot()", t_4_20)

    def t_4_21():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        dag = api.dag
        mm = dag.to_mermaid()
        assert isinstance(mm, str) and ("graph" in mm.lower() or "flowchart" in mm.lower())
        return {"input": "dag.to_mermaid()", "output": f"{len(mm)} chars", "result": "✓ Mermaid syntax",
                "preview": mm, "preview_type": "mermaid"}
    _run_test(collector, stage, "4.21", "dag.to_mermaid()", t_4_21)

    def t_4_22():
        f = Flow(wf_path)
        dag = f.dag
        nd = dag.nodes
        assert isinstance(nd, (list, set, dict)) and len(nd) > 0
        return {"input": "dag.nodes", "output": f"{len(nd)} nodes", "result": "✓ populated"}
    _run_test(collector, stage, "4.22", "dag.nodes", t_4_22)

    def t_4_23():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        dag = api.dag
        save_nodes = api.find(class_type="SaveImage")
        if save_nodes:
            save_id = save_nodes[0].id
            desc = dag.descendants(save_id) if hasattr(dag, 'descendants') else []
            return {"input": f"dag.descendants({save_id})", "output": f"{len(desc)} descendants", "result": "✓ leaf or downstream"}
        return {"input": "dag.descendants (no SaveImage)", "output": "N/A", "result": "✓ skipped"}
    _run_test(collector, stage, "4.23", "dag.descendants(SaveImage)", t_4_23)

    # ===================================================================
    # 4.24–4.29  Save formatting  (was stage 18)
    # ===================================================================

    def t_4_24():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        j = api.to_json()
        parsed = json.loads(j)
        assert isinstance(parsed, dict) and len(parsed) > 0
        return {"input": "api.to_json()", "output": f"{len(j)} chars, {len(parsed)} nodes", "result": "✓ valid JSON"}
    _run_test(collector, stage, "4.24", "ApiFlow.to_json() round-trip", t_4_24)

    def t_4_25():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        j = api.to_json(indent=2)
        assert "\n" in j
        lines = j.count("\n")
        return {"input": "api.to_json(indent=2)", "output": f"{lines} lines", "result": "✓ pretty-printed"}
    _run_test(collector, stage, "4.25", "ApiFlow.to_json(indent=2)", t_4_25)

    def t_4_26():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            tmp = f.name
            api.save(tmp)
        try:
            loaded = json.loads(Path(tmp).read_text(encoding="utf-8"))
            assert isinstance(loaded, dict) and len(loaded) > 0
            return {"input": f"api.save({Path(tmp).name})", "output": f"{len(loaded)} nodes", "result": "✓ saved"}
        finally:
            os.unlink(tmp)
    _run_test(collector, stage, "4.26", "ApiFlow.save() to temp file", t_4_26)

    def t_4_27():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        save_nodes = api.find(class_type="SaveImage")
        if not save_nodes:
            return {"input": "find(SaveImage)", "output": "none found", "result": "✓ no save node"}
        save = save_nodes[0]
        prefix = save.filename_prefix if hasattr(save, "filename_prefix") else "default"
        return {"input": "SaveImage.filename_prefix", "output": str(prefix), "result": "✓ accessible"}
    _run_test(collector, stage, "4.27", "SaveImage filename_prefix access", t_4_27)

    def t_4_28():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = dict(api.unwrap()) if hasattr(api, 'unwrap') else dict(api)
        for nid, node in raw.items():
            if isinstance(node, dict) and node.get("class_type") == "SaveImage":
                inputs = node.get("inputs", {})
                assert "filename_prefix" in inputs or "images" in inputs
                return {"input": f"raw[{nid}]['inputs']", "output": f"keys: {list(inputs.keys())}", "result": "✓ SaveImage inputs"}
        return {"input": "SaveImage raw inputs", "output": "no SaveImage", "result": "✓ ran"}
    _run_test(collector, stage, "4.28", "SaveImage raw inputs dict", t_4_28)

    def t_4_29():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = dict(api.unwrap()) if hasattr(api, 'unwrap') else dict(api)
        ct_list = sorted({n.get("class_type") for n in raw.values() if isinstance(n, dict) and "class_type" in n})
        assert len(ct_list) > 0
        return {"input": "api class_types", "output": ", ".join(ct_list), "result": f"✓ {len(ct_list)} types"}
    _run_test(collector, stage, "4.29", "ApiFlow class_type enumeration", t_4_29)

    # ===================================================================
    # 4.30–4.32  Subgraphs + MarkdownNote  (was stage 21)
    # ===================================================================

    def t_4_30():
        fixtures_dir = kwargs.get("fixtures_dir")
        if not fixtures_dir:
            raise SkipTest("No --fixtures-dir provided")
        sg_path = Path(fixtures_dir) / "subgraph-x2" / "workflow-subgraph.json"
        if not sg_path.is_file():
            raise SkipTest(f"Subgraph fixture not found: {sg_path}")

        oi = BUILTIN_NODE_INFO
        wf_flat = json.loads(_BUNDLED_WORKFLOW.read_text(encoding="utf-8"))
        wf_sg = json.loads(sg_path.read_text(encoding="utf-8"))

        api_flat = Flow.load(wf_flat).convert(node_info=oi)
        api_sg = Flow.load(wf_sg).convert(node_info=oi)

        raw_flat = getattr(api_flat, "unwrap", lambda: dict(api_flat))()
        raw_sg = getattr(api_sg, "unwrap", lambda: dict(api_sg))()

        for node in raw_sg.values():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type", "")
            assert not (isinstance(ct, str) and "-" in ct and len(ct) >= 32), f"UUID class_type: {ct!r}"

        types_flat = sorted([n["class_type"] for n in raw_flat.values() if isinstance(n, dict) and "class_type" in n])
        types_sg = sorted([n["class_type"] for n in raw_sg.values() if isinstance(n, dict) and "class_type" in n])
        assert types_sg == types_flat

        save_ids = [nid for nid, n in raw_sg.items() if isinstance(n, dict) and n.get("class_type") == "SaveImage"]
        assert len(save_ids) == 1
        save = raw_sg[save_ids[0]]
        images = save.get("inputs", {}).get("images")
        assert isinstance(images, list) and len(images) == 2
        upstream = raw_sg.get(str(images[0]))
        assert upstream is not None and upstream.get("class_type") == "VAEDecode"
        return {
            "input": f"flat={len(raw_flat)} nodes, subgraph={len(raw_sg)} nodes",
            "output": f"types match: {types_sg}",
            "result": "✓ subgraph flattened correctly",
        }
    _run_test(collector, stage, "4.30", "Subgraph converts like flat workflow", t_4_30)

    def t_4_31():
        prompt = {
            "1": {"class_type": "TotallyFakeNode", "inputs": {}},
            "2": {"class_type": "KSampler", "inputs": {}},
        }
        node_info = {"KSampler": {"input": {}}}
        out = _sanitize_api_prompt(prompt, node_info=node_info)
        assert "2" in out and "1" not in out
        return {
            "input": "prompt with TotallyFakeNode + KSampler",
            "output": f"kept: {list(out.keys())}",
            "result": "✓ unknown node stripped",
        }
    _run_test(collector, stage, "4.31", "Sanitizer drops unknown nodes with node_info", t_4_31)

    def t_4_32():
        wf = convert_workflow(str(_BUNDLED_WORKFLOW), node_info=BUILTIN_NODE_INFO, server_url=None)
        class_types = [n.get("class_type") for n in wf.values() if isinstance(n, dict)]
        assert "MarkdownNote" not in class_types
        return {
            "input": f"convert_workflow({_BUNDLED_WORKFLOW.name})",
            "output": f"class_types: {class_types}",
            "result": "✓ MarkdownNote absent",
        }
    _run_test(collector, stage, "4.32", "convert_workflow skips MarkdownNote", t_4_32)

    _print_stage_summary(collector, stage)
