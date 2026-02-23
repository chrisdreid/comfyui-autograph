"""Stage 8 — Flow Core API: constructor, source, links, extra, nodes, convert, dag, round-trip."""

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
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 8: Flow Core API"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from collections.abc import MutableMapping
    from autoflow import Flow, NodeInfo

    wf_path = str(_BUNDLED_WORKFLOW)

    def t_8_1():
        f = Flow(wf_path)
        assert f is not None, "Flow(path) returned None"
        assert isinstance(f, MutableMapping), "Flow should be a MutableMapping"
        return {"input": f"Flow({Path(wf_path).name})", "output": type(f).__name__, "result": "✓ MutableMapping"}
    _run_test(collector, stage, "8.1", "Flow(path) constructor", t_8_1)

    def t_8_2():
        f = Flow(wf_path)
        s = f.source
        assert isinstance(s, str), f"source is not str: {type(s)}"
        assert len(s) > 0, "source is empty"
        return {"input": "flow.source", "output": s[:60], "result": "✓ source string"}
    _run_test(collector, stage, "8.2", "flow.source property", t_8_2)

    def t_8_3():
        f = Flow(wf_path)
        links = f.links
        assert links is not None, "flow.links is None"
        assert hasattr(links, '__len__'), "links has no __len__"
        assert len(links) > 0, "links is empty"
        return {"input": "flow.links", "output": f"{len(links)} links", "result": "✓ links accessible"}
    _run_test(collector, stage, "8.3", "flow.links property", t_8_3)

    def t_8_4():
        f = Flow(wf_path)
        extra = f.extra
        assert extra is not None, "flow.extra is None"
        ds = extra.ds
        assert ds is not None, "extra.ds is None"
        return {"input": "flow.extra", "output": f"ds={ds!r}"[:60], "result": "✓ DictView"}
    _run_test(collector, stage, "8.4", "flow.extra returns DictView", t_8_4)

    def t_8_5():
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        nodes = f.nodes
        assert hasattr(nodes, '__len__'), "nodes has no __len__"
        assert hasattr(nodes, '__iter__'), "nodes has no __iter__"
        return {"input": "flow.nodes", "output": f"{len(nodes)} nodes", "result": "✓ iterable with len"}
    _run_test(collector, stage, "8.5", "flow.nodes is iterable with len", t_8_5)

    def t_8_6():
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        ks = f.nodes.KSampler
        assert ks is not None, "flow.nodes.KSampler is None"
        assert hasattr(ks, '__len__'), f"KSampler result has no __len__: {type(ks)}"
        assert len(ks) >= 1, "KSampler should have at least 1 instance"
        return {"input": "flow.nodes.KSampler", "output": f"{len(ks)} instance(s)", "result": "✓ FlowNodeProxy"}
    _run_test(collector, stage, "8.6", "flow.nodes.KSampler → FlowNodeProxy", t_8_6)

    def t_8_7():
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        clips = f.nodes.CLIPTextEncode
        assert clips is not None, "flow.nodes.CLIPTextEncode is None"
        assert hasattr(clips, '__len__'), f"CLIPTextEncode has no __len__: {type(clips)}"
        assert len(clips) == 2, f"Expected 2 CLIPTextEncode, got {len(clips)}"
        return {"input": "flow.nodes.CLIPTextEncode", "output": f"{len(clips)} instances", "result": "✓ FlowNodeGroup"}
    _run_test(collector, stage, "8.7", "flow.nodes.CLIPTextEncode → FlowNodeGroup", t_8_7)

    def t_8_8():
        f = Flow(wf_path)
        n = len(f.nodes)
        assert isinstance(n, int), f"len(nodes) is {type(n)}"
        assert n > 0, "len(nodes) is 0"
        return {"input": "len(flow.nodes)", "output": str(n), "result": "✓ non-zero"}
    _run_test(collector, stage, "8.8", "len(flow.nodes)", t_8_8)

    def t_8_9():
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        items = list(f.nodes)
        assert len(items) > 0, "iter(nodes) yielded nothing"
        assert isinstance(items[0], str), f"iter yielded {type(items[0])}"
        return {"input": "iter(flow.nodes)", "output": f"{len(items)} items, type={type(items[0]).__name__}", "result": "✓ iterable"}
    _run_test(collector, stage, "8.9", "iter(flow.nodes) yields items", t_8_9)

    def t_8_10():
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        k = f.nodes.keys()
        v = f.nodes.values()
        it = f.nodes.items()
        assert len(list(k)) > 0, "keys() empty"
        assert len(list(v)) > 0, "values() empty"
        assert len(list(it)) > 0, "items() empty"
        return {"input": "keys()/values()/items()", "output": f"{len(list(k))} keys", "result": "✓ all non-empty"}
    _run_test(collector, stage, "8.10", "flow.nodes.keys()/values()/items()", t_8_10)

    def t_8_11():
        f = Flow(wf_path)
        lst = f.nodes.to_list()
        dct = f.nodes.to_dict()
        assert isinstance(lst, list), f"to_list() returned {type(lst)}"
        assert isinstance(dct, dict), f"to_dict() returned {type(dct)}"
        assert len(lst) > 0, "to_list() empty"
        return {"input": "to_list()/to_dict()", "output": f"list={len(lst)}, dict={len(dct)}", "result": "✓ conversions"}
    _run_test(collector, stage, "8.11", "flow.nodes.to_list()/to_dict()", t_8_11)

    def t_8_12():
        from autoflow import ApiFlow
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        api = f.convert(node_info=BUILTIN_NODE_INFO)
        assert isinstance(api, ApiFlow), f"convert() returned {type(api)}"
        assert len(api) > 0, "Converted ApiFlow is empty"
        return {"input": "flow.convert()", "output": f"ApiFlow with {len(api)} nodes", "result": "✓ converted"}
    _run_test(collector, stage, "8.12", "flow.convert() → ApiFlow", t_8_12)

    def t_8_13():
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        result = f.convert_with_errors(node_info=BUILTIN_NODE_INFO)
        assert result is not None, "convert_with_errors returned None"
        assert hasattr(result, "ok"), "No .ok on result"
        assert result.ok, f"Conversion failed: {getattr(result, 'errors', '?')}"
        assert result.data is not None, "result.data is None"
        return {"input": "convert_with_errors()", "output": f"ok={result.ok}", "result": "✓ clean conversion"}
    _run_test(collector, stage, "8.13", "flow.convert_with_errors()", t_8_13)

    def t_8_14():
        f = Flow(wf_path)
        dag = f.dag
        assert dag is not None, "flow.dag is None"
        assert isinstance(dag, dict), f"dag is {type(dag)}, expected dict subclass"
        return {"input": "flow.dag", "output": f"{len(dag.edges)} edges", "result": "✓ Dag built"}
    _run_test(collector, stage, "8.14", "flow.dag returns Dag", t_8_14)

    def t_8_15():
        f = Flow(wf_path, node_info=BUILTIN_NODE_INFO)
        ni = f.node_info
        assert ni is not None, "flow.node_info is None after passing node_info="
        return {"input": "Flow(path, node_info=dict)", "output": f"{len(ni)} types", "result": "✓ stored"}
    _run_test(collector, stage, "8.15", "Flow(path, node_info=dict) stores node_info", t_8_15)

    def t_8_16():
        f = Flow(wf_path)
        f.fetch_node_info(BUILTIN_NODE_INFO)
        assert f.node_info is not None, "node_info still None after fetch_node_info(dict)"
        return {"input": "fetch_node_info(dict)", "output": f"{len(f.node_info)} types", "result": "✓ attached"}
    _run_test(collector, stage, "8.16", "flow.fetch_node_info(dict)", t_8_16)

    def t_8_17():
        f = Flow(wf_path)
        j = f.to_json()
        f2 = Flow(j)
        assert len(f2.nodes) == len(f.nodes), "Node count mismatch after round-trip"
        return {"input": "Flow(flow.to_json())", "output": f"{len(f2.nodes)} nodes", "result": "✓ round-trip"}
    _run_test(collector, stage, "8.17", "Round-trip: Flow(flow.to_json())", t_8_17)

    def t_8_18():
        with open(wf_path, "r") as fh:
            d = json.load(fh)
        f = Flow(d)
        assert len(f.nodes) > 0, "Flow from dict has no nodes"
        return {"input": "Flow(dict)", "output": f"{len(f.nodes)} nodes", "result": "✓ dict constructor"}
    _run_test(collector, stage, "8.18", "Flow(dict) constructor", t_8_18)

    def t_8_19():
        with open(wf_path, "rb") as fh:
            b = fh.read()
        f = Flow(b)
        assert len(f.nodes) > 0, "Flow from bytes has no nodes"
        return {"input": f"Flow(bytes, {len(b)} B)", "output": f"{len(f.nodes)} nodes", "result": "✓ bytes constructor"}
    _run_test(collector, stage, "8.19", "Flow(bytes) constructor", t_8_19)

    def t_8_20():
        f = Flow(wf_path)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            f.save(tmp_path)
            f2 = Flow(tmp_path)
            assert len(f2.nodes) == len(f.nodes), "Node count mismatch after save→reload"
            return {"input": f"save({Path(tmp_path).name})", "output": f"{len(f2.nodes)} nodes", "result": "✓ save round-trip"}
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    _run_test(collector, stage, "8.20", "flow.save() → reload", t_8_20)

    _print_stage_summary(collector, stage)
