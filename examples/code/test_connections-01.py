"""Test all builder v2 features with REAL node_info.

Usage:
    python test_v2.py                                  # uses env vars
    python test_v2.py --node-info path/to/node-info.json
    python test_v2.py --server-url http://localhost:8188

Env var hierarchy:
    1. AUTOFLOW_NODE_INFO_SOURCE  (file path)
    2. AUTOFLOW_COMFYUI_SERVER_URL (server fetch)
    3. CLI args override env vars
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from autoflow import Flow, NodeInfo
from autoflow.connection import (
    get_input_default,
    get_connection_input_names,
    _is_connection_only_input,
)


def load_node_info(args):
    """Load node_info from args, env vars, or error."""
    # CLI args take priority
    if args.node_info:
        return NodeInfo(args.node_info)
    if args.server_url:
        return NodeInfo("fetch", server_url=args.server_url)

    # Env vars
    src = os.environ.get("AUTOFLOW_NODE_INFO_SOURCE")
    if src:
        return NodeInfo(src)
    url = os.environ.get("AUTOFLOW_COMFYUI_SERVER_URL")
    if url:
        return NodeInfo("fetch", server_url=url)

    print("ERROR: No node_info source configured.")
    print("  Set AUTOFLOW_NODE_INFO_SOURCE or AUTOFLOW_COMFYUI_SERVER_URL env var,")
    print("  or pass --node-info <path> or --server-url <url>")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Test builder v2 features")
    parser.add_argument("--node-info", help="Path to node_info JSON file")
    parser.add_argument("--server-url", help="ComfyUI server URL")
    args = parser.parse_args()

    ni = load_node_info(args)
    ni_dict = dict(ni)
    print(f"✅ Loaded {len(ni_dict)} node types\n")

    passed = 0
    failed = 0

    # ── Test 1: >> operator ──────────────────────────────────────────────
    print("═══ Test 1: >> operator ═══")
    try:
        flow = Flow.create(node_info=ni)
        ckpt = flow.add_node("CheckpointLoaderSimple")
        pos = flow.add_node("CLIPTextEncode", text="a cat")
        neg = flow.add_node("CLIPTextEncode", text="bad")
        lat = flow.add_node("EmptyLatentImage", width=512, height=512)
        ks = flow.add_node("KSampler", seed=42, steps=20, cfg=7.0)
        vae = flow.add_node("VAEDecode")
        save = flow.add_node("SaveImage", filename_prefix="test")

        ckpt >> ks.model
        ckpt >> pos.clip
        ckpt >> neg.clip
        lat >> ks.latent_image
        pos >> ks.positive
        neg >> ks.negative
        ks >> vae.samples
        ckpt >> vae.vae
        vae >> save.images

        n_links = len(flow._flow["links"])
        assert n_links == 9, f"Expected 9 links, got {n_links}"
        print(f"  ✅ {n_links} links connected via >>")
        passed += 1
        flow.save('../test-01.json')
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 2: flow.connect() path syntax ───────────────────────────────
    print("\n═══ Test 2: flow.connect() path syntax ═══")
    try:
        flow2 = Flow.create(node_info=ni)
        flow2.add_node("CheckpointLoaderSimple")
        flow2.add_node("KSampler")
        flow2.connect("CheckpointLoaderSimple/MODEL", "KSampler/model")
        assert len(flow2._flow["links"]) == 1
        print(f"  ✅ flow.connect() path works")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 3: NodeSet.connect() ────────────────────────────────────────
    print("\n═══ Test 3: NodeSet.connect() ═══")
    try:
        flow3 = Flow.create(node_info=ni)
        c3 = flow3.add_node("CheckpointLoaderSimple")
        flow3.add_node("KSampler")
        flow3.nodes.KSampler.connect("model", c3)
        assert len(flow3._flow["links"]) == 1
        print(f"  ✅ NodeSet.connect() works")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 4: COMBO defaults ───────────────────────────────────────────
    print("\n═══ Test 4: COMBO defaults ═══")
    try:
        # Test the _is_connection_only_input function
        assert not _is_connection_only_input(["COMBO", {"options": ["a", "b"]}])
        assert _is_connection_only_input(["MODEL", {"tooltip": "x"}])

        # Test with a real COMBO from node_info if one exists
        combo_found = False
        for cls_name, info in ni_dict.items():
            for section in ["required", "optional"]:
                for name, spec in info.get("input", {}).get(section, {}).items():
                    if isinstance(spec, list) and len(spec) >= 1 and spec[0] == "COMBO":
                        d = get_input_default(cls_name, name, ni_dict)
                        print(f"  Real COMBO: {cls_name}.{name} → default={d!r}")
                        combo_found = True
                        break
                if combo_found:
                    break
            if combo_found:
                break
        if not combo_found:
            print("  (no COMBO-type inputs found in this node_info)")
        print(f"  ✅ COMBO classification correct")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 5: forceInput ───────────────────────────────────────────────
    print("\n═══ Test 5: forceInput ═══")
    try:
        assert _is_connection_only_input(["FLOAT", {"default": 1.0, "forceInput": True}])
        assert not _is_connection_only_input(["FLOAT", {"default": 1.0}])

        # Find a real forceInput in node_info
        fi_found = False
        for cls_name, info in ni_dict.items():
            for section in ["required", "optional"]:
                for name, spec in info.get("input", {}).get(section, {}).items():
                    if isinstance(spec, list) and len(spec) >= 2 and isinstance(spec[1], dict):
                        if spec[1].get("forceInput"):
                            conn_names = get_connection_input_names(cls_name, ni_dict)
                            assert name in conn_names, f"{cls_name}.{name} should be connection"
                            print(f"  Real forceInput: {cls_name}.{name} → classified as connection ✅")
                            fi_found = True
                            break
                if fi_found:
                    break
            if fi_found:
                break
        if not fi_found:
            print("  (no forceInput inputs found in this node_info)")
        print(f"  ✅ forceInput classification correct")
        passed += 1 
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 6: ni.<TAB> completion ──────────────────────────────────────
    print("\n═══ Test 6: NodeInfo tab completion ═══")
    try:
        d = dir(ni)
        assert "KSampler" in d, "KSampler should be in dir(ni)"
        assert "CLIPTextEncode" in d, "CLIPTextEncode should be in dir(ni)"
        print(f"  dir(ni) has {len(d)} entries")
        print(f"  ✅ Tab completion works")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 7: add_node(ni.CLIPTextEncode) ──────────────────────────────
    print("\n═══ Test 7: add_node(ni.CLIPTextEncode) ═══")
    try:
        flow7 = Flow.create(node_info=ni)
        node = flow7.add_node(ni.CLIPTextEncode, text="test")
        assert node.type == "CLIPTextEncode"
        print(f"  ✅ add_node(ni.CLIPTextEncode) → type={node.type}")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 8: ni.find() ────────────────────────────────────────────────
    print("\n═══ Test 8: ni.find() ═══")
    try:
        results = ni.find("sampler")
        print(f"  ni.find('sampler') → {len(results)} results")
        for r in results[:3]:
            print(f"    {getattr(r, '_autoflow_addr', '?')}")
        assert len(results) >= 1, "Should find at least 1 sampler"
        print(f"  ✅ ni.find() works")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 9: node.inputs / node.outputs tab completion ──────────────────
    print("\n═══ Test 9: Slot Discovery (inputs/outputs) ═══")
    try:
        flow9 = Flow.create(node_info=ni)
        ckpt9 = flow9.add_node("CheckpointLoaderSimple")
        ks9 = flow9.add_node("KSampler")

        inp_dir = dir(ks9.inputs)
        out_dir = dir(ckpt9.outputs)
        assert "model" in inp_dir, f"'model' not in ks.inputs dir: {inp_dir}"
        assert "MODEL" in out_dir, f"'MODEL' not in ckpt.outputs dir: {out_dir}"
        assert "inputs" in dir(ks9), "'inputs' not in dir(ks)"
        assert "outputs" in dir(ks9), "'outputs' not in dir(ks)"
        print(f"  ks.inputs dir: {inp_dir}")
        print(f"  ckpt.outputs dir: {out_dir}")
        print(f"  ✅ Slot Discovery works")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 10: explicit outputs >> inputs ───────────────────────────────
    print("\n═══ Test 10: ckpt.outputs.MODEL >> ks.inputs.model ═══")
    try:
        flow10 = Flow.create(node_info=ni)
        ckpt10 = flow10.add_node("CheckpointLoaderSimple")
        ks10 = flow10.add_node("KSampler")
        ckpt10.outputs.MODEL >> ks10.inputs.model
        assert len(flow10._flow["links"]) == 1
        print(f"  ✅ Explicit outputs >> inputs works")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 11: flow.connect() with SlotRef + fan-out ────────────────────
    print("\n═══ Test 11: flow.connect() with SlotRef fan-out ═══")
    try:
        flow11 = Flow.create(node_info=ni)
        ckpt11 = flow11.add_node("CheckpointLoaderSimple")
        pos11 = flow11.add_node("CLIPTextEncode", text="a")
        neg11 = flow11.add_node("CLIPTextEncode", text="b")

        # Fan-out: CLIP to both CLIPTextEncode nodes
        flow11.connect(ckpt11.outputs.CLIP, [pos11.inputs.clip, neg11.inputs.clip])
        assert len(flow11._flow["links"]) == 2, f"Expected 2 links, got {len(flow11._flow['links'])}"
        print(f"  ✅ Fan-out works: 1 output → 2 inputs")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 12: << pull operator ─────────────────────────────────────────
    print("\n═══ Test 12: << pull operator ═══")
    try:
        flow12 = Flow.create(node_info=ni)
        ckpt12 = flow12.add_node("CheckpointLoaderSimple")
        ks12 = flow12.add_node("KSampler")
        pos12 = flow12.add_node("CLIPTextEncode", text="test")

        # Pull: input << source_node (auto-resolve)
        ks12.inputs.model << ckpt12
        assert len(flow12._flow["links"]) == 1

        # Pull: input << source.outputs.OUTPUT (explicit)
        pos12.inputs.clip << ckpt12.outputs.CLIP
        assert len(flow12._flow["links"]) == 2

        print(f"  ✅ << pull operator works (auto + explicit)")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 13: >> list fan-out + .connect() ─────────────────────────────
    print("\n═══ Test 13: >> list fan-out + .connect() ═══")
    try:
        flow13 = Flow.create(node_info=ni)
        ckpt13 = flow13.add_node("CheckpointLoaderSimple")
        pos13 = flow13.add_node("CLIPTextEncode", text="a")
        neg13 = flow13.add_node("CLIPTextEncode", text="b")

        # >> with list
        ckpt13.outputs.CLIP >> [pos13.inputs.clip, neg13.inputs.clip]
        assert len(flow13._flow["links"]) == 2

        # .connect() on output slot
        ks13 = flow13.add_node("KSampler")
        vae13 = flow13.add_node("VAEDecode")
        ckpt13.outputs.MODEL.connect(ks13.inputs.model)
        ckpt13.outputs.VAE.connect(vae13.inputs.vae)
        assert len(flow13._flow["links"]) == 4

        print(f"  ✅ >> [list] + .connect() work ({len(flow13._flow['links'])} links)")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

    # ── Test 14: disconnect via .disconnect(), << None, >> None ───────────
    print("\n═══ Test 14: disconnect ═══")
    try:
        flow14 = Flow.create(node_info=ni)
        ckpt14 = flow14.add_node("CheckpointLoaderSimple")
        ks14 = flow14.add_node("KSampler")
        pos14 = flow14.add_node("CLIPTextEncode", text="a")
        neg14 = flow14.add_node("CLIPTextEncode", text="b")

        # Connect everything first
        ckpt14.outputs.MODEL >> ks14.inputs.model
        ckpt14.outputs.CLIP >> [pos14.inputs.clip, neg14.inputs.clip]
        assert len(flow14._flow["links"]) == 3

        # 1. input.disconnect()
        ks14.inputs.model.disconnect()
        assert len(flow14._flow["links"]) == 2
        print(f"  ✅ input.disconnect() → 2 links remain")

        # Reconnect for next test
        ckpt14.outputs.MODEL >> ks14.inputs.model
        assert len(flow14._flow["links"]) == 3

        # 2. input << None
        ks14.inputs.model << None
        assert len(flow14._flow["links"]) == 2
        print(f"  ✅ input << None → 2 links remain")

        # 3. output.disconnect(specific_target)
        ckpt14.outputs.CLIP.disconnect(pos14.inputs.clip)
        assert len(flow14._flow["links"]) == 1
        print(f"  ✅ output.disconnect(target) → 1 link remains")

        # 4. output >> None (disconnect all)
        ckpt14.outputs.CLIP >> None
        assert len(flow14._flow["links"]) == 0
        print(f"  ✅ output >> None → 0 links remain")

        print(f"  ✅ All disconnect patterns work")
        passed += 1
    except Exception as e:
        print(f"  ❌ {e}")
        import traceback; traceback.print_exc()
        failed += 1

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'═' * 50}")
    print(f"  {passed} passed, {failed} failed")
    if failed == 0:
        print("  🎉 ALL TESTS PASSED")
    else:
        print("  ⚠️  SOME TESTS FAILED")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
