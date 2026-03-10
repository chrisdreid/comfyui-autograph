## Execute (serverless ComfyUI node execution)

`execute()` runs ComfyUI nodes **in-process** (no ComfyUI HTTP server required).

If you want to run against a **running ComfyUI server**, use `submit(server_url=...)` instead (see [`submit-and-images.md`](submit-and-images.md)).

---

### Environment requirements (terminal / REPL)

You must run in an environment where ComfyUI’s Python modules are importable:

- Activate the **same venv/conda env** you use to run ComfyUI (so `torch`, `comfy`, `nodes`, custom nodes import correctly).
- Ensure **ComfyUI repo root** is on `PYTHONPATH` (or run with cwd at the repo root).
- Ensure **autograph** is importable (installed, or on `PYTHONPATH` for local development).

Example (Linux/macOS):

```bash
cd /path/to/ComfyUI
source venv/bin/activate  # or your conda/uv/poetry equivalent
export PYTHONPATH="/path/to/ComfyUI:/path/to/ComfyUI-autograph:${PYTHONPATH}"
```

Example (Windows PowerShell):

```powershell
cd C:\path\to\ComfyUI
.\venv\Scripts\Activate.ps1
$env:PYTHONPATH = "C:\path\to\ComfyUI;C:\path\to\ComfyUI-autograph;" + $env:PYTHONPATH
```

Quick environment check helper:

```python
from autograph import comfyui_available

comfyui_available(verify=False)  # quick path check
comfyui_available(verify=True)   # full import check
```

---

### Execute a workflow (workspace `Flow`)

```python
from autograph import Flow

flow = Flow("workflow.json", node_info="node_info.json")
res = flow.execute()
print(res.prompt_id)
```

---

### Execute a workflow (API payload `ApiFlow`)

```python
from autograph import ApiFlow

api = ApiFlow("workflow-api.json")
res = api.execute()
print(res.prompt_id)
```

---

### Save outputs after serverless execute

Serverless `execute()` does not fetch from `/view` (no server). Instead it resolves output refs to
paths using ComfyUI’s `folder_paths` (when available) and reads/copies directly from disk.

```python
from autograph import Flow

flow = Flow("workflow.json", node_info="node_info.json")
res = flow.execute()

# Save registered outputs using the same filename template features as submit() results:
paths = res.save(output_path="renders", filename="myfile.###.{ext}", index_offset=2001)
print(paths)
```

Notes:
- Only **registered outputs** (history-style refs) can be saved.
- For common nodes like `SaveImage`, autograph can also infer outputs as a fallback by scanning the output dir.

---

### Events (`on_event`)

`execute()` can stream best-effort events into a callback:

```python
from autograph import Flow, ProgressPrinter

flow = Flow("workflow.json", node_info="node_info.json")
flow.execute(on_event=ProgressPrinter())
```

Serverless execution does not guarantee ComfyUI-native `type="progress"` events. Many nodes print their
own progress to stdout (e.g. sampler step bars).

---

### Advanced knobs

- `init_extra_nodes`: default `False`. If `True`, calls ComfyUI’s optional `nodes.init_extra_nodes()` during init (may trigger background work depending on installed nodes).
- `cleanup`: default `True`. Best-effort cleanup after execution (unload models / empty GPU cache where available). Set `False` to keep models warm for repeated runs.

---

### How `execute()` works (high level)

ComfyUI “server mode” normally looks like:

- You POST an API prompt dict to `/prompt`
- ComfyUI queues + executes internally
- You get progress via `/ws`
- You read results from `/history/<prompt_id>` (and outputs via `/view`)

`execute()` intentionally skips **all** of that HTTP/websocket surface.

Instead, autograph executes the same workflow graph directly in Python by importing ComfyUI modules locally and calling node classes.

#### Core idea: run nodes directly (node runner)

When you call:

```python
res = flow.execute()
```

autograph:

- Imports ComfyUI locally (`comfy.*`, `nodes.NODE_CLASS_MAPPINGS`)
- Builds a dependency DAG from your `ApiFlow` prompt dict (node input refs like `["4", 0]`)
- Topologically sorts nodes so upstream deps run first
- For each node id in order:
  - Instantiates the ComfyUI node class from `nodes.NODE_CLASS_MAPPINGS[class_type]`
  - Resolves input references
  - Calls the node’s `FUNCTION` method with those resolved kwargs
  - Stores the returned outputs for downstream nodes

#### Why we have a `PromptServer.instance` stub

Many custom nodes were written assuming ComfyUI is running with its server layer and will reference things like:

- `server.PromptServer.instance`
- `.routes` (to register HTTP endpoints)
- runtime state like `.last_node_id` (used by preview/progress helpers)

In serverless mode we are **not** starting the server, but we still want those nodes to import.
autograph creates a minimal stub and assigns it to `server.PromptServer.instance` so import-time code doesn’t crash.

This stub does not implement real HTTP or websockets — it only exists to keep “server-assuming” custom nodes from failing at import/runtime.

#### How outputs are returned (history parity without `/history`)

In node-runner mode there is no `/history`, so autograph synthesizes a similar structure:

- Prefer node “UI” returns when available (some output nodes return `{"ui": {...}}` describing images/files)
- Fallback: for common cases like `SaveImage`, scan ComfyUI’s output directory for newly created files and convert those into history-style refs.

---

### Limitations (today)

- Some nodes depend on server-only subsystems. The stub keeps many nodes importable, but not all behaviors can be identical without running the full server stack.
- Output inference via disk scan is best-effort and may miss files if:
  - the output directory differs from what `folder_paths` reports
  - filenames don’t use `SaveImage.filename_prefix`
  - outputs are written but not registered / discoverable

If you need exact parity, use `submit(server_url=...)` against a running server.


