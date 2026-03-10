#!/usr/bin/env python3
"""
autograph — Modular Test Suite
==============================

Slim orchestrator that auto-discovers test modules and runs them in order.
Every module exports ``run(collector, **kwargs)`` and uses the shared
``harness`` infrastructure.

Phases (``phase_*.py``): 8 sequential phases following the data pipeline.

Usage::

    # Run all phases (CI-safe, no prompts)
    python examples/unittests/main.py --non-interactive

    # Legacy: run all old stages
    python examples/unittests/main.py --legacy --non-interactive

    # Run with node-info override
    python examples/unittests/main.py --non-interactive --node-info /path/to/node-info.json

    # Include docs tests
    python examples/unittests/main.py --non-interactive --docs

    # Run a specific phase
    python examples/unittests/main.py --phase 3

    # List all discovered modules
    python examples/unittests/main.py --list
"""

from __future__ import annotations

import argparse
import importlib
import json as _json_mod
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Ensure repo root is on sys.path
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Ensure the unittests directory is also on sys.path so ``import harness``
# works from stage modules.
_UNITTEST_DIR = Path(__file__).resolve().parent
if str(_UNITTEST_DIR) not in sys.path:
    sys.path.insert(0, str(_UNITTEST_DIR))

from harness import (  # noqa: E402
    ResultCollector,
    _print_stage_summary,
    generate_html_report,
    clean_output_dir,
    discover_fixtures,
    copy_ground_truth,
    _BUNDLED_WORKFLOW,
)


# ---------------------------------------------------------------------------
# Phase discovery
# ---------------------------------------------------------------------------
_STAGES_DIR = _UNITTEST_DIR / "stages"



def _discover_phases() -> List[Tuple[int, str, Any]]:
    """Return sorted list of (phase_num, module_name, module) for every phase_*.py."""
    phases: List[Tuple[int, str, Any]] = []
    for path in sorted(_STAGES_DIR.glob("phase_*.py")):
        stem = path.stem  # e.g. phase_03_flow
        parts = stem.split("_", 2)  # ["phase", "03", "flow"]
        if len(parts) < 2:
            continue
        try:
            num = int(parts[1])
        except ValueError:
            continue
        mod_name = f"stages.{stem}"
        mod = importlib.import_module(mod_name)
        phases.append((num, mod_name, mod))
    return phases


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------
def _check_pil() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def _check_server(url: Optional[str]) -> Optional[str]:
    """Return the server URL if reachable, else None."""
    if not url:
        return None
    try:
        resp = urllib.request.urlopen(f"{url.rstrip('/')}/system_stats", timeout=3)
        if resp.status == 200:
            return url
    except Exception:
        pass
    return None


def _check_comfyui_modules() -> Optional[str]:
    """Return ComfyUI root path if importable, else None."""
    try:
        from autograph.convert import _detect_comfyui_root_from_imports
        root = _detect_comfyui_root_from_imports()
        return str(root) if root else None
    except Exception:
        return None


def _check_binary(name: str, override: Optional[str] = None) -> Optional[str]:
    """Return full path to a binary, or None."""
    if override:
        p = shutil.which(override)
        return p if p else override if os.path.isfile(override) else None
    return shutil.which(name)


def detect_environment(args) -> Dict[str, Any]:
    """Probe for all optional environments and return a summary dict."""
    server_url = args.server_url or os.environ.get("AUTOGRAPH_COMFYUI_SERVER_URL")

    # Interactive prompts
    fixtures_dir = args.fixtures_dir
    if not args.non_interactive:
        if not fixtures_dir:
            default_fixtures = _REPO_ROOT / "autograph-test-suite" / "fixtures"
            hint = f" [{default_fixtures}]" if default_fixtures.is_dir() else ""
            ans = input(f"\nFixtures directory{hint} (Enter for default, 'skip' to skip): ").strip()
            if ans.lower() == "skip":
                fixtures_dir = None
            elif ans:
                fixtures_dir = ans
            elif default_fixtures.is_dir():
                fixtures_dir = str(default_fixtures)
        if not server_url:
            ans = input("ComfyUI server URL [http://localhost:8188] (Enter for localhost, 'skip' to skip): ").strip()
            if ans.lower() == "skip":
                server_url = None
            elif ans:
                server_url = ans
            else:
                server_url = "http://localhost:8188"

    # --- Detect everything ---
    has_pil = _check_pil()
    server_ok = _check_server(server_url)
    comfyui_root = _check_comfyui_modules()

    # Python & pip versions
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    try:
        import pip
        pip_version = pip.__version__
    except ImportError:
        pip_version = None

    # ffmpeg / magick — try CLI arg, then PATH, then interactive prompt
    ffmpeg_bin = args.ffmpeg_bin if hasattr(args, 'ffmpeg_bin') else None
    magick_bin = args.magick_bin if hasattr(args, 'magick_bin') else None
    ffmpeg_path = _check_binary("ffmpeg", ffmpeg_bin)
    magick_path = _check_binary("magick", magick_bin) or _check_binary("convert", magick_bin)

    if not args.non_interactive:
        if not ffmpeg_path:
            ans = input("  ffmpeg not found. Enter path (or Enter to skip): ").strip()
            if ans:
                ffmpeg_path = _check_binary(ans) or (ans if os.path.isfile(ans) else None)
        if not magick_path:
            ans = input("  magick/convert not found. Enter path (or Enter to skip): ").strip()
            if ans:
                magick_path = _check_binary(ans) or (ans if os.path.isfile(ans) else None)

    env = {
        "fixtures_dir": fixtures_dir,
        "server_url": server_ok,
        "has_pil": has_pil,
        "comfyui_root": comfyui_root,
        "ffmpeg_path": ffmpeg_path,
        "magick_path": magick_path,
        "python_version": python_version,
        "pip_version": pip_version,
    }

    # Print banner
    print(f"\n{'='*60}")
    print("  Environment")
    print(f"{'='*60}")

    def _status(ok, detail=None):
        if ok:
            return f"✅ {detail}" if detail else "✅"
        return "❌ not found"

    print(f"  Python         ✅ {python_version}")
    print(f"  pip            {_status(pip_version, pip_version)}")
    print(f"  PIL/Pillow     {_status(has_pil, 'available')}")
    print(f"  ComfyUI server {_status(server_ok, server_ok)}")
    print(f"  ComfyUI modules{' ' if not comfyui_root else ''}{_status(comfyui_root, comfyui_root)}")
    print(f"  ffmpeg         {_status(ffmpeg_path, ffmpeg_path)}")
    print(f"  magick         {_status(magick_path, magick_path)}")
    if fixtures_dir:
        print(f"  Fixtures       ✅ {fixtures_dir}")
    else:
        print(f"  Fixtures       ⏭️  none")
    print()

    return env


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="autograph — Modular Test Suite")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Skip all prompted stages (CI mode)")
    parser.add_argument("--fixtures-dir", type=str, default=None,
                        help="Path to fixtures directory")
    parser.add_argument("--server-url", type=str, default=None,
                        help="ComfyUI server URL for live tests")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory for results")
    parser.add_argument("--port", type=int, default=None,
                        help="Serve results on this HTTP port")
    parser.add_argument("--no-clean", action="store_true",
                        help="Don't wipe output directory before running")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open report in browser")
    parser.add_argument("--phase", type=int, nargs="*", default=None,
                        help="Run only specific phase number(s)")
    parser.add_argument("--node-info", type=str, default=None,
                        help="Path to node-info.json (overrides all other sources)")
    parser.add_argument("--docs", action="store_true",
                        help="Include Phase 8 docs tests")
    parser.add_argument("--ffmpeg-bin", type=str, default=None,
                        help="Path to ffmpeg binary (default: auto-detect)")
    parser.add_argument("--magick-bin", type=str, default=None,
                        help="Path to ImageMagick binary (default: auto-detect magick/convert)")
    parser.add_argument("--list", action="store_true",
                        help="List all discovered modules and exit")
    args = parser.parse_args()

    all_modules = _discover_phases()
    mode_label = "Phase"

    if args.list:
        print(f"\n{'='*60}")
        print(f"  Discovered {mode_label} Modules")
        print(f"{'='*60}\n")
        for num, mod_name, mod in all_modules:
            label = getattr(mod, "STAGE", mod_name)
            print(f"  {num:3d}  {label}")
        print()
        return 0

    # --- Resolve output directory ---
    if args.output_dir:
        output_dir = Path(args.output_dir)
    elif args.fixtures_dir:
        output_dir = Path(args.fixtures_dir).parent / "outputs"
    else:
        output_dir = _REPO_ROOT / "autograph-test-suite" / "outputs"

    if not args.no_clean:
        clean_output_dir(output_dir)
        print(f"  🧹 Cleaned output directory: {output_dir}")
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    # --- Detect environment ---
    env = detect_environment(args)

    # Build kwargs passed to every stage/phase
    stage_kwargs: Dict[str, Any] = {
        "output_dir": output_dir,
        "docs": args.docs,
        **env,
    }

    # If --node-info provided, load it and inject into kwargs
    if args.node_info:
        import json as _json
        ni_path = Path(args.node_info).resolve()
        if not ni_path.is_file():
            print(f"  ❌ --node-info file not found: {ni_path}")
            return 1
        with open(ni_path, "r", encoding="utf-8") as _fh:
            stage_kwargs["node_info_override"] = _json.load(_fh)
        print(f"  📄 Node-info override: {ni_path}")

    # --- Filter modules ---
    filter_nums = args.phase
    if filter_nums is not None:
        num_set = set(filter_nums)
        run_modules = [(n, m, mod) for n, m, mod in all_modules if n in num_set]
        if not run_modules:
            print(f"  ⚠️  No {mode_label.lower()}s matched: {filter_nums}")
            return 1
    else:
        run_modules = all_modules

    # --- Run ---
    collector = ResultCollector()

    print(f"\n{'='*60}")
    print("  autograph — Modular Test Suite")
    print(f"{'='*60}")
    print(f"  Running {len(run_modules)} {mode_label.lower()}(s)...\n")

    t0 = time.monotonic()
    for num, mod_name, mod in run_modules:
        try:
            ret = mod.run(collector, **stage_kwargs)
            # Stage 5 returns discovered fixtures — pass them to later stages
            if isinstance(ret, list) and ret:
                stage_kwargs["fixtures"] = ret
        except Exception as exc:
            # If a module itself blows up, record a single ERROR for it.
            from harness import _run_test
            mod_label = getattr(mod, "STAGE", mod_name)
            _run_test(collector, mod_label, f"{num}.0",
                      f"{mode_label} {num} module load/run",
                      lambda: (_ for _ in ()).throw(exc))
    elapsed = time.monotonic() - t0

    # --- Final summary ---
    print(f"\n{'='*60}")
    print("  FINAL RESULTS")
    print(f"{'='*60}")

    total = len(collector.results)
    passed = sum(1 for r in collector.results if r.status == "PASS")
    failed = sum(1 for r in collector.results if r.status == "FAIL")
    errors = sum(1 for r in collector.results if r.status == "ERROR")
    skipped = sum(1 for r in collector.results if r.status == "SKIP")

    print(f"\n  Total: {total} | ✅ {passed} passed | ❌ {failed} failed | 💥 {errors} errors | ⏭️  {skipped} skipped")
    print(f"  ⏱️  Elapsed: {elapsed:.1f}s")

    if collector.all_passed:
        print("\n  🎉 ALL TESTS PASSED\n")
    else:
        print("\n  ⚠️  SOME TESTS FAILED:\n")
        for r in collector.results:
            if r.status in ("FAIL", "ERROR"):
                print(f"    ❌ [{r.test_id}] {r.name}")
                if r.message:
                    for line in r.message.strip().split("\n")[:5]:
                        print(f"       {line}")
                print()

    # --- HTML report ---
    # Prefer fixtures from stage runs (they have generated_images/progress_log),
    # fall back to fresh discovery for ground-truth-only display.
    fixtures = stage_kwargs.get("fixtures") or discover_fixtures(env.get("fixtures_dir", "") or "")
    # Ensure ground-truth images are copied into the output directory so the
    # HTML report can reference them via relative paths.
    if fixtures:
        for fx in fixtures:
            copy_ground_truth(fx, output_dir)
    report_path = str(output_dir / "index.html")
    run_config = {
        "python_version": env.get("python_version"),
        "pip_version": env.get("pip_version"),
        "has_pil": env.get("has_pil"),
        "server_url": env.get("server_url"),
        "comfyui_root": env.get("comfyui_root"),
        "ffmpeg_path": env.get("ffmpeg_path"),
        "magick_path": env.get("magick_path"),
        "fixtures_dir": env.get("fixtures_dir"),
        "output_dir": str(output_dir),
    }
    generate_html_report(collector, report_path, fixtures=fixtures or None,
                         run_config=run_config)
    print(f"  📄 Report: {report_path}")

    # --- Optional HTTP server ---
    if args.port:
        port = args.port
        print(f"\n  🌐 Serving results at http://localhost:{port}")
        print(f"     Serving from: {output_dir}")
        print(f"     Press Ctrl+C to stop\n")

        if not args.no_browser:
            import threading

            def _open_browser():
                time.sleep(1.0)
                webbrowser.open(f"http://localhost:{port}")
            threading.Thread(target=_open_browser, daemon=True).start()

        try:
            subprocess.run(
                [sys.executable, "-m", "http.server", str(port)],
                cwd=str(output_dir),
            )
        except KeyboardInterrupt:
            print("\n  Server stopped.")
    elif not args.no_browser:
        try:
            webbrowser.open(f"file://{os.path.abspath(report_path)}")
        except Exception:
            pass

    return 0 if collector.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
