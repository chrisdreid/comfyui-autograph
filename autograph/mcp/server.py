"""FastMCP server for comfyui-autograph.

Build the server with :func:`build_server` (useful for tests) or run it with
:func:`main` (the console-script entry point — defaults to stdio transport).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, List, Optional, Union

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ImageContent, TextContent

from autograph.version import __version__

from . import prompts as _prompts
from . import resources as _resources
from . import tools as _tools
from .config import McpConfig, load_config
from .session import SessionStore


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _materialize_content_blocks(blocks: List[Dict[str, Any]]) -> List[Union[TextContent, ImageContent]]:
    out: List[Union[TextContent, ImageContent]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "image":
            out.append(
                ImageContent(
                    type="image",
                    data=str(b.get("data", "")),
                    mimeType=str(b.get("mimeType", "image/png")),
                )
            )
        else:
            out.append(TextContent(type="text", text=str(b.get("text", ""))))
    return out


def _result_with_images(payload: Dict[str, Any]) -> List[Union[TextContent, ImageContent]]:
    blocks = payload.pop("content_blocks", []) or []
    summary = TextContent(type="text", text=json.dumps(payload, default=str, indent=2))
    return [summary, *_materialize_content_blocks(blocks)]


# ---------------------------------------------------------------------------
# What a client is told when it connects
# ---------------------------------------------------------------------------

INSTRUCTIONS = """\
Build, edit, check and run ComfyUI workflows.

Start with `comfyui_status`. Then get a workflow into a session (every session has a `workflow_id`):
- `search_templates` -> `load_template`: ComfyUI's own ready-made workflows. The quickest way to a known-good graph.
- `load_workflow`: a file or JSON, in workspace or API format.
- `create_workflow`: empty, or a whole graph in one call with `nodes` in ComfyUI's API format.

Find nodes before using them; never guess a class_type or an input name:
- `list_node_types(query=..., input_type=..., output_type=...)` finds node types ("what gives CONDITIONING").
- `describe_node_type` gives each input (type, default, range, choices, widget or connection) and each output with its index.
- `list_input_options` / `list_models` list the files a dropdown offers (checkpoints, LoRAs, uploaded images).

Edit with `add_node`, `set_workflow_values`, `connect_nodes`, `disconnect_input`, `remove_node`, `set_node_mode`.
Inputs are written as in the API format: a value, or a link `[node_id, output_index]`. `add_node` and
`set_workflow_values` take both, so a node can be added fully wired in one call. Links are type-checked, and a
refused change says what would fit. `inspect_workflow` shows the graph in the same notation.

`validate_workflow` checks what ComfyUI checks when a job is queued (unknown node types, missing inputs, mismatched
links, values outside a widget's choices or range) and names the node, the input and what to do.

`run_workflow` validates, queues, waits and returns the images. A job that outlasts the wait returns
`timed_out: true` with its `prompt_id`: continue with `wait_for_job`, or check with `get_job_status`. Each output has
`as_input`, the value a loader node takes to read it back, for chaining runs. `upload_file` sends a local file to
ComfyUI's input folder. `get_logs` shows the server log when a job fails without a clear error.

Model files and custom nodes cannot be installed from here: when `validate_workflow` reports a missing model or node
type, tell the user what to install.
"""


def _annotated(mcp: FastMCP, title: str, *, read_only: bool = False, destructive: bool = False,
               idempotent: bool = False, open_world: bool = True, content: bool = False) -> Callable[[Any], Any]:
    """``mcp.tool()`` with the hints clients use to decide what needs confirming.

    ``open_world`` is False for tools that only touch the session held by this
    server (no request to ComfyUI, no file written where the caller points).
    ``content`` marks tools that return text and image blocks instead of one object.
    """
    try:
        from mcp.types import ToolAnnotations

        hints = ToolAnnotations(
            title=title,
            readOnlyHint=read_only,
            destructiveHint=destructive if not read_only else False,
            idempotentHint=idempotent or read_only,
            openWorldHint=open_world,
        )
    except ImportError:                       # an older SDK without annotations
        return mcp.tool()
    if content:
        # The tool returns text and image blocks, which have no output schema worth sending.
        try:
            return mcp.tool(annotations=hints, structured_output=False)
        except TypeError:
            pass
    try:
        return mcp.tool(annotations=hints)
    except TypeError:
        return mcp.tool()


async def _in_thread(ctx: Optional[Context], fn: Callable[..., Dict[str, Any]], **kwargs: Any) -> Dict[str, Any]:
    """Run a tool that waits on ComfyUI in a worker thread, passing its progress on to the client."""
    import anyio

    async def report(done: float, total: Optional[float], message: str) -> None:
        if ctx is None:
            return
        try:
            await ctx.report_progress(done, total, message)
        except TypeError:
            await ctx.report_progress(done, total)
        except Exception:
            pass

    def on_progress(done: float, total: Optional[float], message: str) -> None:
        try:
            anyio.from_thread.run(report, done, total, message)
        except Exception:
            pass

    return await anyio.to_thread.run_sync(lambda: fn(on_progress=on_progress, **kwargs))


Workflow = Optional[Union[str, Dict[str, Any]]]


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def build_server(
    config: Optional[McpConfig] = None,
    store: Optional[SessionStore] = None,
) -> FastMCP:
    cfg = config or load_config()
    sessions = store or SessionStore()

    mcp = FastMCP("comfyui-autograph", instructions=INSTRUCTIONS)

    def tool(title: str, **hints: Any) -> Callable[[Any], Any]:
        return _annotated(mcp, title, **hints)

    # =======================================================================
    # Server and discovery
    # =======================================================================

    @tool("ComfyUI status", read_only=True)
    def comfyui_status(server_url: Optional[str] = None, refresh: bool = False) -> Dict[str, Any]:
        """Whether ComfyUI is reachable, its version, devices and free memory, queue depth and number of node types. Call this first. `refresh=true` re-reads the node list (after custom nodes were installed)."""
        return _tools.comfyui_status(cfg, server_url=server_url, refresh=refresh)

    @tool("Search node types", read_only=True)
    def list_node_types(
        query: Optional[str] = None,
        category: Optional[str] = None,
        input_type: Optional[str] = None,
        output_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        include_deprecated: bool = False,
        server_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Find node types on the server. All filters combine.

        `query`: words looked for in the class name, display name, search aliases, category and description ("load image", "ksampler", "upscale").
        `input_type` / `output_type`: nodes that take / give a connection of that type, e.g. output_type="CONDITIONING" for what can feed a sampler's `positive`, input_type="LATENT" for what a latent can go into.
        `category`: part of a category path ("loaders", "image/upscaling").
        Each result has `class_type`, `takes` (connection inputs as name:TYPE, `?` = optional) and `gives` (output types in index order). Best match first; `next_offset` pages. Follow with `describe_node_type` for the widgets."""
        return _tools.list_node_types(
            cfg, query=query, category=category, limit=limit, server_url=server_url,
            input_type=input_type, output_type=output_type, offset=offset, include_deprecated=include_deprecated,
        )

    @tool("Describe node type", read_only=True)
    def describe_node_type(
        class_type: Union[str, List[str]],
        max_options: int = 25,
        server_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Everything needed to add and wire a node type. `class_type` is one name or a list of names.

        `inputs` (in the node's order): `name`, `type`, `required`, `kind` ("widget" takes a value, "connection" takes a link, "connection_group" is a growing set of connections addressed as "group.member"), and for widgets `default`, `min`/`max`/`step`, `options` (the first `max_options` of `options_total`; `list_input_options` searches the rest), `multiline`, `seed`, `upload`. A dropdown with `inputs_per_option` adds inputs named "dropdown.input" depending on the choice.
        `outputs`: `index`, `name`, `type`; a link to an output is `[node_id, index]`.
        An unknown name returns the closest matches."""
        return _tools.describe_node_type(cfg, class_type=class_type, server_url=server_url, max_options=max_options)

    @tool("List a dropdown's choices", read_only=True)
    def list_input_options(
        class_type: str,
        input_name: str,
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        server_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The choices of one dropdown input, e.g. ("CheckpointLoaderSimple", "ckpt_name") or ("LoadImage", "image"). `query` filters by substring; `next_offset` pages. These are the exact strings the input accepts."""
        return _tools.list_input_options(
            cfg, class_type=class_type, input_name=input_name, query=query, limit=limit, offset=offset,
            server_url=server_url,
        )

    @tool("List models", read_only=True)
    def list_models(
        folder: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 100,
        server_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Model files installed in ComfyUI. Without `folder`: the folder names (checkpoints, loras, vae, controlnet, ...). With one: its files, filtered by `query`."""
        return _tools.list_models(cfg, folder=folder, server_url=server_url, query=query, limit=limit)

    # =======================================================================
    # Getting a workflow
    # =======================================================================

    @tool("Search ComfyUI templates", read_only=True)
    def search_templates(
        query: Optional[str] = None,
        media_type: Optional[str] = None,
        local_only: bool = False,
        limit: int = 10,
        server_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search the ready-made workflows that ship with ComfyUI (text to image, image edit, video, audio, 3D, upscaling, ...). `media_type`: image, video, audio, 3d or llm. `local_only=true` leaves out templates that call paid partner APIs. Results name the models a template needs and their download size; most used first. Load one with `load_template`."""
        return _tools.search_templates(
            cfg, query=query, media_type=media_type, limit=limit, local_only=local_only, server_url=server_url
        )

    @tool("Load a ComfyUI template", open_world=True)
    def load_template(name: str, label: Optional[str] = None, server_url: Optional[str] = None) -> Dict[str, Any]:
        """Load a template (by `name` from `search_templates`) into a new session. Returns the `workflow_id`, the nodes that take the template's input files and give its outputs, and `ready_to_run` with the `problems` to fix first (typically model files that are not installed)."""
        return _tools.load_template(cfg, sessions, name=name, label=label, server_url=server_url)

    @tool("Load a workflow")
    def load_workflow(source: Union[str, Dict[str, Any]], label: Optional[str] = None) -> Dict[str, Any]:
        """Load a workflow into a new session and return its `workflow_id`. `source` is a file path (a workflow .json, or a PNG saved by ComfyUI with the workflow embedded), a JSON string, or the workflow object, in workspace format (as saved by the editor) or API format. Subgraphs are flattened so every node has one id, the id ComfyUI uses in errors and results."""
        return _tools.load_workflow(cfg, sessions, source=source, label=label)

    @tool("Create a workflow")
    def create_workflow(
        nodes: Optional[Dict[str, Any]] = None,
        starter: Optional[str] = None,
        label: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start a new session and return its `workflow_id`.

        With `nodes`, build the whole graph in one call from ComfyUI's API format:
        {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
         "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["1", 1]}}}
        where a link is [node_id, output_index]. The result says whether the graph is valid and lists every problem; fix them with `set_workflow_values` / `connect_nodes`.
        With `starter`, begin from a library workflow (`search_local_workflows`). With neither, start empty and use `add_node`."""
        return _tools.create_workflow(cfg, sessions, starter=starter, label=label, nodes=nodes)

    @tool("List workflow sources", read_only=True, open_world=False)
    def list_workflow_sources() -> Dict[str, Any]:
        """Websites where ComfyUI workflows are published. Nothing is fetched here: fetch a workflow JSON yourself and pass it to `load_workflow` or `merge_workflow`."""
        return _tools.list_workflow_sources(cfg)

    @tool("Search the local workflow library", read_only=True, open_world=False)
    def search_local_workflows(
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """Search the local workflow library (bundled starters, ~/.comfyui-autograph/workflows, the project's own, AUTOGRAPH_MCP_LIBRARY_DIRS)."""
        return _tools.search_local_workflows(cfg, query=query, tags=tags, limit=limit)

    @tool("Load a library workflow")
    def load_local_workflow(name: str, label: Optional[str] = None) -> Dict[str, Any]:
        """Load a workflow from the local library (by name or filename) into a new session."""
        return _tools.load_local_workflow(cfg, sessions, name=name, label=label)

    # =======================================================================
    # Reading and checking a workflow
    # =======================================================================

    @tool("Inspect a workflow", read_only=True, open_world=False)
    def inspect_workflow(
        workflow_id: Optional[str] = None,
        workflow: Workflow = None,
        node_ids: Optional[List[str]] = None,
        class_type: Optional[str] = None,
        brief: bool = False,
    ) -> Dict[str, Any]:
        """Read a workflow: every node with `id`, `class_type`, `inputs` by name (a value, or a link [node_id, output_index]), `outputs` (name:TYPE and the inputs each one feeds), `missing` (required inputs with nothing connected), `title`, and `mode` when bypassed or muted. `output_nodes` are the nodes that produce results.

        `brief=true` leaves out widget values (links only): use it first on large workflows, then pass `node_ids` or `class_type` for the nodes of interest. Give `workflow_id`, or `workflow` (path / JSON / object) to read one without a session."""
        return _tools.inspect_workflow(
            cfg, sessions, workflow=workflow, workflow_id=workflow_id, brief=brief, node_ids=node_ids,
            class_type=class_type,
        )

    @tool("Validate a workflow", read_only=True)
    def validate_workflow(
        workflow_id: Optional[str] = None,
        workflow: Workflow = None,
        server_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Check a workflow against the server before running it: unknown node types, required inputs with nothing connected, links between types that do not fit or to outputs that do not exist, values that are not among a dropdown's choices (missing model files) or outside a number's range, no output node, nodes that feed nothing.

        Each entry in `errors` / `warnings` has a `code`, a `message`, the `node_id` and `input`, and what to do: `closest` (near matches), `candidates` (outputs in the graph that fit, as links), `hint`. `ok: true` means ComfyUI will accept it."""
        return _tools.validate_workflow(
            cfg, sessions, workflow=workflow, workflow_id=workflow_id, server_url=server_url
        )

    @tool("Convert to API format", read_only=True, open_world=False)
    def convert_workflow(workflow_id: Optional[str] = None, workflow: Workflow = None) -> Dict[str, Any]:
        """Convert a workspace workflow to the API format ComfyUI executes and return it as `api_workflow`, with the same `errors` / `warnings` as `validate_workflow`."""
        return _tools.convert_workflow(cfg, sessions, workflow=workflow, workflow_id=workflow_id)

    @tool("Get the workflow JSON", read_only=True, open_world=False)
    def get_workflow(workflow_id: str, format: str = "workspace") -> Dict[str, Any]:
        """The session's workflow as JSON: "workspace" (what the ComfyUI editor opens) or "api" (what the server executes). Large; `inspect_workflow` is the compact way to read a graph."""
        return _tools.get_workflow(cfg, sessions, workflow_id=workflow_id, format=format)

    # =======================================================================
    # Editing
    # =======================================================================

    @tool("Add a node", open_world=False)
    def add_node(
        workflow_id: str,
        class_type: str,
        inputs: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a node. `inputs` maps input names to widget values and to links `[node_id, output_index]` from nodes already in the graph, so a node can be added fully wired:
        add_node(workflow_id, "KSampler", {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0], "seed": 7, "steps": 20})
        Widgets left out keep their defaults. The node is not added if an input name does not exist, a value has the wrong type or is out of range, or a link's types do not fit; the error says what is valid. Returns the `node_id`, the node as `inspect_workflow` shows it, and `missing` required connections."""
        return _tools.add_node(
            cfg, sessions, workflow_id=workflow_id, class_type=class_type, inputs=inputs, title=title
        )

    @tool("Set inputs of existing nodes", open_world=False, idempotent=True)
    def set_workflow_values(
        updates: List[Dict[str, Any]],
        workflow_id: Optional[str] = None,
        workflow: Workflow = None,
    ) -> Dict[str, Any]:
        """Change inputs of nodes that exist. Each update is `{"node_id": "3", "inputs": {name: value}}`; instead of `node_id`, `class_type` or `title` selects every matching node. A value is a widget value, a link `[node_id, output_index]`, or null to disconnect. `"set_title": "..."` renames the node.

        Example: [{"node_id": "6", "inputs": {"text": "a red fox"}}, {"class_type": "KSampler", "inputs": {"seed": 42, "steps": 30}}]
        Every change is checked against the node type; `applied` reports each one, and a refused one says why and what fits (`closest`, valid `inputs`, `min`/`max`)."""
        return _tools.set_workflow_values(
            cfg, sessions, updates=updates, workflow=workflow, workflow_id=workflow_id
        )

    @tool("Connect two nodes", open_world=False, idempotent=True)
    def connect_nodes(
        workflow_id: str,
        from_node: str,
        to_node: str,
        to_input: str,
        from_output: Optional[Union[str, int]] = None,
    ) -> Dict[str, Any]:
        """Link an output of `from_node` to the input `to_input` of `to_node`. `from_output` is the output's index or name; left out, the one output whose type fits is used. An input holds one link: connecting replaces the previous one. The types must fit; a refusal lists the outputs and inputs that do."""
        return _tools.connect_nodes(
            cfg, sessions,
            workflow_id=workflow_id,
            from_node=from_node,
            to_node=to_node,
            to_input=to_input,
            from_output=from_output,
        )

    @tool("Disconnect an input", open_world=False, destructive=True, idempotent=True)
    def disconnect_input(workflow_id: str, node_id: str, input_name: str) -> Dict[str, Any]:
        """Remove the link feeding one input of a node."""
        return _tools.disconnect_input(
            cfg, sessions, workflow_id=workflow_id, node_id=node_id, input_name=input_name
        )

    @tool("Remove nodes", open_world=False, destructive=True)
    def remove_node(workflow_id: str, node_id: Union[str, List[str]]) -> Dict[str, Any]:
        """Delete a node (or a list of nodes) and every link touching it. `inputs_left_unconnected` lists the inputs of other nodes that lost their source."""
        return _tools.remove_node(cfg, sessions, workflow_id=workflow_id, node_id=node_id)

    @tool("Bypass, mute or re-enable nodes", open_world=False, idempotent=True)
    def set_node_mode(workflow_id: str, node_ids: Union[str, List[str]], mode: str = "active") -> Dict[str, Any]:
        """Switch nodes on or off without removing them. `mode`: "bypassed" (skipped, its input passes through to what it fed: the way to turn off a LoRA or an upscaler), "muted" (does not run, and neither does what depends on it), "active"."""
        return _tools.set_node_mode(cfg, sessions, workflow_id=workflow_id, node_ids=node_ids, mode=mode)

    @tool("Merge a workflow fragment", open_world=False)
    def merge_workflow(
        workflow_id: str,
        fragment: Union[str, Dict[str, Any]],
        auto_connect: bool = True,
    ) -> Dict[str, Any]:
        """Add another workflow's nodes (a path, JSON or object, in workspace or API format) to the session. Its node ids are renumbered. With `auto_connect`, open inputs and outputs are wired to the existing graph where exactly one connection of the right type is possible. Returns the nodes added, what was connected, and what is still open with the candidates for each."""
        return _tools.merge_workflow(
            cfg, sessions, workflow_id=workflow_id, fragment=fragment, auto_connect=auto_connect
        )

    @tool("Save the workflow to a file", destructive=True, idempotent=True)
    def save_workflow(workflow_id: str, path: Optional[str] = None, format: str = "workspace") -> Dict[str, Any]:
        """Write the session's workflow to `path` (overwriting it): "workspace" format opens in the ComfyUI editor, "api" is what the server executes. Without `path`, the session's own checkpoint file is written."""
        return _tools.save_workflow(cfg, sessions, workflow_id=workflow_id, path=path, format=format)

    @tool("List sessions", read_only=True, open_world=False)
    def list_sessions() -> Dict[str, Any]:
        """The workflow sessions held by this server: `workflow_id`, label, source file, node count, checkpoint file."""
        return _tools.list_sessions(cfg, sessions)

    @tool("Close a session", open_world=False, destructive=True, idempotent=True)
    def close_session(workflow_id: str, delete_checkpoint: bool = False) -> Dict[str, Any]:
        """Drop a session from memory; `delete_checkpoint=true` also deletes its checkpoint file."""
        return _tools.close_session(
            cfg, sessions, workflow_id=workflow_id, delete_checkpoint=delete_checkpoint
        )

    # =======================================================================
    # Running
    # =======================================================================

    @tool("Run a workflow", content=True)
    async def run_workflow(
        ctx: Context,
        workflow_id: Optional[str] = None,
        workflow: Workflow = None,
        wait: bool = True,
        wait_timeout: Optional[float] = None,
        fetch_outputs: bool = True,
        save_to: Optional[str] = None,
        inline_images: bool = True,
        max_inline: Optional[int] = None,
        skip_validation: bool = False,
        server_url: Optional[str] = None,
    ) -> List[Union[TextContent, ImageContent]]:
        """Validate, queue and (with `wait`) wait for a workflow, then return its outputs. Give `workflow_id`, or `workflow` (path / JSON / object).

        `status` is "completed", "failed" (with `errors` naming the node), "cancelled", or still "running" / "pending" with `timed_out: true` when it outlasts `wait_timeout` seconds (default 120): the job keeps running, continue with `wait_for_job(prompt_id)`. A workflow that does not validate is not queued (`stage: "validation"`, with the same `errors` as `validate_workflow`).
        Outputs are saved under `save_to` (default: the configured output folder); images are also returned inline up to `max_inline`. Each output has `as_input`, the value a loader node takes to read that file back. Text outputs come back in `texts`."""
        payload = await _in_thread(
            ctx, _tools.run_workflow, config=cfg, store=sessions,
            workflow=workflow, workflow_id=workflow_id, wait=wait, fetch_outputs=fetch_outputs, save_to=save_to,
            inline_images=inline_images, max_inline=max_inline, server_url=server_url,
            wait_timeout=wait_timeout, skip_validation=skip_validation,
        )
        return _result_with_images(payload)

    @tool("Queue a workflow")
    def queue_workflow(
        workflow_id: Optional[str] = None,
        workflow: Workflow = None,
        skip_validation: bool = False,
        server_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validate and queue a workflow without waiting. Returns the `prompt_id` for `wait_for_job`, `get_job_status`, `cancel_job` and `fetch_outputs`."""
        return _tools.queue_workflow(
            cfg, sessions, workflow=workflow, workflow_id=workflow_id, server_url=server_url,
            skip_validation=skip_validation,
        )

    @tool("Wait for a job", read_only=True, content=True)
    async def wait_for_job(
        ctx: Context,
        prompt_id: str,
        timeout: Optional[float] = None,
        fetch_outputs: bool = True,
        save_to: Optional[str] = None,
        inline_images: bool = True,
        max_inline: Optional[int] = None,
        server_url: Optional[str] = None,
    ) -> List[Union[TextContent, ImageContent]]:
        """Wait up to `timeout` seconds (default 120) for a queued or running job and return what `run_workflow` returns: its `status`, and its outputs once it has completed. `timed_out: true` means it is still going; call again."""
        payload = await _in_thread(
            ctx, _tools.wait_for_job, config=cfg, prompt_id=prompt_id, timeout=timeout,
            fetch_outputs=fetch_outputs, save_to=save_to, inline_images=inline_images, max_inline=max_inline,
            server_url=server_url,
        )
        return _result_with_images(payload)

    @tool("Job status", read_only=True)
    def get_job_status(prompt_id: Optional[str] = None, server_url: Optional[str] = None) -> Dict[str, Any]:
        """Where a job is, without waiting: "pending" (with its queue position), "running", "completed" (with its outputs), "failed" (with the error and the node it happened in), "cancelled". Without `prompt_id`: what is running and waiting now, and the most recent jobs."""
        return _tools.get_job_status(cfg, prompt_id=prompt_id, server_url=server_url)

    @tool("Cancel a job", destructive=True, idempotent=True)
    def cancel_job(prompt_id: str, server_url: Optional[str] = None) -> Dict[str, Any]:
        """Cancel one job: removed from the queue if it is waiting, interrupted if it is running."""
        return _tools.cancel_job(cfg, prompt_id=prompt_id, server_url=server_url)

    @tool("Interrupt the running job", destructive=True, idempotent=True)
    def interrupt(server_url: Optional[str] = None) -> Dict[str, Any]:
        """Stop whatever ComfyUI is running now. To cancel a specific job, including one still waiting, use `cancel_job`."""
        return _tools.interrupt(cfg, server_url=server_url)

    @tool("Recent jobs", read_only=True)
    def get_history(
        prompt_id: Optional[str] = None,
        limit: int = 10,
        server_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The most recent jobs (newest first) with their status and output files; with `prompt_id`, the same as `get_job_status`. The full record of a job is the resource comfyui://history/{prompt_id}."""
        return _tools.get_history(cfg, prompt_id=prompt_id, limit=limit, server_url=server_url)

    @tool("Free memory", destructive=True, idempotent=True)
    def free_memory(unload_models: bool = True, server_url: Optional[str] = None) -> Dict[str, Any]:
        """Have ComfyUI unload its models and free memory, e.g. after an out-of-memory failure or before a large model. The next run loads its models again."""
        return _tools.free_memory(cfg, unload_models=unload_models, server_url=server_url)

    @tool("Server log", read_only=True)
    def get_logs(lines: int = 60, query: Optional[str] = None, server_url: Optional[str] = None) -> Dict[str, Any]:
        """The last `lines` lines of the ComfyUI server log, optionally only those containing `query`. For failures that come without a clear error (a custom node that did not load, out of memory)."""
        return _tools.get_logs(cfg, lines=lines, query=query, server_url=server_url)

    # =======================================================================
    # Files and outputs
    # =======================================================================

    @tool("Upload a file to ComfyUI")
    def upload_file(
        local_path: str,
        accept: Optional[Union[str, List[str]]] = None,
        subfolder: Optional[str] = None,
        overwrite: bool = False,
        server_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload a local file (or the matching files of a directory) to ComfyUI's input folder. Each uploaded file has `as_input`, the value to give a loader node (LoadImage.image, LoadAudio.audio, LoadVideo.file, ...).

        `accept` limits what is sent: a kind ("image", "audio", "video", "text", "archive", "model"), a MIME pattern ("image/*"), an extension, or a list of those."""
        return _tools.upload_file(
            cfg,
            local_path=local_path,
            accept=accept,
            subfolder=subfolder,
            overwrite=overwrite,
            server_url=server_url,
        )

    @tool("Download a job's outputs", content=True)
    async def fetch_outputs(
        prompt_id: str,
        save_to: Optional[str] = None,
        kinds: Optional[Union[str, List[str]]] = None,
        inline_images: bool = True,
        max_inline: Optional[int] = None,
        server_url: Optional[str] = None,
    ) -> List[Union[TextContent, ImageContent]]:
        """Download the outputs of a finished job to `save_to` and return images inline (same limits as `run_workflow`). `kinds` limits it to e.g. "images"."""
        import anyio

        payload = await anyio.to_thread.run_sync(lambda: _tools.fetch_outputs(
            cfg,
            prompt_id=prompt_id,
            save_to=save_to,
            kinds=kinds,
            inline_images=inline_images,
            max_inline=max_inline,
            server_url=server_url,
        ))
        return _result_with_images(payload)

    @tool("List a job's outputs", read_only=True)
    def list_outputs(prompt_id: str, server_url: Optional[str] = None) -> Dict[str, Any]:
        """The files and texts a job produced, without downloading them. Each file has `as_input` (the value a loader node takes to read it back) and a `resource_uri`."""
        return _tools.list_outputs(cfg, prompt_id=prompt_id, server_url=server_url)

    # =======================================================================
    # Resources
    # =======================================================================

    @mcp.resource("comfyui://node-info", mime_type="application/json")
    def _resource_node_info() -> str:
        return _resources.node_info_resource(cfg)

    @mcp.resource("comfyui://history/{prompt_id}", mime_type="application/json")
    def _resource_history(prompt_id: str) -> str:
        return json.dumps(_resources.history_resource(cfg, prompt_id), default=str, indent=2)

    @mcp.resource("comfyui://outputs/{prompt_id}/{filename}")
    def _resource_output(prompt_id: str, filename: str) -> bytes:
        return _resources.output_resource(cfg, prompt_id, filename)

    # =======================================================================
    # Prompts
    # =======================================================================

    @mcp.prompt(
        name="text_to_image",
        description="Guided text-to-image conversation: load workflow → set values → render.",
    )
    def _prompt_text_to_image(
        prompt: str,
        negative: str = "",
        model: str = "auto",
        steps: int = 20,
        seed: int = 0,
    ) -> str:
        return _prompts.text_to_image(prompt, negative=negative, model=model, steps=steps, seed=seed)

    @mcp.prompt(
        name="diagnose_workflow",
        description="Walk through validation errors on a workflow and propose fixes.",
    )
    def _prompt_diagnose_workflow() -> str:
        return _prompts.diagnose_workflow()

    @mcp.prompt(
        name="vibe_build_workflow",
        description="Vibe-build a ComfyUI workflow: load or start, search the library, graft fragments, run.",
    )
    def _prompt_vibe_build(goal: str = "describe what the user wants to render") -> str:
        return _prompts.vibe_build_workflow(goal)

    return mcp


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="comfyui-autograph-mcp",
        description="MCP server for the comfyui-autograph ComfyUI client.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"comfyui-autograph-mcp {__version__}",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio",),
        default="stdio",
        help="Transport to use (only stdio is supported today; documented for forward compatibility).",
    )
    parser.parse_args(argv)

    server = build_server()
    try:
        server.run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
