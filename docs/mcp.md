# MCP Server (`comfyui-autograph[mcp]`)

`comfyui-autograph` ships an optional **Model Context Protocol** server so any
MCP-capable IDE — Claude Desktop, Claude Code, Cursor, VS Code (Copilot,
Continue, Cline), Zed, and others — can drive ComfyUI through natural-language
tool calls.

It is built for **making and changing workflows**, not only running them: find
node types by what they do or by the types they take and give, build a graph
node by node or all at once, read it back by input name, edit it, check it
against the server before queueing, run it, and feed the result into the next
run, all without opening the GUI. Every edit is checked as it is made, and a
refused one says what would fit. Generated images come back inline so the
assistant can see them.

The MCP server lives inside the `autograph` package as a subpackage
(`autograph.mcp`), but it is **opt-in**: importing `autograph` does not import
it, and installing `comfyui-autograph` without the `[mcp]` extra still pulls in
zero runtime dependencies.

---

## Install

The `mcp` Python SDK requires **Python 3.10+**. The core `comfyui-autograph`
library still works on Python 3.7+; only the `[mcp]` extra has the higher floor.

```bash
# Zero-install via uv (no global pollution):
uvx --from "comfyui-autograph[mcp]" comfyui-autograph-mcp

# Or pip install:
pip install "comfyui-autograph[mcp]"
comfyui-autograph-mcp                     # console-script entry point
python -m autograph.mcp                   # equivalent
```

If `mcp` isn't installed, importing `autograph.mcp` raises a friendly
`ImportError` pointing back here.

---

## Configure your IDE

Drop-in JSON snippets ship in [`examples/mcp/`](../examples/mcp/) for Claude
Desktop, Claude Code, Cursor, VS Code (Copilot), Continue, and Zed. Every one
uses the canonical command:

```json
{
  "mcpServers": {
    "comfyui-autograph": {
      "command": "uvx",
      "args": ["--from", "comfyui-autograph[mcp]", "comfyui-autograph-mcp"],
      "env": { "AUTOGRAPH_COMFYUI_SERVER_URL": "http://127.0.0.1:8188" }
    }
  }
}
```

If `AUTOGRAPH_COMFYUI_SERVER_URL` is unset the MCP falls back to
`http://127.0.0.1:8188`, so a default local ComfyUI just works.

---

## How it works

### Sessions

The server keeps **workflow sessions**, each with a `workflow_id`:

1. **Open**: `load_template`, `load_workflow`, `create_workflow` or
   `load_local_workflow` returns a `workflow_id`.
2. **Read and edit**: `inspect_workflow`, `add_node`, `set_workflow_values`,
   `connect_nodes`, ... take that `workflow_id` and work on the live graph.
3. **Checkpoint**: every edit writes
   `~/.comfyui-autograph/sessions/<workflow_id>.json`, a workspace file that
   opens in ComfyUI. If the IDE restarts, the work is on disk.
4. **Run**: `run_workflow(workflow_id=...)` checks, queues, waits and returns
   the images.
5. **Save / close**: `save_workflow(workflow_id, path=...)`,
   `close_session(workflow_id, delete_checkpoint=True)`.

`inspect_workflow`, `validate_workflow`, `convert_workflow`, `run_workflow` and
`queue_workflow` also take an inline `workflow` (file path, JSON string or
object, in workspace or API format) when no session is needed.

### One notation for reading and writing

Inputs are read and written by name, the way ComfyUI's API format writes them:
a value, or a link `[node_id, output_index]`.

```json
{"id": "5", "class_type": "KSampler",
 "inputs": {"seed": 7, "steps": 20, "model": ["1", 0], "positive": ["2", 0]},
 "outputs": ["LATENT -> 6.samples"],
 "missing": ["negative:CONDITIONING", "latent_image:LATENT"]}
```

That is what `inspect_workflow` returns for a node, and the same `inputs`
object is what `add_node`, `set_workflow_values` and `create_workflow(nodes=...)`
accept. A node can be added fully wired in one call:

```
add_node(workflow_id, "KSampler",
         {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
          "latent_image": ["4", 0], "seed": 7, "steps": 20})
```

and a whole graph can be built in one call from an API-format prompt, which
is the format assistants know best. The node ids given are the ids in the
session:

```
create_workflow(nodes={
  "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
  "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["1", 1]}},
  ...})
```

The session holds a workspace graph, so what was built this way saves as a
file the ComfyUI editor opens, laid out left to right.

Workflows with **subgraphs** are flattened when they are loaded. Every node
then has one id, and it is the id ComfyUI uses in its errors and results, so an
error on "node 507" is a node the assistant can see and edit.

### The node list

`list_node_types`, `describe_node_type`, `validate_workflow` and the editing
tools all work from the server's node list (`/object_info`). It is fetched
once, kept for five minutes, and re-read after an upload or when a value is
missing from a list that may have changed. A copy is saved to
`~/.comfyui-autograph/node_info_cache.json`; when ComfyUI cannot be reached
that copy is used (results say `from_cache`), so a workflow can still be
built and checked offline.

---

## Tools

### Server and discovery

| Tool | What it does |
| --- | --- |
| `comfyui_status(refresh?)` | Reachable? Version, devices and free memory, queue depth, number of node types. |
| `list_node_types(query?, category?, input_type?, output_type?, limit=20, offset?)` | Search node types by words (class name, display name, search aliases, category, description) and by what they take or give: `output_type="CONDITIONING"` finds what can feed a sampler's `positive`. Each result lists its connection inputs and output types. |
| `describe_node_type(class_type, max_options=25)` | One node type or a list of them: every input with type, widget or connection, default, range and choices; every output with its index. Unknown names return the closest matches. |
| `list_input_options(class_type, input_name, query?, limit=50, offset?)` | The choices of one dropdown (checkpoints, samplers, uploaded images), searchable. |
| `list_models(folder?, query?, limit=100)` | Model folders, or the files in one. |

### Getting a workflow

| Tool | What it does |
| --- | --- |
| `search_templates(query?, media_type?, local_only?, limit=10)` | Search the workflows that ship with ComfyUI (served by the server itself). Results name the models a template needs and their download size, and flag templates that call paid partner APIs. |
| `load_template(name, label?)` | Open a template in a new session. Says which nodes take its input files and give its outputs, and whether it is ready to run or what is missing (model files, custom nodes). |
| `load_workflow(source, label?)` | Open a file path, JSON string or object: workspace or API format, or a PNG saved by ComfyUI with the workflow embedded. |
| `create_workflow(nodes?, starter?, label?)` | New session: empty, from a library starter, or a whole graph from an API-format `nodes` object. |
| `search_local_workflows(query?, tags?, limit=20)` | Search the local library (bundled starters + `~/.comfyui-autograph/workflows` + project-local + `AUTOGRAPH_MCP_LIBRARY_DIRS`). |
| `load_local_workflow(name, label?)` | Open a library workflow in a new session. |
| `list_workflow_sources()` | Websites where workflows are published. Nothing is fetched by the server; the assistant fetches a JSON itself and passes it to `load_workflow` or `merge_workflow`. |

### Reading and checking

| Tool | What it does |
| --- | --- |
| `inspect_workflow(workflow_id? \| workflow?, node_ids?, class_type?, brief?)` | Every node with its inputs by name (values and links), outputs and what they feed, missing required connections, title, bypassed/muted. `brief` leaves out widget values; `node_ids` / `class_type` narrow it down. |
| `validate_workflow(workflow_id? \| workflow?)` | Pre-flight against the server. See [Validation](#validation). |
| `convert_workflow(workflow_id? \| workflow?)` | The API-format prompt, with the same errors and warnings. |
| `get_workflow(workflow_id, format="workspace"\|"api")` | The full JSON. |

### Editing (session only)

| Tool | What it does |
| --- | --- |
| `add_node(workflow_id, class_type, inputs?, title?)` | Add a node with widget values and links in one call. Refused, with the valid names and ranges, if an input does not exist, a value has the wrong type or is out of range, or a link's types do not fit. |
| `set_workflow_values(updates, workflow_id? \| workflow?)` | Change inputs of existing nodes selected by `node_id`, `class_type` or `title`: values, links, `null` to disconnect, `set_title` to rename. Each change is checked and reported separately. |
| `connect_nodes(workflow_id, from_node, to_node, to_input, from_output?)` | Link two nodes. Type-checked; with no `from_output`, the one output that fits is used. |
| `disconnect_input(workflow_id, node_id, input_name)` | Remove the link feeding one input. |
| `remove_node(workflow_id, node_id)` | Delete one node or a list, with their links; reports the inputs left unconnected. |
| `set_node_mode(workflow_id, node_ids, mode)` | `bypassed` (skipped, input passes through), `muted`, or `active`. |
| `merge_workflow(workflow_id, fragment, auto_connect=True)` | Graft another workflow (workspace or API format) into the session. |
| `save_workflow(workflow_id, path?, format="workspace"\|"api")` | Write to disk. |
| `list_sessions()` / `close_session(workflow_id, delete_checkpoint=False)` | Session housekeeping. |

### Running

| Tool | What it does |
| --- | --- |
| `run_workflow(workflow_id? \| workflow?, wait=True, wait_timeout?, fetch_outputs=True, save_to?, inline_images=True, max_inline?, skip_validation?)` | Validate, queue, wait, return outputs. See [Running](#running-and-long-jobs). |
| `queue_workflow(workflow_id? \| workflow?, skip_validation?)` | Validate and queue; returns the `prompt_id`. |
| `wait_for_job(prompt_id, timeout?, ...)` | Wait for a queued or running job and return what `run_workflow` returns. |
| `get_job_status(prompt_id?)` | `pending` (with queue position), `running`, `completed` (with outputs), `failed` (with the node and error), `cancelled`. Without an id: the queue and the most recent jobs. |
| `cancel_job(prompt_id)` | Remove a waiting job from the queue, or interrupt it if it is running. |
| `interrupt()` | Stop whatever is running now. |
| `get_history(prompt_id?, limit=10)` | Recent jobs, newest first, with status and output files. |
| `free_memory(unload_models=True)` | Have ComfyUI unload models and free memory. |
| `get_logs(lines=60, query?)` | The tail of the ComfyUI server log. |

### Files and outputs

| Tool | What it does |
| --- | --- |
| `upload_file(local_path, accept?, subfolder?, overwrite=False)` | Send files to ComfyUI's input folder. Each has `as_input`, the value for a loader node. `accept` takes kinds (`image`/`audio`/`video`/`text`/`archive`/`model`), MIME patterns, exact MIME types, or extensions. |
| `fetch_outputs(prompt_id, save_to?, kinds?, inline_images=True, max_inline?)` | Download the outputs of a finished job. |
| `list_outputs(prompt_id)` | List them without downloading. Each file has `as_input` and a `comfyui://` resource URI. |

Every tool carries MCP annotations (`readOnlyHint`, `destructiveHint`,
`idempotentHint`, `openWorldHint`), so a client can let searches and
inspections through and ask before a file is overwritten or a job is cancelled.

---

## Validation

`validate_workflow` checks what ComfyUI checks when a job is queued, before
queueing it, and says what to do about each problem. It was compared with
ComfyUI 0.45's own validator on the 678 templates that ship with it: the same
verdict on every one.

| `code` | Meaning | Comes with |
| --- | --- | --- |
| `unknown_node_type` | The server has no such node type. | `closest` names |
| `missing_input` | A required input has nothing connected. | `candidates`: outputs in the graph that fit, as links |
| `type_mismatch` | A link joins types that do not connect. | `expected`, `got`, `candidates` |
| `bad_output_index` / `dangling_link` | A link to an output or node that does not exist. | the node's `outputs` |
| `value_not_in_list` / `no_options` | A value that is not among a dropdown's choices: usually a model or input file that is not installed. | `closest`, `options_total` |
| `out_of_range` / `wrong_type` | A number outside `min`/`max`, or text where a number belongs. | `min`, `max` |
| `value_on_connection` | A value where only a link will do. | `candidates` |
| `no_output_node` | Nothing saves or previews a result. | |
| `unused_nodes` (warning) | Nodes that feed no output and will not run. | `node_ids` |
| `unknown_input` (warning) | An input name the node does not have; ComfyUI ignores it. | `closest` |

Problems on nodes that feed no output are warnings, as they do not stop the
rest. Links follow the ComfyUI editor's rule: equal types in any case, comma
lists that share a member, wildcards.

The editing tools apply the same checks as they go, so most mistakes are
refused at the call that makes them, with the valid names, the range, or the
outputs that would fit.

---

## Running and long jobs

`run_workflow` does four things: checks the workflow, queues it, waits, and
fetches the outputs.

* **Not valid**: nothing is queued. The result has `ok: false`,
  `stage: "validation"` and the same `errors` as `validate_workflow`. Problems
  with a value alone (a file missing from a list, a number out of range) are
  left for ComfyUI to judge, since a node may check its own inputs; if ComfyUI
  refuses the job its errors come back (`stage: "submit"`) together with the
  near matches found here.
* **Completed**: `status: "completed"`, `outputs` (saved under `save_to`,
  images also inline), `texts` for text outputs, `duration_s`.
* **Failed while running**: `status: "failed"` and `errors` naming the node,
  the exception and the last lines of the traceback.
* **Still going**: after `wait_timeout` seconds (default 120,
  `AUTOGRAPH_MCP_WAIT_TIMEOUT_S`) the call returns
  `timed_out: true` with the `prompt_id` and the current `status` instead of
  failing. The job keeps running: `wait_for_job(prompt_id)` continues waiting,
  `get_job_status` checks once, `cancel_job` stops it. While waiting, progress
  notifications are sent, so clients that reset their own timeout on progress
  keep the call open.

Each output file has **`as_input`** (for example `"cat_00001_.png [output]"`):
the value a loader node takes to read that file back. Feeding one run's result
into the next is `add_node(..., "LoadImage", {"image": as_input})`.

---

## The `merge_workflow` graft engine

This is the heart of "vibe-add a feature." When the LLM finds a workflow JSON
online (or in the library) that does what the user wants, it hands the JSON to
`merge_workflow` and gets back a structured report.

What the engine does:

1. **Renumber** — every node id and link id in the fragment shifts by the active
   workflow's `last_node_id` / `last_link_id` so nothing collides.
2. **Insert** — nodes and links splice into the active graph; positions get
   offset (default `+450, 0`) so the fragment lands to the right.
3. **Detect dangling slots** — fragment inputs with no incoming link, fragment
   outputs with no outgoing link.
4. **Auto-stitch by type** — for each dangling fragment input, look for a
   producer of that type in the existing graph (excluding the fragment).
   Wire it if there's exactly one match. Same for dangling outputs that find
   a unique free input.
5. **Suggest interposers** — when a fragment node both takes and produces the
   same slot type (e.g. `LoraLoader` on `MODEL`), the engine flags it as an
   "interposer" and emits a hint explaining how to slot it into an existing
   path (disconnect → connect → connect).

Return shape:

```json
{
  "added_nodes":              [{ "node_id": "42", "class_type": "VAEDecode", "title": "" }],
  "node_id_map":              { "1": "42" },
  "auto_connected":           [{ "from": {...}, "to": {...}, "slot_type": "VAE", "reason": "unique-source-by-type" }],
  "still_dangling_inputs":    [{ "node_id": "42", "input_name": "samples", "input_type": "LATENT", "candidates": [...] }],
  "still_dangling_outputs":   [],
  "interposer_suggestions":   []
}
```

The LLM then calls `connect_nodes` for each entry in `still_dangling_*` until
`validate_workflow` is clean.

---

## The local workflow library

The MCP searches these directories in priority order:

1. Anything in `$AUTOGRAPH_MCP_LIBRARY_DIRS` (colon-separated dirs).
2. `./.autograph-workflows/` (project-local).
3. `~/.comfyui-autograph/workflows/` (user-home).
4. The bundled `examples/workflows/starters/` directory.

A library entry is a `*.json` workflow plus an optional `*.metadata.json`
sidecar:

```json
{
  "name": "txt2img-basic",
  "title": "Text-to-image (basic)",
  "description": "...",
  "tags": ["txt2img", "basic"],
  "models_required": [],
  "source": "https://github.com/comfyanonymous/ComfyUI_examples"
}
```

Drop your own workflows into `~/.comfyui-autograph/workflows/` — they'll be
discoverable on the next call to `search_local_workflows`.

---

## Resources

* `comfyui://node-info` — full node_info catalog (JSON)
* `comfyui://history/{prompt_id}` — single job's history (JSON)
* `comfyui://outputs/{prompt_id}/{filename}` — output file bytes

## Prompts

* `text_to_image(prompt, negative?, model?, steps?, seed?)`: find or build a
  text-to-image workflow, set it up, validate, run.
* `diagnose_workflow()`: go through `validate_workflow`'s errors code by code
  and fix what can be fixed.
* `vibe_build_workflow(goal)`: the full loop: start from a template or from
  scratch, look nodes up, edit, validate, run, chain.

The server also sends `instructions` when a client connects: a short version
of this page that tells the assistant which tool to reach for at each step.

---

## Configuration (env vars)

| Variable | Default | Purpose |
| --- | --- | --- |
| `AUTOGRAPH_COMFYUI_SERVER_URL` | `http://127.0.0.1:8188` | ComfyUI server URL |
| `AUTOGRAPH_TIMEOUT_S` | `30` | HTTP timeout (seconds) |
| `AUTOGRAPH_OUTPUT_PATH` | `./` | Where to save fetched files |
| `AUTOGRAPH_MCP_MAX_INLINE_IMAGE_BYTES` | `2000000` | Per-image inline cap (bytes) |
| `AUTOGRAPH_MCP_MAX_INLINE_IMAGES` | `4` | Max images returned inline per call |
| `AUTOGRAPH_MCP_WAIT_TIMEOUT_S` | `120` | How long `run_workflow` / `wait_for_job` wait before returning the job id |
| `AUTOGRAPH_MCP_SESSION_DIR` | `~/.comfyui-autograph/sessions` | Where session checkpoints land |
| `AUTOGRAPH_MCP_LIBRARY_DIRS` | _(unset)_ | Colon-separated extra workflow library dirs |

---

## Image return policy

Generated images come back as inline base64 `ImageContent` so the LLM can see
them, with two safety caps (configurable per call or via env vars):

* **Per-image bytes cap** — anything bigger is saved to disk and returned as a
  file path + `comfyui://` resource URI.
* **Per-call count cap** — once `max_inline_images` images have been inlined,
  the rest fall back to file paths/URIs.

Smallest images are inlined first. Pass `inline_images=False` to skip inlining
entirely (useful for grid renders).

---

## A walkthrough

User: *"Take `flow.json`, add a LoRA stage, and render with seed 42."*

1. `comfyui_status`: the server is up.
2. `load_workflow(source="flow.json")` returns `wf_xxxx`.
3. `inspect_workflow(workflow_id="wf_xxxx", brief=true)`: the wiring. The
   checkpoint loader is node 4; its MODEL goes to the sampler (node 3) and its
   CLIP to the two text encoders (6 and 7).
4. `list_node_types(query="lora")` finds `LoraLoader`;
   `describe_node_type("LoraLoader")` gives its inputs (`model`, `clip`,
   `lora_name`, `strength_model`, `strength_clip`) and outputs (0 MODEL, 1 CLIP);
   `list_models(folder="loras")` shows what is installed.
5. One call adds the LoRA, wired to the checkpoint:
   `add_node("wf_xxxx", "LoraLoader", {"model": ["4", 0], "clip": ["4", 1], "lora_name": "detail.safetensors", "strength_model": 0.8})`
   returns node 10.
6. One call reroutes the consumers and sets the seed:
   `set_workflow_values("wf_xxxx", [{"node_id": "3", "inputs": {"model": ["10", 0], "seed": 42}}, {"class_type": "CLIPTextEncode", "inputs": {"clip": ["10", 1]}}])`.
7. `validate_workflow("wf_xxxx")`: `ok: true`.
8. `run_workflow("wf_xxxx")`: the image comes back inline.
9. `save_workflow("wf_xxxx", path="flow.lora.json")`.

If a step is wrong (a misspelled input, a link to the wrong output, a LoRA
that is not installed), the call that made the mistake says so and names what
would fit, so the assistant corrects it on the next call instead of finding
out when the job fails.

---

## What this server does not do

It builds, edits, checks and runs workflows. It does not download models,
install custom nodes, or start and stop ComfyUI; when a workflow needs a model
or a node type that is missing, the tools say which, and the user installs it.
Comfy's own MCP server ([`comfy-mcp`](https://github.com/Comfy-Org/comfy-mcp))
covers those jobs through `comfy-cli`, and the two can be connected side by
side. Hosted generation and Comfy Cloud accounts are outside its scope too.

---

## Verify it works

```bash
# 1. Core stays clean (no mcp installed)
pip install -e .
python -c "import autograph; print(autograph.__version__)"

# 2. With the extra
pip install -e ".[mcp]"
comfyui-autograph-mcp --help
python -m autograph.mcp --help

# 3. Inspect tools interactively
npx @modelcontextprotocol/inspector \
  uvx --from "comfyui-autograph[mcp]" comfyui-autograph-mcp
```
