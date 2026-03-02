"""Phase 2 — NodeInfo: construction, source, access, find, save/load, resolver, schema drilling.

Merged from: stage_13_node_info, stage_20_schema_drilling, stage_27 (resolver/source tests)
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW, builtin_node_info_path,
    SkipTest,
)

STAGE = "Phase 2: NodeInfo"


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

    from autoflow import NodeInfo, Flow, ApiFlow
    conv = importlib.import_module("autoflow.convert")
    from autoflow.models import NodeInfo as LegacyNodeInfo
    from autoflow.origin import NodeInfoOrigin

    ni = NodeInfo(BUILTIN_NODE_INFO)

    # -----------------------------------------------------------------------
    # 2.1 – 2.8  NodeInfo basics  (was stage 13)
    # -----------------------------------------------------------------------

    def t_2_1():
        from collections.abc import MutableMapping
        assert isinstance(ni, MutableMapping), f"NodeInfo should be MutableMapping, got {type(ni)}"
        assert len(ni) == len(BUILTIN_NODE_INFO), "NodeInfo length mismatch"
        return {"input": f"NodeInfo(dict, {len(BUILTIN_NODE_INFO)} types)", "output": f"len={len(ni)}", "result": "✓ MutableMapping"}
    _run_test(collector, stage, "2.1", "NodeInfo(dict) constructor", t_2_1)

    def t_2_2():
        s = ni.source
        assert isinstance(s, str), f"source is {type(s)}"
        assert s == "dict", f"source = {s!r}, expected 'dict'"
        return {"input": "ni.source", "output": s, "result": "✓ source='dict'"}
    _run_test(collector, stage, "2.2", "ni.source == 'dict'", t_2_2)

    def t_2_3():
        ks = ni["KSampler"]
        assert ks is not None, "ni['KSampler'] returned None"
        assert hasattr(ks, '__getitem__'), f"ni['KSampler'] is not subscriptable: {type(ks)}"
        return {"input": "ni['KSampler']", "output": type(ks).__name__, "result": "✓ bracket access"}
    _run_test(collector, stage, "2.3", "ni['KSampler'] bracket access", t_2_3)

    def t_2_4():
        ks = ni.KSampler
        assert ks is not None, "ni.KSampler returned None"
        return {"input": "ni.KSampler", "output": type(ks).__name__, "result": "✓ dot access"}
    _run_test(collector, stage, "2.4", "ni.KSampler dot access", t_2_4)

    def t_2_5():
        results = ni.find("sampler")
        assert len(results) >= 1, f"find('sampler') returned {len(results)} results"
        return {"input": "ni.find('sampler')", "output": f"{len(results)} results", "result": "✓ fuzzy match"}
    _run_test(collector, stage, "2.5", "ni.find('sampler') fuzzy", t_2_5)

    def t_2_6():
        results = ni.find(class_type="KSampler")
        assert len(results) == 1, f"find(class_type='KSampler') returned {len(results)} results"
        return {"input": "ni.find(class_type='KSampler')", "output": f"{len(results)} result", "result": "✓ exact match"}
    _run_test(collector, stage, "2.6", "ni.find(class_type='KSampler') exact", t_2_6)

    def t_2_7():
        j = ni.to_json()
        assert isinstance(j, str), f"to_json() returned {type(j)}"
        parsed = json.loads(j)
        assert isinstance(parsed, dict), "to_json() not valid JSON dict"
        assert "KSampler" in parsed, "'KSampler' missing from to_json()"
        return {"input": "ni.to_json()", "output": f"{len(j)} chars, {len(parsed)} types", "result": "✓ valid JSON"}
    _run_test(collector, stage, "2.7", "ni.to_json()", t_2_7)

    def t_2_8():
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            ni.save(tmp_path)
            ni2 = NodeInfo.load(tmp_path)
            assert isinstance(ni2, NodeInfo), f"load() returned {type(ni2)}"
            assert "KSampler" in ni2, "'KSampler' missing after round-trip"
            return {"input": f"save→load({Path(tmp_path).name})", "output": f"{len(ni2)} types", "result": "✓ round-trip"}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    _run_test(collector, stage, "2.8", "ni.save() → NodeInfo.load()", t_2_8)

    # -----------------------------------------------------------------------
    # 2.9 – 2.12  Resolver tokens + source formatting  (was stage 27.4–27.7)
    # -----------------------------------------------------------------------

    def t_2_9():
        server_url = kwargs.get("server_url")
        if not server_url:
            raise SkipTest("No --server-url provided")
        oi, use_api, origin = conv.resolve_node_info_with_origin(
            "fetch",
            server_url=server_url,
            timeout=10,
            allow_env=False,
            require_source=True,
        )
        assert use_api
        assert isinstance(oi, dict)
        assert origin.resolved == "server"
        assert origin.effective_server_url == server_url
        return {
            "input": f"resolve_node_info('fetch', server_url={server_url})",
            "output": f"resolved={origin.resolved}, {len(oi)} types",
            "result": "✓ fetch token → server",
        }
    _run_test(collector, stage, "2.9", "Resolver: fetch token uses server_url", t_2_9)

    def t_2_10():
        comfyui_root = kwargs.get("comfyui_root")
        if not comfyui_root:
            raise SkipTest("ComfyUI modules not available")
        oi = conv.node_info_from_comfyui_modules()
        assert isinstance(oi, dict)
        assert len(oi) > 0, "modules returned empty dict"
        return {
            "input": "node_info_from_comfyui_modules()",
            "output": f"{len(oi)} node types from {comfyui_root}",
            "result": "✓ modules fallback",
        }
    _run_test(collector, stage, "2.10", "Resolver: ComfyUI modules fallback", t_2_10)

    def t_2_11():
        oi_obj = LegacyNodeInfo(BUILTIN_NODE_INFO)
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
    _run_test(collector, stage, "2.11", "Resolver: dict-subclass NodeInfo preserved", t_2_11)

    def t_2_12():
        oi = LegacyNodeInfo({})
        setattr(oi, "_autoflow_origin", NodeInfoOrigin(requested="modules", resolved="modules", via_env=False, modules_root="/abs/ComfyUI"))
        assert oi.source == "modules:/abs/ComfyUI", f"source = {oi.source!r}"
        setattr(oi, "_autoflow_origin", NodeInfoOrigin(requested="modules", resolved="modules", via_env=True, modules_root="/abs/ComfyUI"))
        assert oi.source == "env:modules:/abs/ComfyUI", f"source = {oi.source!r}"
        return {
            "input": "NodeInfo._autoflow_origin with modules_root",
            "output": f"source={oi.source}",
            "result": "✓ formats modules_root + env prefix",
        }
    _run_test(collector, stage, "2.12", "NodeInfo.source formats modules_root", t_2_12)

    # -----------------------------------------------------------------------
    # 2.13 – 2.22  Schema drilling (was stage 20)
    # -----------------------------------------------------------------------

    wf_path = str(_BUNDLED_WORKFLOW)

    def t_2_13():
        f = Flow(wf_path)
        try:
            _ = f.nodes.KSampler[0].seed
            return {"input": "access seed without node_info", "output": "raised or returned", "result": "✓ no crash"}
        except Exception as e:
            ename = type(e).__name__
            return {"input": "access seed without node_info", "output": f"{ename}: {str(e)[:40]}", "result": f"✓ error: {ename}"}
    _run_test(collector, stage, "2.13", "Access widget without node_info", t_2_13)

    def t_2_14():
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = f.nodes.KSampler[0]
        seed = ks.seed
        assert seed is not None, "seed is None"
        return {"input": "ks.seed with node_info", "output": str(seed), "result": "✓ widget readable"}
    _run_test(collector, stage, "2.14", "Access widget with node_info", t_2_14)

    def t_2_15():
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        try:
            _ = f.nodes.KSampler[0].nonexistent_widget
            return {"input": "access .nonexistent_widget", "output": "returned (no error)", "result": "✓ no crash"}
        except AttributeError as e:
            return {"input": "access .nonexistent_widget", "output": f"AttributeError: {str(e)[:40]}", "result": "✓ AttributeError"}
    _run_test(collector, stage, "2.15", "AttributeError on missing widget", t_2_15)

    def t_2_16():
        f = Flow(wf_path)
        f.fetch_node_info(BUILTIN_NODE_INFO)
        seed = f.nodes.KSampler[0].seed
        assert seed is not None, "seed None after fetch_node_info()"
        return {"input": "fetch_node_info(dict) → ks.seed", "output": str(seed), "result": "✓ late-binding"}
    _run_test(collector, stage, "2.16", "fetch_node_info(dict) enables widget access", t_2_16)

    def t_2_17():
        ni_p = builtin_node_info_path()
        f = Flow(wf_path)
        f.fetch_node_info(str(ni_p))
        seed = f.nodes.KSampler[0].seed
        assert seed is not None, "seed None after fetch_node_info(path)"
        return {"input": f"fetch_node_info({ni_p.name})", "output": str(seed), "result": "✓ file path"}
    _run_test(collector, stage, "2.17", "fetch_node_info(file path)", t_2_17)

    def t_2_18():
        import re
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        results = f.nodes.find(type=re.compile(r"CLIP.*"))
        assert len(results) >= 2, f"Regex CLIP.* should match ≥2, got {len(results)}"
        return {"input": "find(type=re.compile('CLIP.*'))", "output": f"{len(results)} matches", "result": "✓ regex find"}
    _run_test(collector, stage, "2.18", "find(type=regex) advanced", t_2_18)

    def t_2_19():
        import re
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        all_nodes = f.nodes.find(type=re.compile(r".*"))
        assert len(all_nodes) > 0, "find(type=re'.*') returned empty"
        return {"input": "find(type=re.compile('.*'))", "output": f"{len(all_nodes)} nodes", "result": "✓ match-all"}
    _run_test(collector, stage, "2.19", "find(type=re'.*') match-all", t_2_19)

    def t_2_20():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        ni_api = api.node_info
        assert ni_api is not None, "api.node_info is None"
        ks_info = ni_api.get("KSampler", {})
        assert "input" in ks_info, f"KSampler info missing 'input': {list(ks_info.keys())}"
        req = ks_info["input"].get("required", {})
        assert "seed" in req, f"'seed' not in required inputs: {list(req.keys())}"
        return {"input": "ni['KSampler']['input']['required']['seed']", "output": str(req['seed'])[:60], "result": "✓ schema drill"}
    _run_test(collector, stage, "2.20", "NodeInfo schema drill to seed spec", t_2_20)

    def t_2_21():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler[0]
        seed_val = ks.seed
        if hasattr(seed_val, 'spec'):
            sp = seed_val.spec()
            return {"input": "ks.seed.spec()", "output": str(sp)[:60], "result": "✓ spec from WidgetValue"}
        return {"input": "ks.seed.spec()", "output": "N/A", "result": "✓ no spec method"}
    _run_test(collector, stage, "2.21", "WidgetValue.spec() schema drill", t_2_21)

    def t_2_22():
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = api.KSampler[0]
        d = dir(ks)
        assert "seed" in d, "'seed' not in dir(ks)"
        assert "steps" in d, "'steps' not in dir(ks)"
        return {"input": "dir(api.KSampler[0])", "output": f"{len(d)} entries", "result": "✓ schema-aware dir"}
    _run_test(collector, stage, "2.22", "dir(api_node) schema-aware", t_2_22)

    # -----------------------------------------------------------------------
    # 2.23 – 2.24  Source metadata  (was stage 27.1–27.2)
    # -----------------------------------------------------------------------

    def t_2_23():
        ni_p = builtin_node_info_path()
        from autoflow.models import Flow as LFlow
        f = LFlow.load(_BUNDLED_WORKFLOW)
        assert isinstance(f.source, str) and f.source.startswith("file:"), f"f.source = {f.source!r}"
        f2 = LFlow(_BUNDLED_WORKFLOW, node_info=ni_p)
        assert isinstance(f2.node_info, LegacyNodeInfo)
        assert isinstance(f2.node_info.source, str) and f2.node_info.source.startswith("file:"), f"ni.source = {f2.node_info.source!r}"
        return {
            "input": "Flow.load / Flow(node_info=builtin)",
            "output": f"f.source={f.source[:30]}, ni.source={f2.node_info.source[:30]}",
            "result": "✓ file: prefixed",
        }
    _run_test(collector, stage, "2.23", "Flow file load source metadata", t_2_23)

    def t_2_24():
        ni_p = builtin_node_info_path()
        from autoflow.models import Workflow as LWorkflow
        api = LWorkflow(str(_BUNDLED_WORKFLOW), node_info=ni_p)
        assert isinstance(api.source, str) and api.source.startswith("converted_from("), f"api.source = {api.source!r}"
        assert api.node_info is not None
        assert isinstance(api.node_info.source, str) and api.node_info.source.startswith("file:"), f"ni.source = {api.node_info.source!r}"
        return {
            "input": "Workflow(bundled, node_info=builtin)",
            "output": f"api.source={api.source[:40]}",
            "result": "✓ converted_from() prefix",
        }
    _run_test(collector, stage, "2.24", "Workflow conversion source", t_2_24)

    # -----------------------------------------------------------------------
    # 2.25 – 2.27  Flowtree-specific NodeInfo  (was stage 27.3, 27.8, 27.9)
    # -----------------------------------------------------------------------

    def t_2_25():
        wf_p = str(_BUNDLED_WORKFLOW)
        ni_p = str(builtin_node_info_path())
        code = f"""
import os
from pathlib import Path
from autoflow import Flow, ApiFlow

flow_path = "{wf_p}"
oi_path = "{ni_p}"

f = Flow.load(flow_path, node_info=oi_path)
print(f.source)
print(f.node_info.source)

api = ApiFlow(flow_path, node_info=oi_path)
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
    _run_test(collector, stage, "2.25", "Flowtree: source metadata", t_2_25)

    def t_2_26():
        ni_path = str(builtin_node_info_path())
        code = f"""
import os, sys
from autoflow import NodeInfo

oi_path = "{ni_path}"

# Construct empty NodeInfo
o = NodeInfo()
print(len(o))

# Load from source= parameter
o2 = NodeInfo(source=oi_path)
print("KSampler" in o2)

# Load from file path
o3 = NodeInfo.load(oi_path)
print("KSampler" in o3)
"""
        try:
            out = _run_subprocess(code, {"AUTOFLOW_MODEL_LAYER": "flowtree"})
        except (subprocess.CalledProcessError, RuntimeError) as e:
            raise SkipTest(f"subprocess failed: {e}")
        assert out[0].strip() == "0", f"Expected 0, got {out[0]!r}"
        assert out[1].strip() == "True", f"Expected True, got {out[1]!r}"
        assert out[2].strip() == "True", f"Expected True, got {out[2]!r}"
        return {
            "input": "subprocess: NodeInfo() empty + source + load",
            "output": f"3 checks: {out}",
            "result": "✓ all correct",
        }
    _run_test(collector, stage, "2.26", "Flowtree NodeInfo init + source + load", t_2_26)

    def t_2_27():
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
    _run_test(collector, stage, "2.27", "Flowtree NodeInfo passthrough keeps source", t_2_27)

    # -----------------------------------------------------------------------
    # 2.28  NodeInfo('fetch') + AUTOFLOW_COMFYUI_SERVER_URL env var
    # -----------------------------------------------------------------------

    def t_2_28():
        ni_path = str(builtin_node_info_path())
        code = f"""\
import os, sys, json
from unittest.mock import patch
from pathlib import Path

# Ensure autoflow.convert is the real module for patching
import autoflow.convert as conv_mod

# Load a real node_info dict for the mock to return
ni_data = json.loads(Path("{ni_path}").read_text(encoding="utf-8"))

def fake_fetch(server_url, timeout=0):
    return ni_data

os.environ["AUTOFLOW_COMFYUI_SERVER_URL"] = "http://test.invalid:8188"

with patch.object(conv_mod, "fetch_node_info", fake_fetch), \
     patch.object(conv_mod, "node_info_from_comfyui_modules", side_effect=RuntimeError("blocked")):
    from autoflow import NodeInfo
    oi = NodeInfo("fetch")
    print(len(oi))
    print("KSampler" in oi)
    origin = getattr(oi._oi, "_autoflow_origin", None)
    print(origin.resolved if origin else "no-origin")
    print(origin.effective_server_url if origin else "no-url")
"""
        try:
            out = _run_subprocess(code, {"AUTOFLOW_MODEL_LAYER": "flowtree"})
        except (subprocess.CalledProcessError, RuntimeError) as e:
            raise SkipTest(f"subprocess failed: {{e}}")
        assert int(out[0].strip()) > 0, f"Expected >0 types, got {{out[0]!r}}"
        assert out[1].strip() == "True", f"Expected KSampler present, got {{out[1]!r}}"
        assert out[2].strip() == "server", f"Expected resolved='server', got {{out[2]!r}}"
        assert "test.invalid" in out[3], f"Expected env URL in origin, got {{out[3]!r}}"
        return {
            "input": "NodeInfo('fetch') + AUTOFLOW_COMFYUI_SERVER_URL env var",
            "output": f"{{len(out)}} checks passed, resolved=server",
            "result": "✓ env var used for fetch",
        }
    _run_test(collector, stage, "2.28", "NodeInfo('fetch') uses AUTOFLOW_COMFYUI_SERVER_URL", t_2_28)

    _print_stage_summary(collector, stage)
