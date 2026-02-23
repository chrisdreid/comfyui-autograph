# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.3] - 2026-02-23

### Fixed
- `NodeInfo('modules')` now returns all available nodes (previously only ~64 core nodes; now matches server's `/object_info` endpoint)
  - Added `_ensure_extra_nodes_loaded()` — lazily calls ComfyUI's `init_extra_nodes()` when `NODE_CLASS_MAPPINGS` is under-populated
  - Added `_fix_comfyui_imports()` — fixes `utils` package shadowing caused by `comfy.utils` poisoning Python's import resolution
  - Added `_ensure_promptserver_instance()` — provides a lightweight `PromptServer.instance` stub so custom nodes that access it at import time don't crash
  - Background threads spawned by custom nodes during init (e.g. ComfyUI-Manager's registry fetch) are suppressed

### Changed
- Bumped version from 1.3.2 to 1.3.3

---

## [1.3.2] - 2026-02-21

### Added
- `FlowNodeProxy.bypass` / `NodeRef.bypass` property — `node.bypass = True` sets LiteGraph mode 4 (bypassed), `False` resets to mode 0 (normal)
- Converter skips bypassed nodes (mode 2 = muted, mode 4 = bypassed) during workspace → API conversion, matching ComfyUI GUI behavior
- Comprehensive offline test suite: **154 tests** across 15 stages covering `Flow`, `FlowNodeProxy`/`NodeRef`, `NodeSet`/`FlowNodeGroup`, `WidgetValue`, `ApiFlow`+`NodeProxy`, `NodeInfo`, `DictView`/`ListView`, and `Workflow` factory
- `bypass_types` fixture support for controlling which node types get bypassed in test submissions

### Changed
- Bumped version from 1.3.1 to 1.3.2
- Test suite uses capability-based assertions (`MutableMapping`, `hasattr`) for resilience across model layers

---

## [1.3.1] - 2026-02-20

### Fixed
- `api_mapping()` now uses `flow.unwrap()` (returns underlying legacy dict) before `deepcopy`, fixing a regression where the flowtree `Flow` wrapper was being deepcopied incorrectly
- Metadata passthrough during `Flow.convert()` preserved correctly

### Changed
- Bumped version from 1.3.0 to 1.3.1

---

## [1.3.0] - 2026-02-20

### Added
- `WidgetValue` transparent wrapper — widget attributes now carry `.choices()`, `.tooltip()`, and `.spec()` methods while still comparing/hashing as raw values (`node.seed == 200` works)
- `AUTOFLOW_COMFYUI_SERVER_URL` env var auto-fallback — `Flow` auto-fetches `node_info` from this URL when no explicit source is set
- `UserWarning` emitted when `Flow` is created without `node_info`, guiding users to set the env var or pass it explicitly
- `NodeRef.__repr__` shows clean path-keyed widget dict: `{'nodes.KSampler[0]': {'seed': 200, 'steps': 20}}`
- `NodeRef.__dir__` filtered to show only widgets + useful methods (hides raw JSON noise)
- Constructor-style `__repr__` on `Flow`, `ApiFlow`, `NodeInfo` — shows class name + inner structure: `Flow(nodes={...}, links=10)`, `ApiFlow({...})`, `NodeInfo(count=N, types=[...])`
- `ApiFlow.items()` / `.keys()` / `.values()` — dict-like iteration over `{Type[i]: widget_dict}` pairs
- `FlowTreeNodesView.items()` / `.keys()` / `.values()` — dict-like iteration over `{nodes.Type[i]: widget_dict}` pairs
- Curated `__dir__` on `ApiFlow`, `NodeSet`, `FlowTreeNodesView`, and `WidgetValue` — tab completion shows only user-facing attributes (node types, widgets, methods)

### Changed
- **Renamed `object_info` → `node_info` throughout** (breaking: class `ObjectInfo` → `NodeInfo`, env var `AUTOFLOW_OBJECT_INFO_SOURCE` → `AUTOFLOW_NODE_INFO_SOURCE`, CLI `--object-info-path` → `--node-info-path`, `--download-object-info-path` → `--download-node-info-path`, doc file `object-info-and-env.md` → `node-info-and-env.md`)
- `FlowNodeProxy.__getattr__` wraps widget values in `WidgetValue` for schema introspection

---

## [1.2.0] - 2026-02-18

### Added
- `Workflow(...)` — unified entry point that loads workspace *or* API payload, auto-converts, and optionally submits
- `NodeInfo.fetch(...)` / `NodeInfo.from_comfyui_modules()` — first-class node_info helpers with env-driven auto-resolution
- `AUTOFLOW_NODE_INFO_SOURCE` env var (`fetch` / `modules` / `server` / `file`) for automatic node_info resolution
- `.execute()` serverless rendering — run ComfyUI workflows in-process via `NODE_CLASS_MAPPINGS` (no HTTP server required)
- `comfyui_available()` public helper for environment detection
- `Dag` / `.dag()` graph helpers (stdlib-only toposort, `.to_mermaid()`, `.to_dot()`)
- `ProgressTracker` enriching WebSocket events with `node_current`, `nodes_completed`, `nodes_progress`, timing metrics
- `ProgressPrinter` improvements: `event_types=[...]` filtering, `raw=True` debug output, custom `format="..."` strings
- WebSocket idle timeout (default 5 s, configurable via `AUTOFLOW_WS_IDLE_TIMEOUT_S`) with `/history` fallback
- Cached-node fast path: skip WebSocket when all nodes are cached; DAG-based inference for missing events
- Optional `/queue` polling (`poll_queue=True`) to report queue state while waiting
- `SubmissionResult.save()` — one-call output saving (images + files)
- `SubmissionResult.fetch_files(output_types=...)` — registered-output file fetching via `/history` + `/view`
- Default-on metadata patching from `workflow["extra"]` into ApiFlow nodes (with per-key operators and opt-out)
- `force_recompute()` cache-busting helper
- `map_strings()` / `map_paths()` declarative mapping helpers
- `chain_callbacks()` for composing progress callbacks
- Subgraph flattening for nested `definitions.subgraphs`
- CLI: `--submit` mode with `--save-files`, `--output-types`, `--filepattern`, `--index-offset`, `--no-wait`, `--progress-raw`
- `FEATURES.md` quick-glance page with production-focused hooks
- `CHANGELOG.md` (this file)

### Changed
- Default model layer is now `flowtree` (navigation-first wrappers, promoted from experimental)
- Conversion node inclusion now driven by `node_info` membership (no hardcoded UI-node skip list)
- Unified output saving APIs around `output_path` with shared filename templating (`{src_frame}`, `###`, `%0Nd`)
- `ErrorSeverity` / `ErrorCategory` use `str` mixin for JSON compatibility
- `api.py` public API cleaned: private `_`-prefixed names replaced with public equivalents
- Refined DAG toposort API: `Dag.toposort()` returns a `Dag`, added `dag.nodes.toposort()` and `dag.entities.toposort()`

### Removed
- `api_legacy.py` compatibility shim (merged into modular split)

---

## [1.1.0] - 2026-02-14

### Added
- Polymorphic `.load()` on `Flow`, `ApiFlow`, `NodeInfo`, and `Workflow` — accepts `dict`, `bytes`, JSON string, file path, or ComfyUI PNG
- PNG metadata extraction (stdlib-only, no Pillow) — recover workflows from any ComfyUI-exported PNG
- OOP node access with mutable `DictView` drilling proxies:
  - `api.ksampler[0].seed = 42` (case-insensitive, indexable, iterable)
  - `flow.nodes.ksampler[0].type` / `flow.extra.ds.scale`
  - `obj.KSampler.input.required.seed` / path syntax `obj["KSampler/input/required/seed"]`
- Schema-aware dot access on Flow nodes via attached `node_info` (drill `widgets_values` by name)
- `.find(...)` helpers with deep key/value filters, regex support, and `depth=` control
- `.attrs()` introspection on node proxies (raw keys + schema-derived widget names)
- `ListView` for attribute drilling into single-item list-of-dicts
- `.path()` / `.address()` on proxy objects for node addressing
- `api_mapping()` callback-first mapping with rich context (upstream links, `node_info` param types, typed overwrites)
- Subgraph-aware conversion (inline/flatten `definitions.subgraphs`, nested supported)
- CLI: `--submit` with progress output and optional `--save-images` / `--filepattern`
- Centralized env-driven defaults (args → env → default) for timeouts, polling, depth, client_id

### Changed
- Bumped version to `1.1.0`
- Standardized public API argument names: `server_url`, `output_path`, `include_bytes` (breaking, no backward compat)
- CLI flags standardized: `--input-path`, `--output-path`, `--node-info-path` (short flags unchanged)
- Removed implicit localhost defaults for server operations (must pass `server_url=` or set env)
- Removed legacy `FLO2API_*` env var fallback
- Terminology change: "API prompt" → "API payload" throughout codebase and docs

### Removed
- Top-level `submit`, `get_images`, `node_info` free-function exports (use object methods instead)
- Legacy short/alias arguments (`obj=`, `server=`, `meta=`, `output=`)

---

## [1.0.0] - 2026-02-10

### Added
- Initial public release
- Strict `Flow` (workspace `workflow.json`) and `ApiFlow` (API payload `workflow-api.json`) dict-subclass types
- `Workflow` smart-wrapper factory: auto-detects format, converts workspace → `ApiFlow` by default
- Workspace → API payload conversion with structured error reporting (`ConvertResult`, `ConversionError`)
- Offline conversion with saved `node_info.json`
- Online conversion via ComfyUI server `/object_info`
- `ApiFlow.submit()` to send API payloads and fetch output images
- Stdlib WebSocket progress callbacks via `submit(wait=True, on_event=...)`
- `ProgressPrinter` and `chain_callbacks()` helpers
- `map_strings()` / `map_paths()` for workflow templating (literal + regex replacements)
- `force_recompute()` for opt-in cache avoidance
- Callback-first mapping with workflow-level `extra` passthrough and typed overwrites
- CLI entrypoint (`python -m autoflow`)
- Comprehensive documentation: `README.md`, `docs/advanced.md`, `docs/load-vs-convert.md`, `docs/submit-and-images.md`, `docs/node-info-and-env.md`, and more
- MIT License

---

## [0.x] - 2026-02-05

### Added
- Project inception as `flow2api`
- Core conversion engine (workspace → API payload)
- HTTP helpers (`_http_json`, server URL resolution)
- Initial README, examples, and example scripts
