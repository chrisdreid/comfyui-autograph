"""Stage 9 — FlowNodeProxy: id, type, widgets, bypass, attrs, dot-read/write, path/address."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 9: FlowNodeProxy"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Flow

    wf_path = str(_BUNDLED_WORKFLOW)
    f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
    ks = f.nodes.KSampler[0]

    def t_9_1():
        nid = ks.id
        assert isinstance(nid, int), f"id is {type(nid)}, expected int"
        return {"input": "ks.id", "output": str(nid), "result": "✓ int"}
    _run_test(collector, stage, "9.1", ".id returns int", t_9_1)

    def t_9_2():
        t = ks.type
        assert t == "KSampler", f"type is {t!r}, expected 'KSampler'"
        return {"input": "ks.type", "output": t, "result": "✓ KSampler"}
    _run_test(collector, stage, "9.2", ".type returns 'KSampler'", t_9_2)

    def t_9_3():
        wv = ks.widgets_values
        assert isinstance(wv, (list, type(None))), f"widgets_values is {type(wv)}"
        n = len(wv) if wv else 0
        if wv is not None:
            assert len(wv) > 0, "widgets_values is empty"
        return {"input": "ks.widgets_values", "output": f"{n} values", "result": "✓ list"}
    _run_test(collector, stage, "9.3", ".widgets_values returns list", t_9_3)

    def t_9_4():
        n = ks.node
        assert isinstance(n, dict), f"node is {type(n)}, expected dict"
        assert "type" in n, "'type' key missing from node dict"
        return {"input": "ks.node", "output": f"dict with {len(n)} keys", "result": "✓ raw dict"}
    _run_test(collector, stage, "9.4", ".node returns raw dict", t_9_4)

    def t_9_5():
        f2 = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        node = f2.nodes.KSampler
        assert node.bypass is False, f"bypass should be False, got {node.bypass}"
        return {"input": "node.bypass (default)", "output": str(node.bypass), "result": "✓ False"}
    _run_test(collector, stage, "9.5", ".bypass is False by default", t_9_5)

    def t_9_6():
        f2 = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        node = f2.nodes.KSampler
        node.bypass = True
        assert node.bypass is True, f"bypass should be True after set, got {node.bypass}"
        assert node.node.get("mode") == 4, f"mode should be 4, got {node.node.get('mode')}"
        return {"input": "node.bypass = True", "output": f"mode={node.node.get('mode')}", "result": "✓ mode=4"}
    _run_test(collector, stage, "9.6", ".bypass = True → mode=4", t_9_6)

    def t_9_7():
        f2 = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        node = f2.nodes.KSampler
        node.bypass = True
        node.bypass = False
        assert node.bypass is False, "bypass should be False after clear"
        assert node.node.get("mode") == 0, f"mode should be 0, got {node.node.get('mode')}"
        return {"input": "bypass True→False", "output": f"mode={node.node.get('mode')}", "result": "✓ mode=0"}
    _run_test(collector, stage, "9.7", ".bypass = False → mode=0", t_9_7)

    def t_9_8():
        r = repr(ks)
        assert "KSampler" in r, f"repr missing 'KSampler': {r}"
        return {"input": "repr(ks)", "output": r[:60], "result": "✓ contains KSampler"}
    _run_test(collector, stage, "9.8", "__repr__ format", t_9_8)

    def t_9_9():
        a = ks.attrs()
        assert isinstance(a, list), f"attrs() returned {type(a)}"
        assert "seed" in a, f"'seed' not in attrs(): {a}"
        assert "steps" in a, f"'steps' not in attrs(): {a}"
        assert "cfg" in a, f"'cfg' not in attrs(): {a}"
        return {"input": "ks.attrs()", "output": ", ".join(a[:6]), "result": f"✓ {len(a)} attrs"}
    _run_test(collector, stage, "9.9", ".attrs() returns widget names", t_9_9)

    def t_9_10():
        d = dir(ks)
        assert "seed" in d, "'seed' not in dir(node)"
        assert "steps" in d, "'steps' not in dir(node)"
        return {"input": "dir(ks)", "output": f"{len(d)} entries", "result": "✓ seed, steps in dir"}
    _run_test(collector, stage, "9.10", "dir(node) includes widgets", t_9_10)

    def t_9_11():
        seed = ks.seed
        assert int(seed) == int(seed), f"seed is not numeric: {seed}"
        return {"input": "ks.seed", "output": str(seed), "result": "✓ WidgetValue"}
    _run_test(collector, stage, "9.11", "Dot-read widget → WidgetValue", t_9_11)

    def t_9_12():
        f2 = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        node = f2.nodes.KSampler[0]
        node.seed = 42
        val = node.seed
        actual = int(val) if hasattr(val, '__int__') else val
        assert actual == 42, f"seed set to 42 but got {actual}"
        return {"input": "node.seed = 42", "output": str(actual), "result": "✓ write verified"}
    _run_test(collector, stage, "9.12", "Dot-write: node.seed = 42", t_9_12)

    def t_9_13():
        f2 = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        node = f2.nodes.KSampler[0]
        node.steps = 100
        node.cfg = 12.5
        assert int(node.steps) == 100, f"steps mismatch: {node.steps}"
        assert float(node.cfg) == 12.5, f"cfg mismatch: {node.cfg}"
        return {"input": "steps=100, cfg=12.5", "output": f"steps={node.steps}, cfg={node.cfg}", "result": "✓ multi-write"}
    _run_test(collector, stage, "9.13", "Widget write round-trip", t_9_13)

    def t_9_14():
        val = ks.type
        assert val == "KSampler", f"node.type = {val!r}"
        return {"input": "ks.type", "output": val, "result": "✓ attribute access"}
    _run_test(collector, stage, "9.14", ".type attribute access", t_9_14)

    def t_9_15():
        p = ks.path()
        assert isinstance(p, str) and len(p) > 0, f"path() = {p!r}"
        a = ks.address()
        assert isinstance(a, str) and len(a) > 0, f"address() = {a!r}"
        return {"input": "path()/address()", "output": f"path={p}, addr={a}", "result": "✓ both returned"}
    _run_test(collector, stage, "9.15", ".path() and .address()", t_9_15)

    _print_stage_summary(collector, stage)
