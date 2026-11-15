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

    from autograph import Flow, ApiFlow, convert_with_errors, upload_file, upload_image
    from autograph.api import convert_workflow, _sanitize_api_prompt

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
                node["_meta"] = {"AUTOGRAPH_test": True}
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

    def t_4_33():
        workflow = {
            "last_node_id": 1,
            "last_link_id": 0,
            "nodes": [
                {
                    "id": 1,
                    "type": "LoadImage",
                    "mode": 0,
                    "inputs": [],
                    "outputs": [],
                    "widgets_values": ["src.jpeg", "image"],
                }
            ],
            "links": [],
        }
        node_info = {
            "LoadImage": {
                "input": {
                    "required": {
                        "image": [["example.png"], {"image_upload": True}],
                    }
                }
            }
        }
        api = convert_workflow(workflow, node_info=node_info, server_url=None)
        image = api["1"]["inputs"].get("image")
        assert image == "src.jpeg", f"Expected LoadImage.image to survive conversion, got {image!r}"
        return {"input": "LoadImage widgets_values=['src.jpeg', 'image']", "output": image, "result": "✓ upload filename preserved"}
    _run_test(collector, stage, "4.33", "LoadImage upload filename survives stale node_info choices", t_4_33)

    def t_4_34():
        import autograph.net as net

        old_urlopen = net.urllib.request.urlopen
        requests = []

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return self.payload

        def fake_urlopen(req, timeout):
            body = req.data or b""
            requests.append((req.full_url, body, timeout))
            subfolder = "faces" if b"\r\n\r\nfaces\r\n" in body else ""
            payload = json.dumps({"name": "src.jpeg", "subfolder": subfolder, "type": "input"}).encode("utf-8")
            return FakeResponse(payload)

        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "src.jpeg"
            img.write_bytes(b"fake image bytes")
            try:
                net.urllib.request.urlopen = fake_urlopen
                uploaded = upload_file(img, server_url="http://comfy.example", subfolder="faces", overwrite=True)
                assert uploaded.path == "faces/src.jpeg"
                assert uploaded.kind == "image"
                assert uploaded.mime_type == "image/jpeg"
                assert requests[-1][0] == "http://comfy.example/upload/image"
                assert b'name="overwrite"\r\n\r\ntrue' in requests[-1][1]

                api = ApiFlow({"1": {"class_type": "LoadImage", "inputs": {}}})
                api.upload_image(img, server_url="http://comfy.example")
                assert api.unwrap()["1"]["inputs"]["image"] == "src.jpeg"

                flow = Flow(
                    {
                        "last_node_id": 1,
                        "last_link_id": 0,
                        "nodes": [{"id": 1, "type": "LoadImage", "inputs": [], "outputs": [], "widgets_values": []}],
                        "links": [],
                    },
                    node_info={"LoadImage": {"input": {"required": {"image": [["example.png"], {"image_upload": True}]}}}},
                )
                flow.upload_image(img, server_url="http://comfy.example")
                assert flow.unwrap()["nodes"][0]["widgets_values"][0] == "src.jpeg"

                audio = Path(td) / "voice.wav"
                audio.write_bytes(b"fake audio bytes")
                uploaded_audio = upload_file(audio, server_url="http://comfy.example", accept="audio")
                assert uploaded_audio.kind == "audio"
                assert uploaded_audio.mime_type in ("audio/x-wav", "audio/wav")
            finally:
                net.urllib.request.urlopen = old_urlopen

        return {"input": "upload_file(src.jpeg) + ApiFlow/Flow helpers", "output": "src.jpeg", "result": "✓ upload helpers patch LoadImage"}
    _run_test(collector, stage, "4.34", "upload_file helpers upload and patch LoadImage", t_4_34)

    # ===================================================================
    # 4.35–4.37  Subgraph promoted widgets + V3 schemas (regression)
    # ===================================================================

    _SG_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    _SG_NODE_INFO = {
        "TestSwitch": {"input": {"required": {"switch": ["BOOLEAN", {}]}}},
        "TestSlice": {"input": {"required": {"duration": ["FLOAT", {"default": 0.0}]}}},
    }
    _SG_WORKFLOW = {
        "last_node_id": 5,
        "last_link_id": 20,
        "nodes": [
            {
                "id": 5,
                "type": _SG_ID,
                "inputs": [
                    {"name": "switch", "type": "BOOLEAN", "link": None, "widget": {"name": "switch"}},
                ],
                "outputs": [],
                # Promoted values: one entry per widget-promoted subgraph input.
                "widgets_values": [False, 60.5],
            }
        ],
        "links": [],
        "definitions": {
            "subgraphs": [
                {
                    "id": _SG_ID,
                    "name": "Test Subgraph",
                    "inputs": [
                        {"id": "i0", "name": "switch", "type": "BOOLEAN", "linkIds": [10]},
                        {"id": "i1", "name": "duration", "type": "FLOAT", "linkIds": [11]},
                    ],
                    "outputs": [],
                    "nodes": [
                        {
                            "id": 1,
                            "type": "TestSwitch",
                            "inputs": [{"name": "switch", "type": "BOOLEAN", "link": 10, "widget": {"name": "switch"}}],
                            "outputs": [],
                            # Stale inner value — the instance's False must win.
                            "widgets_values": [True],
                        },
                        {
                            "id": 2,
                            "type": "TestSlice",
                            "inputs": [{"name": "duration", "type": "FLOAT", "link": 11, "widget": {"name": "duration"}}],
                            "outputs": [],
                            "widgets_values": [0.0],
                        },
                    ],
                    "links": [
                        {"id": 10, "origin_id": -10, "origin_slot": 0, "target_id": 1, "target_slot": 0, "type": "BOOLEAN"},
                        {"id": 11, "origin_id": -10, "origin_slot": 1, "target_id": 2, "target_slot": 0, "type": "FLOAT"},
                    ],
                }
            ]
        },
    }

    def t_4_35():
        api = Flow.load(json.loads(json.dumps(_SG_WORKFLOW))).convert(node_info=_SG_NODE_INFO)
        raw = getattr(api, "unwrap", lambda: dict(api))()
        by_type = {n["class_type"]: n for n in raw.values() if isinstance(n, dict)}
        sw = by_type["TestSwitch"]["inputs"]
        sl = by_type["TestSlice"]["inputs"]
        assert sw.get("switch") is False, f"promoted literal lost/stale: {sw!r}"
        assert sl.get("duration") == 60.5, f"promoted literal lost/stale: {sl!r}"
        return {
            "input": "subgraph instance widgets_values [False, 60.5], inner stale [True]/[0.0]",
            "output": f"switch={sw.get('switch')!r} duration={sl.get('duration')!r}",
            "result": "✓ promoted widget literals carried onto inner nodes",
        }
    _run_test(collector, stage, "4.35", "Subgraph promoted widget literals survive flattening", t_4_35)

    def t_4_36():
        node_info = {
            "TestResize": {
                "input": {
                    "required": {
                        "resize_type": [
                            "COMFY_DYNAMICCOMBO_V3",
                            {
                                "options": [
                                    {"key": "scale dimensions", "inputs": {"required": {"width": ["INT", {"default": 512}], "height": ["INT", {"default": 512}]}}},
                                    {"key": "scale by multiplier", "inputs": {"required": {"multiplier": ["FLOAT", {"default": 1.0}]}}},
                                ]
                            },
                        ],
                        "scale_method": ["COMBO", {"options": ["nearest", "lanczos"], "default": "lanczos"}],
                    }
                }
            }
        }
        wf = {
            "last_node_id": 1,
            "last_link_id": 0,
            "nodes": [
                {"id": 1, "type": "TestResize", "inputs": [], "outputs": [], "widgets_values": ["scale by multiplier", 2.0, "lanczos"]}
            ],
            "links": [],
        }
        api = Flow.load(wf).convert(node_info=node_info)
        raw = getattr(api, "unwrap", lambda: dict(api))()
        inputs = raw["1"]["inputs"]
        assert inputs.get("resize_type") == "scale by multiplier", f"combo key mangled: {inputs!r}"
        assert inputs.get("resize_type.multiplier") == 2.0, f"sub-widget missing: {inputs!r}"
        assert inputs.get("scale_method") == "lanczos", f"trailing widget shifted: {inputs!r}"
        return {
            "input": "widgets_values ['scale by multiplier', 2.0, 'lanczos']",
            "output": json.dumps(inputs),
            "result": "✓ dynamic combo key + dotted sub-widget emitted",
        }
    _run_test(collector, stage, "4.36", "COMFY_DYNAMICCOMBO_V3 emits option key + sub-widgets", t_4_36)

    def t_4_37():
        from autograph.convert import get_widget_input_names
        node_info = {
            "T": {
                "input": {
                    "required": {
                        # Widgets:
                        "value": ["BOOLEAN", {}],
                        "combo": [["a", "b"]],
                        "steps": ["INT", {"tooltip": "tooltip-only INT is still a widget"}],
                        "color": ["COLOR", {"default": "#ffffff", "socketless": True}],
                        "editor_state": ["BOUNDING_BOXES", {"default": [], "socketless": False}],
                        "frame_rate": ["FLOAT,INT", {"default": 25.0, "widgetType": "FLOAT"}],
                        # Connections:
                        "on_false": ["COMFY_MATCHTYPE_V3", {"template": {"template_id": "x", "allowed_types": "*"}}],
                        "img": ["IMAGE", {}],
                        "model": ["MODEL", {"display_name": "model"}],
                        "grow": ["COMFY_AUTOGROW_V3", {"template": {"input": {"required": {"image": ["IMAGE", {}]}}, "prefix": "image"}}],
                        "multi_conn": ["BOUNDING_BOX,ARRAY,STRING", {"tooltip": "unmarked multi-type is a connection"}],
                        "forced": ["INT", {"forceInput": True}],
                    }
                }
            }
        }
        names = get_widget_input_names("T", node_info, use_api=True)
        expected = ["value", "combo", "steps", "color", "editor_state", "frame_rate"]
        assert names == expected, f"widget classification wrong: {names!r} != {expected!r}"
        return {
            "input": "6 widget specs (incl. socketless/widgetType) + 6 connection specs",
            "output": f"widgets: {names}",
            "result": "✓ V3 widget/connection classification",
        }
    _run_test(collector, stage, "4.37", "Widget classification: V3 widgets vs connections", t_4_37)

    def t_4_38():
        sg_a = "aaaa0000-0000-4000-8000-000000000001"
        sg_b = "bbbb0000-0000-4000-8000-000000000002"
        node_info = {
            "TestProduce": {"input": {}, "output": ["IMAGE"]},
            "TestConsume": {"input": {"required": {"img": ["IMAGE", {}], "val": ["INT", {"default": 0}]}}},
        }
        wf = {
            "last_node_id": 11,
            "last_link_id": 30,
            "nodes": [
                # Upstream instance first, so flattening it consumes/rewrites
                # link 30 before the downstream instance resolves it.
                {"id": 10, "type": sg_a, "inputs": [], "outputs": [{"name": "out", "links": [30]}], "widgets_values": []},
                {
                    "id": 11,
                    "type": sg_b,
                    "inputs": [{"name": "img", "type": "IMAGE", "link": 30}],
                    "outputs": [],
                    # Null-padded serialization variant: one promoted INT widget.
                    "widgets_values": [None, None, 42],
                },
            ],
            "links": [[30, 10, 0, 11, 0, "IMAGE"]],
            "definitions": {
                "subgraphs": [
                    {
                        "id": sg_a,
                        "name": "Producer",
                        "inputs": [],
                        "outputs": [{"id": "o0", "name": "out", "type": "IMAGE"}],
                        "nodes": [{"id": 1, "type": "TestProduce", "inputs": [], "outputs": [{"name": "o", "links": [1]}], "widgets_values": []}],
                        "links": [{"id": 1, "origin_id": 1, "origin_slot": 0, "target_id": -20, "target_slot": 0, "type": "IMAGE"}],
                    },
                    {
                        "id": sg_b,
                        "name": "Consumer",
                        "inputs": [
                            {"id": "i0", "name": "img", "type": "IMAGE", "linkIds": [2]},
                            {"id": "i1", "name": "val", "type": "INT", "linkIds": [3]},
                        ],
                        "outputs": [],
                        "nodes": [
                            {
                                "id": 1,
                                "type": "TestConsume",
                                "inputs": [
                                    {"name": "img", "type": "IMAGE", "link": 2},
                                    {"name": "val", "type": "INT", "link": 3, "widget": {"name": "val"}},
                                ],
                                "outputs": [],
                                "widgets_values": [0],
                            }
                        ],
                        "links": [
                            {"id": 2, "origin_id": -10, "origin_slot": 0, "target_id": 1, "target_slot": 0, "type": "IMAGE"},
                            {"id": 3, "origin_id": -10, "origin_slot": 1, "target_id": 1, "target_slot": 1, "type": "INT"},
                        ],
                    },
                ]
            },
        }
        api = Flow.load(wf).convert(node_info=node_info)
        raw = getattr(api, "unwrap", lambda: dict(api))()
        by_type = {n["class_type"]: (nid, n) for nid, n in raw.items() if isinstance(n, dict)}
        prod_id, _ = by_type["TestProduce"]
        _, consume = by_type["TestConsume"]
        img = consume["inputs"].get("img")
        assert img == [prod_id, 0], f"chained subgraph link lost: {consume['inputs']!r}"
        assert consume["inputs"].get("val") == 42, f"null-padded promoted value wrong: {consume['inputs']!r}"
        return {
            "input": "SG_A instance output → SG_B instance input; wv [null, null, 42]",
            "output": json.dumps(consume["inputs"]),
            "result": "✓ chained subgraphs + null-padded widgets_values",
        }
    _run_test(collector, stage, "4.38", "Chained subgraph instances + null-padded instance widgets", t_4_38)

    def t_4_39():
        node_info = {
            "Loader": {"input": {"required": {"ckpt_name": [["a.safetensors", "b.safetensors"], {}]}}, "output": ["MODEL"]},
            "Sampler": {
                "input": {
                    "required": {
                        "model": ["MODEL", {}],
                        "seed": ["INT", {"default": 0, "min": 0, "max": 2**64, "control_after_generate": True}],
                        "steps": ["INT", {"default": 20, "min": 1, "max": 10000}],
                        "sampler_name": ["COMBO", {"options": ["euler", "dpmpp_2m"], "default": "euler"}],
                        "denoise": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0}],
                    }
                },
                "output": ["LATENT"],
            },
        }
        wf = {
            "last_node_id": 2,
            "last_link_id": 1,
            "nodes": [
                {"id": 1, "type": "Loader", "inputs": [], "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}],
                 "widgets_values": ["installed_later.safetensors"]},
                {"id": 2, "type": "Sampler", "inputs": [{"name": "model", "type": "MODEL", "link": 1}], "outputs": [],
                 "widgets_values": [7, "fixed", 30, "sampler_from_another_server", 0.5]},
            ],
            "links": [[1, 1, 0, 2, 0, "MODEL"]],
        }
        res = Flow.load(wf).convert_with_errors(node_info=node_info)
        raw = getattr(res.data, "unwrap", lambda: dict(res.data))()
        assert raw["1"]["inputs"]["ckpt_name"] == "installed_later.safetensors", f"model name lost: {raw['1']['inputs']!r}"
        want = {"model": ["1", 0], "seed": 7, "steps": 30, "sampler_name": "sampler_from_another_server", "denoise": 0.5}
        assert raw["2"]["inputs"] == want, f"unlisted choice broke alignment: {raw['2']['inputs']!r}"
        noted = [w for w in res.warnings if "not among the options" in w.message]
        assert len(noted) == 2, f"expected a warning per unlisted choice: {[w.message for w in res.warnings]!r}"
        return {
            "input": "ckpt_name / sampler_name values that node_info does not list",
            "output": json.dumps(raw["2"]["inputs"]),
            "result": "✓ values kept (with warnings), alignment intact",
        }
    _run_test(collector, stage, "4.39", "Choice values missing from node_info are kept, not nulled", t_4_39)

    def t_4_40():
        node_info = {
            "Src": {"input": {}, "output": ["IMAGE"]},
            "Pass": {"input": {"required": {"image": ["IMAGE", {}]}}, "output": ["IMAGE"]},
            "Mix": {"input": {"required": {"a": ["IMAGE", {}]}, "optional": {"b": ["IMAGE", {}], "c": ["IMAGE", {}], "d": ["IMAGE", {}]}}, "output": []},
        }

        def node(i, t, mode=0, inputs=None, outs=1):
            return {"id": i, "type": t, "mode": mode, "inputs": inputs or [],
                    "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": []}] * outs, "widgets_values": []}

        wf = {
            "last_node_id": 6,
            "last_link_id": 6,
            "nodes": [
                node(1, "Src"),
                node(2, "Src", mode=2),                                                   # muted source
                node(3, "Pass", mode=4, inputs=[{"name": "image", "type": "IMAGE", "link": 1}]),   # bypassed, passes node 1 through
                node(4, "Pass", mode=4, inputs=[{"name": "image", "type": "IMAGE", "link": None}]),  # bypassed, nothing behind it
                node(5, "Mix", outs=0, inputs=[
                    {"name": "a", "type": "IMAGE", "link": 2},
                    {"name": "b", "type": "IMAGE", "link": 3},
                    {"name": "c", "type": "IMAGE", "link": 4},
                    {"name": "d", "type": "IMAGE", "link": 5},
                ]),
            ],
            "links": [[1, 1, 0, 3, 0, "IMAGE"], [2, 1, 0, 5, 0, "IMAGE"], [3, 2, 0, 5, 1, "IMAGE"],
                      [4, 3, 0, 5, 2, "IMAGE"], [5, 4, 0, 5, 3, "IMAGE"]],
        }
        res = Flow.load(wf).convert_with_errors(node_info=node_info)
        raw = getattr(res.data, "unwrap", lambda: dict(res.data))()
        assert set(raw) == {"1", "5"}, f"unexpected nodes: {sorted(raw)}"
        inputs = raw["5"]["inputs"]
        assert inputs == {"a": ["1", 0], "c": ["1", 0]}, f"links to absent nodes must be dropped: {inputs!r}"
        for nid, n in raw.items():
            for v in n["inputs"].values():
                assert not (isinstance(v, list) and len(v) == 2 and str(v[0]) not in raw), f"dangling link in {nid}: {v!r}"
        dropped = [w for w in res.warnings if w.message.startswith("Dropped input")]
        assert sorted(w.details["input"] for w in dropped) == ["b", "d"], [w.message for w in res.warnings]
        return {
            "input": "inputs fed by a muted node, a bypassed pass-through, and a bypassed dead end",
            "output": json.dumps(inputs),
            "result": "✓ like the ComfyUI editor: pass through, else drop the input",
        }
    _run_test(collector, stage, "4.40", "Links from muted / dead-end bypassed nodes are dropped", t_4_40)

    def t_4_41():
        node_info = {
            "Src": {"input": {}, "output": ["IMAGE"]},
            "ApiEdit": {
                "input": {
                    "required": {
                        "prompt": ["STRING", {"default": "", "multiline": True}],
                        "model": ["COMFY_DYNAMICCOMBO_V3", {"options": [
                            {"key": "m1", "inputs": {"required": {
                                "images": ["COMFY_AUTOGROW_V3", {"template": {"input": {"required": {"image": ["IMAGE", {}]}}, "names": ["image_1", "image_2"], "min": 1}}],
                                "mask": ["MASK", {}],
                                "resolution": ["COMBO", {"options": ["1K", "2K"]}],
                                "count": ["INT", {"default": 1, "min": 1, "max": 10}],
                            }}},
                            {"key": "m2", "inputs": {"required": {"resolution": ["COMBO", {"options": ["1K"]}]}}},
                        ]}],
                        "seed": ["INT", {"default": 0, "min": 0, "max": 2147483647, "control_after_generate": True}],
                    },
                    "optional": {"system_prompt": ["STRING", {"default": "", "multiline": True}]},
                },
                "output": ["IMAGE"],
            },
        }
        wf = {
            "last_node_id": 2,
            "last_link_id": 1,
            "nodes": [
                {"id": 1, "type": "Src", "inputs": [], "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [1]}], "widgets_values": []},
                {"id": 2, "type": "ApiEdit",
                 "inputs": [{"name": "model.images.image_1", "type": "IMAGE", "link": 1},
                            {"name": "model.images.image_2", "type": "IMAGE", "link": None}],
                 "outputs": [],
                 "widgets_values": ["make it night", "m1", "2K", 3, 123456, "randomize", ""]},
            ],
            "links": [[1, 1, 0, 2, 0, "IMAGE"]],
        }
        api = Flow.load(wf).convert(node_info=node_info)
        inputs = getattr(api, "unwrap", lambda: dict(api))()["2"]["inputs"]
        want = {
            "prompt": "make it night", "model": "m1", "model.resolution": "2K", "model.count": 3,
            "seed": 123456, "system_prompt": "", "model.images.image_1": ["1", 0],
        }
        assert inputs == want, f"dynamic combo with connection sub-inputs misaligned: {inputs!r}"
        return {
            "input": "option with autogrow + mask connections, then a seed with control_after_generate",
            "output": json.dumps(inputs),
            "result": "✓ connection sub-inputs take no widget slot; 'randomize' is not a system prompt",
        }
    _run_test(collector, stage, "4.41", "Dynamic combo: connection sub-inputs and control_after_generate stay aligned", t_4_41)

    def t_4_42():
        node_info = {
            "Pose": {
                "input": {
                    "required": {
                        "format": ["COMFY_DYNAMICCOMBO_V3", {"options": [
                            {"key": "glb", "inputs": {"required": {
                                "style": ["COMFY_DYNAMICCOMBO_V3", {"options": [
                                    {"key": "mesh", "inputs": {"required": {}}},
                                    {"key": "sticks", "inputs": {"required": {
                                        "radius": ["FLOAT", {"default": 0.02, "min": 0.0, "max": 1.0}],
                                        "hands": ["BOOLEAN", {"default": True}],
                                    }}},
                                ]}],
                                "smooth": ["INT", {"default": 0, "min": 0, "max": 30}],
                            }}},
                        ]}],
                        "fps": ["INT", {"default": 30, "min": 1, "max": 240}],
                    }
                },
                "output": [],
            }
        }
        wf = {"last_node_id": 1, "last_link_id": 0, "links": [], "nodes": [
            {"id": 1, "type": "Pose", "inputs": [], "outputs": [], "widgets_values": ["glb", "sticks", 0.5, False, 0, 24]}
        ]}
        api = Flow.load(wf).convert(node_info=node_info)
        inputs = getattr(api, "unwrap", lambda: dict(api))()["1"]["inputs"]
        want = {"format": "glb", "format.style": "sticks", "format.style.radius": 0.5, "format.style.hands": False,
                "format.smooth": 0, "fps": 24}
        assert inputs == want, f"nested dynamic combo out of order: {inputs!r}"
        assert list(inputs) == list(want), f"sub-widgets must follow their combo: {list(inputs)!r}"
        return {"input": "dynamic combo inside a dynamic combo", "output": json.dumps(inputs), "result": "✓ depth-first order"}
    _run_test(collector, stage, "4.42", "Nested dynamic combos expand depth-first", t_4_42)

    def t_4_43():
        node_info = {
            "Boxes": {"input": {"required": {
                "width": ["INT", {"default": 1024, "min": 64, "max": 16384}],
                "editor_state": ["BOUNDING_BOXES", {"default": [], "socketless": False}],
                "tags": ["COMBO", {"options": ["a", "b", "c"], "multiselect": True}],
            }}, "output": []},
        }
        boxes = [{"x": 1, "y": 2}, {"x": 3, "y": 4}]
        wf = {"last_node_id": 1, "last_link_id": 0, "links": [], "nodes": [
            {"id": 1, "type": "Boxes", "inputs": [], "outputs": [], "widgets_values": [896, boxes, ["a", "c"]]}
        ]}
        api = Flow.load(wf).convert(node_info=node_info)
        inputs = getattr(api, "unwrap", lambda: dict(api))()["1"]["inputs"]
        assert inputs == {"width": 896, "editor_state": {"__value__": boxes}, "tags": {"__value__": ["a", "c"]}}, f"{inputs!r}"
        return {"input": "widget values that are lists", "output": json.dumps(inputs),
                "result": "✓ wrapped as {'__value__': [...]} so they are not read as links"}
    _run_test(collector, stage, "4.43", "List-valued widget values are wrapped like the editor does", t_4_43)

    def t_4_44():
        node_info = {
            "Gen": {"input": {"required": {
                "model": ["COMBO", {"options": ["m-2", "m-1"]}],
                "prompt": ["STRING", {"multiline": True}],
                "count": ["INT", {"default": 1, "min": 1, "max": 10}],
            }, "optional": {
                "quality": ["COMBO", {"options": ["medium", "low"]}],
                "note": ["STRING", {}],
            }}, "output": []},
        }
        # Saved before the node gained "quality" and "note".
        wf = {"last_node_id": 1, "last_link_id": 0, "links": [], "nodes": [
            {"id": 1, "type": "Gen", "inputs": [], "outputs": [], "widgets_values": ["m-1", "a fox", 2]}
        ]}
        api = Flow.load(wf).convert(node_info=node_info)
        inputs = getattr(api, "unwrap", lambda: dict(api))()["1"]["inputs"]
        assert inputs == {"model": "m-1", "prompt": "a fox", "count": 2, "quality": "medium", "note": ""}, f"{inputs!r}"
        return {"input": "workflow older than two of the node's inputs", "output": json.dumps(inputs),
                "result": "✓ new widgets start where the editor starts them, not at null"}
    _run_test(collector, stage, "4.44", "Widgets the workflow has no value for get the editor's initial value", t_4_44)

    def t_4_45():
        node_info = {
            "Src": {"input": {}, "output": ["IMAGE"]},
            "PrimInt": {"input": {"required": {"value": ["INT", {"default": 0}]}}, "output": ["INT"]},
            "Blend": {"input": {"required": {"base": ["IMAGE", {}], "overlay": ["IMAGE", {}]}}, "output": ["IMAGE", "IMAGE"]},
            "Sampler": {"input": {"required": {
                "image": ["IMAGE", {}],
                "steps": ["INT", {"default": 20, "min": 1, "max": 10000}],
                "curve": ["CURVE", {"default": {"points": [[0, 0], [1, 1]]}, "socketless": True}],
            }}, "output": []},
        }
        img = {"name": "IMAGE", "type": "IMAGE", "links": []}
        wf = {
            "last_node_id": 5,
            "last_link_id": 5,
            "nodes": [
                {"id": 1, "type": "Src", "inputs": [], "outputs": [img], "widgets_values": []},
                {"id": 2, "type": "Src", "inputs": [], "outputs": [img], "widgets_values": []},
                {"id": 3, "type": "Blend", "mode": 4, "outputs": [img, img], "widgets_values": [],
                 "inputs": [{"name": "base", "type": "IMAGE", "link": 1}, {"name": "overlay", "type": "IMAGE", "link": 2}]},
                {"id": 4, "type": "PrimInt", "mode": 2, "inputs": [], "outputs": [{"name": "INT", "type": "INT", "links": [4]}],
                 "widgets_values": [99]},
                {"id": 5, "type": "Sampler", "outputs": [],
                 "inputs": [{"name": "image", "type": "IMAGE", "link": 3},
                            {"name": "steps", "type": "INT", "widget": {"name": "steps"}, "link": 4}],
                 "widgets_values": [30, {"points": [[0, 0], [0.5, 0.8], [1, 1]]}]},
            ],
            # Sampler.image comes from the bypassed Blend's *second* output.
            "links": [[1, 1, 0, 3, 0, "IMAGE"], [2, 2, 0, 3, 1, "IMAGE"], [3, 3, 1, 5, 0, "IMAGE"], [4, 4, 0, 5, 1, "INT"]],
        }
        res = Flow.load(wf).convert_with_errors(node_info=node_info)
        inputs = getattr(res.data, "unwrap", lambda: dict(res.data))()["5"]["inputs"]
        assert inputs["image"] == ["2", 0], f"bypass must pass the input at the output's own index: {inputs['image']!r}"
        assert inputs["steps"] == 30, f"a widget fed by a muted node keeps its own value: {inputs.get('steps')!r}"
        assert inputs["curve"] == {"__type__": "CURVE", "__value__": {"points": [[0, 0], [0.5, 0.8], [1, 1]]}}, f"{inputs['curve']!r}"
        kept = [w for w in res.warnings if "own widget value" in w.message]
        assert len(kept) == 1 and kept[0].details["input"] == "steps", [w.message for w in res.warnings]
        return {
            "input": "bypassed node with two IMAGE inputs (second output used); widget fed by a muted node; a CURVE widget",
            "output": json.dumps(inputs),
            "result": "✓ same choices as the ComfyUI editor's graphToPrompt",
        }
    _run_test(collector, stage, "4.45", "Bypass slot choice, widget fallback for muted sources, typed curve values", t_4_45)

    def t_4_46():
        node_info = {
            "Load3D": {"input": {"required": {
                "model_file": ["COMBO", {"options": ["a.glb"], "file_upload": True}],
                "image": ["LOAD_3D", {}],
                "width": ["INT", {"default": 1024, "min": 1, "max": 4096}],
                "height": ["INT", {"default": 1024, "min": 1, "max": 4096}],
            }}, "output": ["IMAGE", "FILE_3D"]},
            "Save3D": {"input": {"required": {
                "model_3d": ["FILE_3D", {}],
                "filename_prefix": ["STRING", {"default": "3d/ComfyUI"}],
                "viewport_state": ["LOAD_3D", {}],
                "width": ["INT", {"default": 1024, "min": 1, "max": 4096}],
                "height": ["INT", {"default": 1024, "min": 1, "max": 4096}],
            }}, "output": []},
            "Webcam": {"input": {"required": {
                "image": ["WEBCAM", {}],
                "width": ["INT", {"default": 0, "min": 0, "max": 16384}],
                "capture_on_queue": ["BOOLEAN", {"default": True}],
            }}, "output": ["IMAGE"]},
        }
        wf = {
            "last_node_id": 3,
            "last_link_id": 1,
            "nodes": [
                # The editor saves its three button widgets between model_file and image.
                {"id": 1, "type": "Load3D", "inputs": [],
                 "outputs": [{"name": "image", "type": "IMAGE", "links": []}, {"name": "model_3d", "type": "FILE_3D", "links": [1]}],
                 "widgets_values": ["toy.glb", "upload3dmodel", "uploadExtraResources", "clear", "", 512, 768]},
                {"id": 2, "type": "Save3D", "inputs": [{"name": "model_3d", "type": "FILE_3D", "link": 1}], "outputs": [],
                 "widgets_values": ["3d/out", "", 640, 480]},
                {"id": 3, "type": "Webcam", "inputs": [], "outputs": [], "widgets_values": [None, 320, False]},
            ],
            "links": [[1, 1, 1, 2, 0, "FILE_3D"]],
        }
        res = Flow.load(wf).convert_with_errors(node_info=node_info)
        raw = getattr(res.data, "unwrap", lambda: dict(res.data))()
        assert raw["1"]["inputs"] == {"model_file": "toy.glb", "image": "", "width": 512, "height": 768}, f"{raw['1']['inputs']!r}"
        assert raw["2"]["inputs"] == {"model_3d": ["1", 1], "filename_prefix": "3d/out", "viewport_state": "", "width": 640, "height": 480}, f"{raw['2']['inputs']!r}"
        assert raw["3"]["inputs"] == {"image": "", "width": 320, "capture_on_queue": False}, f"{raw['3']['inputs']!r}"
        told = sorted((w.details["class_type"], w.details["input"]) for w in res.warnings if w.details and w.details.get("queue_time"))
        assert told == [("Load3D", "image"), ("Save3D", "viewport_state"), ("Webcam", "image")], told
        return {
            "input": "LOAD_3D and WEBCAM widgets between other widgets and the editor's button entries",
            "output": json.dumps(raw["1"]["inputs"]),
            "result": "✓ they take their slot, keep the saved value, and each one is reported",
        }
    _run_test(collector, stage, "4.46", "Widgets the editor fills at queue time are aligned and reported", t_4_46)

    _print_stage_summary(collector, stage)
