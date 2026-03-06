# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.0] - 2026-03-05

### Added
- **Builder API** — `Flow.create()`, `flow.add_node()`, `flow.remove_node()` for programmatic workflow construction
- **Slot Discovery** — `node.inputs` / `node.outputs` return dict-like `InputsView`/`OutputsView` with tab completion, `.status()`, `.keys()`, `.pop()`, etc.
- **Connection Operators** — `>>` (push), `<<` (pull), `.connect()`, `.disconnect()` with `None` for disconnection and list fan-out
- **Attr ↔ Input Promotion** — `to_input()` / `to_attr()` on both `NodeRef` and `WidgetValue`, auto-promotion via `.inputs.attr_name` access, auto-demotion on disconnect
- **Node GUI properties** — `bypass`, `mute`, `mode`, `color`, `bgcolor`, `title`, `collapsed`, `pos`, `size` on `NodeRef` for full ComfyUI frontend parity
- **Groups** — `flow.add_group()`, `flow.remove_group()`, `flow.groups` with auto-bounding from node positions
- **Canvas viewport** — `flow.canvas_scale`, `flow.canvas_offset` for zoom/pan state
- **Extra metadata** — `flow.extra` dict access for frontend version, extensions, etc.
- **Execution order** — `flow.compute_order()` computes and sets topological order on all nodes
- **Node removal** — `node.remove()` / `node.delete()` convenience methods
- **Workflow embedding** — `Flow.submit(embed_workflow=True)` auto-embeds workspace JSON in PNG metadata
- **`WidgetValue.to_input()` / `.to_attr()`** — promote/demote directly from attribute access: `node.width.to_input()`
- Tests 9.17–9.31 in `phase_09_builder.py` (31 total Phase 9 tests)
- **Auto-save path** — `flow.save()` with no args re-saves to the last loaded/saved path; `flow._filepath` tracks it
- **REPL-friendly status()** — `node.inputs.status()` and `node.outputs.status()` display nicely in REPL without `print()`

### Fixed
- **Widget values scramble on `__setattr__`** — `FlowNodeProxy.__setattr__` now updates values in-place in the original `widgets_values` array, preserving frontend-only values like `control_after_generate` that aren't in server `object_info`
- **`submit(wait=True)` hangs after job completes** — history poll loop stopped re-fetching once ComfyUI returned `{}` because `if history is None` was always False; now re-polls until `prompt_id` appears in history
- **`embed_workflow` serialization** — uses `to_json()` path instead of `dict()` + `json.dumps(default=str)`, avoiding stringification of custom types

### Changed
- `NodeRef.__setattr__` routes Python property descriptors through the descriptor protocol before falling through to the proxy
- `NodeRef.__dir__` includes all GUI property names for tab completion
- Bumped version from 1.4.1 to 1.5.0

---

## [1.4.1] - 2026-03-02

### Fixed
- **`AUTOFLOW_COMFYUI_SERVER_URL` ignored by `NodeInfo('fetch')`** — `NodeInfo.__init__` hardcoded `allow_env=False` when an explicit input was provided, preventing the env var from being read. Changed to `allow_env=bool(allow_env)`.
- **Graceful error messages** — replaced chained `ModuleNotFoundError` tracebacks (`from e`) with clean single-raise errors (`from None`) and actionable guidance listing all resolution options (env var, `server_url=`, file path, ComfyUI directory). Affected locations:
  - `node_info_from_comfyui_modules()` in `convert.py`
  - `get_widget_input_names()` in `convert.py`
  - Resolver `fetch→modules` fallback in `convert.py` — now catches the modules failure and raises a combined error explaining both server and modules failed
  - `NodeInfo.__init__` fallback error in `flowtree.py` — updated stale message to list all available options
- **Test CLI reads `AUTOFLOW_COMFYUI_SERVER_URL`** — `detect_environment()` in `main.py` now falls back to the env var when `--server-url` is not provided

### Added
- Test `t_2_28` in `phase_02_nodeinfo.py` — covers the exact failing scenario: `NodeInfo('fetch')` with `AUTOFLOW_COMFYUI_SERVER_URL` env var set and no explicit `server_url`

### Removed
- **Legacy `stage_*.py` test files** (27 files) — all test coverage is now in the 8 `phase_*.py` files
- `--legacy` and `--stage` CLI flags from `main.py`
- `_discover_stages()` function from `main.py`

---

## [1.4.0] - 2026-02-23

### Breaking Changes
- **`Workflow` class deprecated** — `ApiFlow` is now the single entry point for loading both API payloads *and* workspace `workflow.json` files. `Workflow` remains as a thin compatibility alias that emits a `DeprecationWarning`.
- `ApiFlow(x)` now auto-detects workspace-format workflows (with `nodes`/`links`) and converts them in-place — no need to call `Flow.convert()` first.

### Added
- **Auto-convert in `ApiFlow.__init__`** — pass a workspace `workflow.json` directly and it's converted to API format automatically (uses `node_info` from arg, env var, or server)
- **README shields** — PyPI version, Python versions, MIT license, GitHub stars, issues, and download count badges
- `needs_comfyui_runtime` attribute on doc test `Example` dataclass — enables per-block timeout tuning for serverless execution tests
- `_looks_incomplete_snippet()` heuristic in doc test harness — skips one-liner illustrative prose blocks (e.g. `res = flow.execute()` appearing in explanatory sections)

### Fixed
- **Source metadata tracking** — `ApiFlow.__init__` auto-convert path now correctly sets `source` to `converted_from(file:...)` instead of raw `file:...`, matching the behavior of `Flow.convert()` (fixes tests 2.24/2.25)
- Doc test timeouts for serverless `.execute()` blocks — increased from 15 s to 120 s (these load models + run inference)
- Conditional ComfyUI runtime doc blocks — blocks requiring `comfy.*` modules now attempt the import and only skip if unavailable, allowing them to run in full ComfyUI environments

### Changed
- **Documentation overhaul:**
  - `node-info-and-env.md` rewritten with "All Resolution Methods" comparison section (mermaid diagram, table, 4 step-by-step examples)
  - `README.md` updated: all `Workflow` references → `ApiFlow`, mermaid diagrams and code examples refreshed
  - 5 feature docs simplified: removed explicit `node_info="node_info.json"` from `force-recompute.md`, `map-strings-and-paths.md`, `mapping.md`, `progress-events.md`, `submit-and-images.md` (rely on default server-based resolution)
  - `convert.md` and `load-vs-convert.md` kept as-is (intentionally teach different resolution methods)
- Bumped version from 1.3.3 → 1.4.0

### Deprecated
- `Workflow` class — use `ApiFlow` directly. `Workflow(...)` still works but emits `DeprecationWarning` and will be removed in a future release.

---

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
