# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.3.0] - 2026-08-16

### Fixed
- **Subgraph promoted widget values were dropped** — flattening ignored the instance node's `widgets_values`, so literal values set on promoted widgets (e.g. a promoted `switch` boolean or `duration` float) never reached the inner nodes, which either kept their stale internal values or lost the input entirely. Instance values are now mapped onto widget-promoted subgraph inputs (in subgraph-input order) and carried onto the inner inputs they promote; externally linked promoted inputs still follow the link.
- **V3 widget classification** — inputs declared as `["BOOLEAN", {}]` / `["INT", {}]` / etc. with an empty options dict (common in V3 node schemas, e.g. `PrimitiveBoolean.value`, `ComfySwitchNode.switch`) were misclassified as connection-only and dropped from the API prompt, producing `required_input_missing` node_errors that ComfyUI reports inside an HTTP 200 while silently skipping the dependent output nodes. Widget-vs-connection classification now follows the type name (INT/FLOAT/BOOLEAN/STRING/COMBO and dynamic combos are widgets; `COMFY_MATCHTYPE_V3` and other plain types are connections).
- **`COMFY_DYNAMICCOMBO_V3` support** — dynamic combo widgets now convert as the selected option **key** (e.g. `"scale by multiplier"` instead of a misaligned neighboring value) and emit the selected option's sub-widget inputs as dotted names (e.g. `resize_type.multiplier`), which previously were dropped and shifted every subsequent widget value.
- **`node_errors` surfaced on submit** — a `/prompt` response carrying non-empty `node_errors` now emits a `RuntimeWarning` + log warning listing the failing nodes and the dependent output nodes ComfyUI will skip, and `SubmissionResult.node_errors` exposes the details. Previously a prompt could half-render and still look like a clean success.

### Added
- **MCP server (optional)** — ships an opt-in Model Context Protocol server as a new `autograph.mcp` subpackage so any MCP-capable IDE (Claude Desktop, Claude Code, Cursor, VS Code, Continue, Zed) can drive ComfyUI through natural-language tool calls. Install with `pip install "comfyui-autograph[mcp]"` and run via the new `comfyui-autograph-mcp` console script (or `python -m autograph.mcp`).
- **29 MCP tools** for end-to-end back-end workflow editing:
  - **Server / introspection (4)**: `comfyui_status`, `list_node_types`, `describe_node_type`, `list_models`.
  - **Inspection / editing (4)**: `inspect_workflow`, `convert_workflow`, `validate_workflow`, `set_workflow_values` — read-only or session-aware.
  - **Builder API (9)**: `load_workflow`, `create_workflow`, `add_node`, `connect_nodes`, `disconnect_input`, `remove_node`, `merge_workflow`, `save_workflow`, `get_workflow`.
  - **Sessions (2)**: `list_sessions`, `close_session`.
  - **Library + sources (3)**: `list_workflow_sources`, `search_local_workflows`, `load_local_workflow`.
  - **Execution (4)**: `run_workflow`, `queue_workflow`, `get_history`, `interrupt`.
  - **Files / outputs (3)**: `upload_file`, `fetch_outputs`, `list_outputs`.
- **Stateful workflow sessions** with auto-checkpoint to `~/.comfyui-autograph/sessions/<workflow_id>.json` (overridable via `AUTOGRAPH_MCP_SESSION_DIR`) so work-in-progress survives IDE restarts. Editing tools mutate the live session by `workflow_id`; read-only tools also accept inline workflow JSON / file paths.
- **`merge_workflow` graft engine** — splice a workflow fragment (e.g. JSON the assistant found online) into the active session: renumbers node and link IDs, inserts, detects dangling slots, auto-wires unique-by-type matches against the existing graph, returns a structured report listing connections made plus suggestions for what still needs the LLM's attention. Recognizes "interposer" nodes (same slot type in and out, e.g. `LoraLoader` on `MODEL`) and emits a hint for slotting them into existing paths.
- **Local workflow library** — `search_local_workflows` and `load_local_workflow` browse `~/.comfyui-autograph/workflows/`, project-local `./.autograph-workflows/`, any colon-separated dirs in `AUTOGRAPH_MCP_LIBRARY_DIRS`, and a bundled `examples/workflows/starters/` set. Optional `*.metadata.json` sidecars carry tags, description, and source.
- **Curated online sources catalog** — `list_workflow_sources` returns a hand-picked list of public workflow URLs (ComfyUI examples on GitHub, Civitai, OpenArt, GitHub code search, ComfyUI-Manager). The MCP itself never scrapes; the assistant uses its own WebFetch on these.
- **Structured error feedback** — `run_workflow` and `queue_workflow` parse ComfyUI's `/prompt` 400 bodies and history `execution_error` messages into actionable `[{node_id, class_type, error_type, message, details?}]` arrays so the LLM can iterate without raw stack traces. Failures return `ok: false`.
- **Inline image return policy** — `run_workflow` and `fetch_outputs` return generated images inline as base64 up to per-image and per-call caps configurable via `AUTOGRAPH_MCP_MAX_INLINE_IMAGE_BYTES` / `AUTOGRAPH_MCP_MAX_INLINE_IMAGES`; oversized or numerous outputs fall back to file paths and `comfyui://outputs/...` resource URIs.
- **MCP resources** — `comfyui://node-info`, `comfyui://history/{prompt_id}`, `comfyui://outputs/{prompt_id}/{filename}` for browsing without burning tool calls.
- **Conversation prompt templates** — `text_to_image`, `diagnose_workflow`, and a new `vibe_build_workflow` end-to-end build template.
- IDE drop-in JSON snippets in [`examples/mcp/`](examples/mcp/) plus a [`docs/mcp.md`](docs/mcp.md) reference. The core `comfyui-autograph` package remains zero-dependency; only the `[mcp]` extra pulls in `mcp>=1.7.1` (which itself requires Python 3.10+).
- **`SubmissionResult.node_errors`** — new property exposing per-node validation failures from the `/prompt` response (empty dict when none), so callers can fail hard instead of trusting a half-rendered "success".

## [2.2.0] - 2026-05-06

### Added
- **Input file uploads** — added `autograph.upload_file()` for uploading image, audio, video, text, archive, model, or arbitrary files to ComfyUI's upload endpoint with friendly `accept` templates.
- **Flow and ApiFlow upload helpers** — added `Flow.upload_file()` and `ApiFlow.upload_file()` convenience methods that upload a single file and patch a workflow input value.
- **Directory uploads** — `upload_file()` accepts directories, filters by friendly templates such as `accept="image"` or `accept=["audio", "video"]`, and preserves relative subdirectories under the optional `subfolder` argument.
- **Upload result wrappers** — added `FileUploadResult` and `FileUploadResults` with `.path`, `.paths()`, `.kind`, and `.mime_type` helpers for values suitable for ComfyUI file inputs.
- **Image upload compatibility** — kept `upload_image()`, `Flow.upload_image()`, `ApiFlow.upload_image()`, `ImageUploadResult`, and `ImageUploadResults` as image-specific wrappers.
- Documentation examples for uploading input files before submission.

### Fixed
- **`LoadImage.image` conversion with stale node info** — upload-backed widgets such as `image_upload` now accept arbitrary string filenames instead of treating the server's current choices list as exhaustive. This prevents values like `"src.jpeg"` from becoming `None` during workflow conversion.

---

## [2.0.0] - 2026-03-06 — comfyui-autograph

### 🚀 Package Renamed: `comfyui-autoflow` → `comfyui-autograph`

This is the first release under the new name **comfyui-autograph** (v2.0.0).

#### Breaking Changes
- **Package name**: `comfyui-autoflow` → `comfyui-autograph`
- **Imports**: `import autoflow` → `import autograph`
- **CLI**: `autoflow` → `autograph` / `python -m autograph`
- **Environment variables**: all `AUTOFLOW_*` → `AUTOGRAPH_*`
  - `AUTOFLOW_COMFYUI_SERVER_URL` → `AUTOGRAPH_COMFYUI_SERVER_URL`
  - `AUTOFLOW_MODEL_LAYER` → `AUTOGRAPH_MODEL_LAYER`
  - `AUTOFLOW_NODEINFO_SOURCE` → `AUTOGRAPH_NODEINFO_SOURCE`
  - (and all others)
- **Internal attributes**: `_autoflow_origin` → `_autograph_origin`
- **Workflow metadata namespace**: `extra.autoflow` → `extra.autograph`

#### Migration
```bash
pip uninstall comfyui-autoflow
pip install comfyui-autograph
```
Then update your imports and environment variables.

---

## [1.5.1] - 2026-03-06 — FINAL RELEASE of comfyui-autoflow

### ⚠️ Deprecation Notice

This was the **final release** of `comfyui-autoflow`. The project continues as `comfyui-autograph` (v2.0.0+).

- `import autoflow` emits a `DeprecationWarning` directing users to `pip install comfyui-autograph`
- No code changes from v1.5.0 except the deprecation warning

---

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
- **`AUTOGRAPH_COMFYUI_SERVER_URL` ignored by `NodeInfo('fetch')`** — `NodeInfo.__init__` hardcoded `allow_env=False` when an explicit input was provided, preventing the env var from being read. Changed to `allow_env=bool(allow_env)`.
- **Graceful error messages** — replaced chained `ModuleNotFoundError` tracebacks (`from e`) with clean single-raise errors (`from None`) and actionable guidance listing all resolution options (env var, `server_url=`, file path, ComfyUI directory). Affected locations:
  - `node_info_from_comfyui_modules()` in `convert.py`
  - `get_widget_input_names()` in `convert.py`
  - Resolver `fetch→modules` fallback in `convert.py` — now catches the modules failure and raises a combined error explaining both server and modules failed
  - `NodeInfo.__init__` fallback error in `flowtree.py` — updated stale message to list all available options
- **Test CLI reads `AUTOGRAPH_COMFYUI_SERVER_URL`** — `detect_environment()` in `main.py` now falls back to the env var when `--server-url` is not provided

### Added
- Test `t_2_28` in `phase_02_nodeinfo.py` — covers the exact failing scenario: `NodeInfo('fetch')` with `AUTOGRAPH_COMFYUI_SERVER_URL` env var set and no explicit `server_url`

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
- `AUTOGRAPH_COMFYUI_SERVER_URL` env var auto-fallback — `Flow` auto-fetches `node_info` from this URL when no explicit source is set
- `UserWarning` emitted when `Flow` is created without `node_info`, guiding users to set the env var or pass it explicitly
- `NodeRef.__repr__` shows clean path-keyed widget dict: `{'nodes.KSampler[0]': {'seed': 200, 'steps': 20}}`
- `NodeRef.__dir__` filtered to show only widgets + useful methods (hides raw JSON noise)
- Constructor-style `__repr__` on `Flow`, `ApiFlow`, `NodeInfo` — shows class name + inner structure: `Flow(nodes={...}, links=10)`, `ApiFlow({...})`, `NodeInfo(count=N, types=[...])`
- `ApiFlow.items()` / `.keys()` / `.values()` — dict-like iteration over `{Type[i]: widget_dict}` pairs
- `FlowTreeNodesView.items()` / `.keys()` / `.values()` — dict-like iteration over `{nodes.Type[i]: widget_dict}` pairs
- Curated `__dir__` on `ApiFlow`, `NodeSet`, `FlowTreeNodesView`, and `WidgetValue` — tab completion shows only user-facing attributes (node types, widgets, methods)

### Changed
- **Renamed `object_info` → `node_info` throughout** (breaking: class `ObjectInfo` → `NodeInfo`, env var `AUTOGRAPH_OBJECT_INFO_SOURCE` → `AUTOGRAPH_NODE_INFO_SOURCE`, CLI `--object-info-path` → `--node-info-path`, `--download-object-info-path` → `--download-node-info-path`, doc file `object-info-and-env.md` → `node-info-and-env.md`)
- `FlowNodeProxy.__getattr__` wraps widget values in `WidgetValue` for schema introspection

---

## [1.2.0] - 2026-02-18

### Added
- `Workflow(...)` — unified entry point that loads workspace *or* API payload, auto-converts, and optionally submits
- `NodeInfo.fetch(...)` / `NodeInfo.from_comfyui_modules()` — first-class node_info helpers with env-driven auto-resolution
- `AUTOGRAPH_NODE_INFO_SOURCE` env var (`fetch` / `modules` / `server` / `file`) for automatic node_info resolution
- `.execute()` serverless rendering — run ComfyUI workflows in-process via `NODE_CLASS_MAPPINGS` (no HTTP server required)
- `comfyui_available()` public helper for environment detection
- `Dag` / `.dag()` graph helpers (stdlib-only toposort, `.to_mermaid()`, `.to_dot()`)
- `ProgressTracker` enriching WebSocket events with `node_current`, `nodes_completed`, `nodes_progress`, timing metrics
- `ProgressPrinter` improvements: `event_types=[...]` filtering, `raw=True` debug output, custom `format="..."` strings
- WebSocket idle timeout (default 5 s, configurable via `AUTOGRAPH_WS_IDLE_TIMEOUT_S`) with `/history` fallback
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
- CLI entrypoint (`python -m autograph`)
- Comprehensive documentation: `README.md`, `docs/advanced.md`, `docs/load-vs-convert.md`, `docs/submit-and-images.md`, `docs/node-info-and-env.md`, and more
- MIT License

---

## [0.x] - 2026-02-05

### Added
- Project inception as `flow2api`
- Core conversion engine (workspace → API payload)
- HTTP helpers (`_http_json`, server URL resolution)
- Initial README, examples, and example scripts
