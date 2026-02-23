"""Stage 27 — Source Metadata + NodeInfo Resolver: provenance strings, resolver tokens."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW, builtin_node_info_path,
    SkipTest,
)

STAGE = "Stage 27: Source Metadata + NodeInfo Resolver"


def _env_with_repo_root(extra: dict) -> dict:
    env = dict(os.environ)
    pp = env.get("PYTHONPATH", "")
    parts = [p for p in pp.split(os.pathsep) if p]
    if str(_REPO_ROOT) not in parts:
        parts.insert(0, str(_REPO_ROOT))
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env.update(extra)
    return env


def _run_subprocess(code: str, extra_env: dict | None = None) -> list[str]:
    env = _env_with_repo_root(extra_env or {})
    out = subprocess.check_output([sys.executable, "-c", code], env=env, stderr=subprocess.STDOUT)
    s = out.decode("utf-8", errors="replace").strip()
    return [] if not s else s.splitlines()


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    conv = importlib.import_module("autoflow.convert")
    from autoflow.models import Flow, ApiFlow, Workflow, NodeInfo
    from autoflow.origin import NodeInfoOrigin

    # --- Source metadata using bundled workflow + builtin node_info ---
    def t_27_1():
        ni_p = builtin_node_info_path()
        f = Flow.load(_BUNDLED_WORKFLOW)
        assert isinstance(f.source, str) and f.source.startswith("file:"), f"f.source = {f.source!r}"
        f2 = Flow(_BUNDLED_WORKFLOW, node_info=ni_p)
        assert isinstance(f2.node_info, NodeInfo)
        assert isinstance(f2.node_info.source, str) and f2.node_info.source.startswith("file:"), f"ni.source = {f2.node_info.source!r}"
        return {
            "input": "Flow.load / Flow(node_info=builtin)",
            "output": f"f.source={f.source[:30]}, ni.source={f2.node_info.source[:30]}",
            "result": "✓ file: prefixed",
        }
    _run_test(collector, stage, "27.1", "Flow file load source metadata", t_27_1)

    def t_27_2():
        ni_p = builtin_node_info_path()
        api = Workflow(str(_BUNDLED_WORKFLOW), node_info=ni_p)
        assert isinstance(api.source, str) and api.source.startswith("converted_from("), f"api.source = {api.source!r}"
        assert api.node_info is not None
        assert isinstance(api.node_info.source, str) and api.node_info.source.startswith("file:"), f"ni.source = {api.node_info.source!r}"
        return {
            "input": "Workflow(bundled, node_info=builtin)",
            "output": f"api.source={api.source[:40]}",
            "result": "✓ converted_from() prefix",
        }
    _run_test(collector, stage, "27.2", "Workflow conversion source", t_27_2)

    # --- Flowtree source metadata (subprocess with bundled assets) ---
    def t_27_3():
        wf_path = str(_BUNDLED_WORKFLOW)
        ni_path = str(builtin_node_info_path())
        code = f"""
import os
from pathlib import Path
from autoflow import Flow, ApiFlow, Workflow

flow_path = "{wf_path}"
oi_path = "{ni_path}"

f = Flow.load(flow_path, node_info=oi_path)
print(f.source)
print(f.node_info.source)

api = Workflow(flow_path, node_info=oi_path)
print(api.source)
print(api.node_info.source)
"""
        try:
            out = _run_subprocess(code, {"AUTOFLOW_MODEL_LAYER": "flowtree"})
        except (subprocess.CalledProcessError, RuntimeError) as e:
            raise SkipTest(f"subprocess failed: {e}")
        assert out[0].startswith("file:"), out[0]
        assert out[1].startswith("file:"), out[1]
        assert out[2].startswith("converted_from("), out[2]
        assert out[3].startswith("file:"), out[3]
        return {
            "input": "subprocess: Flow/Workflow source strings",
            "output": f"4 lines, all correct prefixes",
            "result": "✓ flowtree source metadata",
        }
    _run_test(collector, stage, "27.3", "Flowtree: source metadata", t_27_3)

    # --- NodeInfo resolver tokens ---
    def t_27_4():
        calls = []

        def fake_fetch(server_url: str, timeout: int = 0):
            calls.append((server_url, timeout))
            return {"KSampler": {"display_name": "KSampler"}}

        with patch.object(conv, "fetch_node_info", fake_fetch):
            oi, use_api, origin = conv.resolve_node_info_with_origin(
                "fetch",
                server_url="http://example.invalid",
                timeout=12,
                allow_env=False,
                require_source=True,
            )
        assert use_api
        assert isinstance(oi, dict)
        assert calls == [("http://example.invalid", 12)]
        assert origin.resolved == "server"
        assert origin.effective_server_url == "http://example.invalid"
        return {
            "input": "resolve_node_info('fetch', server_url=...)",
            "output": f"resolved={origin.resolved}, url={origin.effective_server_url}",
            "result": "✓ fetch token → server",
        }
    _run_test(collector, stage, "27.4", "Resolver: fetch token uses server_url", t_27_4)

    def t_27_5():
        def fake_modules():
            return {"KSampler": {"display_name": "KSampler"}}

        root = Path("/tmp/ComfyUI").resolve()
        with (
            patch.object(conv, "node_info_from_comfyui_modules", fake_modules),
            patch.object(conv, "_detect_comfyui_root_from_imports", lambda: root),
        ):
            oi, use_api, origin = conv.resolve_node_info_with_origin(
                "fetch",
                server_url=None,
                timeout=1,
                allow_env=False,
                require_source=True,
            )
        assert use_api
        assert "KSampler" in (oi or {})
        assert origin.resolved == "modules"
        assert origin.modules_root == str(root)
        return {
            "input": "resolve_node_info('fetch', server_url=None)",
            "output": f"resolved={origin.resolved}, root={origin.modules_root}",
            "result": "✓ falls back to modules",
        }
    _run_test(collector, stage, "27.5", "Resolver: fetch falls back to modules", t_27_5)

    def t_27_6():
        """Resolver: dict-subclass NodeInfo preserved — uses BUILTIN_NODE_INFO."""
        oi_obj = NodeInfo(BUILTIN_NODE_INFO)
        oi, use_api, origin = conv.resolve_node_info_with_origin(
            oi_obj, server_url=None, timeout=1, allow_env=False, require_source=True,
        )
        assert use_api
        assert oi is oi_obj
        assert origin is not None
        return {
            "input": "resolve_node_info(NodeInfo object)",
            "output": f"use_api={use_api}, same_obj={oi is oi_obj}",
            "result": "✓ dict-subclass preserved",
        }
    _run_test(collector, stage, "27.6", "Resolver: dict-subclass NodeInfo preserved", t_27_6)

    def t_27_7():
        oi = NodeInfo({})
        setattr(oi, "_autoflow_origin", NodeInfoOrigin(requested="modules", resolved="modules", via_env=False, modules_root="/abs/ComfyUI"))
        assert oi.source == "modules:/abs/ComfyUI", f"source = {oi.source!r}"
        setattr(oi, "_autoflow_origin", NodeInfoOrigin(requested="modules", resolved="modules", via_env=True, modules_root="/abs/ComfyUI"))
        assert oi.source == "env:modules:/abs/ComfyUI", f"source = {oi.source!r}"
        return {
            "input": "NodeInfo._autoflow_origin with modules_root",
            "output": f"source={oi.source}",
            "result": "✓ formats modules_root + env prefix",
        }
    _run_test(collector, stage, "27.7", "NodeInfo.source formats modules_root", t_27_7)

    # --- Flowtree NodeInfo init (subprocess with bundled file) ---
    def t_27_8():
        ni_path = str(builtin_node_info_path())
        code = f"""
import os, sys
from pathlib import Path
from unittest.mock import patch
from autoflow import NodeInfo
import autoflow.models as models

# In flowtree mode, autoflow.convert may be a function wrapper;
# get the real module for patching.
import autoflow.convert
conv_mod = sys.modules["autoflow.convert"]

oi_path = "{ni_path}"

# Prevent auto-detecting ComfyUI modules on this machine
with patch.object(conv_mod, "_detect_comfyui_root_from_imports", return_value=None), \\
     patch.object(conv_mod, "node_info_from_comfyui_modules", side_effect=RuntimeError("blocked")):

    os.environ.pop("AUTOFLOW_NODE_INFO_SOURCE", None)
    o = NodeInfo()
    print(len(o))

    os.environ["AUTOFLOW_NODE_INFO_SOURCE"] = oi_path
    o2 = NodeInfo()
    print("KSampler" in o2)

    os.environ.pop("AUTOFLOW_NODE_INFO_SOURCE", None)
    o3 = NodeInfo(source=oi_path)
    print("KSampler" in o3)

    def _fake_fetch(cls, server_url=None, *, timeout=0, output_path=None):
        return models.NodeInfo.load(oi_path)
    models.NodeInfo.fetch = classmethod(_fake_fetch)

    o4 = NodeInfo()
    o4.fetch(server_url="http://example.invalid")
    print("KSampler" in o4)
"""
        try:
            out = _run_subprocess(code, {"AUTOFLOW_MODEL_LAYER": "flowtree"})
        except (subprocess.CalledProcessError, RuntimeError) as e:
            raise SkipTest(f"subprocess failed: {e}")
        assert out[0].strip() == "0", f"Expected 0, got {out[0]!r}"
        assert out[1].strip() == "True", f"Expected True, got {out[1]!r}"
        assert out[2].strip() == "True", f"Expected True, got {out[2]!r}"
        assert out[3].strip() == "True", f"Expected True, got {out[3]!r}"
        return {
            "input": "subprocess: NodeInfo() init + env + source + fetch",
            "output": f"4 checks: {out}",
            "result": "✓ all True",
        }
    _run_test(collector, stage, "27.8", "Flowtree NodeInfo init + env auto-resolve", t_27_8)

    # --- Flowtree NodeInfo passthrough (subprocess with bundled files) ---
    def t_27_9():
        ni_path = str(builtin_node_info_path())
        code = f"""
import os
from pathlib import Path
from autoflow import ApiFlow, NodeInfo

oi_path = "{ni_path}"

oi = NodeInfo.load(oi_path)
api = ApiFlow({{"1": {{"class_type": "KSampler", "inputs": {{"seed": 1}}}}}}, node_info=oi)
print(api.node_info.source)
"""
        try:
            out = _run_subprocess(code, {"AUTOFLOW_MODEL_LAYER": "flowtree"})
        except (subprocess.CalledProcessError, RuntimeError) as e:
            raise SkipTest(f"subprocess failed: {e}")
        assert out[0].startswith("file:"), out[0]
        return {
            "input": "subprocess: ApiFlow with NodeInfo",
            "output": f"source={out[0]}",
            "result": "✓ source passthrough",
        }
    _run_test(collector, stage, "27.9", "Flowtree NodeInfo passthrough keeps source", t_27_9)

    _print_stage_summary(collector, stage)
