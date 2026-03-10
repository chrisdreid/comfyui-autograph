"""Flow Builder API — Complete Feature Demo

This demo exercises every feature of the builder API.
Run with: python examples/demo_builder.py

Requires: node_info.json in the repo root (or set AUTOGRAPH_COMFYUI_SERVER_URL)
"""

import json
import sys
import os
from pathlib import Path

# ── Setup ────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autograph import Flow, NodeInfo, Connection

# Load node_info — try local file, then env server, then built-in test data
ni_path = Path(__file__).resolve().parents[1] / "node-info.json"
if ni_path.exists():
    ni = NodeInfo(json.loads(ni_path.read_text(encoding="utf-8")))
    print(f"✅ Loaded node_info from {ni_path.name} ({len(dict(ni))} types)\n")
elif os.environ.get("AUTOGRAPH_COMFYUI_SERVER_URL"):
    ni = NodeInfo("fetch")
    print(f"✅ Fetched node_info from server ({len(dict(ni))} types)\n")
else:
    print("⚠️  No node_info.json found and no server URL set.")
    print("   Place node-info.json in repo root or set AUTOGRAPH_COMFYUI_SERVER_URL\n")
    sys.exit(1)


def section(title):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ═══════════════════════════════════════════════════════════════════════════
#  1. CREATE AN EMPTY FLOW
# ═══════════════════════════════════════════════════════════════════════════
section("1. Flow.create() — Empty flow from scratch")

flow = Flow.create(node_info=ni)
print(f"   Created empty flow")
print(f"   Nodes: {len(flow._flow['nodes'])}")
print(f"   Links: {len(flow._flow['links'])}")


# ═══════════════════════════════════════════════════════════════════════════
#  2. ADD NODES WITH DEFAULT WIDGETS
# ═══════════════════════════════════════════════════════════════════════════
section("2. flow.add_node() — Add nodes with defaults & overrides")

ckpt = flow.add_node("CheckpointLoaderSimple", ckpt_name="v1-5-pruned-emaonly.safetensors")
print(f"   ckpt = add_node('CheckpointLoaderSimple', ckpt_name='v1-5-pruned-emaonly.safetensors')")
print(f"   → id={ckpt.addr}, type={ckpt.type}")

pos = flow.add_node("CLIPTextEncode", text="a stunning photograph of a snow leopard in golden hour light")
neg = flow.add_node("CLIPTextEncode", text="blurry, low quality, watermark, text")
print(f"   pos = add_node('CLIPTextEncode', text='a stunning photograph...')")
print(f"   neg = add_node('CLIPTextEncode', text='blurry, low quality...')")

lat = flow.add_node("EmptyLatentImage", width=768, height=768, batch_size=1)
print(f"   lat = add_node('EmptyLatentImage', width=768, height=768, batch_size=1)")

ks = flow.add_node("KSampler", seed=12345, steps=25, cfg=7.5)
print(f"   ks  = add_node('KSampler', seed=12345, steps=25, cfg=7.5)")

vae = flow.add_node("VAEDecode")
print(f"   vae = add_node('VAEDecode')  — no overrides, all defaults")

save = flow.add_node("SaveImage", filename_prefix="demo_output")
print(f"   save = add_node('SaveImage', filename_prefix='demo_output')")

print(f"\n   Total nodes: {len(flow._flow['nodes'])}")


# ═══════════════════════════════════════════════════════════════════════════
#  3. DOT NOTATION — READ AND WRITE WIDGET VALUES
# ═══════════════════════════════════════════════════════════════════════════
section("3. Dot notation — Read & write widget values")

print(f"   ks.seed     = {ks.seed}")
print(f"   ks.steps    = {ks.steps}")
print(f"   ks.cfg      = {ks.cfg}")
print(f"   pos.text    = {pos.text!r}")

print(f"\n   ks.seed = 99999  (write)")
ks.seed = 99999
print(f"   ks.seed     = {ks.seed}  ← updated")

print(f"\n   ks.steps = 30  (write)")
ks.steps = 30
print(f"   ks.steps    = {ks.steps}  ← updated")


# ═══════════════════════════════════════════════════════════════════════════
#  4. CONNECT NODES
# ═══════════════════════════════════════════════════════════════════════════
section("4. node.connect() — Wire connections (auto-resolve)")

# No need to specify the output name — it auto-resolves from the input type!
ks.connect("model", ckpt)           # input 'model' expects MODEL → auto-finds MODEL output
ks.connect("positive", pos)         # input 'positive' expects CONDITIONING → auto-finds it
ks.connect("negative", neg)         # same
ks.connect("latent_image", lat)     # input 'latent_image' expects LATENT → auto-finds it
pos.connect("clip", ckpt)           # input 'clip' expects CLIP → auto-finds CLIP output
neg.connect("clip", ckpt)
vae.connect("samples", ks)          # expects LATENT → auto-finds KSampler's LATENT output
vae.connect("vae", ckpt)            # expects VAE → auto-finds it
save.connect("images", vae)         # expects IMAGE → auto-finds it

# You can still specify explicitly when needed (e.g. disambiguation):
# ks.connect("model", ckpt, "MODEL")      # by name
# ks.connect("model", ckpt, 0)            # by index

print(f"   Connected 9 links (all auto-resolved):")
for lnk in flow._flow["links"]:
    print(f"     link {lnk[0]}: node {lnk[1]}[{lnk[2]}] → node {lnk[3]}[{lnk[4]}] ({lnk[5]})")


# ═══════════════════════════════════════════════════════════════════════════
#  5. INSPECT CONNECTIONS (read-only properties)
# ═══════════════════════════════════════════════════════════════════════════
section("5. node.connections — Input connections")

conns = ks.connections
print(f"   ks.connections ({len(conns)} connections):")
for name, c in conns.items():
    print(f"     {name}: from node {c.from_node_id} output[{c.from_output}] ({c.from_class_type})")

section("5b. node.downstream — Output connections")

ds = ckpt.downstream
print(f"   ckpt.downstream ({len(ds)} output slots used):")
for slot_idx, conn_list in ds.items():
    for c in conn_list:
        print(f"     output[{slot_idx}] → node {c.to_node_id} input '{c.to_input_name}' ({c.to_class_type})")


# ═══════════════════════════════════════════════════════════════════════════
#  6. DISCONNECT AND RECONNECT
# ═══════════════════════════════════════════════════════════════════════════
section("6. node.disconnect() + reconnect")

print(f"   Links before disconnect: {len(flow._flow['links'])}")
ks.disconnect("model")
print(f"   ks.disconnect('model')")
print(f"   Links after disconnect:  {len(flow._flow['links'])}")
print(f"   ks.connections: {list(ks.connections.keys())}  ← 'model' gone")

ks.connect("model", ckpt)  # auto-resolves to MODEL output
print(f"   ks.connect('model', ckpt)  ← reconnected (auto-resolved)")
print(f"   Links after reconnect:   {len(flow._flow['links'])}")


# ═══════════════════════════════════════════════════════════════════════════
#  7. NAVIGATION — flow.nodes / flow.find()
# ═══════════════════════════════════════════════════════════════════════════
section("7. Navigation — flow.nodes / flow.find()")

print(f"   flow.nodes → {len(list(flow.nodes))} nodes")
print(f"   flow.nodes.KSampler → {flow.nodes.KSampler}")

found = flow.find(type="CLIPTextEncode")
print(f"   flow.find(type='CLIPTextEncode') → {len(found)} matches")
for f in found:
    print(f"     id={f.id}, text={f.text!r}")


# ═══════════════════════════════════════════════════════════════════════════
#  8. DIR / AUTOCOMPLETE
# ═══════════════════════════════════════════════════════════════════════════
section("8. dir(node) — Autocomplete / introspection")

d = dir(ks)
widget_names = ks._widget_names()
print(f"   dir(ks) includes: {[x for x in d if not x.startswith('_')][:12]}...")
print(f"   ks._widget_names() = {widget_names}")
print(f"   ks._widget_dict()  = {ks._widget_dict()}")


# ═══════════════════════════════════════════════════════════════════════════
#  9. REMOVE NODE (cleans up all connections)
# ═══════════════════════════════════════════════════════════════════════════
section("9. flow.remove_node() — Remove with cleanup")

extra = flow.add_node("EmptyLatentImage", width=256, height=256)
print(f"   Added extra EmptyLatentImage (id={extra.addr})")
print(f"   Nodes: {len(flow._flow['nodes'])}, Links: {len(flow._flow['links'])}")

flow.remove_node(extra)
print(f"   flow.remove_node(extra)")
print(f"   Nodes: {len(flow._flow['nodes'])}, Links: {len(flow._flow['links'])}  ← cleaned up")


# ═══════════════════════════════════════════════════════════════════════════
#  10. AUTO LAYOUT
# ═══════════════════════════════════════════════════════════════════════════
section("10. flow.auto_layout() — Position by topology")

flow.auto_layout()
positions = {n["type"]: n["pos"] for n in flow._flow["nodes"]}
print("   Positions after auto_layout():")
for ntype, pos_ in sorted(positions.items(), key=lambda x: x[1][0]):
    print(f"     {ntype:30s} x={pos_[0]:5d}, y={pos_[1]:5d}")


# ═══════════════════════════════════════════════════════════════════════════
#  11. SAVE — Roundtrip test
# ═══════════════════════════════════════════════════════════════════════════
section("11. flow.save() + reload")

out_path = Path(__file__).resolve().parent / "demo_builder_output.json"
flow.save(str(out_path))
print(f"   Saved to: {out_path}")

# Reload and verify
flow2 = Flow(str(out_path), node_info=ni)
print(f"   Reloaded: {len(flow2._flow['nodes'])} nodes, {len(flow2._flow['links'])} links")
ks2 = flow2.find(type="KSampler")[0]
print(f"   flow2 KSampler.seed  = {ks2.seed}")
print(f"   flow2 KSampler.steps = {ks2.steps}")


# ═══════════════════════════════════════════════════════════════════════════
#  12. CONNECTION DATACLASS — Direct construction
# ═══════════════════════════════════════════════════════════════════════════
section("12. Connection dataclass")

c = Connection(
    input_name="model",
    from_node_id="1",
    from_output=0,
    from_class_type="CheckpointLoaderSimple",
)
print(f"   Connection object: {c}")
print(f"   c.input_name      = {c.input_name}")
print(f"   c.from_node_id    = {c.from_node_id}")
print(f"   c.from_output     = {c.from_output}")
print(f"   c.from_class_type = {c.from_class_type}")


# ═══════════════════════════════════════════════════════════════════════════
#  13. CONNECTION HELPERS — Inspect node_info
# ═══════════════════════════════════════════════════════════════════════════
section("13. Connection helpers — Inspect node_info")

from autograph.connection import (
    get_connection_input_names,
    get_output_slots,
    get_all_input_names,
    get_input_default,
)

ni_dict = dict(ni)

conn_inputs = get_connection_input_names("KSampler", ni_dict)
print(f"   Connection inputs for KSampler: {conn_inputs}")

out_slots = get_output_slots("KSampler", ni_dict)
print(f"   Output slots for KSampler:      {out_slots}")

all_inputs = get_all_input_names("KSampler", ni_dict)
print(f"   All inputs for KSampler:        {all_inputs}")

defaults = {n: get_input_default("KSampler", n, ni_dict) for n in all_inputs}
print(f"   Defaults for KSampler:")
for name, val in defaults.items():
    print(f"     {name:25s} = {val!r}")


# ═══════════════════════════════════════════════════════════════════════════
#  14. DAG — Dependency graph
# ═══════════════════════════════════════════════════════════════════════════
section("14. flow.dag — Dependency graph on builder flows")

try:
    dag = flow.dag
    print(f"   dag.edges: {len(dag.edges)} edges")
    for src, dst in list(dag.edges)[:6]:
        print(f"     {src} → {dst}")
    if len(dag.edges) > 6:
        print(f"     ...and {len(dag.edges) - 6} more")
except Exception as e:
    print(f"   ⚠️  dag not available on builder flows: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  DONE
# ═══════════════════════════════════════════════════════════════════════════
section("Done!")
print(f"   Output file: {out_path}")
print(f"   Open in ComfyUI to verify the workflow renders correctly.\n")
