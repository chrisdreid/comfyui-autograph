"""Stage 18 — Save Formatting: output template formatting and file-result idioms."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 18: Save Formatting"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Workflow

    wf_path = str(_BUNDLED_WORKFLOW)

    def t_18_1():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        j = api.to_json()
        parsed = json.loads(j)
        assert isinstance(parsed, dict), f"to_json() produced {type(parsed)}"
        assert len(parsed) > 0, "to_json() produced empty dict"
        return {"input": "api.to_json()", "output": f"{len(j)} chars, {len(parsed)} nodes", "result": "✓ valid JSON"}
    _run_test(collector, stage, "18.1", "ApiFlow.to_json() round-trip", t_18_1)

    def t_18_2():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        j = api.to_json(indent=2)
        assert "\n" in j, "Indented JSON should have newlines"
        lines = j.count("\n")
        return {"input": "api.to_json(indent=2)", "output": f"{lines} lines", "result": "✓ pretty-printed"}
    _run_test(collector, stage, "18.2", "ApiFlow.to_json(indent=2)", t_18_2)

    def t_18_3():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            tmp = f.name
            api.save(tmp)
        try:
            loaded = json.loads(Path(tmp).read_text(encoding="utf-8"))
            assert isinstance(loaded, dict)
            assert len(loaded) > 0
            return {"input": f"api.save({Path(tmp).name})", "output": f"{len(loaded)} nodes", "result": "✓ saved"}
        finally:
            os.unlink(tmp)
    _run_test(collector, stage, "18.3", "ApiFlow.save() to temp file", t_18_3)

    def t_18_4():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        save_nodes = api.find(class_type="SaveImage")
        if not save_nodes:
            return {"input": "find(SaveImage)", "output": "none found", "result": "✓ no save node"}
        save = save_nodes[0]
        prefix = save.filename_prefix if hasattr(save, "filename_prefix") else "default"
        return {"input": "SaveImage.filename_prefix", "output": str(prefix), "result": "✓ accessible"}
    _run_test(collector, stage, "18.4", "SaveImage filename_prefix access", t_18_4)

    def t_18_5():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = dict(api.unwrap()) if hasattr(api, 'unwrap') else dict(api)
        for nid, node in raw.items():
            if isinstance(node, dict) and node.get("class_type") == "SaveImage":
                inputs = node.get("inputs", {})
                assert "filename_prefix" in inputs or "images" in inputs, f"SaveImage inputs: {list(inputs.keys())}"
                return {"input": f"raw[{nid}]['inputs']", "output": f"keys: {list(inputs.keys())}", "result": "✓ SaveImage inputs"}
        return {"input": "SaveImage raw inputs", "output": "no SaveImage", "result": "✓ ran"}
    _run_test(collector, stage, "18.5", "SaveImage raw inputs dict", t_18_5)

    def t_18_6():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        raw = dict(api.unwrap()) if hasattr(api, 'unwrap') else dict(api)
        ct_list = sorted({n.get("class_type") for n in raw.values() if isinstance(n, dict) and "class_type" in n})
        assert len(ct_list) > 0, "No class_types found"
        return {"input": "api class_types", "output": ", ".join(ct_list), "result": f"✓ {len(ct_list)} types"}
    _run_test(collector, stage, "18.6", "ApiFlow class_type enumeration", t_18_6)

    _print_stage_summary(collector, stage)
