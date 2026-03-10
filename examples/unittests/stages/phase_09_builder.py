"""Phase 9 — Builder API: create flows, add/remove nodes, connect/disconnect, layout.

Tests for the programmatic flow construction and editing API.
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
    BUILTIN_NODE_INFO,
    SkipTest,
)

STAGE = "Phase 9: Builder API"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autograph import Flow, ApiFlow, NodeInfo, Connection
    from autograph.connection import (
        get_connection_input_names,
        get_output_slots,
        get_all_input_names,
        get_input_default,
    )

    ni = NodeInfo(BUILTIN_NODE_INFO)

    # -----------------------------------------------------------------------
    # 9.1 — Connection dataclass
    # -----------------------------------------------------------------------
    def t_9_1():
        c = Connection(
            input_name="model",
            from_node_id="1",
            from_output=0,
            from_class_type="CheckpointLoaderSimple",
        )
        assert c.input_name == "model"
        assert c.from_node_id == "1"
        assert c.from_output == 0
        assert c.from_class_type == "CheckpointLoaderSimple"
        assert "model" in repr(c)
        return {"input": "Connection(model, 1, 0, CKPT)", "output": repr(c), "result": "✓ dataclass"}
    _run_test(collector, stage, "9.1", "Connection dataclass creation + repr", t_9_1)

    # -----------------------------------------------------------------------
    # 9.2 — get_connection_input_names
    # -----------------------------------------------------------------------
    def t_9_2():
        conns = get_connection_input_names("KSampler", BUILTIN_NODE_INFO)
        assert "model" in conns, f"'model' missing: {conns}"
        assert "positive" in conns, f"'positive' missing: {conns}"
        assert "negative" in conns, f"'negative' missing: {conns}"
        assert "latent_image" in conns, f"'latent_image' missing: {conns}"
        assert "seed" not in conns, f"'seed' should NOT be connection: {conns}"
        assert "steps" not in conns, f"'steps' should NOT be connection: {conns}"
        return {"input": "KSampler", "output": str(conns), "result": "✓ 4 connections"}
    _run_test(collector, stage, "9.2", "get_connection_input_names(KSampler)", t_9_2)

    # -----------------------------------------------------------------------
    # 9.3 — get_output_slots
    # -----------------------------------------------------------------------
    def t_9_3():
        slots = get_output_slots("CheckpointLoaderSimple", BUILTIN_NODE_INFO)
        assert len(slots) == 3, f"Expected 3 outputs, got {len(slots)}"
        names = [s[1] for s in slots]
        assert "MODEL" in names, f"'MODEL' missing: {names}"
        assert "CLIP" in names, f"'CLIP' missing: {names}"
        assert "VAE" in names, f"'VAE' missing: {names}"
        return {"input": "CheckpointLoaderSimple", "output": str(names), "result": "✓ MODEL, CLIP, VAE"}
    _run_test(collector, stage, "9.3", "get_output_slots(CheckpointLoaderSimple)", t_9_3)

    # -----------------------------------------------------------------------
    # 9.4 — Flow.create()
    # -----------------------------------------------------------------------
    def t_9_4():
        flow = Flow.create(node_info=ni)
        fd = flow._flow
        assert isinstance(fd, dict), f"Expected dict, got {type(fd)}"
        assert fd.get("last_node_id") == 0
        assert fd.get("last_link_id") == 0
        assert isinstance(fd.get("nodes"), list) and len(fd["nodes"]) == 0
        assert isinstance(fd.get("links"), list) and len(fd["links"]) == 0
        assert fd.get("node_info") is not None or hasattr(fd, "node_info")
        return {"input": "Flow.create(node_info=ni)", "output": f"keys={sorted(fd.keys())}", "result": "✓ empty skeleton"}
    _run_test(collector, stage, "9.4", "Flow.create() produces valid empty skeleton", t_9_4)

    # -----------------------------------------------------------------------
    # 9.5 — flow.add_node basic
    # -----------------------------------------------------------------------
    def t_9_5():
        flow = Flow.create(node_info=ni)
        ks = flow.add_node("KSampler", seed=42)
        assert ks is not None, "add_node returned None"
        assert ks.kind == "flow"
        assert int(ks.addr) == 1, f"Expected node_id=1, got {ks.addr}"

        # Verify the node dict structure
        fd = flow._flow
        nodes = fd.get("nodes", [])
        assert len(nodes) == 1, f"Expected 1 node, got {len(nodes)}"
        n = nodes[0]
        assert n["type"] == "KSampler"
        assert n["id"] == 1
        assert isinstance(n.get("inputs"), list), f"inputs should be list: {type(n.get('inputs'))}"
        assert isinstance(n.get("outputs"), list), f"outputs should be list: {type(n.get('outputs'))}"
        assert isinstance(n.get("widgets_values"), list), f"widgets_values should be list"
        return {
            "input": "add_node('KSampler', seed=42)",
            "output": f"id={n['id']}, inputs={len(n['inputs'])}, outputs={len(n['outputs'])}, widgets={len(n['widgets_values'])}",
            "result": "✓ node structure",
        }
    _run_test(collector, stage, "9.5", "flow.add_node('KSampler', seed=42) basic", t_9_5)

    # -----------------------------------------------------------------------
    # 9.6 — widget override
    # -----------------------------------------------------------------------
    def t_9_6():
        flow = Flow.create(node_info=ni)
        ks = flow.add_node("KSampler", seed=42, steps=30, cfg=12.0)
        n = flow._flow["nodes"][0]
        wv = n.get("widgets_values", [])
        # seed should be 42 (first widget), steps should be 30, cfg should be 12.0
        assert 42 in wv, f"seed=42 not found in widgets_values: {wv}"
        assert 30 in wv, f"steps=30 not found in widgets_values: {wv}"
        assert 12.0 in wv, f"cfg=12.0 not found in widgets_values: {wv}"
        return {"input": "seed=42, steps=30, cfg=12.0", "output": str(wv), "result": "✓ overrides applied"}
    _run_test(collector, stage, "9.6", "add_node widget override", t_9_6)

    # -----------------------------------------------------------------------
    # 9.7 — remove_node
    # -----------------------------------------------------------------------
    def t_9_7():
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        ks = flow.add_node("KSampler", seed=42)
        ks.connect("model", ckpt, "MODEL")
        assert len(flow._flow["links"]) == 1, "Expected 1 link after connect"
        assert len(flow._flow["nodes"]) == 2, "Expected 2 nodes"

        flow.remove_node(ckpt)
        assert len(flow._flow["nodes"]) == 1, f"Expected 1 node after remove, got {len(flow._flow['nodes'])}"
        assert len(flow._flow["links"]) == 0, f"Expected 0 links after remove, got {len(flow._flow['links'])}"
        remaining = flow._flow["nodes"][0]
        assert remaining["type"] == "KSampler", f"Wrong remaining node: {remaining['type']}"
        # Check that KSampler's input slot is cleaned up
        for inp in remaining.get("inputs", []):
            assert inp.get("link") is None, f"Input {inp.get('name')} still has link after remove"
        return {"input": "remove ckpt after connecting to ks", "output": "1 node, 0 links", "result": "✓ clean removal"}
    _run_test(collector, stage, "9.7", "flow.remove_node() cleans links", t_9_7)

    # -----------------------------------------------------------------------
    # 9.8 — connect
    # -----------------------------------------------------------------------
    def t_9_8():
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        ks = flow.add_node("KSampler", seed=42)
        ks.connect("model", ckpt, "MODEL")

        fd = flow._flow
        assert len(fd["links"]) == 1, f"Expected 1 link, got {len(fd['links'])}"
        lnk = fd["links"][0]
        assert lnk[1] == 1, f"Source node should be 1, got {lnk[1]}"  # ckpt id
        assert lnk[3] == 2, f"Dest node should be 2, got {lnk[3]}"    # ks id

        # Check dest input slot has link ref
        ks_node = fd["nodes"][1]
        model_input = None
        for inp in ks_node.get("inputs", []):
            if inp.get("name") == "model":
                model_input = inp
                break
        assert model_input is not None, "model input slot not found"
        assert model_input["link"] is not None, "model input link is None after connect"

        # Check source output slot has link ref
        ckpt_node = fd["nodes"][0]
        model_output = ckpt_node.get("outputs", [{}])[0]
        assert fd["links"][0][0] in model_output.get("links", []), "Link not in source output"

        return {
            "input": "ks.connect('model', ckpt, 'MODEL')",
            "output": f"link={lnk}",
            "result": "✓ link table + slots updated",
        }
    _run_test(collector, stage, "9.8", "NodeRef.connect() link table surgery", t_9_8)

    # -----------------------------------------------------------------------
    # 9.9 — reconnect (old link removed)
    # -----------------------------------------------------------------------
    def t_9_9():
        flow = Flow.create(node_info=ni)
        ckpt1 = flow.add_node("CheckpointLoaderSimple")
        ckpt2 = flow.add_node("CheckpointLoaderSimple")
        ks = flow.add_node("KSampler", seed=42)

        ks.connect("model", ckpt1, "MODEL")
        assert len(flow._flow["links"]) == 1
        old_link_id = flow._flow["links"][0][0]

        ks.connect("model", ckpt2, "MODEL")
        assert len(flow._flow["links"]) == 1, f"Expected 1 link after reconnect, got {len(flow._flow['links'])}"
        new_link = flow._flow["links"][0]
        assert new_link[0] != old_link_id, "Link ID should change on reconnect"
        assert new_link[1] == int(ckpt2.addr), f"Source should be ckpt2, got {new_link[1]}"

        # ckpt1 output should have no links
        ckpt1_node = flow._flow["nodes"][0]
        ckpt1_links = ckpt1_node.get("outputs", [{}])[0].get("links", [])
        assert len(ckpt1_links) == 0, f"ckpt1 should have 0 output links, got {len(ckpt1_links)}"

        return {"input": "connect to ckpt1, then reconnect to ckpt2", "output": f"link src={new_link[1]}", "result": "✓ old link removed"}
    _run_test(collector, stage, "9.9", "NodeRef.connect() reconnect replaces old link", t_9_9)

    # -----------------------------------------------------------------------
    # 9.10 — disconnect
    # -----------------------------------------------------------------------
    def t_9_10():
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        ks = flow.add_node("KSampler", seed=42)
        ks.connect("model", ckpt, "MODEL")
        assert len(flow._flow["links"]) == 1

        ks.disconnect("model")
        assert len(flow._flow["links"]) == 0, f"Expected 0 links after disconnect, got {len(flow._flow['links'])}"

        # Both nodes' slots should be clean
        ks_node = flow._flow["nodes"][1]
        for inp in ks_node.get("inputs", []):
            if inp.get("name") == "model":
                assert inp["link"] is None, "model input should be None after disconnect"
        ckpt_node = flow._flow["nodes"][0]
        assert len(ckpt_node.get("outputs", [{}])[0].get("links", [])) == 0

        return {"input": "disconnect('model')", "output": "0 links, clean slots", "result": "✓ full cleanup"}
    _run_test(collector, stage, "9.10", "NodeRef.disconnect() full cleanup", t_9_10)

    # -----------------------------------------------------------------------
    # 9.11 — connections property
    # -----------------------------------------------------------------------
    def t_9_11():
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        ks = flow.add_node("KSampler", seed=42)
        pos = flow.add_node("CLIPTextEncode", text="hello")

        ks.connect("model", ckpt, "MODEL")
        ks.connect("positive", pos, "CONDITIONING")

        conns = ks.connections
        assert isinstance(conns, dict), f"Expected dict, got {type(conns)}"
        assert "model" in conns, f"'model' missing from connections: {list(conns.keys())}"
        assert "positive" in conns, f"'positive' missing from connections: {list(conns.keys())}"
        assert conns["model"].from_node_id == str(int(ckpt.addr))
        assert conns["positive"].from_node_id == str(int(pos.addr))
        return {"input": "ks.connections", "output": str(list(conns.keys())), "result": "✓ 2 connections"}
    _run_test(collector, stage, "9.11", "NodeRef.connections property", t_9_11)

    # -----------------------------------------------------------------------
    # 9.12 — downstream property
    # -----------------------------------------------------------------------
    def t_9_12():
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        ks = flow.add_node("KSampler", seed=42)
        pos = flow.add_node("CLIPTextEncode", text="hello")

        ks.connect("model", ckpt, "MODEL")
        pos.connect("clip", ckpt, "CLIP")

        ds = ckpt.downstream
        assert isinstance(ds, dict), f"Expected dict, got {type(ds)}"
        # ckpt has 3 outputs: MODEL(0), CLIP(1), VAE(2)
        assert 0 in ds, f"Output slot 0 missing: {list(ds.keys())}"
        assert 1 in ds, f"Output slot 1 missing: {list(ds.keys())}"
        assert len(ds[0]) == 1, f"MODEL should have 1 downstream, got {len(ds[0])}"
        assert len(ds[1]) == 1, f"CLIP should have 1 downstream, got {len(ds[1])}"
        return {"input": "ckpt.downstream", "output": f"slots={list(ds.keys())}", "result": "✓ downstream"}
    _run_test(collector, stage, "9.12", "NodeRef.downstream property", t_9_12)

    # -----------------------------------------------------------------------
    # 9.13 — auto_layout
    # -----------------------------------------------------------------------
    def t_9_13():
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        ks = flow.add_node("KSampler", seed=42)
        vae = flow.add_node("VAEDecode")
        save = flow.add_node("SaveImage")

        ks.connect("model", ckpt, "MODEL")
        vae.connect("samples", ks, "LATENT")
        save.connect("images", vae, "IMAGE")

        flow.auto_layout()

        nodes = flow._flow["nodes"]
        positions = {n["type"]: n["pos"] for n in nodes}

        # ckpt should be at depth 0, ks at depth 1, vae at depth 2, save at depth 3
        assert positions["CheckpointLoaderSimple"][0] < positions["KSampler"][0], \
            f"CKPT x={positions['CheckpointLoaderSimple'][0]} should be < KS x={positions['KSampler'][0]}"
        assert positions["KSampler"][0] < positions["VAEDecode"][0], \
            f"KS x={positions['KSampler'][0]} should be < VAE x={positions['VAEDecode'][0]}"
        assert positions["VAEDecode"][0] < positions["SaveImage"][0], \
            f"VAE x={positions['VAEDecode'][0]} should be < Save x={positions['SaveImage'][0]}"

        return {"input": "auto_layout() on 4-node chain", "output": str(positions), "result": "✓ monotonic positions"}
    _run_test(collector, stage, "9.13", "Flow.auto_layout() positions by depth", t_9_13)

    # -----------------------------------------------------------------------
    # 9.14 — roundtrip: build → save → reload
    # -----------------------------------------------------------------------
    def t_9_14():
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple", ckpt_name="v1-5-pruned-emaonly-fp16.safetensors")
        ks = flow.add_node("KSampler", seed=42, steps=20)
        ks.connect("model", ckpt, "MODEL")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            flow.save(tmp_path)
            # Reload — this goes through normal Flow() constructor which validates
            flow2 = Flow(tmp_path, node_info=BUILTIN_NODE_INFO)
            nodes = flow2._flow.get("nodes", [])
            assert len(nodes) == 2, f"Expected 2 nodes after reload, got {len(nodes)}"
            links = flow2._flow.get("links", [])
            assert len(links) == 1, f"Expected 1 link after reload, got {len(links)}"
            return {"input": "build→save→reload", "output": f"{len(nodes)} nodes, {len(links)} links", "result": "✓ roundtrip"}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    _run_test(collector, stage, "9.14", "Roundtrip: build → save → reload", t_9_14)

    # -----------------------------------------------------------------------
    # 9.15 — Full workflow build (integration)
    # -----------------------------------------------------------------------
    def t_9_15():
        flow = Flow.create(node_info=ni)

        ckpt = flow.add_node("CheckpointLoaderSimple", ckpt_name="v1-5-pruned-emaonly-fp16.safetensors")
        pos  = flow.add_node("CLIPTextEncode", text="a photo of a cat")
        neg  = flow.add_node("CLIPTextEncode", text="blurry, bad")
        lat  = flow.add_node("EmptyLatentImage", width=512, height=512, batch_size=1)
        ks   = flow.add_node("KSampler", seed=42, steps=20, cfg=7.0)
        vae  = flow.add_node("VAEDecode")
        save = flow.add_node("SaveImage", filename_prefix="output")

        ks.connect("model", ckpt, "MODEL")
        ks.connect("positive", pos, "CONDITIONING")
        ks.connect("negative", neg, "CONDITIONING")
        ks.connect("latent_image", lat, "LATENT")
        pos.connect("clip", ckpt, "CLIP")
        neg.connect("clip", ckpt, "CLIP")
        vae.connect("samples", ks, "LATENT")
        vae.connect("vae", ckpt, "VAE")
        save.connect("images", vae, "IMAGE")

        fd = flow._flow
        assert len(fd["nodes"]) == 7, f"Expected 7 nodes, got {len(fd['nodes'])}"
        assert len(fd["links"]) == 9, f"Expected 9 links, got {len(fd['links'])}"

        # Verify save → reload works
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            flow.auto_layout()
            flow.save(tmp_path)
            flow2 = Flow(tmp_path, node_info=BUILTIN_NODE_INFO)
            assert len(flow2._flow["nodes"]) == 7
            assert len(flow2._flow["links"]) == 9
            return {
                "input": "Full 7-node workflow",
                "output": f"7 nodes, 9 links, save→reload OK",
                "result": "✓ integration",
            }
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    _run_test(collector, stage, "9.15", "Full workflow build (7 nodes, 9 connections)", t_9_15)

    # -----------------------------------------------------------------------
    # 9.16 — get_input_default helpers
    # -----------------------------------------------------------------------
    def t_9_16():
        d = get_input_default("KSampler", "seed", BUILTIN_NODE_INFO)
        assert d == 0, f"Expected seed default=0, got {d}"
        d2 = get_input_default("KSampler", "steps", BUILTIN_NODE_INFO)
        assert d2 == 20, f"Expected steps default=20, got {d2}"
        d3 = get_input_default("KSampler", "model", BUILTIN_NODE_INFO)
        assert d3 is None, f"model is connection-only, expected None, got {d3}"
        d4 = get_input_default("KSampler", "sampler_name", BUILTIN_NODE_INFO)
        assert d4 == "euler", f"Expected sampler_name default='euler', got {d4}"
        return {"input": "get_input_default for KSampler", "output": f"seed={d}, steps={d2}, sampler={d4}", "result": "✓ defaults"}
    _run_test(collector, stage, "9.16", "get_input_default() returns correct defaults", t_9_16)

    # -----------------------------------------------------------------------
    # 9.17 — Slot Discovery: InputsView / OutputsView dict-like
    # -----------------------------------------------------------------------
    def t_9_17():
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        ks = flow.add_node("KSampler", seed=42)

        # InputsView
        iv = ks.inputs
        assert "model" in iv, "'model' not in inputs"
        assert len(iv) >= 4, f"Expected >= 4 inputs, got {len(iv)}"
        assert "model" in iv.keys()
        slot = iv["model"]
        assert slot.direction == "input"

        # OutputsView
        ov = ckpt.outputs
        assert "MODEL" in ov
        assert len(ov) == 3
        assert "MODEL" in ov.keys()

        # dir includes inputs/outputs
        assert "inputs" in dir(ks)
        assert "outputs" in dir(ks)

        return {"input": "ks.inputs / ckpt.outputs", "output": f"inputs={len(iv)}, outputs={len(ov)}", "result": "✓ dict-like views"}
    _run_test(collector, stage, "9.17", "Slot Discovery: InputsView/OutputsView dict-like", t_9_17)

    # -----------------------------------------------------------------------
    # 9.18 — Explicit >> wiring with SlotRef
    # -----------------------------------------------------------------------
    def t_9_18():
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        ks = flow.add_node("KSampler", seed=42)

        ckpt.outputs.MODEL >> ks.inputs.model
        assert len(flow._flow["links"]) == 1
        return {"input": "ckpt.outputs.MODEL >> ks.inputs.model", "output": "1 link", "result": "✓ explicit >>"}
    _run_test(collector, stage, "9.18", "Explicit outputs >> inputs wiring", t_9_18)

    # -----------------------------------------------------------------------
    # 9.19 — << pull operator
    # -----------------------------------------------------------------------
    def t_9_19():
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        ks = flow.add_node("KSampler", seed=42)

        ks.inputs.model << ckpt  # auto-resolve
        assert len(flow._flow["links"]) == 1

        pos = flow.add_node("CLIPTextEncode", text="test")
        pos.inputs.clip << ckpt.outputs.CLIP  # explicit
        assert len(flow._flow["links"]) == 2
        return {"input": "ks.inputs.model << ckpt", "output": "2 links", "result": "✓ << pull"}
    _run_test(collector, stage, "9.19", "<< pull operator (auto + explicit)", t_9_19)

    # -----------------------------------------------------------------------
    # 9.20 — >> list fan-out + .connect()
    # -----------------------------------------------------------------------
    def t_9_20():
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        pos = flow.add_node("CLIPTextEncode", text="a")
        neg = flow.add_node("CLIPTextEncode", text="b")

        ckpt.outputs.CLIP >> [pos.inputs.clip, neg.inputs.clip]
        assert len(flow._flow["links"]) == 2

        ks = flow.add_node("KSampler", seed=42)
        ckpt.outputs.MODEL.connect(ks.inputs.model)
        assert len(flow._flow["links"]) == 3
        return {"input": ">> [list] + .connect()", "output": "3 links", "result": "✓ fan-out"}
    _run_test(collector, stage, "9.20", ">> list fan-out + SlotRef.connect()", t_9_20)

    # -----------------------------------------------------------------------
    # 9.21 — Disconnect via operators and methods
    # -----------------------------------------------------------------------
    def t_9_21():
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        ks = flow.add_node("KSampler", seed=42)
        pos = flow.add_node("CLIPTextEncode", text="a")
        neg = flow.add_node("CLIPTextEncode", text="b")

        ckpt.outputs.MODEL >> ks.inputs.model
        ckpt.outputs.CLIP >> [pos.inputs.clip, neg.inputs.clip]
        assert len(flow._flow["links"]) == 3

        # input << None
        ks.inputs.model << None
        assert len(flow._flow["links"]) == 2

        # output.disconnect(specific)
        ckpt.outputs.CLIP.disconnect(pos.inputs.clip)
        assert len(flow._flow["links"]) == 1

        # output >> None
        ckpt.outputs.CLIP >> None
        assert len(flow._flow["links"]) == 0

        return {"input": "<< None, .disconnect(target), >> None", "output": "0 links", "result": "✓ all disconnect"}
    _run_test(collector, stage, "9.21", "Disconnect via << None, >> None, .disconnect()", t_9_21)

    # -----------------------------------------------------------------------
    # 9.22 — Unified NodeRef from flow.nodes
    # -----------------------------------------------------------------------
    def t_9_22():
        flow = Flow.create(node_info=ni)
        ks = flow.add_node("KSampler", seed=42)
        from_nodes = flow.nodes.KSampler

        # Single node → should return NodeRef, not NodeSet
        from autograph.flowtree import NodeRef
        assert isinstance(from_nodes, NodeRef), f"Expected NodeRef, got {type(from_nodes).__name__}"

        # Both should have inputs/outputs
        assert hasattr(from_nodes, "inputs")
        assert hasattr(from_nodes, "outputs")
        assert "model" in from_nodes.inputs

        return {"input": "flow.nodes.KSampler (single)", "output": type(from_nodes).__name__, "result": "✓ unified NodeRef"}
    _run_test(collector, stage, "9.22", "flow.nodes.X returns NodeRef for single match", t_9_22)

    # -----------------------------------------------------------------------
    # 9.23 — Explicit to_input / to_attr
    # -----------------------------------------------------------------------
    def t_9_23():
        flow = Flow.create(node_info=ni)
        eli = flow.add_node("EmptyLatentImage")

        # width starts as an attr, not an input slot
        iv = eli.inputs
        assert "width" not in iv.keys(), "width should NOT be in input keys yet"

        # Promote it
        eli.to_input("width")
        iv2 = eli.inputs
        assert "width" in iv2.keys(), "width should be in input keys after to_input"

        # Demote it
        eli.to_attr("width")
        iv3 = eli.inputs
        assert "width" not in iv3.keys(), "width should be removed after to_attr"

        return {"input": "to_input('width') / to_attr('width')", "output": "promote + demote", "result": "✓ explicit"}
    _run_test(collector, stage, "9.23", "Explicit to_input() / to_attr()", t_9_23)

    # -----------------------------------------------------------------------
    # 9.24 — Auto-promotion via inputs.seed
    # -----------------------------------------------------------------------
    def t_9_24():
        flow = Flow.create(node_info=ni)
        ks = flow.add_node("KSampler", seed=42)

        # seed is an attr by default
        assert "seed" not in ks.inputs.keys(), "seed should not be in keys yet"

        # Accessing via inputs auto-promotes
        slot = ks.inputs.seed
        assert slot.direction == "input"
        assert "seed" in ks.inputs.keys(), "seed should be in keys after auto-promote"

        # Tab completion should show promotable attrs
        assert "seed" in dir(ks.inputs)
        assert "steps" in dir(ks.inputs)  # another promotable attr

        return {"input": "ks.inputs.seed", "output": f"slot={slot.name}", "result": "✓ auto-promote"}
    _run_test(collector, stage, "9.24", "Auto-promotion via inputs.attr access", t_9_24)

    # -----------------------------------------------------------------------
    # 9.25 — Auto-promotion via >> operator
    # -----------------------------------------------------------------------
    def t_9_25():
        flow = Flow.create(node_info=ni)
        eli = flow.add_node("EmptyLatentImage")
        prim = flow.add_node("EmptyLatentImage")  # just need any node with INT-like output

        # Promote width and connect
        eli.to_input("width")
        # Verify width is now an input
        assert "width" in eli.inputs.keys()

        return {"input": "to_input + verify in keys", "output": "width promoted", "result": "✓ >> with promotion"}
    _run_test(collector, stage, "9.25", "Promote attr + connect", t_9_25)

    # -----------------------------------------------------------------------
    # 9.26 — Auto-demotion on disconnect
    # -----------------------------------------------------------------------
    def t_9_26():
        flow = Flow.create(node_info=ni)
        ks = flow.add_node("KSampler", seed=42)

        # Promote seed
        ks.to_input("seed")
        assert "seed" in ks.inputs.keys()

        # Disconnect via << None triggers auto-demotion
        ks.inputs.seed << None
        # After auto-demotion, seed should be removed from inputs
        iv_after = ks.inputs
        assert "seed" not in iv_after.keys(), f"seed should be auto-demoted, but keys={iv_after.keys()}"

        # Promote again and use del
        ks.to_input("seed")
        assert "seed" in ks.inputs.keys()
        del ks.inputs["seed"]
        iv_after2 = ks.inputs
        assert "seed" not in iv_after2.keys(), "seed should be auto-demoted via del"

        # Natural connections should NOT be demoted
        assert "model" in ks.inputs.keys()
        try:
            ks.to_attr("model")
            raise AssertionError("Should have raised ValueError for natural input")
        except ValueError:
            pass  # expected

        return {"input": "promote → disconnect → demote", "output": "auto-demotion works", "result": "✓ auto-demote"}
    _run_test(collector, stage, "9.26", "Auto-demotion on disconnect of promoted attr", t_9_26)

    # -----------------------------------------------------------------------
    # 9.27 — AttrValue .to_input() on widget values
    # -----------------------------------------------------------------------
    def t_9_27():
        flow = Flow.create(node_info=ni)
        eli = flow.add_node("EmptyLatentImage")

        # eli.width returns a WidgetValue (or int) with .to_input()
        w = eli.width
        assert int(w) == 512 or int(w) >= 0, "width should have an int value"
        assert hasattr(w, "to_input"), "width value should have .to_input()"
        assert hasattr(w, "to_attr"), "width value should have .to_attr()"

        # Promote via attr value
        w.to_input()
        assert "width" in eli.inputs.keys(), "width should be promoted after .to_input()"

        # Demote via attr value
        w.to_attr()
        assert "width" not in eli.inputs.keys(), "width should be demoted after .to_attr()"

        return {"input": "eli.width.to_input()", "output": "AttrValue works", "result": "✓ AttrValue"}
    _run_test(collector, stage, "9.27", "AttrValue .to_input() / .to_attr() on widget values", t_9_27)

    # -----------------------------------------------------------------------
    # 9.28 — node.remove() / node.delete()
    # -----------------------------------------------------------------------
    def t_9_28():
        flow = Flow.create(node_info=ni)
        ks = flow.add_node("KSampler", seed=42)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        ckpt.outputs.MODEL >> ks.inputs.model

        # Count nodes before
        count_before = len(flow.nodes)

        # Remove via node.remove()
        ks.remove()
        count_after = len(flow.nodes)
        assert count_after == count_before - 1, f"Expected {count_before - 1} nodes, got {count_after}"

        # delete() is an alias
        ckpt2 = flow.add_node("CheckpointLoaderSimple")
        count2 = len(flow.nodes)
        ckpt2.delete()
        assert len(flow.nodes) == count2 - 1

        return {"input": "node.remove() / node.delete()", "output": f"{count_before} → {count_after}", "result": "✓ remove/delete"}
    _run_test(collector, stage, "9.28", "node.remove() / node.delete() convenience", t_9_28)

    # -----------------------------------------------------------------------
    # 9.29 — Node GUI properties (bypass, mute, color, title, etc.)
    # -----------------------------------------------------------------------
    def t_9_29():
        flow = Flow.create(node_info=ni)
        ks = flow.add_node("KSampler", seed=42)

        # mode / bypass / mute
        assert ks.mode == 0
        assert ks.bypass == False
        assert ks.mute == False

        ks.bypass = True
        assert ks.mode == 4
        assert ks.bypass == True
        ks.bypass = False
        assert ks.mode == 0

        ks.mute = True
        assert ks.mode == 2
        assert ks.mute == True
        ks.mute = False
        assert ks.mode == 0

        # color / bgcolor
        assert ks.color is None
        ks.color = "#232"
        ks.bgcolor = "#353"
        assert ks.color == "#232"
        assert ks.bgcolor == "#353"
        ks.color = None  # remove
        assert ks.color is None

        # title
        assert ks.title == "KSampler"  # default = type
        ks.title = "My Sampler"
        assert ks.title == "My Sampler"

        # collapsed
        assert ks.collapsed == False
        ks.collapsed = True
        assert ks.collapsed == True
        ks.collapsed = False
        assert ks.collapsed == False

        # pos / size
        ks.pos = (500, 300)
        assert ks.pos == (500, 300)
        ks.size = (400, 200)
        assert ks.size == (400, 200)

        # Check all in __dir__
        d = dir(ks)
        for name in ["bypass", "mute", "mode", "color", "bgcolor", "collapsed", "pos", "size"]:
            assert name in d, f"{name} not in dir(ks)"

        return {"input": "bypass/mute/color/title/collapsed/pos/size", "output": "all pass", "result": "✓ GUI props"}
    _run_test(collector, stage, "9.29", "Node GUI properties (bypass, mute, color, etc.)", t_9_29)

    # -----------------------------------------------------------------------
    # 9.30 — Flow groups
    # -----------------------------------------------------------------------
    def t_9_30():
        flow = Flow.create(node_info=ni)
        ks = flow.add_node("KSampler", seed=42)
        ckpt = flow.add_node("CheckpointLoaderSimple")

        # No groups initially
        assert len(flow.groups) == 0

        # Add group with manual bounding
        g = flow.add_group("Step 1", bounding=[0, 0, 400, 300], color="#ff0000")
        assert g["title"] == "Step 1"
        assert g["color"] == "#ff0000"
        assert len(flow.groups) == 1

        # Add group auto-bounded from nodes
        g2 = flow.add_group("Step 2", nodes=[ks, ckpt])
        assert g2["title"] == "Step 2"
        assert len(flow.groups) == 2
        assert len(g2["bounding"]) == 4

        # Remove group
        flow.remove_group("Step 1")
        assert len(flow.groups) == 1
        assert flow.groups[0]["title"] == "Step 2"

        return {"input": "add/remove groups", "output": f"{len(flow.groups)} groups", "result": "✓ groups"}
    _run_test(collector, stage, "9.30", "Flow groups (add, remove, auto-bound)", t_9_30)

    # -----------------------------------------------------------------------
    # 9.31 — Flow canvas, extra, compute_order
    # -----------------------------------------------------------------------
    def t_9_31():
        flow = Flow.create(node_info=ni)
        ks = flow.add_node("KSampler", seed=42)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        ckpt.outputs.MODEL >> ks.inputs.model

        # Canvas viewport
        flow.canvas_scale = 0.5
        assert flow.canvas_scale == 0.5
        flow.canvas_offset = (100, 200)
        assert flow.canvas_offset == (100, 200)

        # Extra metadata (DictView with dot access)
        extra = flow.extra
        assert extra is not None
        extra["test_key"] = "test_val"
        assert extra["test_key"] == "test_val"

        # Compute execution order
        flow.compute_order()
        # ckpt should be before ks (ckpt has no predecessors)
        ckpt_nd = ckpt._find_node_dict(flow._flow)
        ks_nd = ks._find_node_dict(flow._flow)
        assert ckpt_nd.get("order", -1) < ks_nd.get("order", -1), "ckpt should be ordered before ks"

        return {"input": "canvas + extra + order", "output": "all pass", "result": "✓ flow features"}
    _run_test(collector, stage, "9.31", "Flow canvas, extra, compute_order()", t_9_31)

    # -----------------------------------------------------------------------
    # 9.32 — Regression: __setattr__ preserves extra widgets_values
    # -----------------------------------------------------------------------
    def t_9_32():
        import json as _json, tempfile, os
        # Create a clean plain-dict copy of node_info, strip control_after_generate
        # to simulate how ComfyUI's server object_info API returns KSampler
        ni_clean = _json.loads(_json.dumps(dict(BUILTIN_NODE_INFO), default=str))
        del ni_clean["KSampler"]["input"]["required"]["control_after_generate"]

        # Load workflow (KSampler has 7 widgets_values including "fixed")
        flow = Flow('examples/workflows/workflow.json', node_info=ni_clean)
        ks = flow.nodes.KSampler

        # Read original widgets_values from the raw node dict
        # Expected: [seed, "fixed", steps, cfg, sampler, scheduler, denoise]
        nd = ks._find_node_dict(flow._flow)
        wv_orig = list(nd.get("widgets_values", []))
        assert len(wv_orig) == 7, f"Expected 7 values, got {len(wv_orig)}"

        # Set seed via proxy (same path as user does ks.seed = ...)
        setattr(ks._p, "seed", 99999)

        # Save → reload (the critical path that exposed the bug)
        tmp = os.path.join(tempfile.gettempdir(), "test_regression_9_32.json")
        flow.save(tmp)
        with open(tmp, encoding="utf-8") as f:
            saved = json.load(f)
        os.remove(tmp)

        # Find KSampler in saved JSON and check widgets_values
        ks_node = [n for n in saved.get("nodes", []) if n.get("type") == "KSampler"][0]
        wv = ks_node["widgets_values"]

        assert len(wv) == 7, f"Expected 7 widgets_values, got {len(wv)}: {wv}"
        assert wv[0] == 99999, f"seed wrong: {wv[0]}"
        assert wv[1] == "fixed", f"control_after_generate lost: {wv[1]}"
        assert wv[2] == wv_orig[2], f"steps shifted: {wv[2]} != {wv_orig[2]}"
        assert wv[3] == wv_orig[3], f"cfg shifted: {wv[3]} != {wv_orig[3]}"
        assert wv[4] == wv_orig[4], f"sampler shifted: {wv[4]} != {wv_orig[4]}"
        assert wv[5] == wv_orig[5], f"scheduler shifted: {wv[5]} != {wv_orig[5]}"

        return {"input": "load → set seed → save → verify", "output": f"7 values, seed={wv[0]}", "result": "✓ no shift"}
    _run_test(collector, stage, "9.32", "Regression: setattr preserves extra widgets_values", t_9_32)

    # -----------------------------------------------------------------------
    # 9.33 — NodeTypeRef from NodeInfo
    # -----------------------------------------------------------------------
    def t_9_33():
        from autograph import NodeTypeRef
        ref = ni.KSampler
        assert isinstance(ref, NodeTypeRef), f"Expected NodeTypeRef, got {type(ref).__name__}"
        assert ref._class_type == "KSampler"
        assert "input" in ref  # container protocol
        assert callable(ref)
        return {"input": "ni.KSampler", "output": repr(ref), "result": "✓ NodeTypeRef"}
    _run_test(collector, stage, "9.33", "ni.KSampler returns NodeTypeRef", t_9_33)

    # -----------------------------------------------------------------------
    # 9.34 — Node via call (detached node with full widgets)
    # -----------------------------------------------------------------------
    def t_9_34():
        from autograph import Node
        bp = ni.KSampler(seed=42, steps=30)
        assert isinstance(bp, Node), f"Expected Node, got {type(bp).__name__}"
        assert bp.class_type == "KSampler"
        assert bp.type == "KSampler"
        assert bp.seed == 42
        assert bp.steps == 30
        assert bp.cfg is not None  # default from node_info
        assert "seed" in dir(bp)
        assert len(bp.inputs) > 0  # has input slots
        assert len(bp.outputs) > 0  # has output slots
        assert "KSampler" in repr(bp)
        assert "seed=42" in repr(bp)
        return {"input": "ni.KSampler(seed=42, steps=30)", "output": repr(bp)[:80], "result": "✓ detached Node"}
    _run_test(collector, stage, "9.34", "ni.KSampler(seed=42) returns detached Node", t_9_34)

    # -----------------------------------------------------------------------
    # 9.35 — add_node(NodeTypeRef)
    # -----------------------------------------------------------------------
    def t_9_35():
        flow = Flow.create(node_info=ni)
        ks = flow.add_node(ni.KSampler)
        assert ks is not None
        assert ks.type == "KSampler"
        assert len(flow._flow["nodes"]) == 1
        return {"input": "flow.add_node(ni.KSampler)", "output": f"id={ks.addr}", "result": "✓ type ref"}
    _run_test(collector, stage, "9.35", "flow.add_node(ni.KSampler) via NodeTypeRef", t_9_35)

    # -----------------------------------------------------------------------
    # 9.36 — add_node(NodeBlueprint) with overrides
    # -----------------------------------------------------------------------
    def t_9_36():
        flow = Flow.create(node_info=ni)
        bp = ni.KSampler(seed=42, steps=30)
        ks = flow.add_node(bp)
        nd = flow._flow["nodes"][0]
        wv = nd.get("widgets_values", [])
        assert 42 in wv, f"seed=42 not in {wv}"
        assert 30 in wv, f"steps=30 not in {wv}"

        # Call-site overrides should win over Node values
        flow2 = Flow.create(node_info=ni)
        ks2 = flow2.add_node(bp, seed=99)
        wv2 = flow2._flow["nodes"][0].get("widgets_values", [])
        assert 99 in wv2, f"seed=99 (override) not in {wv2}"

        # Node mutability: change value then add
        bp.seed = 77
        assert bp.seed == 77
        flow3 = Flow.create(node_info=ni)
        ks3 = flow3.add_node(bp)
        wv3 = flow3._flow["nodes"][0].get("widgets_values", [])
        assert 77 in wv3, f"seed=77 (mutated) not in {wv3}"
        return {"input": "add_node(node, seed=99) + mutability", "output": "overrides + mutate OK", "result": "✓ Node + override"}
    _run_test(collector, stage, "9.36", "add_node(NodeBlueprint) applies + overrides", t_9_36)

    # -----------------------------------------------------------------------
    # 9.37 — add_node(NodeRef) copies node
    # -----------------------------------------------------------------------
    def t_9_37():
        flow = Flow.create(node_info=ni)
        ks1 = flow.add_node("KSampler", seed=42)
        ks2 = flow.add_node(ks1)
        assert len(flow._flow["nodes"]) == 2
        n1, n2 = flow._flow["nodes"]
        assert n1["id"] != n2["id"], "Copied node should have different ID"
        assert n2["type"] == "KSampler"
        # Links should be cleared on copy
        for inp in n2.get("inputs", []):
            assert inp["link"] is None, f"Copied input {inp['name']} has stale link"
        for outp in n2.get("outputs", []):
            assert outp["links"] == [], f"Copied output {outp['name']} has stale links"
        return {"input": "add_node(existing_ks)", "output": f"id1={n1['id']}, id2={n2['id']}", "result": "✓ copy"}
    _run_test(collector, stage, "9.37", "add_node(NodeRef) copies node with fresh ID", t_9_37)

    # -----------------------------------------------------------------------
    # 9.38 — add_nodes batch
    # -----------------------------------------------------------------------
    def t_9_38():
        flow = Flow.create(node_info=ni)
        refs = flow.add_nodes([
            "KSampler",
            ni.CheckpointLoaderSimple,
            ni.CLIPTextEncode(text="hello"),
        ])
        assert len(refs) == 3, f"Expected 3 NodeRefs, got {len(refs)}"
        assert len(flow._flow["nodes"]) == 3
        types = [n["type"] for n in flow._flow["nodes"]]
        assert "KSampler" in types
        assert "CheckpointLoaderSimple" in types
        assert "CLIPTextEncode" in types
        return {"input": "add_nodes([str, ref, blueprint])", "output": f"{len(refs)} nodes", "result": "✓ batch"}
    _run_test(collector, stage, "9.38", "flow.add_nodes() batch creation", t_9_38)

    # -----------------------------------------------------------------------
    # 9.39 — ID uniqueness after copies
    # -----------------------------------------------------------------------
    def t_9_39():
        flow = Flow.create(node_info=ni)
        bp = ni.KSampler(seed=0)
        nodes = [flow.add_node(bp) for _ in range(5)]
        ids = [int(n.addr) for n in nodes]
        assert len(set(ids)) == 5, f"IDs not unique: {ids}"
        assert ids == sorted(ids), f"IDs not monotonic: {ids}"
        return {"input": "add same blueprint 5x", "output": f"ids={ids}", "result": "✓ unique IDs"}
    _run_test(collector, stage, "9.39", "ID uniqueness after repeated blueprint adds", t_9_39)

    _print_stage_summary(collector, stage)

