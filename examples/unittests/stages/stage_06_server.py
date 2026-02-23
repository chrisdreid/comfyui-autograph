"""Stage 6 — Server: reachability, live node_info fetch, submit + progress + images."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    FixtureCase, _BUNDLED_WORKFLOW,
)

STAGE = "Stage 6: Server"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    server_url: Optional[str] = kwargs.get("server_url")
    fixtures: Optional[List[FixtureCase]] = kwargs.get("fixtures")
    output_dir: Optional[Path] = kwargs.get("output_dir")

    if not server_url:
        print(f"\n{'='*60}")
        print(f"  {stage} — SKIPPED (no server URL provided)")
        print(f"{'='*60}\n")
        r = collector.begin(stage, "6.0", "Server stage")
        collector.skip(r, "No server URL provided")
        return

    print(f"\n{'='*60}")
    print(f"  {stage} — {server_url}")
    print(f"{'='*60}\n")

    import urllib.request

    def t_6_1():
        try:
            req = urllib.request.urlopen(server_url, timeout=5)
            assert req.status == 200, f"Server returned {req.status}"
            return {"input": server_url, "output": f"HTTP {req.status}", "result": "✓ reachable"}
        except Exception as e:
            raise AssertionError(f"Server not reachable: {e}")
    _run_test(collector, stage, "6.1", "Server reachable", t_6_1)

    def t_6_2():
        from autoflow import NodeInfo
        ni = NodeInfo.fetch(server_url=server_url)
        assert ni is not None, "NodeInfo.fetch returned None"
        return {"input": f"NodeInfo.fetch({server_url})", "output": f"{len(ni)} node types", "result": "✓ fetched live"}
    _run_test(collector, stage, "6.2", "NodeInfo.fetch(server_url)", t_6_2)

    def t_6_3():
        from autoflow import Workflow
        api = Workflow(str(_BUNDLED_WORKFLOW), server_url=server_url)
        assert api is not None, "Live conversion failed"
        return {"input": f"Workflow(wf, server_url={server_url})", "output": type(api).__name__, "result": "✓ live convert"}
    _run_test(collector, stage, "6.3", "Workflow(wf, server_url) live convert", t_6_3)

    if fixtures:
        for i, fx in enumerate(fixtures):
            if not fx.manifest.get("requires_server", False):
                continue

            wf_path = fx.directory / fx.manifest.get("workflow", "workflow.json")
            ni_path = fx.directory / fx.manifest.get("node_info", "node-info.json")
            prefix = f"6.{10 + i * 10}"

            def t_submit(wf=wf_path, ni=ni_path, fixture=fx, pfx=prefix):
                from autoflow import Workflow
                ni_data = None
                if ni.exists():
                    with open(ni, "r", encoding="utf-8") as fh:
                        ni_data = json.load(fh)

                with open(wf, "r", encoding="utf-8") as fh:
                    wf_data = json.load(fh)

                bypass_types = set(fixture.manifest.get("bypass_types", []))
                if bypass_types and isinstance(wf_data.get("nodes"), list):
                    for node in wf_data["nodes"]:
                        if isinstance(node, dict) and node.get("type") in bypass_types:
                            node["mode"] = 4

                api = Workflow(wf_data, node_info=ni_data) if ni_data else Workflow(wf_data, server_url=server_url)

                edits = fixture.manifest.get("edits", {})
                for edit_key, edit_val in edits.items():
                    parts = edit_key.split(".")
                    if len(parts) == 2:
                        node_type, param = parts
                        try:
                            node = getattr(api, node_type)
                            setattr(node, param, edit_val)
                        except AttributeError:
                            pass

                progress_events: List[Dict[str, Any]] = []
                t_start = time.time()

                def on_event(evt: Dict[str, Any]) -> None:
                    progress_events.append({
                        "type": evt.get("type", ""),
                        "data": evt.get("data", {}),
                        "elapsed_s": round(time.time() - t_start, 3),
                    })
                    d = evt.get("data", {})
                    if evt.get("type") == "progress":
                        step = d.get("value", "?")
                        total = d.get("max", "?")
                        print(f"    ⏳ [{fixture.name}] Step {step}/{total}", end="\r")

                res = api.submit(server_url=server_url, wait=True, on_event=on_event)
                print()
                assert res is not None, "Submit returned None"
                fixture.progress_log = progress_events

                if output_dir:
                    prog_dir = output_dir / "progress"
                    prog_dir.mkdir(parents=True, exist_ok=True)
                    prog_file = prog_dir / f"{fixture.directory.name}.json"
                    prog_file.write_text(json.dumps(progress_events, indent=2), encoding="utf-8")

                img_out = None
                if output_dir:
                    img_out = output_dir / fixture.directory.name / "generated"
                    img_out.mkdir(parents=True, exist_ok=True)

                images = res.fetch_images(
                    output_path=str(img_out) if img_out else None,
                    include_bytes=True,
                )
                assert images is not None, "fetch_images returned None"
                assert len(images) > 0, "No images returned"

                if img_out:
                    fixture.generated_images = sorted(img_out.glob("*.png"))
                    if not fixture.generated_images:
                        for idx, img in enumerate(images):
                            img_bytes = img.get("bytes")
                            if isinstance(img_bytes, (bytes, bytearray)):
                                out_file = img_out / f"output_{idx:05d}.png"
                                out_file.write_bytes(img_bytes)
                                fixture.generated_images.append(out_file)

                return {
                    "input": f"[{fixture.name}] submit + progress",
                    "output": f"{len(progress_events)} events, {len(images)} images",
                    "result": f"✓ completed in {round(time.time() - t_start, 1)}s",
                }

            _run_test(collector, stage, f"{prefix}.1",
                      f"[{fx.name}] Submit + progress capture + fetch images", t_submit)

            expected_imgs = fx.manifest.get("expected", {}).get("output_image_count")
            if expected_imgs is not None:
                def t_img_count(fixture=fx, exp=expected_imgs):
                    actual = len(fixture.generated_images)
                    assert actual == exp, f"Expected {exp} output images, got {actual}"
                    return {"input": f"expected={exp}", "output": f"actual={actual}", "result": f"✓ [{fixture.name}] count matches"}
                _run_test(collector, stage, f"{prefix}.2",
                          f"[{fx.name}] Output image count = {expected_imgs}", t_img_count)
    else:
        def t_6_4():
            from autoflow import Workflow
            api = Workflow(str(_BUNDLED_WORKFLOW), server_url=server_url)
            res = api.submit(server_url=server_url, wait=True)
            assert res is not None, "Submit returned None"
            images = res.fetch_images()
            assert images is not None and len(images) > 0, "No images returned"
            return {"input": f"submit({server_url})", "output": f"{len(images)} images", "result": "✓ images fetched"}
        _run_test(collector, stage, "6.4", "submit(wait=True) + fetch_images()", t_6_4)

    _print_stage_summary(collector, stage)
