"""Stage 24 — WebSocket Events: parse_comfy_event parsing."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import ResultCollector, _run_test, _print_stage_summary  # noqa: E402

STAGE = "Stage 24: WebSocket Events"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow.ws import parse_comfy_event

    def t_24_1():
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
    _run_test(collector, stage, "24.1", "Progress message parsing", t_24_1)

    def t_24_2():
        raw = '{"type":"executing","data":{"node":null}}'
        events = parse_comfy_event(raw)
        types = [e.get("type") for e in events]
        assert "completed" in types, f"'completed' not in {types}"
        assert "executing" in types, f"'executing' not in {types}"
        return {
            "input": "executing with node=null",
            "output": f"types={types}",
            "result": "✓ completed + executing emitted",
        }
    _run_test(collector, stage, "24.2", "Executing completion (node=null → completed)", t_24_2)

    def t_24_3():
        raw = '{"type":"progress","data":{}}{"type":"executing","data":{"node":1}}'
        events = parse_comfy_event(raw)
        types = [e.get("type") for e in events]
        assert "progress" in types, f"'progress' not in {types}"
        assert "executing" in types, f"'executing' not in {types}"
        return {
            "input": "two JSON objects in one frame",
            "output": f"{len(events)} events, types={types}",
            "result": "✓ multi-JSON parsed",
        }
    _run_test(collector, stage, "24.3", "Multiple JSON objects in one frame", t_24_3)

    def t_24_4():
        raw = b'{"type":"executed","data":{"node":5,"output":{}}}'
        events = parse_comfy_event(raw)
        assert any(e.get("type") == "executed" for e in events)
        return {
            "input": "bytes input (executed event)",
            "output": f"{len(events)} events",
            "result": "✓ bytes parsed",
        }
    _run_test(collector, stage, "24.4", "Bytes input parsing", t_24_4)

    _print_stage_summary(collector, stage)
