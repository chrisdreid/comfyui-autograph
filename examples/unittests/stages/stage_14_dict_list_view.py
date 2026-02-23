"""Stage 14 — DictView / ListView: proxy object operations."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import ResultCollector, _run_test, _print_stage_summary  # noqa: E402

STAGE = "Stage 14: DictView / ListView"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow.models import DictView, ListView

    def t_14_1():
        d = {"foo": 1, "bar": "baz"}
        dv = DictView(d)
        assert dv.foo == 1, f"dv.foo = {dv.foo}"
        assert dv.bar == "baz", f"dv.bar = {dv.bar}"
        return {"input": "DictView({'foo':1,'bar':'baz'})", "output": f"foo={dv.foo}, bar={dv.bar}", "result": "✓ dot-read"}
    _run_test(collector, stage, "14.1", "DictView dot-read", t_14_1)

    def t_14_2():
        d = {"x": 10}
        dv = DictView(d)
        dv.x = 20
        assert d["x"] == 20, f"Original not mutated: {d}"
        return {"input": "dv.x = 20", "output": f"d['x']={d['x']}", "result": "✓ propagates"}
    _run_test(collector, stage, "14.2", "DictView dot-write propagates", t_14_2)

    def t_14_3():
        d = {"a": 1}
        dv = DictView(d)
        assert dv["a"] == 1, f"dv['a'] = {dv['a']}"
        dv["a"] = 99
        assert d["a"] == 99, f"Original not mutated: {d}"
        return {"input": "dv['a']=99", "output": f"d['a']={d['a']}", "result": "✓ bracket read/write"}
    _run_test(collector, stage, "14.3", "DictView bracket read/write", t_14_3)

    def t_14_4():
        d = {"a": 1, "b": 2}
        dv = DictView(d)
        del dv["a"]
        assert "a" not in d, f"Key not deleted from original: {d}"
        return {"input": "del dv['a']", "output": f"keys={list(d.keys())}", "result": "✓ deleted"}
    _run_test(collector, stage, "14.4", "del DictView['key']", t_14_4)

    def t_14_5():
        d = {"x": 1, "y": 2}
        dv = DictView(d)
        assert set(dv.keys()) == {"x", "y"}, f"keys() = {list(dv.keys())}"
        assert list(dv.values()) == [1, 2] or set(dv.values()) == {1, 2}
        assert len(list(dv.items())) == 2
        return {"input": "keys()/values()/items()", "output": f"keys={list(dv.keys())}", "result": "✓ all work"}
    _run_test(collector, stage, "14.5", "DictView keys()/values()/items()", t_14_5)

    def t_14_6():
        d = {"a": 1}
        dv = DictView(d)
        dv.update({"b": 2})
        assert d == {"a": 1, "b": 2}, f"update() failed: {d}"
        return {"input": "dv.update({'b':2})", "output": str(d), "result": "✓ merged"}
    _run_test(collector, stage, "14.6", "DictView update()", t_14_6)

    def t_14_7():
        d = {"a": 1, "b": 2}
        dv = DictView(d)
        val = dv.pop("a")
        assert val == 1, f"pop() returned {val}"
        assert "a" not in d, f"Key not removed: {d}"
        return {"input": "dv.pop('a')", "output": f"val={val}, keys={list(d.keys())}", "result": "✓ popped"}
    _run_test(collector, stage, "14.7", "DictView pop()", t_14_7)

    def t_14_8():
        d = {"a": 1}
        dv = DictView(d)
        dv2 = dv.copy()
        assert isinstance(dv2, (DictView, dict)), f"copy() returned {type(dv2)}"
        dv2["a"] = 99
        assert d["a"] == 1, "copy() should be independent"
        return {"input": "dv.copy() → modify copy", "output": f"orig={d['a']}, copy={dv2['a']}", "result": "✓ independent"}
    _run_test(collector, stage, "14.8", "DictView copy()", t_14_8)

    def t_14_9():
        dv = DictView({"x": 1})
        r = repr(dv)
        s = str(dv)
        assert isinstance(r, str) and len(r) > 0, f"repr() = {r!r}"
        assert isinstance(s, str) and len(s) > 0, f"str() = {s!r}"
        return {"input": "repr(dv), str(dv)", "output": f"repr={r[:40]}", "result": "✓ string ops"}
    _run_test(collector, stage, "14.9", "DictView repr()/str()", t_14_9)

    def t_14_10():
        data = [10, 20, 30]
        lv = ListView(data)
        assert len(lv) == 3, f"len(lv) = {len(lv)}"
        assert lv[0] == 10, f"lv[0] = {lv[0]}"
        assert lv[2] == 30, f"lv[2] = {lv[2]}"
        items = list(lv)
        assert items == [10, 20, 30], f"list(lv) = {items}"
        return {"input": "ListView([10,20,30])", "output": f"len={len(lv)}, items={items}", "result": "✓ iter+index"}
    _run_test(collector, stage, "14.10", "ListView iteration + indexing", t_14_10)

    _print_stage_summary(collector, stage)
