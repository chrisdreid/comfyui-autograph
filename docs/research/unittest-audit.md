# Test Coverage Audit: Docs Features vs main.py vs test_*.py

## Methodology
Cross-referenced every documented API feature from `docs/*.md` and [FEATURES.md](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/FEATURES.md) against:
- **main.py** stages 0–15 (154 tests)
- **test_*.py** files (20 files, ~91 tests)
- **docs-test.py** harness (runs fenced code blocks from docs in a sandbox)

---

## Feature Coverage Matrix

### 🟢 Well Covered in main.py

| Feature | Doc | main.py stage | Notes |
|---------|-----|---------------|-------|
| Import + version check | — | 0 (5 tests) | |
| `Flow.load()` (path, dict, JSON, bytes, Path) | load-vs-convert.md | 1 (5 tests) | |
| Dot-access: `flow.nodes.KSampler` | load-vs-convert.md | 1.7, 1.8 | |
| Widget dot-access: `api.KSampler.seed` | advanced.md | 1.9, 1.11 | |
| [to_json()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/models.py#1756-1758) + round-trip | — | 1.16, 1.17 | |
| [save()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/flowtree.py#200-211) → reload | — | 1.18 | |
| Tab completion: [dir(flow.nodes)](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/models.py#1045-1058), [dir(api.KSampler)](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/models.py#1045-1058) | advanced.md | 1.20, 1.21 | |
| [Workflow()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/models.py#2025-2117) → ApiFlow | convert.md | 2.1 | |
| MarkdownNote stripping | convert.md | 2.2 | |
| ApiFlow dot-access + path access | load-vs-convert.md | 2.3, 2.4 | |
| [convert_with_errors()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/models.py#1936-1978) | error-handling.md | 2.6 | Basic only |
| [_meta](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/models.py#459-462) access + set + survive to_json | mapping.md | 2.7–2.9 | |
| WidgetValue `.choices()`, `.tooltip()`, `.spec()` | advanced.md | 2.14–2.16 | |
| [find(type=)](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/flowtree.py#1146-1150) exact + case-insensitive | advanced.md | 3.1, 3.2 | |
| [find(type=re.compile(...))](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/flowtree.py#1146-1150) regex | advanced.md | 3.3 | |
| [find(title=)](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/flowtree.py#1146-1150) + regex | advanced.md | 3.4, 3.5 | |
| [find(key=value)](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/flowtree.py#1146-1150) AND + OR | advanced.md | 3.6, 3.7 | |
| [find(node_id=)](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/flowtree.py#1146-1150) | advanced.md | 3.8 | |
| `.path()` / `.address()` | advanced.md | 3.9, 3.10 | |
| `api.find(class_type=)` + regex | advanced.md | 3.11, 3.12 | |
| `api.by_id()` | — | 3.13 | |
| [map_strings()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/map.py#144-203) literal | map-strings-and-paths.md | 4.1 | |
| [force_recompute()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/map.py#255-295) | force-recompute.md | 4.5 | |
| [api_mapping()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/map.py#325-463) callback + overwrite | mapping.md | 4.7, 4.8 | |
| Flow core API (MutableMapping, len, iter, etc.) | — | 8 (20 tests) | |
| FlowNodeProxy (attrs, bypass, widgets, type, id) | — | 9 (15 tests) | |
| FlowNodeGroup (iteration, broadcast, filter) | — | 10 (12 tests) | |
| WidgetValue (equality, hash, choices, tooltip, spec) | — | 11 (15 tests) | |
| ApiFlow + NodeProxy (bracket/dot, find, items/keys/values) | — | 12 (12 tests) | |
| NodeInfo (load, len, keys, find, getattr) | node-info-and-env.md | 13 (8 tests) | |
| DictView / ListView (drilling, set, conversion) | advanced.md | 14 (10 tests) | |
| Workflow factory (auto-detect, use_api flag) | load-vs-convert.md | 15 (4 tests) | |
| Bypass property | — | 9 (in FlowNodeProxy) | |
| Server submission | submit-and-images.md | 6 (server tests) | Requires `--server-url` |
| Fixture conversion + compare | — | 5 (fixture tests) | Requires `--fixtures-dir` |

---

### 🟡 In test_*.py but NOT in main.py

| Feature | Doc | test_*.py file | Tests |
|---------|-----|----------------|-------|
| DAG edges, deps, ancestors | advanced.md | test_dag.py | 3 |
| DAG `.to_dot()`, `.to_mermaid()`, `.toposort()` | advanced.md | test_dag.py | (within above) |
| DAG filters by node_info | advanced.md | test_dag.py | 1 |
| [__dir__](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/models.py#1045-1058) subprocess isolation (ApiFlow, NodeSet, NodeRef) | advanced.md | test_dir_and_widget_introspection.py | 18 |
| `.execute()` serverless (mock inprocess) | execute.md | test_execute_stageA.py | 3 |
| `Flow.submit()` wrapper (mock HTTP) | submit-and-images.md | test_flow_submit.py | 1 |
| Schema-aware drilling ([seed](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/examples/unittests/test_flow_drilling.py#22-26) requires node_info) | advanced.md | test_flow_drilling.py | 6 |
| [fetch_node_info()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/flowtree.py#449-451) (offline + error cases) | node-info-and-env.md | test_flow_drilling.py | 2 |
| Flowtree nav ops (bulk assign, paths, dictpaths) | — | test_flowtree_nav_ops.py | 5 |
| NodeInfo constructor in flowtree mode | node-info-and-env.md | test_flowtree_nodeinfo_init.py | 1 |
| NodeInfo `.source` passthrough | node-info-and-env.md | test_flowtree_nodeinfo_passthrough.py | 1 |
| Legacy model layer parity | node-info-and-env.md | test_legacy_parity_models.py | 6 |
| [map_strings()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/map.py#144-203) regex + rules + [map_paths()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/map.py#205-253) | map-strings-and-paths.md | test_map_helpers.py | 13 |
| MarkdownNote stripping (fixture-based) | convert.md | test_markdownnote_strip.py | 2 |
| `AUTOFLOW_MODEL_LAYER` env switch | node-info-and-env.md | test_model_layer_env_switch.py | 4 |
| NodeInfo resolver tokens + provenance | node-info-and-env.md | test_nodeinfo_resolver_tokens.py | 4 |
| `ImagesResult.save()` patterns (dir, ###, {src_frame}) | submit-and-images.md | test_save_formatting.py | 8 |
| `.source` metadata (flowtree + legacy) | — | test_source_metadata_*.py | 3 |
| Subgraph flattening (fixture-based) | convert.md | test_subgraphs.py | 1 |
| Terminal event emission on cached runs | progress-events.md | test_submit_terminal_event.py | 1 |
| WebSocket message parsing | progress-events.md | test_ws_events.py | 4 |
| [convert_with_errors()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/models.py#1936-1978) with invalid/partial scenarios | error-handling.md | test_error_handling.py | 5 (script) |

**Total: ~91 tests in test_*.py not in main.py**

---

### 🔴 NOT Tested Anywhere (Documented but Untested)

| Feature | Doc | Notes |
|---------|-----|-------|
| PNG loading (`Flow.load("image.png")`, `ApiFlow.load(png_bytes)`) | load-vs-convert.md | Docs show it but no offline unit test |
| [comfyui_available()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/convert.py#560-580) helper | execute.md | Only in docs-test preamble |
| `ProgressPrinter` (construction, format options, raw mode) | progress-events.md | Requires server |
| `chain_callbacks()` | progress-events.md | Requires server |
| `SubmissionResult.fetch_images()` / `.fetch_files()` | submit-and-images.md | Requires server |
| `SubmissionResult.save()` | submit-and-images.md | Requires server |
| `ImageResult.to_pixels()` | submit-and-images.md | Requires Pillow + images |
| ImageMagick / ffmpeg transcode paths | submit-and-images.md | Requires external binaries |
| `AUTOFLOW_NODE_INFO_SOURCE` env var effects | node-info-and-env.md | Partial in test_nodeinfo_resolver_tokens |
| `NodeInfo.fetch(server_url=)` | node-info-and-env.md | Requires server |
| `NodeInfo.from_comfyui_modules()` | node-info-and-env.md | Requires ComfyUI env |
| Metadata-driven targeting (`extra.autoflow.meta.nodes`) | mapping.md | Not tested |
| Key-prefix operators (`+key`, `*key`, `&key`, `-key`) in meta patches | mapping.md | Not tested |
| Per-node patch injection modes (merge, add, replace) | mapping.md | Not tested |
| `convert_callbacks` parameter on [Workflow()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/models.py#2025-2117) | mapping.md | Not tested |
| [api_mapping()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/map.py#325-463) with `win_to_linux` path pattern | mapping.md | Callback tested, not path patterns |
| `flow.nodes.to_dict()` / `.to_list()` | load-vs-convert.md | Not directly tested |
| `api.ksampler.to_dict()` / `.to_list()` | load-vs-convert.md | Not directly tested |
| Path syntax `api["ksampler/seed"]` write | load-vs-convert.md | Read tested in 2.4, write not tested |
| `flow.extra.ds.scale = 0.5` (DictView write propagation) | load-vs-convert.md | Partial in stage 14 |
| [map_strings()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/map.py#144-203) with [rules](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/examples/unittests/test_map_helpers.py#23-46) filtering | map-strings-and-paths.md | In test_map_helpers, not in main.py |

---

## docs-test.py Coverage

The [docs-test.py](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/examples/code/docs-test.py) harness parses fenced code blocks in all `docs/*.md` files and:
- **Python blocks**: [compile()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/map.py#53-60) (syntax check) always; optionally [exec()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/flowtree.py#463-502) in a sandbox
- **Bash blocks**: optionally runs safe CLI invocations
- **JSON blocks**: `json.loads()` and prints summary

It creates a sandbox with sample [workflow.json](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/examples/workflows/workflow.json), `workflow-api.json`, `node_info.json`, and a sample PNG. Blocks marked `# api` are candidates for execution. Blocks that reference [server_url](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/convert.py#582-601) or `localhost` are gated behind `--mode online`.

**Limitation**: docs-test.py validates syntax and basic execution but does **not assert outputs**. It won't catch regressions where code runs without error but produces wrong results.

---

## HTML Report Enhancement Ideas

Current [main.py](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/examples/unittests/main.py) HTML report shows:
- Stage name, pass/fail status, test count
- Expandable rows with test name + status + error message

**Missing**:
- Input data used for each test (e.g., the workflow dict, the find query)
- Output/return values (e.g., what [find()](file:///home/chris/WORK/ComfyUI/ComfyUI-autoflow/autoflow/flowtree.py#1146-1150) returned, what the converted ApiFlow looks like)
- Code snippet showing the test logic
- Diff or comparison when assertions fail

---

## Recommendations

### Priority 1: Absorb critical test_*.py tests into main.py
- **DAG** (test_dag.py) — 3 tests, offline, no dependencies
- **map_strings/map_paths** (test_map_helpers.py) — 13 tests, offline
- **save formatting** (test_save_formatting.py) — 8 tests, offline
- **subgraph flattening** (test_subgraphs.py) — 1 test, offline (needs fixture)
- **WebSocket events** (test_ws_events.py) — 4 tests, offline

### Priority 2: Add tests for undocumented gaps
- PNG loading (create a minimal PNG with embedded workflow metadata)
- `convert_callbacks` parameter
- Metadata-driven targeting (`extra.autoflow.meta.nodes`)
- Path syntax write (`api["ksampler/seed"] = 42`)
- `.to_dict()` / `.to_list()` conversions

### Priority 3: Enhance docs-test.py
- Add output assertions to doc code blocks (e.g., `# expected: ...` comments)
- Run as part of main.py suite (new stage)

### Priority 4: HTML report enhancements
- Capture input/output for each test call
- Show code snippet
- Show captured stdout/stderr
