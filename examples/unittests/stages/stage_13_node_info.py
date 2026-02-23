"""Stage 13 — NodeInfo: construction, source, bracket/dot access, find, to_json, save/load."""

from __future__ import annotations

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
    BUILTIN_NODE_INFO,
)

STAGE = "Stage 13: NodeInfo"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import NodeInfo

    ni = NodeInfo(BUILTIN_NODE_INFO)

    def t_13_1():
        assert ni is not None, "NodeInfo(dict) returned None"
        from collections.abc import MutableMapping
        assert isinstance(ni, MutableMapping), f"NodeInfo should be MutableMapping, got {type(ni)}"
        assert len(ni) == len(BUILTIN_NODE_INFO), "NodeInfo length mismatch"
        return {"input": f"NodeInfo(dict, {len(BUILTIN_NODE_INFO)} types)", "output": f"len={len(ni)}", "result": "✓ MutableMapping"}
    _run_test(collector, stage, "13.1", "NodeInfo(dict) constructor", t_13_1)

    def t_13_2():
        s = ni.source
        assert isinstance(s, str), f"source is {type(s)}"
        assert s == "dict", f"source = {s!r}, expected 'dict'"
        return {"input": "ni.source", "output": s, "result": "✓ source='dict'"}
    _run_test(collector, stage, "13.2", "ni.source == 'dict'", t_13_2)

    def t_13_3():
        ks = ni["KSampler"]
        assert ks is not None, "ni['KSampler'] returned None"
        assert hasattr(ks, '__getitem__'), f"ni['KSampler'] is not subscriptable: {type(ks)}"
        return {"input": "ni['KSampler']", "output": type(ks).__name__, "result": "✓ bracket access"}
    _run_test(collector, stage, "13.3", "ni['KSampler'] bracket access", t_13_3)

    def t_13_4():
        ks = ni.KSampler
        assert ks is not None, "ni.KSampler returned None"
        return {"input": "ni.KSampler", "output": type(ks).__name__, "result": "✓ dot access"}
    _run_test(collector, stage, "13.4", "ni.KSampler dot access", t_13_4)

    def t_13_5():
        results = ni.find("sampler")
        assert len(results) >= 1, f"find('sampler') returned {len(results)} results"
        return {"input": "ni.find('sampler')", "output": f"{len(results)} results", "result": "✓ fuzzy match"}
    _run_test(collector, stage, "13.5", "ni.find('sampler') fuzzy", t_13_5)

    def t_13_6():
        results = ni.find(class_type="KSampler")
        assert len(results) == 1, f"find(class_type='KSampler') returned {len(results)} results"
        return {"input": "ni.find(class_type='KSampler')", "output": f"{len(results)} result", "result": "✓ exact match"}
    _run_test(collector, stage, "13.6", "ni.find(class_type='KSampler') exact", t_13_6)

    def t_13_7():
        j = ni.to_json()
        assert isinstance(j, str), f"to_json() returned {type(j)}"
        parsed = json.loads(j)
        assert isinstance(parsed, dict), "to_json() not valid JSON dict"
        assert "KSampler" in parsed, "'KSampler' missing from to_json()"
        return {"input": "ni.to_json()", "output": f"{len(j)} chars, {len(parsed)} types", "result": "✓ valid JSON"}
    _run_test(collector, stage, "13.7", "ni.to_json()", t_13_7)

    def t_13_8():
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
    _run_test(collector, stage, "13.8", "ni.save() → NodeInfo.load()", t_13_8)

    _print_stage_summary(collector, stage)
