"""Stage 5 — Fixtures: discover fixture.json manifests, offline conversion checks."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    FixtureCase, discover_fixtures, copy_ground_truth,
)

STAGE = "Stage 5: Fixtures"


def run(collector: ResultCollector, **kwargs) -> List[FixtureCase]:
    """Run offline fixture tests. Returns discovered fixtures for later stages."""
    stage = STAGE
    fixtures_dir: Optional[str] = kwargs.get("fixtures_dir")
    output_dir: Optional[Path] = kwargs.get("output_dir")

    if not fixtures_dir:
        print(f"\n{'='*60}")
        print(f"  {stage} — SKIPPED (no fixtures directory provided)")
        print(f"{'='*60}\n")
        r = collector.begin(stage, "5.0", "Fixtures stage")
        collector.skip(r, "No fixtures directory provided")
        return []

    print(f"\n{'='*60}")
    print(f"  {stage} — {fixtures_dir}")
    print(f"{'='*60}\n")

    fdir = Path(fixtures_dir)
    if not fdir.is_dir():
        r = collector.begin(stage, "5.0", "Fixtures directory exists")
        collector.fail(r, f"Not a directory: {fixtures_dir}")
        return []

    fixtures = discover_fixtures(fixtures_dir)
    def t_5_1():
        assert len(fixtures) > 0, f"No fixture.json manifests found in {fixtures_dir}"
        return {"input": fixtures_dir, "output": f"{len(fixtures)} fixtures", "result": "✓ discovery complete"}
    _run_test(collector, stage, "5.1", f"Discover fixtures ({len(fixtures)} found)", t_5_1)

    for i, fx in enumerate(fixtures):
        prefix = f"5.{10 + i * 10}"

        if output_dir:
            copy_ground_truth(fx, output_dir)

        wf_path = fx.directory / fx.manifest.get("workflow", "workflow.json")

        def t_load(wf=wf_path, name=fx.name):
            from autoflow import Flow
            f = Flow.load(str(wf))
            assert f is not None, f"Failed to load {wf.name}"
            return {"input": wf.name, "output": f"{len(f.nodes)} nodes", "result": f"✓ [{name}] loaded"}
        _run_test(collector, stage, f"{prefix}.1", f"[{fx.name}] Load workflow", t_load)

        ni_path = fx.directory / fx.manifest.get("node_info", "node-info.json")
        if ni_path.exists():
            def t_convert(wf=wf_path, ni=ni_path, name=fx.name):
                from autoflow import Workflow
                with open(ni, "r", encoding="utf-8") as fh:
                    node_info = json.load(fh)
                api = Workflow(str(wf), node_info=node_info)
                assert api is not None, "Conversion failed"
                j = api.to_json()
                parsed = json.loads(j)
                assert isinstance(parsed, dict), "to_json() not valid JSON"
                return {"input": f"{wf.name} + {ni.name}", "output": f"{len(parsed)} API nodes", "result": f"✓ [{name}] converted"}
            _run_test(collector, stage, f"{prefix}.2", f"[{fx.name}] Convert with node_info", t_convert)

            expected_count = fx.manifest.get("expected", {}).get("api_node_count")
            if expected_count:
                def t_count(wf=wf_path, ni=ni_path, exp=expected_count, name=fx.name):
                    from autoflow import Workflow
                    with open(ni, "r", encoding="utf-8") as fh:
                        node_info = json.load(fh)
                    api = Workflow(str(wf), node_info=node_info)
                    raw = getattr(api, "unwrap", lambda: api)()
                    count = sum(
                        1 for _, v in raw.items()
                        if isinstance(v, dict) and "class_type" in v
                    )
                    assert count == exp, f"Expected {exp} API nodes, got {count}"
                    return {"input": f"expected={exp}", "output": f"actual={count}", "result": f"✓ [{name}] count matches"}
                _run_test(collector, stage, f"{prefix}.3", f"[{fx.name}] API node count = {expected_count}", t_count)

        if fx.ground_truth_images:
            def t_gt(imgs=fx.ground_truth_images, name=fx.name):
                for img in imgs:
                    assert img.exists(), f"Ground-truth image missing: {img}"
                return {"input": f"{len(imgs)} ground-truth images", "output": ", ".join(i.name for i in imgs), "result": f"✓ [{name}] all exist"}
            _run_test(collector, stage, f"{prefix}.4", f"[{fx.name}] Ground-truth images ({len(fx.ground_truth_images)})", t_gt)

    _print_stage_summary(collector, stage)
    return fixtures
