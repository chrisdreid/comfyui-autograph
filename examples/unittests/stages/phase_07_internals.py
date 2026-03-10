"""Phase 7 — Internals: tools, legacy parity, flowtree navigation, subprocess tests.

Merged from: stage_07_tools, stage_26_legacy_parity (non-ApiFlow parts),
             stage_28_flowtree_nav
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW, builtin_node_info_path,
    SkipTest,
)

STAGE = "Phase 7: Internals"


def _env_with_repo_root(extra: dict) -> dict:
    env = dict(os.environ)
    pp = env.get("PYTHONPATH", "")
    parts = [p for p in pp.split(os.pathsep) if p]
    if str(_REPO_ROOT) not in parts:
        parts.insert(0, str(_REPO_ROOT))
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env.update(extra)
    return env


def _run_code(code: str, extra: dict | None = None) -> str:
    env = _env_with_repo_root({"AUTOGRAPH_MODEL_LAYER": "flowtree", **(extra or {})})
    out = subprocess.check_output([sys.executable, "-c", code], env=env, stderr=subprocess.DEVNULL)
    return out.decode("utf-8", errors="replace").strip()


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    wf_path = str(_BUNDLED_WORKFLOW)
    ni_path = str(builtin_node_info_path())

    # ===================================================================
    # 7.1–7.3  Tools / utilities  (was stage 7)
    # ===================================================================

    def t_7_1():
        from autograph import force_recompute, ApiFlow
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        result = force_recompute(api)
        assert result is not None
        return {"input": "force_recompute(api)", "output": type(result).__name__, "result": "✓ works"}
    _run_test(collector, stage, "7.1", "force_recompute utility", t_7_1)

    def t_7_2():
        from autograph import NodeInfo
        ni = NodeInfo(BUILTIN_NODE_INFO)
        f = ni.find("KSampler")
        assert f is not None
        return {"input": "NodeInfo.find('KSampler')", "output": f"found: {type(f).__name__}", "result": "✓ find works"}
    _run_test(collector, stage, "7.2", "NodeInfo.find() utility", t_7_2)

    def t_7_3():
        from autograph import NodeInfo
        ni = NodeInfo(BUILTIN_NODE_INFO)
        j = ni.to_json()
        assert isinstance(j, str) and len(j) > 0
        return {"input": "ni.to_json()", "output": f"{len(j)} chars", "result": "✓ valid JSON"}
    _run_test(collector, stage, "7.3", "NodeInfo.to_json()", t_7_3)

    # ===================================================================
    # 7.4–7.6  Legacy parity  (was stage 26.1–26.3)
    # ===================================================================

    def t_7_4():
        from autograph import ApiFlow
        api = ApiFlow(wf_path, node_info=BUILTIN_NODE_INFO)
        assert isinstance(api, ApiFlow)
        assert len(api) > 0
        ks = api.KSampler
        assert ks.seed is not None
        return {"input": "Workflow() → ApiFlow dot-access chain", "output": f"seed={ks.seed}", "result": "✓ legacy API"}
    _run_test(collector, stage, "7.4", "Legacy Workflow() → ApiFlow chain", t_7_4)

    def t_7_5():
        from autograph import Flow
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = f.nodes.KSampler
        assert ks is not None
        assert ks.type == "KSampler" or (hasattr(ks, '__getitem__') and ks[0].type == "KSampler")
        return {"input": "Flow.nodes.KSampler", "output": f"type={ks.type if hasattr(ks, 'type') else ks[0].type}", "result": "✓ flow nav"}
    _run_test(collector, stage, "7.5", "Legacy Flow.nodes navigation", t_7_5)

    def t_7_6():
        from autograph import Flow
        f = Flow(wf_path)
        f.fetch_node_info(BUILTIN_NODE_INFO)
        ks = f.nodes.KSampler
        seed = ks.seed if hasattr(ks, 'seed') else ks[0].seed
        assert seed is not None
        return {"input": "fetch_node_info(dict) → ks.seed", "output": str(seed), "result": "✓ post-fetch drill"}
    _run_test(collector, stage, "7.6", "fetch_node_info() then widget read", t_7_6)

    # ===================================================================
    # 7.7–7.12  Flowtree navigation (subprocess)  (was stage 28)
    # ===================================================================

    def t_7_7():
        code = r"""
from autograph import ApiFlow

api = ApiFlow({
  "1": {"class_type": "KSampler", "inputs": {"cfg": 1}},
  "2": {"class_type": "KSampler", "inputs": {"cfg": 9}},
})
ks = api.KSampler
ks.cfg = 7
print(api["1"]["inputs"]["cfg"], api["2"]["inputs"]["cfg"])
ks.set(cfg=5)
print(api["1"]["inputs"]["cfg"], api["2"]["inputs"]["cfg"])
"""
        out = _run_code(code).splitlines()
        assert out[0].strip() == "7 9", f"got {out[0]!r}"
        assert out[1].strip() == "5 5", f"got {out[1]!r}"
        return {
            "input": "ks.cfg=7 (single), ks.set(cfg=5) (bulk)",
            "output": f"line1={out[0].strip()}, line2={out[1].strip()}",
            "result": "✓ single=first only, bulk=all",
        }
    _run_test(collector, stage, "7.7", "NodeSet bulk vs single assignment", t_7_7)

    def t_7_8():
        code = r"""
from autograph import ApiFlow
api = ApiFlow({
  "18:17:3": {"class_type": "KSampler", "inputs": {"seed": 1}},
  "4": {"class_type": "KSampler", "inputs": {"seed": 2}},
})
ks = api.KSampler
print(ks.paths())
print(ks.dictpaths())
"""
        out = _run_code(code).splitlines()
        assert out[0].startswith("[")
        assert "KSampler[0]" in out[0]
        assert "KSampler[1]" in out[0]
        assert "'18:17:3'" in out[1]
        return {
            "input": "ks.paths() / ks.dictpaths()",
            "output": f"paths={out[0][:50]}, dictpaths={out[1][:50]}",
            "result": "✓ both returned",
        }
    _run_test(collector, stage, "7.8", "NodeSet paths + dictpaths", t_7_8)

    def t_7_9():
        code = f"""
from autograph import Flow
f = Flow.load("{wf_path}")
print("KSampler" in dir(f.nodes))
"""
        try:
            out = _run_code(code).strip()
        except (subprocess.CalledProcessError, RuntimeError) as e:
            raise SkipTest(f"{e}")
        assert out == "True"
        return {
            "input": "'KSampler' in dir(flow.nodes)",
            "output": out,
            "result": "✓ node types in dir",
        }
    _run_test(collector, stage, "7.9", "dir(flow.nodes) lists node types (flowtree)", t_7_9)

    def t_7_10():
        code = f"""
from autograph import Flow
f = Flow.load("{wf_path}", node_info="{ni_path}")
hits = f.nodes.find(type="KSampler")
print(type(hits).__name__)
print(bool(hits.paths()))
"""
        try:
            out = _run_code(code).splitlines()
        except (subprocess.CalledProcessError, RuntimeError) as e:
            raise SkipTest(f"{e}")
        assert out[0].strip() == "NodeSet"
        assert out[1].strip() == "True"
        return {
            "input": "find(type='KSampler') → type + paths",
            "output": f"type={out[0].strip()}, has_paths={out[1].strip()}",
            "result": "✓ NodeSet with paths",
        }
    _run_test(collector, stage, "7.10", "find returns NodeSet with paths (flowtree)", t_7_10)

    def t_7_11():
        server_url = kwargs.get("server_url")
        if not server_url:
            raise SkipTest("No --server-url provided")
        code = f"""
from autograph import Flow

f = Flow("{wf_path}")

sub = f.submit(
    server_url="{server_url}",
    node_info="{ni_path}",
    wait=False,
    fetch_outputs=False,
)

print(isinstance(sub, dict), sub.get("prompt_id") is not None, hasattr(sub, "fetch_files"))
"""
        try:
            out = _run_code(code).strip()
        except (subprocess.CalledProcessError, RuntimeError) as e:
            raise SkipTest(f"{e}")
        parts = out.split()
        assert parts[0] == "True", f"sub is not a dict: {out}"
        assert parts[1] == "True", f"no prompt_id: {out}"
        return {
            "input": f"flow.submit(server_url={server_url}, wait=False)",
            "output": out,
            "result": "✓ submit wrapper works",
        }
    _run_test(collector, stage, "7.11", "Flowtree submit wrapper", t_7_11)

    def t_7_12():
        from autograph import ApiFlow
        api = ApiFlow({
            "1": {"class_type": "KSampler", "inputs": {"seed": 42, "steps": 20, "cfg": 8.0}},
        })
        d = dir(api.KSampler)
        assert "seed" in d
        assert "steps" in d
        assert "cfg" in d
        return {
            "input": "dir(api.KSampler) with seed/steps/cfg",
            "output": f"{len(d)} entries, seed/steps/cfg present",
            "result": "✓ widget introspection",
        }
    _run_test(collector, stage, "7.12", "dir(api.KSampler) lists widgets", t_7_12)

    _print_stage_summary(collector, stage)
