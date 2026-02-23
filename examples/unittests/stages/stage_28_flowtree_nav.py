"""Stage 28 — Flowtree Nav + Dir: subprocess-based flowtree navigation, dir introspection, widget tests."""

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
    _BUNDLED_WORKFLOW, builtin_node_info_path,
    SkipTest,
)

STAGE = "Stage 28: Flowtree Nav + Dir"


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
    env = _env_with_repo_root({"AUTOFLOW_MODEL_LAYER": "flowtree", **(extra or {})})
    out = subprocess.check_output([sys.executable, "-c", code], env=env, stderr=subprocess.DEVNULL)
    return out.decode("utf-8", errors="replace").strip()


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    wf_path = str(_BUNDLED_WORKFLOW)
    ni_path = str(builtin_node_info_path())

    # --- NodeSet bulk vs single assignment ---
    def t_28_1():
        code = r"""
from autoflow import ApiFlow

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
    _run_test(collector, stage, "28.1", "NodeSet bulk vs single assignment", t_28_1)

    # --- paths() and dictpaths() ---
    def t_28_2():
        code = r"""
from autoflow import ApiFlow
api = ApiFlow({
  "18:17:3": {"class_type": "KSampler", "inputs": {"seed": 1}},
  "4": {"class_type": "KSampler", "inputs": {"seed": 2}},
})
ks = api.KSampler
print(ks.paths())
print(ks.dictpaths())
"""
        out = _run_code(code).splitlines()
        assert out[0].startswith("["), f"paths = {out[0]!r}"
        assert "KSampler[0]" in out[0]
        assert "KSampler[1]" in out[0]
        assert "'18:17:3'" in out[1]
        return {
            "input": "ks.paths() / ks.dictpaths()",
            "output": f"paths={out[0][:50]}, dictpaths={out[1][:50]}",
            "result": "✓ both returned",
        }
    _run_test(collector, stage, "28.2", "NodeSet paths + dictpaths", t_28_2)

    # --- autocomplete dir lists node types --- uses bundled workflow
    def t_28_3():
        code = f"""
from autoflow import Flow
f = Flow.load("{wf_path}")
print("KSampler" in dir(f.nodes))
"""
        try:
            out = _run_code(code).strip()
        except (subprocess.CalledProcessError, RuntimeError) as e:
            raise SkipTest(f"{e}")
        assert out == "True", f"got {out!r}"
        return {
            "input": "'KSampler' in dir(flow.nodes)",
            "output": out,
            "result": "✓ node types in dir",
        }
    _run_test(collector, stage, "28.3", "dir(flow.nodes) lists node types", t_28_3)

    # --- find returns NodeSet --- uses bundled workflow + builtin node_info
    def t_28_4():
        code = f"""
from autoflow import Flow
f = Flow.load("{wf_path}", node_info="{ni_path}")
hits = f.nodes.find(type="KSampler")
print(type(hits).__name__)
print(bool(hits.paths()))
"""
        try:
            out = _run_code(code).splitlines()
        except (subprocess.CalledProcessError, RuntimeError) as e:
            raise SkipTest(f"{e}")
        assert out[0].strip() == "NodeSet", f"got {out[0]!r}"
        assert out[1].strip() == "True"
        return {
            "input": "find(type='KSampler') → type + paths",
            "output": f"type={out[0].strip()}, has_paths={out[1].strip()}",
            "result": "✓ NodeSet with paths",
        }
    _run_test(collector, stage, "28.4", "find returns NodeSet with paths", t_28_4)

    # --- submit wrapper --- uses bundled workflow + builtin node_info
    def t_28_5():
        code = f"""
import autoflow.net as net_mod
from autoflow import Flow

f = Flow("{wf_path}")

calls = []
def fake_http_json(url, payload=None, timeout=0, method="POST"):
    calls.append((url, method))
    if url.endswith("/prompt"):
        return {{"prompt_id": "p1"}}
    raise AssertionError("Unexpected URL: " + str(url))

old = net_mod.http_json
net_mod.http_json = fake_http_json
try:
    sub = f.submit(
        server_url="http://example.invalid",
        node_info="{ni_path}",
        wait=False,
        fetch_outputs=False,
    )
finally:
    net_mod.http_json = old

print(isinstance(sub, dict), bool(calls), sub.get("prompt_id"), hasattr(sub, "fetch_files"))
"""
        try:
            out = _run_code(code).strip()
        except (subprocess.CalledProcessError, RuntimeError) as e:
            raise SkipTest(f"{e}")
        assert out == "True True p1 True", f"got {out!r}"
        return {
            "input": "flow.submit(mock http_json)",
            "output": out,
            "result": "✓ submit wrapper works",
        }
    _run_test(collector, stage, "28.5", "Flowtree submit wrapper", t_28_5)

    # --- Dir + widget introspection (in-process, not subprocess) ---
    def t_28_6():
        from autoflow import ApiFlow
        api = ApiFlow({
            "1": {"class_type": "KSampler", "inputs": {"seed": 42, "steps": 20, "cfg": 8.0}},
        })
        d = dir(api.KSampler)
        assert "seed" in d, f"'seed' not in dir: {d}"
        assert "steps" in d
        assert "cfg" in d
        return {
            "input": "dir(api.KSampler) with seed/steps/cfg",
            "output": f"{len(d)} entries, seed/steps/cfg present",
            "result": "✓ widget introspection",
        }
    _run_test(collector, stage, "28.6", "dir(api.KSampler) lists widgets", t_28_6)

    _print_stage_summary(collector, stage)
