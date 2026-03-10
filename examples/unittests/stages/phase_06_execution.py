"""Phase 6 — Server: WS event parsing, server connectivity, node-info fetch,
fixture-driven submit, and image generation.

Merged from: stage_06_server, stage_24_ws_events, stage_05_fixtures
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    BUILTIN_NODE_INFO, _BUNDLED_WORKFLOW, SkipTest,
    FixtureCase, discover_fixtures,
)

STAGE = "Phase 6: Server"

_FIXTURES_DIR = _REPO_ROOT / "autograph-test-suite" / "fixtures"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    server_url = kwargs.get("server_url")
    fixtures_dir = kwargs.get("fixtures_dir")
    output_dir = kwargs.get("output_dir")
    if output_dir:
        output_dir = Path(output_dir)

    # ===================================================================
    # 6.1–6.4  WebSocket event parsing  (was stage 24)
    # ===================================================================

    from autograph.ws import parse_comfy_event

    def t_6_1():
        raw = '{"type":"progress","data":{"value":3,"max":10,"node":null}}'
        events = parse_comfy_event(raw, client_id="c", prompt_id="p")
        assert any(e.get("type") == "progress" for e in events)
        ev = [e for e in events if e.get("type") == "progress"][0]
        assert ev.get("client_id") == "c"
        assert ev.get("prompt_id") == "p"
        assert ev.get("data", {}).get("value") == 3
        return {
            "input": "progress JSON (value=3, max=10)",
            "output": f"{len(events)} events, value={ev['data']['value']}",
            "result": "✓ progress parsed",
        }
    _run_test(collector, stage, "6.1", "Progress message parsing", t_6_1)

    def t_6_2():
        raw = '{"type":"executing","data":{"node":null}}'
        events = parse_comfy_event(raw)
        types = [e.get("type") for e in events]
        assert "completed" in types
        assert "executing" in types
        return {
            "input": "executing with node=null",
            "output": f"types={types}",
            "result": "✓ completed + executing emitted",
        }
    _run_test(collector, stage, "6.2", "Executing completion (node=null → completed)", t_6_2)

    def t_6_3():
        raw = '{"type":"progress","data":{}}{"type":"executing","data":{"node":1}}'
        events = parse_comfy_event(raw)
        types = [e.get("type") for e in events]
        assert "progress" in types and "executing" in types
        return {
            "input": "two JSON objects in one frame",
            "output": f"{len(events)} events, types={types}",
            "result": "✓ multi-JSON parsed",
        }
    _run_test(collector, stage, "6.3", "Multiple JSON objects in one frame", t_6_3)

    def t_6_4():
        raw = b'{"type":"executed","data":{"node":5,"output":{}}}'
        events = parse_comfy_event(raw)
        assert any(e.get("type") == "executed" for e in events)
        return {
            "input": "bytes input (executed event)",
            "output": f"{len(events)} events",
            "result": "✓ bytes parsed",
        }
    _run_test(collector, stage, "6.4", "Bytes input parsing", t_6_4)

    # ===================================================================
    # 6.5–6.7  Server connectivity + live node-info  (requires --server-url)
    # ===================================================================

    if not server_url:
        def t_6_5():
            raise SkipTest("No --server-url provided")
        _run_test(collector, stage, "6.5", "Server connectivity (skipped: no server)", t_6_5)
    else:
        import urllib.request

        def t_6_5():
            try:
                resp = urllib.request.urlopen(f"{server_url}/system_stats", timeout=5)
                data = json.loads(resp.read())
                assert isinstance(data, dict)
                return {"input": f"GET {server_url}/system_stats", "output": f"{len(data)} keys", "result": "✓ reachable"}
            except Exception as e:
                raise SkipTest(f"Server unreachable: {e}")
        _run_test(collector, stage, "6.5", "Server connectivity", t_6_5)

        def t_6_6():
            from autograph import NodeInfo
            ni = NodeInfo.fetch(server_url=server_url)
            assert ni is not None, "NodeInfo.fetch returned None"
            count = len(ni)
            assert count > 0, "NodeInfo is empty"
            return {"input": f"NodeInfo.fetch({server_url})", "output": f"{count} node types", "result": "✓ live node-info"}
        _run_test(collector, stage, "6.6", "NodeInfo.fetch(server_url) live", t_6_6)

        def t_6_7():
            from autograph import ApiFlow
            api = ApiFlow(str(_BUNDLED_WORKFLOW), server_url=server_url)
            assert api is not None, "Live conversion failed"
            return {"input": f"Workflow(wf, server_url=...)", "output": type(api).__name__, "result": "✓ live convert"}
        _run_test(collector, stage, "6.7", "Workflow(wf, server_url) live convert", t_6_7)

    # ===================================================================
    # 6.8+  Fixture-driven submit tests  (requires --server-url + fixtures)
    # ===================================================================

    # Use harness discover_fixtures (looks for fixture.json, not manifest.json)
    if fixtures_dir:
        fx_dir_str = str(fixtures_dir)
    elif _FIXTURES_DIR.is_dir():
        fx_dir_str = str(_FIXTURES_DIR)
    else:
        fx_dir_str = ""

    fixtures: List[FixtureCase] = discover_fixtures(fx_dir_str) if fx_dir_str else []

    if server_url and fixtures:
        from autograph import ApiFlow

        for idx, fx in enumerate(fixtures):
            prefix = f"6.{8 + idx}"

            wf_file = fx.directory / fx.manifest.get("workflow", "workflow.json")
            ni_file = fx.directory / fx.manifest.get("node_info", "node-info.json")

            if not wf_file.is_file():
                def t_skip(fixture=fx):
                    raise SkipTest(f"Workflow file not found: {fixture.directory.name}")
                _run_test(collector, stage, f"{prefix}.0", f"[{fx.name}] workflow missing", t_skip)
                continue

            def t_submit(fixture=fx, wf=wf_file, ni=ni_file):
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

                api_wf = ApiFlow(wf_data, node_info=ni_data) if ni_data else Workflow(wf_data, server_url=server_url)

                edits = fixture.manifest.get("edits", {})
                for edit_key, edit_val in edits.items():
                    parts = edit_key.split(".")
                    if len(parts) == 2:
                        node_type, param = parts
                        try:
                            node = getattr(api_wf, node_type)
                            setattr(node, param, edit_val)
                        except AttributeError:
                            pass

                progress_events: List[Dict[str, Any]] = []
                t_start = time.time()

                def on_event(evt):
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

                res = api_wf.submit(server_url=server_url, wait=True, on_event=on_event)
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
                assert images is not None and len(images) > 0, "No images returned"

                if img_out:
                    fixture.generated_images = sorted(img_out.glob("*.png"))
                    if not fixture.generated_images:
                        for i, img in enumerate(images):
                            img_bytes = img.get("bytes")
                            if isinstance(img_bytes, (bytes, bytearray)):
                                out_file = img_out / f"output_{i:05d}.png"
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

    elif server_url and not fixtures:
        # Fallback: submit the bundled workflow when no fixtures are available
        def t_6_fallback():
            from autograph import ApiFlow
            api_wf = ApiFlow(str(_BUNDLED_WORKFLOW), server_url=server_url)

            img_out = None
            if output_dir:
                img_out = output_dir / "bundled-workflow" / "generated"
                img_out.mkdir(parents=True, exist_ok=True)

            res = api_wf.submit(server_url=server_url, wait=True)
            assert res is not None, "Submit returned None"

            images = res.fetch_images(
                output_path=str(img_out) if img_out else None,
                include_bytes=True,
            )
            assert images is not None and len(images) > 0, "No images returned"

            # Save images from bytes if fetch_images didn't write to disk
            if img_out:
                saved = sorted(img_out.glob("*.png"))
                if not saved:
                    for i, img in enumerate(images):
                        img_bytes = img.get("bytes")
                        if isinstance(img_bytes, (bytes, bytearray)):
                            out_file = img_out / f"output_{i:05d}.png"
                            out_file.write_bytes(img_bytes)

            return {"input": f"submit({server_url})", "output": f"{len(images)} images", "result": "✓ images fetched"}
        _run_test(collector, stage, "6.8", "submit(wait=True) + fetch_images()", t_6_fallback)

    # Return fixtures so main.py can pass them to the HTML report
    kwargs["fixtures"] = fixtures

    _print_stage_summary(collector, stage)

    # Return fixtures for downstream use (report generation)
    return fixtures
