"""Stage 7 — Tools: PIL image create/save/reload, fixture image comparison."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import (  # noqa: E402
    ResultCollector, _run_test, _print_stage_summary,
    FixtureCase,
)

STAGE = "Stage 7: Tools"


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    has_pil: bool = kwargs.get("has_pil", False)
    fixtures: Optional[List[FixtureCase]] = kwargs.get("fixtures")

    if not has_pil:
        print(f"\n{'='*60}")
        print(f"  {stage} — SKIPPED (no tools available)")
        print(f"{'='*60}\n")
        r = collector.begin(stage, "7.0", "Tools stage")
        collector.skip(r, "PIL not available")
        return

    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    def t_7_1():
        from PIL import Image
        img = Image.new("RGB", (64, 64), color=(255, 0, 0))
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp.name)
            loaded = Image.open(tmp.name)
            assert loaded.size == (64, 64), f"Image size mismatch: {loaded.size}"
            os.unlink(tmp.name)
        return {"input": "Image.new(RGB, 64×64, red)", "output": f"size={loaded.size}", "result": "✓ PIL round-trip"}
    _run_test(collector, stage, "7.1", "PIL: create + load image", t_7_1)

    if fixtures:
        for i, fx in enumerate(fixtures):
            if not fx.ground_truth_images or not fx.generated_images:
                continue

            def t_compare(fixture=fx):
                from PIL import Image
                for gt_img in fixture.ground_truth_images:
                    gt = Image.open(gt_img)
                    for gen_path in fixture.generated_images:
                        gen = Image.open(gen_path)
                        assert gt.size == gen.size, (
                            f"Size mismatch: ground-truth {gt.size} vs "
                            f"generated {gen.size}"
                        )
                return {
                    "input": f"[{fixture.name}] {len(fixture.ground_truth_images)} GT × {len(fixture.generated_images)} gen",
                    "output": f"all sizes match",
                    "result": f"✓ [{fixture.name}] dims OK",
                }
            _run_test(collector, stage, f"7.{10 + i}",
                      f"[{fx.name}] Image dimensions match ground-truth", t_compare)

    _print_stage_summary(collector, stage)
