# FEATURES (quick glance)

### What if you could `load, edit, and submit` ComfyUI workflows without ever exporting an API workflow from the GUI?

### What if you could `batch-convert and patch workflows offline`  No running ComfyUI instance required?

### What if you could attach studio `metadata` to your workflow and have it carry through the entire production lifecycle?

### What if you could `render` comfyui node workflows with all of the above features, `without ever launching the comfyui server`?

- # Let me introduce `comfyui-autograph`

`autograph` is **production-ready ComfyUI automation**, built by production for production.

This page is intentionally short. Each feature links to the relevant docs for details.

---

## Solve the "ComfyUI GUI export" problem

- **Submit `workflow.json` without the GUI export step**: Load workspace flows, convert to `ApiFlow`, and optionally submit to ComfyUI.  
  - Details: [`docs/convert.md`](docs/convert.md), [`docs/submit-and-images.md`](docs/submit-and-images.md)

- **Accept both formats**: Work with **workspace** (`workflow.json`) *or* **API payload** (`workflow-api.json`) as inputs (both are editable).  
  - Details: [`docs/load-vs-convert.md`](docs/load-vs-convert.md)

---

## Conversion that matches real ComfyUI behavior

- **Subgraphs (nested) supported**: Flattens `definitions.subgraphs` into a normal renderable API payload.  
  - Details: [`docs/convert.md`](docs/convert.md), tests: [`examples/unittests/test_subgraphs.py`](examples/unittests/test_subgraphs.py)

- **Offline or online schema**: Convert using saved `node_info.json` (reproducible, no server), or fetch `/object_info` from a running ComfyUI instance (explicit).  
  - Details: [`docs/node-info-and-env.md`](docs/node-info-and-env.md)

---

## Load workflows from many sources

- **Polymorphic `.load()`**: Pass a `dict`, `bytes`, JSON string, file path, or PNG — auto-detected.  
  - Details: [`docs/load-vs-convert.md`](docs/load-vs-convert.md)

- **Extract from PNG metadata**: Load `Flow` / `ApiFlow` directly from ComfyUI PNG outputs (embedded workflow data).  
  - Details: [`README.md`](README.md) (PNG examples), related: [`autograph/pngmeta.py`](autograph/pngmeta.py)

---

## Edit & target nodes safely (automation-friendly)

- **Dot syntax + find**: Easy node access and edits (inputs, seeds, etc.) with `.find(...)` helpers.  
  - Details: [`README.md`](README.md) (OOP node access), [`docs/convert.md`](docs/convert.md) (subgraph editing)

- **Widget-value repr**: `NodeRef` and `NodeSet` display widget values as dicts — e.g. `f.nodes.CheckpointLoaderSimple` → `{'nodes.CheckpointLoaderSimple[0]': {'ckpt_name': 'sd_xl_base_1.0.safetensors'}}`.

- **Widget introspection**: `.choices()` returns valid combo options, `.tooltip()` shows help text, `.spec()` gives the raw `node_info` spec — all available on any input attribute.

- **Stable addressing**: `.path()` / `.address()` helpers for repeatable targeting (including flattened subgraph-style IDs like `18:17:3`).  
  - Details: [`docs/convert.md`](docs/convert.md)

- **Node bypass / mute**: `node.bypass = True` skips nodes during conversion (mirrors the ComfyUI GUI mute/bypass), `node.bypass = False` re-enables.  
  - Converter automatically excludes bypassed (mode 4) and muted (mode 2) nodes from the API payload

- **Metadata that carries through**: Add metadata to the workflow that persists into the converted payload (and can be used for targeting/mapping).  
  - Details: [`docs/mapping.md`](docs/mapping.md)

---

## Mapping (pipelines / batch runs)

- **Map across nodes**: Patch seeds, prompts, paths, etc. via callbacks or helpers.  
  - Details: [`docs/mapping.md`](docs/mapping.md), [`docs/map-strings-and-paths.md`](docs/map-strings-and-paths.md)

- **Force recompute / cache-busting**: Helpers to ensure reruns actually re-execute.  
  - Details: [`docs/force-recompute.md`](docs/force-recompute.md)

---

## Submit, progress, and fetch outputs (optional online mode)

- **Submit + wait + fetch**: Send to ComfyUI, wait for completion, fetch registered output images/files.  
  - Details: [`docs/submit-and-images.md`](docs/submit-and-images.md)

- **Live progress events (WebSocket)**: Hook render events with callbacks for real-time progress control/logging.  
  - Details: [`docs/progress-events.md`](docs/progress-events.md)

- **Serverless execution (`.execute`)**: Run a `Flow` / `ApiFlow` end-to-end without running the ComfyUI HTTP server (in-process ComfyUI node execution).  
  - Details: [`docs/execute.md`](docs/execute.md)

- **Output saving patterns (full control)**: Save outputs with directory mode, patterns like `frame.###.png` / `%04d`, `{src_frame}` token templates, and `index_offset`.  
  - Details: [`docs/submit-and-images.md`](docs/submit-and-images.md)

---

## Service-ready ergonomics

- **Structured errors + partial results**: Convert workflows with categorized errors/warnings (API-friendly).  
  - Details: [`docs/error-handling.md`](docs/error-handling.md)

- **FastAPI integration patterns**: Example service patterns for conversion/submission.  
  - Details: [`docs/fastapi.md`](docs/fastapi.md)

---

## Minimal deps, explicit network

- **Stdlib-only by default**: Optional Pillow / ImageMagick / ffmpeg integrations are opt-in.  
  - Details: [`docs/submit-and-images.md`](docs/submit-and-images.md)

- **Network is explicit**: No surprise server calls unless you opt into online conversion/submission.  
  - Details: [`docs/node-info-and-env.md`](docs/node-info-and-env.md), [`docs/submit-and-images.md`](docs/submit-and-images.md)

