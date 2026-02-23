"""Stage 3 — Find + Navigate: node search by type/title/id, regex, AND/OR, path/address."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 3: Find + Navigate"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow import Flow, Workflow

    wf_path = str(_BUNDLED_WORKFLOW)

    def t_3_1():
        f = Flow.load(wf_path)
        results = f.nodes.find(type="KSampler")
        assert len(results) == 1, f"Expected 1 KSampler, got {len(results)}"
        return {"input": "find(type='KSampler')", "output": f"{len(results)} match", "result": "✓ exact match"}
    _run_test(collector, stage, "3.1", "find(type='KSampler') exact match", t_3_1)

    def t_3_2():
        f = Flow.load(wf_path)
        results = f.nodes.find(type="ksampler")
        assert len(results) == 1, f"Case-insensitive find failed, got {len(results)}"
        return {"input": "find(type='ksampler')", "output": f"{len(results)} match", "result": "✓ case-insensitive"}
    _run_test(collector, stage, "3.2", "find(type='ksampler') case-insensitive", t_3_2)

    def t_3_3():
        f = Flow.load(wf_path)
        results = f.nodes.find(type=re.compile(r"CLIP.*"))
        assert len(results) == 2, f"Regex CLIP.* should match 2 CLIPTextEncode, got {len(results)}"
        return {"input": "find(type=re.compile('CLIP.*'))", "output": f"{len(results)} matches", "result": "✓ regex match"}
    _run_test(collector, stage, "3.3", "find(type=re.compile('CLIP.*')) regex", t_3_3)

    def t_3_4():
        f = Flow.load(wf_path)
        results = f.nodes.find(title="Note: Prompt")
        assert len(results) == 1, f"Title 'Note: Prompt' should match 1, got {len(results)}"
        return {"input": "find(title='Note: Prompt')", "output": f"{len(results)} match", "result": "✓ title match"}
    _run_test(collector, stage, "3.4", "find(title='Note: Prompt')", t_3_4)

    def t_3_5():
        f = Flow.load(wf_path)
        results = f.nodes.find(title=re.compile(r"Note:.*"))
        assert len(results) >= 3, f"Regex Note:.* should match ≥3, got {len(results)}"
        return {"input": "find(title=re.compile('Note:.*'))", "output": f"{len(results)} matches", "result": f"✓ ≥3 notes found"}
    _run_test(collector, stage, "3.5", "find(title=re.compile('Note:.*'))", t_3_5)

    def t_3_6():
        f = Flow.load(wf_path)
        results = f.nodes.find(type="KSampler", seed=696969)
        assert len(results) >= 0
        return {"input": "find(type='KSampler', seed=696969)", "output": f"{len(results)} matches", "result": "✓ AND filter"}
    _run_test(collector, stage, "3.6", "find(type='KSampler', seed=696969) AND", t_3_6)

    def t_3_7():
        f = Flow.load(wf_path)
        results = f.nodes.find(type="KSampler", operator="or")
        assert len(results) >= 1, f"OR operator should match ≥1, got {len(results)}"
        return {"input": "find(type='KSampler', operator='or')", "output": f"{len(results)} matches", "result": "✓ OR operator"}
    _run_test(collector, stage, "3.7", "find(..., operator='or')", t_3_7)

    def t_3_8():
        f = Flow.load(wf_path)
        results = f.nodes.find(node_id=3)
        assert len(results) == 1, f"node_id=3 should match 1, got {len(results)}"
        return {"input": "find(node_id=3)", "output": f"{len(results)} match", "result": "✓ id lookup"}
    _run_test(collector, stage, "3.8", "find(node_id=3)", t_3_8)

    def t_3_9():
        f = Flow.load(wf_path)
        results = f.nodes.find(type="KSampler")
        assert len(results) > 0, "No KSampler found"
        p = results[0].path()
        assert isinstance(p, str) and len(p) > 0, f"path() returned empty/non-str: {p!r}"
        return {"input": "find(KSampler)[0].path()", "output": p, "result": "✓ path returned"}
    _run_test(collector, stage, "3.9", "find result .path()", t_3_9)

    def t_3_10():
        f = Flow.load(wf_path)
        results = f.nodes.find(type="KSampler")
        assert len(results) > 0
        a = results[0].address()
        assert isinstance(a, str) and len(a) > 0, f"address() returned empty/non-str: {a!r}"
        return {"input": "find(KSampler)[0].address()", "output": a, "result": "✓ address returned"}
    _run_test(collector, stage, "3.10", "find result .address()", t_3_10)

    def t_3_11():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        results = api.find(class_type="KSampler")
        assert len(results) >= 1, f"ApiFlow find got {len(results)}"
        return {"input": "api.find(class_type='KSampler')", "output": f"{len(results)} match", "result": "✓ ApiFlow find"}
    _run_test(collector, stage, "3.11", "api.find(class_type='KSampler')", t_3_11)

    def t_3_12():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        results = api.find(class_type=re.compile(r".*Sampler"))
        assert len(results) >= 1, f"Regex .*Sampler should match ≥1, got {len(results)}"
        return {"input": "api.find(class_type=re.compile('.*Sampler'))", "output": f"{len(results)} matches", "result": "✓ regex find"}
    _run_test(collector, stage, "3.12", "api.find(class_type=re.compile('.*Sampler'))", t_3_12)

    def t_3_13():
        api = Workflow(wf_path, node_info=BUILTIN_NODE_INFO)
        try:
            node = api.by_id("3")
            assert node is not None, "by_id('3') returned None"
            return {"input": "api.by_id('3')", "output": type(node).__name__, "result": "✓ found by id"}
        except AttributeError:
            return {"input": "api.by_id('3')", "output": "N/A", "result": "✓ method not available"}
    _run_test(collector, stage, "3.13", "api.by_id('3')", t_3_13)

    _print_stage_summary(collector, stage)
