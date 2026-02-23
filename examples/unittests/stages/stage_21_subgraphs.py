"""Stage 21 — Subgraphs + MarkdownNote: subgraph flattening and UI-node stripping."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    _BUNDLED_WORKFLOW, BUILTIN_NODE_INFO,
)

STAGE = "Stage 21: Subgraphs + MarkdownNote"

# Bundled subgraph test fixture — a workflow with 2-level nested subgraphs
_SUBGRAPH_WORKFLOW = _REPO_ROOT / "autoflow-test-suite" / "fixtures" / "subgraph-x2" / "workflow-subgraph.json"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Flow
    from autoflow.api import ApiFlow, convert_workflow, _sanitize_api_prompt

    # --- Subgraph flattening (bundled workflow as flat, subgraph-x2 fixture as subgraph) ---
    def t_21_1():
        oi = BUILTIN_NODE_INFO
        wf_flat = json.loads(_BUNDLED_WORKFLOW.read_text(encoding="utf-8"))
        wf_sg = json.loads(_SUBGRAPH_WORKFLOW.read_text(encoding="utf-8"))

        api_flat = Flow.load(wf_flat).convert(node_info=oi)
        api_sg = Flow.load(wf_sg).convert(node_info=oi)

        # Use unwrap() to get raw dict format for comparison
        raw_flat = getattr(api_flat, "unwrap", lambda: dict(api_flat))()
        raw_sg = getattr(api_sg, "unwrap", lambda: dict(api_sg))()

        # No UUID class_type should remain after flattening
        for node in raw_sg.values():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type", "")
            assert not (isinstance(ct, str) and "-" in ct and len(ct) >= 32), f"UUID class_type: {ct!r}"

        types_flat = sorted([n["class_type"] for n in raw_flat.values() if isinstance(n, dict) and "class_type" in n])
        types_sg = sorted([n["class_type"] for n in raw_sg.values() if isinstance(n, dict) and "class_type" in n])
        assert types_sg == types_flat, f"types mismatch: {types_sg} vs {types_flat}"

        save_ids = [nid for nid, n in raw_sg.items() if isinstance(n, dict) and n.get("class_type") == "SaveImage"]
        assert len(save_ids) == 1
        save = raw_sg[save_ids[0]]
        images = save.get("inputs", {}).get("images")
        assert isinstance(images, list) and len(images) == 2
        upstream = raw_sg.get(str(images[0]))
        assert upstream is not None and upstream.get("class_type") == "VAEDecode"
        return {
            "input": f"flat={len(raw_flat)} nodes, subgraph={len(raw_sg)} nodes",
            "output": f"types match: {types_sg}",
            "result": "✓ subgraph flattened correctly",
        }
    _run_test(collector, stage, "21.1", "Subgraph converts like flat workflow", t_21_1)

    # --- MarkdownNote stripping ---
    def t_21_2():
        prompt = {
            "1": {"class_type": "TotallyFakeNode", "inputs": {}},
            "2": {"class_type": "KSampler", "inputs": {}},
        }
        node_info = {"KSampler": {"input": {}}}
        out = _sanitize_api_prompt(prompt, node_info=node_info)
        assert "2" in out, "'2' missing from sanitized prompt"
        assert "1" not in out, "'1' not stripped from sanitized prompt"
        return {
            "input": "prompt with TotallyFakeNode + KSampler",
            "output": f"kept: {list(out.keys())}",
            "result": "✓ unknown node stripped",
        }
    _run_test(collector, stage, "21.2", "Sanitizer drops unknown nodes with node_info", t_21_2)

    def t_21_3():
        wf = convert_workflow(str(_BUNDLED_WORKFLOW), node_info=BUILTIN_NODE_INFO, server_url=None)
        class_types = [n.get("class_type") for n in wf.values() if isinstance(n, dict)]
        assert "MarkdownNote" not in class_types, f"MarkdownNote found in: {class_types}"
        return {
            "input": f"convert_workflow({_BUNDLED_WORKFLOW.name})",
            "output": f"class_types: {class_types}",
            "result": "✓ MarkdownNote absent",
        }
    _run_test(collector, stage, "21.3", "convert_workflow skips MarkdownNote", t_21_3)

    _print_stage_summary(collector, stage)
