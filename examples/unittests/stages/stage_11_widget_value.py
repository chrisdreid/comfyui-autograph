"""Stage 11 — WidgetValue: equality, arithmetic, ordering, hash, choices, tooltip."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
)

STAGE = "Stage 11: WidgetValue"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    from autoflow.models import WidgetValue

    wv_int = WidgetValue(42)
    wv_float = WidgetValue(3.14)
    wv_str = WidgetValue("euler")
    combo_spec = [["euler", "heun", "dpm"], {}]
    wv_combo = WidgetValue("euler", combo_spec)
    tooltip_spec = ["INT", {"default": 42, "tooltip": "Random seed value"}]
    wv_tooltip = WidgetValue(42, tooltip_spec)

    def t_11_1():
        assert wv_int == 42, f"WidgetValue(42) != 42"
        assert wv_str == "euler", f"WidgetValue('euler') != 'euler'"
        return {"input": "WV(42)==42, WV('euler')=='euler'", "output": "True, True", "result": "✓ equality"}
    _run_test(collector, stage, "11.1", "wv == raw_value", t_11_1)

    def t_11_2():
        assert wv_int != 43, f"WidgetValue(42) == 43"
        assert wv_str != "heun", f"WidgetValue('euler') == 'heun'"
        return {"input": "WV(42)!=43, WV('euler')!='heun'", "output": "True, True", "result": "✓ inequality"}
    _run_test(collector, stage, "11.2", "wv != other_value", t_11_2)

    def t_11_3():
        result = wv_int + 10
        assert result == 52, f"42 + 10 = {result}"
        return {"input": "WV(42) + 10", "output": str(result), "result": "✓ add"}
    _run_test(collector, stage, "11.3", "wv + 10", t_11_3)

    def t_11_4():
        result = 10 + wv_int
        assert result == 52, f"10 + 42 = {result}"
        return {"input": "10 + WV(42)", "output": str(result), "result": "✓ radd"}
    _run_test(collector, stage, "11.4", "10 + wv (radd)", t_11_4)

    def t_11_5():
        result = wv_int - 10
        assert result == 32, f"42 - 10 = {result}"
        return {"input": "WV(42) - 10", "output": str(result), "result": "✓ sub"}
    _run_test(collector, stage, "11.5", "wv - 10", t_11_5)

    def t_11_6():
        result = wv_int * 2
        assert result == 84, f"42 * 2 = {result}"
        return {"input": "WV(42) * 2", "output": str(result), "result": "✓ mul"}
    _run_test(collector, stage, "11.6", "wv * 2", t_11_6)

    def t_11_7():
        result = wv_int / 2
        assert result == 21.0, f"42 / 2 = {result}"
        return {"input": "WV(42) / 2", "output": str(result), "result": "✓ div"}
    _run_test(collector, stage, "11.7", "wv / 2", t_11_7)

    def t_11_8():
        assert wv_int < 100, "42 < 100 failed"
        assert wv_int > 0, "42 > 0 failed"
        assert wv_int <= 42, "42 <= 42 failed"
        assert wv_int >= 42, "42 >= 42 failed"
        return {"input": "WV(42) <100, >0, <=42, >=42", "output": "all True", "result": "✓ ordering"}
    _run_test(collector, stage, "11.8", "wv < 100, wv > 0, etc.", t_11_8)

    def t_11_9():
        assert int(wv_int) == 42, f"int(wv) = {int(wv_int)}"
        assert float(wv_float) == 3.14, f"float(wv) = {float(wv_float)}"
        return {"input": "int(WV(42)), float(WV(3.14))", "output": f"{int(wv_int)}, {float(wv_float)}", "result": "✓ cast"}
    _run_test(collector, stage, "11.9", "int(wv) / float(wv)", t_11_9)

    def t_11_10():
        assert bool(wv_int) is True, "bool(42) should be True"
        assert bool(WidgetValue(0)) is False, "bool(0) should be False"
        return {"input": "bool(WV(42)), bool(WV(0))", "output": "True, False", "result": "✓ bool"}
    _run_test(collector, stage, "11.10", "bool(wv)", t_11_10)

    def t_11_11():
        assert hash(wv_int) == hash(42), f"hash mismatch: {hash(wv_int)} vs {hash(42)}"
        return {"input": "hash(WV(42))", "output": str(hash(wv_int)), "result": "✓ matches hash(42)"}
    _run_test(collector, stage, "11.11", "hash(wv) == hash(raw)", t_11_11)

    def t_11_12():
        assert str(wv_int) == "42", f"str(wv) = {str(wv_int)!r}"
        r = repr(wv_int)
        assert "42" in r, f"repr missing '42': {r}"
        return {"input": "str(WV(42)), repr(WV(42))", "output": f"str={str(wv_int)!r}, repr={r!r}", "result": "✓ string ops"}
    _run_test(collector, stage, "11.12", "str(wv) / repr(wv)", t_11_12)

    def t_11_13():
        assert wv_int.value == 42, f"value = {wv_int.value}"
        assert wv_str.value == "euler", f"value = {wv_str.value}"
        return {"input": ".value property", "output": f"int={wv_int.value}, str={wv_str.value}", "result": "✓ raw values"}
    _run_test(collector, stage, "11.13", ".value property", t_11_13)

    def t_11_14():
        choices = wv_combo.choices()
        assert isinstance(choices, list), f"choices() returned {type(choices)}"
        assert "euler" in choices, f"'euler' not in choices: {choices}"
        assert "heun" in choices, f"'heun' not in choices: {choices}"
        return {"input": "wv_combo.choices()", "output": ", ".join(choices), "result": f"✓ {len(choices)} choices"}
    _run_test(collector, stage, "11.14", ".choices() on combo", t_11_14)

    def t_11_15():
        tt = wv_tooltip.tooltip()
        assert tt == "Random seed value", f"tooltip = {tt!r}"
        return {"input": "wv_tooltip.tooltip()", "output": tt, "result": "✓ tooltip string"}
    _run_test(collector, stage, "11.15", ".tooltip() returns string", t_11_15)

    _print_stage_summary(collector, stage)
