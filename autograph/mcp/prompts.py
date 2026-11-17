"""Reusable conversation templates exposed as MCP prompts."""

from __future__ import annotations

from typing import Optional


TEXT_TO_IMAGE_TEMPLATE = """\
Generate an image with ComfyUI using the autograph MCP tools.

Goal:
  Prompt:    {prompt}
  Negative:  {negative}
  Model:     {model}
  Steps:     {steps}
  Seed:      {seed}

Plan:
  1. `comfyui_status`: confirm the server is reachable.
  2. Get a workflow into a session:
     - `search_templates(query="text to image", local_only=true)` and `load_template(name)` for a ready-made graph.
       `load_template` says which model files the template needs and whether they are installed.
     - Or, if the user gave a workflow file, `load_workflow(source=path)`.
     - Or build one with `create_workflow(nodes=...)`: a loader, two text encoders, an empty latent, a sampler,
       a decoder and a save node. Look each node up with `describe_node_type` first.
  3. Model: "{model}". If it is "auto", pick an installed one: `list_models(folder="checkpoints")` (or the folder the
     loader in the workflow reads from; `list_input_options(class_type, input_name)` gives the exact choices).
  4. `inspect_workflow(workflow_id)` to find the nodes, then one `set_workflow_values` call for the positive and
     negative text, the sampler's `seed` and `steps`, and the model.
  5. `validate_workflow(workflow_id)`. Fix what it reports; if a model file is missing, tell the user which.
  6. `run_workflow(workflow_id)`. If it returns `timed_out: true`, continue with `wait_for_job(prompt_id)`.
  7. Show the image(s) and the saved file paths.
"""


DIAGNOSE_WORKFLOW_TEMPLATE = """\
Find out why a ComfyUI workflow does not run, and fix what can be fixed.

Plan:
  1. `load_workflow(source=...)` (or use the `workflow_id` already open), then `validate_workflow(workflow_id)`.
  2. Go through `errors` by `code`:
     - `unknown_node_type`: `closest` holds near matches. If none fits, the node comes from a custom node that is
       not installed: say which, do not substitute silently.
     - `missing_input`: `candidates` are outputs in the graph that fit; connect one with `connect_nodes`, or add
       the missing node (`list_node_types(output_type=...)`).
     - `type_mismatch`, `bad_output_index`, `dangling_link`: reconnect to an output listed in `candidates`.
     - `value_not_in_list`, `no_options`: a model or input file that is not on the server. `closest` and
       `list_input_options` show what is; if nothing fits, tell the user which file to install.
     - `out_of_range`, `wrong_type`: set a value inside `min`/`max` with `set_workflow_values`.
     - `no_output_node`: add a save or preview node.
  3. Apply the fixes, then `validate_workflow` again until `ok` is true.
  4. If it validates but fails when run, `run_workflow` returns the failing node and the error; `get_logs` has the
     server's side of it.
  5. Summarize what was wrong, what you changed, and what only the user can fix (missing models, custom nodes).
"""


def text_to_image(
    prompt: str,
    negative: Optional[str] = "",
    model: Optional[str] = "auto",
    steps: Optional[int] = 20,
    seed: Optional[int] = 0,
) -> str:
    return TEXT_TO_IMAGE_TEMPLATE.format(
        prompt=prompt,
        negative=negative or "(none)",
        model=model or "auto",
        steps=steps if steps is not None else 20,
        seed=seed if seed is not None else 0,
    )


def diagnose_workflow() -> str:
    return DIAGNOSE_WORKFLOW_TEMPLATE


VIBE_BUILD_TEMPLATE = """\
Build a ComfyUI workflow end to end, without the editor.

Goal:
  {goal}

Plan:
  1. `comfyui_status`: confirm the server is reachable and see what it runs on.
  2. Choose a starting point:
     a. `search_templates(query=...)` -> `load_template(name)`: ComfyUI's own workflows. Prefer this when one is
        close to the goal; adapt it instead of starting from nothing.
     b. A workflow the user has: `load_workflow(source=path or JSON)`.
     c. From scratch: plan the graph, look up every node (step 3), then `create_workflow(nodes=...)` with the whole
        graph in API format, or `create_workflow()` and `add_node` one at a time.
  3. Look nodes up, never guess them:
     - `list_node_types(query=...)` to find a node by what it does; `list_node_types(output_type="X")` for what
       can feed an input of type X; `list_node_types(input_type="X")` for what an output of type X can go into.
     - `describe_node_type([...])` for exact input names, types, defaults, ranges and choices, and output indexes.
     - `list_models(folder)` / `list_input_options(class_type, input_name)` for the files a loader offers.
  4. Read the graph: `inspect_workflow(workflow_id, brief=true)` for the wiring, then `node_ids=[...]` or
     `class_type=...` for the values of the nodes you will change.
  5. Edit:
     - values and links: `set_workflow_values(workflow_id, updates=[{{"node_id": ..., "inputs": {{...}}}}])`;
     - new nodes: `add_node(workflow_id, class_type, inputs)` with links `[node_id, output_index]` in `inputs`;
     - `connect_nodes`, `disconnect_input`, `remove_node`; `set_node_mode(..., "bypassed")` to switch a step off;
     - `merge_workflow(workflow_id, fragment)` to bring in part of another workflow.
     A refused edit says what would fit; use that rather than retrying variations.
  6. `validate_workflow(workflow_id)` after structural changes; fix every error it lists.
  7. `run_workflow(workflow_id)`. On `status: "failed"` the `errors` name the node; fix and run again. On
     `timed_out: true` continue with `wait_for_job(prompt_id)`.
  8. To build on a result, use an output's `as_input` as the value of a loader node in the next run.
  9. Show the result and the saved paths, and offer `save_workflow(workflow_id, path)` so the user can open the
     graph in ComfyUI.
"""


def vibe_build_workflow(goal: str = "describe what the user wants to render") -> str:
    return VIBE_BUILD_TEMPLATE.format(goal=goal)
