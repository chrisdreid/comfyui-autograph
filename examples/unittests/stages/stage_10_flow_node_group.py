"""Stage 10 — FlowNodeGroup: multi-instance nodes, iteration, broadcast, indexing."""

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

STAGE = "Stage 10: FlowNodeGroup"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Flow

    wf_path = str(_BUNDLED_WORKFLOW)
    f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
    clips = f.nodes.CLIPTextEncode

    def t_10_1():
        assert hasattr(clips, '__len__'), f"No __len__ on {type(clips)}"
        assert len(clips) == 2, f"Expected 2 CLIPTextEncode, got {len(clips)}"
        return {"input": "len(CLIPTextEncode)", "output": str(len(clips)), "result": "✓ == 2"}
    _run_test(collector, stage, "10.1", "len(group) == 2", t_10_1)

    def t_10_2():
        c0 = clips[0]
        c1 = clips[1]
        assert hasattr(c0, 'type'), f"clips[0] has no .type: {type(c0)}"
        assert hasattr(c1, 'type'), f"clips[1] has no .type: {type(c1)}"
        assert c0.id != c1.id, f"clips[0].id == clips[1].id == {c0.id}"
        return {"input": "clips[0], clips[1]", "output": f"id0={c0.id}, id1={c1.id}", "result": "✓ distinct proxies"}
    _run_test(collector, stage, "10.2", "group[0]/[1] → FlowNodeProxy", t_10_2)

    def t_10_3():
        refs = list(clips)
        assert len(refs) == 2, f"iter yielded {len(refs)} items"
        for r in refs:
            assert hasattr(r, 'type'), f"iter yielded {type(r)} without .type"
        return {"input": "list(clips)", "output": f"{len(refs)} refs", "result": "✓ iterable"}
    _run_test(collector, stage, "10.3", "iter(group) yields node refs", t_10_3)

    def t_10_4():
        val = clips.text
        assert val is not None, "group.text is None"
        return {"input": "clips.text (broadcast)", "output": str(val)[:40], "result": "✓ broadcast read"}
    _run_test(collector, stage, "10.4", "Broadcast read: group.text", t_10_4)

    def t_10_5():
        f2 = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        clips2 = f2.nodes.CLIPTextEncode
        clips2[0].text = "test text"
        actual = str(clips2[0].text)
        assert actual == "test text", f"Expected 'test text', got {actual!r}"
        return {"input": "clips[0].text = 'test text'", "output": actual, "result": "✓ individual write"}
    _run_test(collector, stage, "10.5", "Individual write: group[0].text = 'new'", t_10_5)

    def t_10_6():
        a = clips.attrs()
        assert isinstance(a, list), f"attrs() returned {type(a)}"
        assert "text" in a, f"'text' not in attrs(): {a}"
        return {"input": "clips.attrs()", "output": ", ".join(a), "result": f"✓ {len(a)} attrs"}
    _run_test(collector, stage, "10.6", "group.attrs()", t_10_6)

    def t_10_7():
        d = dir(clips)
        assert "text" in d, "'text' not in dir(group)"
        return {"input": "dir(clips)", "output": f"{len(d)} entries", "result": "✓ text in dir"}
    _run_test(collector, stage, "10.7", "dir(group) includes widgets", t_10_7)

    def t_10_8():
        k = list(clips.keys())
        v = list(clips.values())
        it = list(clips.items())
        assert len(k) > 0, "keys() is empty"
        assert len(v) > 0, "values() is empty"
        assert len(it) > 0, "items() is empty"
        return {"input": "keys()/values()/items()", "output": f"{len(k)} keys", "result": "✓ all non-empty"}
    _run_test(collector, stage, "10.8", "group.keys()/values()/items()", t_10_8)

    def t_10_9():
        lst = clips.to_list()
        assert isinstance(lst, list), f"to_list() returned {type(lst)}"
        assert len(lst) == 2, f"to_list() has {len(lst)} items"
        return {"input": "clips.to_list()", "output": f"{len(lst)} items", "result": "✓ list"}
    _run_test(collector, stage, "10.9", "group.to_list()", t_10_9)

    def t_10_10():
        dct = clips.to_dict()
        assert isinstance(dct, dict), f"to_dict() returned {type(dct)}"
        assert len(dct) == 2, f"to_dict() has {len(dct)} items"
        return {"input": "clips.to_dict()", "output": f"{len(dct)} entries", "result": "✓ dict"}
    _run_test(collector, stage, "10.10", "group.to_dict()", t_10_10)

    def t_10_11():
        r = repr(clips)
        assert "CLIPTextEncode" in r, f"repr missing 'CLIPTextEncode': {r}"
        return {"input": "repr(clips)", "output": r[:60], "result": "✓ CLIPTextEncode in repr"}
    _run_test(collector, stage, "10.11", "repr(group)", t_10_11)

    def t_10_12():
        last = clips[-1]
        assert hasattr(last, 'type'), f"clips[-1] has no .type: {type(last)}"
        assert last.id == clips[1].id, "clips[-1] should equal clips[1]"
        return {"input": "clips[-1]", "output": f"id={last.id}", "result": "✓ negative index"}
    _run_test(collector, stage, "10.12", "Negative index group[-1]", t_10_12)

    _print_stage_summary(collector, stage)
