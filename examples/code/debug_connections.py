"""Reproduce user's exact scenario and inspect output."""
import json, sys, os
sys.path.insert(0, ".")
from autoflow import Flow, NodeInfo

# Use env vars for node_info source
ni_src = os.environ.get("AUTOFLOW_NODE_INFO_SOURCE")
url = os.environ.get("AUTOFLOW_COMFYUI_SERVER_URL")
if ni_src:
    ni = NodeInfo(ni_src)
elif url:
    ni = NodeInfo("fetch", server_url=url)
else:
    print("ERROR: Set AUTOFLOW_NODE_INFO_SOURCE or AUTOFLOW_COMFYUI_SERVER_URL")
    sys.exit(1)

print(f"Loaded {len(dict(ni))} node types")

# User's exact code (with Flow.create)
flow = Flow.create(node_info=ni)

ckpt = flow.add_node("CheckpointLoaderSimple")
pos = flow.add_node("CLIPTextEncode", text="a cat")
neg = flow.add_node("CLIPTextEncode", text="bad")
lat = flow.add_node("EmptyLatentImage", width=1024, height=1024)
ks = flow.add_node("KSampler", seed=42, steps=20, cfg=7.0)
vae = flow.add_node("VAEDecode")
save = flow.add_node("SaveImage", filename_prefix="test")

# make sure this is set for rendering (clip breaks)
ckpt.ckpt_name = 'sd_xl_base_1.0.safetensors'


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
print(f"Links in memory: {n_links}")

# Save and inspect
out_path =  "debug_connections.json"
flow.auto_layout()
flow.save(out_path)
print(f"Saved to: {out_path}")

# Read back and inspect
with open(out_path, "r") as f:
    data = json.load(f)

print(f"\nSaved keys: {list(data.keys())}")
print(f"Saved links count: {len(data.get('links', []))}")
print(f"Saved last_link_id: {data.get('last_link_id')}")
print(f"Saved last_node_id: {data.get('last_node_id')}")
print(f"Saved nodes: {len(data.get('nodes', []))}")

if data.get("links"):
    print(f"\nLinks:")
    for lnk in data["links"]:
        print(f"  {lnk}")

# Inspect node inputs/outputs for link references
for node in data.get("nodes", []):
    node_id = node.get("id")
    node_type = node.get("type")
    linked_inputs = [i for i in node.get("inputs", []) if i.get("link") is not None]
    linked_outputs = [o for o in node.get("outputs", []) if o.get("links")]
    if linked_inputs or linked_outputs:
        print(f"\n  Node {node_id} ({node_type}):")
        for inp in linked_inputs:
            print(f"    input '{inp['name']}' link={inp['link']}")
        for outp in linked_outputs:
            print(f"    output '{outp['name']}' links={outp['links']}")
