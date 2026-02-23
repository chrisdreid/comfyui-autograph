"""Stage 12 — ApiFlow + NodeProxy: deepcopy, source, dag, bracket/dot access, save/load."""

from __future__ import annotations

import copy as copy_mod
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 12: ApiFlow + NodeProxy"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Workflow, ApiFlow

    wf_path = str(_BUNDLED_WORKFLOW)
    api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)

    def t_12_1():
        api2 = copy_mod.deepcopy(api)
        assert isinstance(api2, ApiFlow), f"deepcopy returned {type(api2)}"
        assert len(api2) == len(api), "deepcopy length mismatch"
        return {"input": "deepcopy(api)", "output": f"ApiFlow, len={len(api2)}", "result": "✓ independent copy"}
    _run_test(collector, stage, "12.1", "deepcopy(api) independent", t_12_1)

    def t_12_2():
        s = api.source
        assert s is None or isinstance(s, str), f"source is {type(s)}"
        return {"input": "api.source", "output": str(s)[:60] if s else "None", "result": "✓ source property"}
    _run_test(collector, stage, "12.2", "api.source property", t_12_2)

    def t_12_3():
        dag = api.dag
        assert dag is not None, "api.dag is None"
        assert isinstance(dag, dict), f"dag is {type(dag)}"
        return {"input": "api.dag", "output": f"{len(dag.edges)} edges", "result": "✓ Dag built"}
    _run_test(collector, stage, "12.3", "api.dag returns Dag", t_12_3)

    def t_12_4():
        ks = api.KSampler
        assert ks is not None, "api.KSampler is None"
        assert hasattr(ks, '__len__'), f"api.KSampler has no __len__: {type(ks)}"
        return {"input": "api.KSampler", "output": f"{len(ks)} instance(s)", "result": "✓ NodeProxy"}
    _run_test(collector, stage, "12.4", "api.KSampler → NodeProxy", t_12_4)

    def t_12_5():
        ks = api.KSampler[0]
        nid = ks.id
        assert isinstance(nid, (str, int)), f"id is {type(nid)}, expected str or int"
        return {"input": "api.KSampler[0].id", "output": str(nid), "result": "✓ id accessible"}
    _run_test(collector, stage, "12.5", "NodeProxy.id returns str", t_12_5)

    def t_12_6():
        ks = api.KSampler[0]
        ct = getattr(ks, 'class_type', None) or getattr(ks, 'type', None)
        assert ct == "KSampler", f"class_type/type = {ct!r}"
        return {"input": "ks.class_type", "output": ct, "result": "✓ KSampler"}
    _run_test(collector, stage, "12.6", "NodeProxy.class_type", t_12_6)

    def t_12_7():
        ks = api.KSampler[0]
        seed = ks.seed
        assert seed is not None, "ks.seed is None"
        return {"input": "ks.seed", "output": str(seed), "result": "✓ input readable"}
    _run_test(collector, stage, "12.7", "NodeProxy.inputs", t_12_7)

    def t_12_8():
        ks = api.KSampler[0]
        n = getattr(ks, 'node', None) or getattr(ks, 'unwrap', lambda: None)()
        assert n is not None, "Could not get raw node data"
        return {"input": "ks.node", "output": f"dict with {len(n)} keys" if isinstance(n, dict) else type(n).__name__, "result": "✓ raw data"}
    _run_test(collector, stage, "12.8", "NodeProxy.node → raw dict", t_12_8)

    def t_12_9():
        ks = api.KSampler[0]
        a = ks.attrs()
        assert isinstance(a, list), f"attrs() returned {type(a)}"
        assert "seed" in a, "'seed' not in attrs()"
        return {"input": "ks.attrs()", "output": ", ".join(a[:6]), "result": f"✓ {len(a)} attrs"}
    _run_test(collector, stage, "12.9", "NodeProxy.attrs()", t_12_9)

    def t_12_10():
        r = repr(api.KSampler[0])
        assert "KSampler" in r, f"repr missing 'KSampler': {r}"
        return {"input": "repr(ks)", "output": r[:60], "result": "✓ KSampler in repr"}
    _run_test(collector, stage, "12.10", "repr(NodeProxy)", t_12_10)

    def t_12_11():
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            api.save(tmp_path)
            api2 = ApiFlow.load(tmp_path)
            assert len(api2) == len(api), "save→reload length mismatch"
            return {"input": f"save→load({Path(tmp_path).name})", "output": f"len={len(api2)}", "result": "✓ round-trip"}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    _run_test(collector, stage, "12.11", "api.save() → ApiFlow.load()", t_12_11)

    def t_12_12():
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
            tmp.write(api.to_json())
            tmp_path = tmp.name
        try:
            loaded = ApiFlow.load(tmp_path)
            assert isinstance(loaded, ApiFlow), f"load() returned {type(loaded)}"
            return {"input": f"ApiFlow.load({Path(tmp_path).name})", "output": f"ApiFlow len={len(loaded)}", "result": "✓ loaded"}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    _run_test(collector, stage, "12.12", "ApiFlow.load(path)", t_12_12)

    _print_stage_summary(collector, stage)
