# Research: Building Flows from Scratch in Python

## Goal

Enable programmatic construction of complete ComfyUI workflow graphs:

```python
from autograph import Flow, NodeInfo

oi = NodeInfo.load("node_info.json")
flow = Flow.create(node_info=oi)

# Add nodes with defaults from node_info
ckpt = flow.add_node("CheckpointLoaderSimple", ckpt_name="v1-5-pruned.safetensors")
pos  = flow.add_node("CLIPTextEncode", text="a photo of a cat")
neg  = flow.add_node("CLIPTextEncode", text="blurry, bad")
lat  = flow.add_node("EmptyLatentImage", width=512, height=512, batch_size=1)
ks   = flow.add_node("KSampler", seed=42, steps=20, cfg=7.0)
vae  = flow.add_node("VAEDecode")
save = flow.add_node("SaveImage", filename_prefix="output")

# Connect outputs to inputs
ks.connect("model", ckpt, "MODEL")
ks.connect("positive", pos, "CONDITIONING")
ks.connect("negative", neg, "CONDITIONING")
ks.connect("latent_image", lat, "LATENT")
pos.connect("clip", ckpt, "CLIP")
neg.connect("clip", ckpt, "CLIP")
vae.connect("samples", ks, "LATENT")
vae.connect("vae", ckpt, "VAE")
save.connect("images", vae, "IMAGE")

flow.save("my_workflow.json")
```

This connects directly to the [InputOutputConnect.md](InputOutputConnect.md) research — `connect()` / `disconnect()` are shared primitives.

---

## What node_info Provides Per Node Type

Each entry in `node_info[class_type]` contains:

```python
oi["KSampler"] = {
    "input": {
        "required": {
            "model": ["MODEL"],                           # connection-only input (type name)
            "positive": ["CONDITIONING"],                 # connection-only input
            "negative": ["CONDITIONING"],                 # connection-only input
            "latent_image": ["LATENT"],                   # connection-only input
            "seed": ["INT", {"default": 0, ...}],         # widget with default
            "steps": ["INT", {"default": 20, ...}],       # widget with default
            "cfg": ["FLOAT", {"default": 8.0, ...}],      # widget with default
            "sampler_name": [["euler", "euler_a", ...],{...}], # combo widget
            "scheduler": [["normal", "karras", ...],{...}],    # combo widget
            "denoise": ["FLOAT", {"default": 1.0, ...}],  # widget with default
        },
        "optional": { ... }
    },
    "output": ["LATENT"],
    "output_name": ["LATENT"],
    "output_is_list": [false],
    "name": "KSampler",
    "display_name": "KSampler",
    "category": "sampling",
    "output_node": false
}
```

**Key distinction:**
- **Connection input**: `["TYPE_NAME"]` or `["TYPE_NAME", {"tooltip": "..."}]` — just a type, no default
- **Widget input**: `["INT", {"default": 0, ...}]` or `[["option1", "option2"], {...}]` — has defaults/constraints

This is the same logic `get_widget_input_names()` already uses (line 894 of `convert.py`).

---

## Workspace Node Schema (what `add_node` must produce)

From studying `examples/workflows/workflow.json`, each Flow node has:

```python
{
    "id": 3,                           # unique int, auto-assigned
    "type": "KSampler",                # from class_type
    "pos": [686, 365],                 # auto-layout or user-specified
    "size": [378, 538],                # defaults or from node_info hints
    "flags": {},                       # default empty
    "order": 9,                        # execution order, can use toposort
    "mode": 0,                         # 0 = active
    "inputs": [                        # connection slots (from node_info required+optional)
        {"name": "model", "type": "MODEL", "link": null},
        {"name": "positive", "type": "CONDITIONING", "link": null},
        ...
    ],
    "outputs": [                       # output slots (from node_info output/output_name)
        {"name": "LATENT", "type": "LATENT", "slot_index": 0, "links": []}
    ],
    "properties": {
        "Node name for S&R": "KSampler"  # standard property
    },
    "widgets_values": [42, "fixed", 20, 7.0, "euler", "normal", 1.0]
    # ^ positional, matches get_widget_input_names() order
}
```

---

## What `add_node()` Needs To Do

```
add_node(class_type, **widget_overrides):

1. Look up class_type in node_info
   - Fail fast if not found
   
2. Build input slots from node_info["input"]["required"] + ["optional"]
   - For each connection-only input: create {"name": name, "type": TYPE, "link": null}
   - For each widget input: record its default value

3. Build output slots from node_info["output"] + ["output_name"]
   - For each output: create {"name": name, "type": TYPE, "slot_index": i, "links": []}

4. Build widgets_values array
   - Use defaults from node_info specs
   - Override with user-supplied **widget_overrides
   - Order must match get_widget_input_names() order

5. Assign node ID
   - flow["last_node_id"] += 1; node["id"] = flow["last_node_id"]

6. Assign position (auto-layout or placeholder)
   - Simple: stack vertically with spacing
   - Better: defer layout to a final auto_layout() call

7. Append to flow["nodes"]

8. Return a FlowNodeProxy (or new BuilderNodeProxy) for chaining
```

---

## What `connect()` Needs To Do

This is the same operation described in [InputOutputConnect.md](InputOutputConnect.md), but from the builder perspective:

```
dst_node.connect(input_name, src_node, output_name_or_index=0):

1. Resolve src output slot
   - By name: find slot in src_node["outputs"] where name matches
   - By index: use output_name_or_index as slot_index

2. Resolve dst input slot
   - Find slot in dst_node["inputs"] where name matches input_name

3. Create link entry
   - flow["last_link_id"] += 1
   - link = [link_id, src_node.id, src_slot_index, dst_node.id, dst_slot_index, type_name]
   - flow["links"].append(link)

4. Update both nodes
   - dst_node["inputs"][slot]["link"] = link_id
   - src_node["outputs"][slot]["links"].append(link_id)
```

---

## What `Flow.create()` Needs To Initialize

```python
Flow.create(node_info=oi)  # returns:
{
    "last_node_id": 0,
    "last_link_id": 0,
    "nodes": [],
    "links": [],
    "groups": [],
    "config": {},
    "extra": {},
    "version": 0.4
}
```

**Problem:** `Flow.__init__` currently validates that `nodes` and `links` are non-empty lists and that `last_node_id`/`last_link_id` exist. `Flow.create()` would need to either:
- Bypass validation (classmethod that constructs directly)
- Relax validation to accept empty `nodes`/`links` lists

---

## Auto-Layout Strategy

Two approaches:

### A. Incremental (during `add_node`)
Each node gets placed at a simple grid position based on insertion order:
```python
col = len(flow["nodes"]) % 4
row = len(flow["nodes"]) // 4
pos = [col * 400, row * 300]
```
Quick but ugly — connections will criss-cross.

### B. Deferred (via `flow.auto_layout()`)
After all nodes and connections are created, run toposort-based layout:
```
1. Build DAG from links
2. Toposort → assign depth per node
3. Column = depth × spacing_x
4. Row = index-within-depth × spacing_y
```
This uses the existing `Dag._toposort_nodes()` infrastructure. Results in a clean left-to-right flow.

**Recommendation:** Use incremental as placeholder, offer `auto_layout()` as polish step.

---

## `disconnect()` Operation

```
node.disconnect(input_name):

1. Find input slot by name
2. Get link_id from slot["link"]
3. Find the link entry in flow["links"]
4. Remove link_id from source node's outputs[slot]["links"]
5. Set destination input slot["link"] = null
6. Remove link entry from flow["links"]
```

---

## Building an ApiFlow from Scratch (simpler variant)

For users who don't need the full workspace format:

```python
api = ApiFlow.create()
api.add_node("4", class_type="CheckpointLoaderSimple",
             inputs={"ckpt_name": "v1-5-pruned.safetensors"})
api.add_node("3", class_type="KSampler",
             inputs={"model": ["4", 0], "seed": 42, ...})
```

This is much simpler — no link table, no slots, no positions. Just `{node_id: {class_type, inputs}}`. Could be a lower-effort starting point.

---

## Implementation Complexity

| Feature | Difficulty | Notes |
|---|---|---|
| `Flow.create()` | Easy | Skeleton dict + bypass validation |
| `add_node()` with defaults from node_info | Medium | Parse input specs, build slots, widgets_values |
| `connect()` on Flow | Medium-Hard | Link table + both nodes' slot updates |
| `disconnect()` on Flow | Medium | Link table cleanup |
| `auto_layout()` (toposort) | Easy-Medium | DAG infra already exists |
| `ApiFlow.create()` + `add_node()` | Easy | No link table, just dict entries |
| Object_info autocomplete/validation | Medium | Validate widget types, enforce required connections |

## Shared Primitives with InputOutputConnect.md

| Primitive | Used by Builder | Used by InputOutputConnect |
|---|---|---|
| `node.connect(input, src, output)` | ✅ | ✅ |
| `node.disconnect(input)` | ✅ | ✅ |
| `node.connections` (read) | ✅ (for validation) | ✅ |
| `node.outputs` (read downstream) | ✅ (for validation) | ✅ |
| Link table management | ✅ | ✅ |

These should be implemented once and shared — the builder would use the same connection primitives as the editing API.

---

## Recommended Implementation Order

1. **`connect()` / `disconnect()` on Flow** — shared foundation for both builder and editor
2. **`Flow.create()`** — empty flow skeleton
3. **`flow.add_node()`** — node_info-driven node construction with defaults
4. **`auto_layout()`** — toposort-based positioning
5. **`ApiFlow.create()` / `api.add_node()`** — simpler variant for API-only users
6. **`node.connections` / `node.outputs`** — read-only inspection (from InputOutputConnect.md)
