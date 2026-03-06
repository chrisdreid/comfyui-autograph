# Node Info Edge Cases & Widget Patterns

Reference guide for ComfyUI `node_info` patterns that affect the Flow Builder API.
Covers 904 node types scanned from a live ComfyUI server's `/object_info` endpoint.

Use this to understand edge cases when adding test coverage or extending the builder.

---

## 1. `control_after_generate` — Frontend-Injected Widget

**Status:** ✅ Handled  
**Count:** 77 nodes  

When an INT widget's options include `"control_after_generate": true`, ComfyUI's frontend
injects an extra combo widget (`"fixed"`, `"increment"`, `"decrement"`, `"randomize"`) directly
after it in the UI. This widget does NOT appear as a separate input in `node_info` — it's a
flag on the parent widget.

If the builder doesn't account for this, `widgets_values` shifts by one position and every
widget after seed shows the wrong value.

### Affected Nodes

| Node | Input | Parent Type |
|------|-------|-------------|
| `KSampler` | `seed` | INT |
| `KSamplerAdvanced` | `noise_seed` | INT |
| `SamplerCustom` | `noise_seed` | INT |
| `RandomNoise` | `noise_seed` | INT |
| `ImageAddNoise` | `seed` | INT |
| `PrimitiveInt` | `value` | INT |
| ...and 71 more | | |

### Spec Example

```json
"seed": [
  "INT",
  {
    "default": 0,
    "min": 0,
    "max": 18446744073709551615,
    "control_after_generate": true,
    "tooltip": "The random seed used for creating the noise."
  }
]
```

### What the builder must do

After appending the INT widget's value, also append the combo value:
```python
widgets_values.append(seed_value)       # e.g. 42
widgets_values.append("randomize")      # injected combo
```

---

## 2. `COMBO` Type Inputs — New-Style Combos

**Status:** ⚠️ Needs fix  
**Count:** 358 nodes  

Two combo patterns exist in `node_info`:

### Classic combo (works today)
Choices are a list in `spec[0]`:
```json
"sampler_name": [
  ["euler", "euler_ancestral", "heun", "dpm_2", "lms", "ddim"],
  {"tooltip": "The sampling algorithm."}
]
```
Default = first item in the list (`"euler"`).

### New-style COMBO (broken today)
Type string `"COMBO"` in `spec[0]`, choices in `spec[1]["options"]`:
```json
"dim": [
  "COMBO",
  {
    "multiselect": false,
    "options": ["x", "-x", "y", "-y", "t", "-t"]
  }
]
```

```json
"seed_behavior": [
  "COMBO",
  {
    "default": "fixed",
    "multiselect": false,
    "options": ["random", "fixed"]
  }
]
```

### Affected Nodes (examples)

| Node | Input | Has Default? |
|------|-------|-------------|
| `LatentConcat` | `dim` | No (`options[0]` = `"x"`) |
| `LatentCut` | `dim` | No (`options[0]` = `"x"`) |
| `LatentBatchSeedBehavior` | `seed_behavior` | Yes (`"fixed"`) |
| `ImageBlend` | `blend_mode` | Check options |
| `BasicScheduler` | `scheduler` | Check options |

### What must change

`get_input_default()` needs to handle `spec[0] == "COMBO"`:
```python
if spec[0] == "COMBO" and isinstance(spec[1], dict):
    if "default" in spec[1]:
        return spec[1]["default"]
    options = spec[1].get("options", [])
    return options[0] if options else None
```

Also affects `get_widget_input_names()` — `"COMBO"` is a string, not a list of choices,
so the `isinstance(spec[0], str)` check must not skip it as a connection-only input.

---

## 3. `forceInput` — Widget Becomes Connection

**Status:** ⚠️ Needs fix  
**Count:** 67 nodes  

When a widget spec includes `"forceInput": true`, ComfyUI's frontend renders it as a
**connection slot** instead of an editable widget — even though it has `default`, `min`, `max`, etc.

This means: no `widgets_values` entry should exist for it, and it should appear in the node's
`inputs` array (connection slots), not just in widget rendering.

### Affected Nodes (examples)

| Node | Input | Original Type | Section |
|------|-------|---------------|---------|
| `CreateHookKeyframesFromFloats` | `floats_strength` | FLOATS | required |
| `WanMoveTracksFromCoords` | `track_coords` | STRING | optional |
| `KlingVideoExtendNode` | `video_id` | STRING | required |
| `RecraftTextToImageNode` | `negative_prompt` | STRING | required |
| `StabilityStableImageUltraNode` | `negative_prompt` | STRING | required |

### Spec Example

```json
"floats_strength": [
  "FLOATS",
  {"default": -1, "min": -1, "step": 0.001, "forceInput": true}
]
```

```json
"track_coords": [
  "STRING",
  {"default": "[]", "forceInput": true, "multiline": false}
]
```

### What must change

`get_widget_input_names()` should skip inputs with `forceInput: true`.
`get_connection_input_names()` should include them (they need connection slots).

---

## 4. File Upload Widgets — Server-Dependent Defaults

**Status:** ⚠️ Known limitation  
**Count:** 9 nodes  

These use `COMBO` type with upload flags. The choices list contains filenames from the
server's input directory — which we can't know offline.

### Affected Nodes

| Node | Input | Flag |
|------|-------|------|
| `LoadImage` | `image` | `image_upload: true` |
| `LoadImageMask` | `image` | `image_upload: true` |
| `LoadImageOutput` | `image` | `image_upload: true`, `image_folder: "output"` |
| `LoadVideo` | `file` | `video_upload: true` |
| `LoadAudio` | `audio` | `audio_upload: true` |
| `Load3D` | `model_file` | `file_upload: true` |

### Spec Example

```json
"image": [
  ["image1.png", "image2.png"],
  {"image_upload": true}
]
```

When fetched from a server, `spec[0]` is a list of actual filenames. Offline, we have no
valid choices. Default should be `""` or the first filename if available.

---

## 5. `display` Flag — Rendering Hints

**Status:** ✅ No action needed (visual only)  
**Count:** 120 nodes  

Tells the frontend how to render a widget. Does NOT affect `widgets_values`.

### Examples

| Node | Input | Display |
|------|-------|---------|
| `EmptyImage` | `color` | `"color"` (color picker for INT 0–16777215) |
| `ImageColorToMask` | `color` | `"number"` |
| `Epsilon Scaling` | `scaling_factor` | `"number"` |

```json
"color": ["INT", {"default": 0, "min": 0, "max": 16777215, "display": "color"}]
```

---

## 6. Optional Inputs

**Status:** ✅ Works today  
**Count:** 256 / 904 nodes have optional inputs  

Optional inputs follow the same spec format as required. Both connection-only and widget
optional inputs exist. The builder already handles them — `get_widget_input_names()` and
`get_connection_input_names()` both scan `["required", "optional"]` sections.

### Complex Nodes (most inputs)

| Node | Required | Optional |
|------|----------|----------|
| `TripoConversionNode` | 2 | 17 |
| `Mask_transform_sum` | 15 | 2 |
| `WanAnimateToVideo` | 9 | 7 |
| `TripoMultiviewToModelNode` | 1 | 14 |

---

## 7. Hidden Inputs

**Status:** ✅ Correctly skipped  

Many nodes have `"hidden"` inputs like `"prompt": "PROMPT"` or `"unique_id": "UNIQUE_ID"`.
These are injected at execution time by the ComfyUI runtime. The builder correctly ignores
them (only scans `required` and `optional`).

---

## 8. Nodes With Many Outputs (>5)

**Status:** ✅ Works today  

| Node | Outputs | Output Names |
|------|---------|-------------|
| `Image_Resize_sum_data` | 11 | width, height, x_offset, y_offset, ... |
| `VHS_VideoInfo` | 10 | source_fps, source_frame_count, ... |
| `BatchCropFromMaskAdvanced` | 9 | original_images, cropped_images, ... |
| `Load3D` | 6 | image, mask, mesh_path, normal, ... |

The builder handles arbitrary output counts — `get_output_slots()` iterates the full
`output` and `output_name` arrays.

---

## 9. `output_is_list` Flag

**Status:** ✅ No builder impact  
**Count:** 16 nodes  

Nodes like `RebatchLatents` and `RebatchImages` set `output_is_list: [true]` to signal
batch/list outputs. Connection type is unchanged — this only affects execution behavior.

---

## Summary — Fix Priority

| # | Pattern | Impact | Nodes | Status |
|---|---------|--------|-------|--------|
| 1 | `COMBO` type defaults | Silent wrong/None defaults | 358 | ⚠️ Fix needed |
| 2 | `forceInput` flag | Widget/connection misclassification | 67 | ⚠️ Fix needed |
| 3 | `control_after_generate` | Positional value shift | 77 | ✅ Fixed |
| 4 | File upload defaults | No valid offline default | 9 | ⚠️ Known |
| 5 | `display` flag | Visual only | 120 | ✅ No action |
| 6 | Optional inputs | Standard handling | 256 | ✅ Works |
| 7 | Hidden inputs | Correctly skipped | many | ✅ Works |
| 8 | Many outputs | Standard handling | 8 | ✅ Works |
| 9 | `output_is_list` | Execution only | 16 | ✅ No action |

### Nodes Recommended for Test Coverage

These nodes exercise the most edge cases and should be in test suites:

```
KSampler              — control_after_generate, classic combos, connections
LatentConcat           — COMBO type input
LatentBatchSeedBehavior — COMBO type with explicit default
LoadImage              — file upload combo, image_upload flag
CreateHookKeyframesFromFloats — forceInput on required
WanMoveTracksFromCoords — forceInput on optional
EmptyImage             — display:color flag
TripoConversionNode    — 17 optional inputs
Image_Resize_sum_data  — 11 outputs
RebatchLatents         — output_is_list
```
