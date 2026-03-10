#!/usr/bin/env python3
"""
harness.py — Shared test infrastructure for the autograph modular test suite.

Provides:
- TestResult / ResultCollector / _run_test  — core test framework
- BUILTIN_NODE_INFO                        — minimal offline node schema
- TEST_CATALOG                             — rich descriptions for HTML report
- FixtureCase / discover_fixtures          — fixture helpers
- _print_stage_summary                     — console output
- generate_html_report                     — HTML dashboard generator
- Path constants (_REPO_ROOT, _BUNDLED_WORKFLOW)
"""

from __future__ import annotations

import copy
import datetime
import html as html_mod
import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_WORKFLOW = _REPO_ROOT / "examples" / "workflows" / "workflow.json"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# External fixture helpers (replaces _fixtures.py)
# ---------------------------------------------------------------------------
def fixture_dir() -> Path:
    """
    Return the directory containing JSON fixtures for offline tests.

    Resolution order:
    - env AUTOGRAPH_TESTDATA_DIR
    - repo-root / _testdata   (local, should be gitignored)
    - ../data                 (common layout when this repo lives next to ComfyUI)
    """
    env = os.environ.get("AUTOGRAPH_TESTDATA_DIR")
    if isinstance(env, str) and env.strip():
        p = Path(env).expanduser().resolve()
        if p.is_dir():
            return p

    local = (_REPO_ROOT / "_testdata").resolve()
    if local.is_dir():
        return local

    sibling = (_REPO_ROOT.parent / "data").resolve()
    if sibling.is_dir():
        return sibling

    cur: Optional[Path] = _REPO_ROOT
    for _ in range(3):
        if cur is None:
            break
        cand = (cur.parent / "data").resolve()
        if cand.is_dir():
            return cand
        cur = cur.parent if cur.parent != cur else None

    raise RuntimeError(
        "Offline test fixtures not found. Set AUTOGRAPH_TESTDATA_DIR, or create ./_testdata/, "
        "or place this repo next to a sibling ../data/ directory."
    )


def fixture_path(name: str) -> Path:
    """Return absolute path to a fixture file in fixture_dir()."""
    base = fixture_dir()
    p = (base / name).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Offline test fixture missing: {p}")
    return p


# ---------------------------------------------------------------------------
# Built-in node_info covering the 6 node types in the bundled workflow.json
# ---------------------------------------------------------------------------
BUILTIN_NODE_INFO: Dict[str, Any] = {
    "CheckpointLoaderSimple": {
        "input": {
            "required": {
                "ckpt_name": [["sd_xl_base_1.0.safetensors", "v1-5-pruned-emaonly-fp16.safetensors"], {}],
            },
        },
        "output": ["MODEL", "CLIP", "VAE"],
        "output_is_list": [False, False, False],
        "output_name": ["MODEL", "CLIP", "VAE"],
        "name": "CheckpointLoaderSimple",
        "display_name": "Load Checkpoint",
        "category": "loaders",
    },
    "CLIPTextEncode": {
        "input": {
            "required": {
                "text": ["STRING", {"multiline": True, "dynamicPrompts": True, "tooltip": "The text to be encoded."}],
                "clip": ["CLIP"],
            },
        },
        "output": ["CONDITIONING"],
        "output_is_list": [False],
        "output_name": ["CONDITIONING"],
        "name": "CLIPTextEncode",
        "display_name": "CLIP Text Encode (Prompt)",
        "category": "conditioning",
    },
    "KSampler": {
        "input": {
            "required": {
                "model": ["MODEL"],
                "positive": ["CONDITIONING"],
                "negative": ["CONDITIONING"],
                "latent_image": ["LATENT"],
                "seed": ["INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "tooltip": "Random seed for generation."}],
                "control_after_generate": [["fixed", "increment", "decrement", "randomize"], {"tooltip": "Seed update mode."}],
                "steps": ["INT", {"default": 20, "min": 1, "max": 10000, "tooltip": "Total denoising steps."}],
                "cfg": ["FLOAT", {"default": 8.0, "min": 0.0, "max": 100.0, "step": 0.1, "round": 0.01, "tooltip": "Classifier-free guidance scale."}],
                "sampler_name": [["euler", "euler_ancestral", "heun", "dpm_2", "dpm_2_ancestral", "lms", "ddim", "uni_pc"], {}],
                "scheduler": [["normal", "karras", "exponential", "sgm_uniform", "simple", "ddim_uniform"], {}],
                "denoise": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01, "tooltip": "Denoising strength."}],
            },
        },
        "output": ["LATENT"],
        "output_is_list": [False],
        "output_name": ["LATENT"],
        "name": "KSampler",
        "display_name": "KSampler",
        "category": "sampling",
    },
    "EmptyLatentImage": {
        "input": {
            "required": {
                "width": ["INT", {"default": 512, "min": 16, "max": 16384, "step": 8, "tooltip": "Image width in pixels."}],
                "height": ["INT", {"default": 512, "min": 16, "max": 16384, "step": 8, "tooltip": "Image height in pixels."}],
                "batch_size": ["INT", {"default": 1, "min": 1, "max": 4096, "tooltip": "Number of latent images in the batch."}],
            },
        },
        "output": ["LATENT"],
        "output_is_list": [False],
        "output_name": ["LATENT"],
        "name": "EmptyLatentImage",
        "display_name": "Empty Latent Image",
        "category": "latent",
    },
    "VAEDecode": {
        "input": {
            "required": {
                "samples": ["LATENT"],
                "vae": ["VAE"],
            },
        },
        "output": ["IMAGE"],
        "output_is_list": [False],
        "output_name": ["IMAGE"],
        "name": "VAEDecode",
        "display_name": "VAE Decode",
        "category": "latent",
    },
    "SaveImage": {
        "input": {
            "required": {
                "images": ["IMAGE"],
                "filename_prefix": ["STRING", {"default": "ComfyUI", "tooltip": "Prefix for saved filenames."}],
            },
        },
        "output": [],
        "output_is_list": [],
        "output_name": [],
        "name": "SaveImage",
        "display_name": "Save Image",
        "category": "image",
        "output_node": True,
    },
}

# Cached path to BUILTIN_NODE_INFO written as a JSON file
_BUILTIN_NODE_INFO_PATH: Optional[Path] = None

def builtin_node_info_path() -> Path:
    """Write BUILTIN_NODE_INFO to a temp JSON file (cached) and return its path."""
    global _BUILTIN_NODE_INFO_PATH
    if _BUILTIN_NODE_INFO_PATH is not None and _BUILTIN_NODE_INFO_PATH.exists():
        return _BUILTIN_NODE_INFO_PATH
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="builtin_node_info_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(BUILTIN_NODE_INFO, f)
    _BUILTIN_NODE_INFO_PATH = Path(tmp)
    return _BUILTIN_NODE_INFO_PATH


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------
@dataclass
class FixtureCase:
    """One test fixture discovered from a fixtures/ subdirectory."""
    name: str
    directory: Path
    manifest: Dict[str, Any]
    progress_log: List[Dict[str, Any]] = field(default_factory=list)
    generated_images: List[Path] = field(default_factory=list)
    ground_truth_images: List[Path] = field(default_factory=list)


def discover_fixtures(fixtures_dir: str) -> List[FixtureCase]:
    """Scan for subdirectories containing fixture.json."""
    cases: List[FixtureCase] = []
    fdir = Path(fixtures_dir)
    if not fdir.is_dir():
        return cases
    for child in sorted(fdir.iterdir()):
        manifest_path = child / "fixture.json"
        if child.is_dir() and manifest_path.exists():
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            gt_dir = child / data.get("expected", {}).get("ground_truth_dir", "ground-truth")
            gt_images = sorted(gt_dir.glob("*.png")) if gt_dir.is_dir() else []
            cases.append(FixtureCase(
                name=data.get("name", child.name),
                directory=child,
                manifest=data,
                ground_truth_images=gt_images,
            ))
    return cases


def clean_output_dir(output_dir: Path) -> None:
    """Wipe output directory contents (except .gitignore)."""
    if output_dir.exists():
        for child in output_dir.iterdir():
            if child.name == ".gitignore":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)


def copy_ground_truth(fixture: FixtureCase, output_dir: Path) -> None:
    """Copy ground-truth images into the output directory for comparison."""
    gt_out = output_dir / fixture.directory.name / "ground-truth"
    gt_out.mkdir(parents=True, exist_ok=True)
    for img in fixture.ground_truth_images:
        shutil.copy2(img, gt_out / img.name)


# ---------------------------------------------------------------------------
# Result collector
# ---------------------------------------------------------------------------
class TestResult:
    """Stores one test outcome with optional rich context."""
    __slots__ = ("stage", "test_id", "name", "status", "message", "duration_s", "detail")

    def __init__(self, stage: str, test_id: str, name: str):
        self.stage = stage
        self.test_id = test_id
        self.name = name
        self.status: str = "PENDING"  # PASS, FAIL, SKIP, ERROR
        self.message: str = ""
        self.duration_s: float = 0.0
        self.detail: Dict[str, Any] = {}  # desc, inputs, outputs, code, etc.


class ResultCollector:
    """Aggregates results across all stages."""

    def __init__(self) -> None:
        self.results: List[TestResult] = []
        self._current: Optional[TestResult] = None

    def begin(self, stage: str, test_id: str, name: str) -> TestResult:
        r = TestResult(stage, test_id, name)
        self.results.append(r)
        self._current = r
        return r

    def pass_(self, r: TestResult, msg: str = "") -> None:
        r.status = "PASS"
        r.message = msg

    def fail(self, r: TestResult, msg: str = "") -> None:
        r.status = "FAIL"
        r.message = msg

    def skip(self, r: TestResult, msg: str = "") -> None:
        r.status = "SKIP"
        r.message = msg

    def error(self, r: TestResult, msg: str = "") -> None:
        r.status = "ERROR"
        r.message = msg

    # --- summaries ---
    def by_stage(self) -> Dict[str, List[TestResult]]:
        out: Dict[str, List[TestResult]] = {}
        for r in self.results:
            out.setdefault(r.stage, []).append(r)
        return out

    @property
    def all_passed(self) -> bool:
        return all(r.status in ("PASS", "SKIP") for r in self.results)


# ---------------------------------------------------------------------------
# Skip exception — raise inside a test to mark as SKIP
# ---------------------------------------------------------------------------
class SkipTest(Exception):
    """Raise inside a test function to mark it as SKIP instead of FAIL."""
    pass


# ---------------------------------------------------------------------------
# Helper to run a test callable and catch everything
# ---------------------------------------------------------------------------
def _run_test(collector: ResultCollector, stage: str, test_id: str, name: str,
              fn: Callable[[], None], *,
              detail: Optional[Dict[str, Any]] = None) -> TestResult:
    r = collector.begin(stage, test_id, name)
    if detail:
        r.detail = detail
    t0 = time.monotonic()
    try:
        ret = fn()
        collector.pass_(r)
        # If fn() returns a dict, merge it into detail for the HTML report.
        # Keys: input, output, result, desc, code — all optional.
        if isinstance(ret, dict):
            r.detail.update(ret)
    except SkipTest as e:
        collector.skip(r, str(e))
    except AssertionError as e:
        collector.fail(r, str(e) or traceback.format_exc())
    except Exception:
        collector.error(r, traceback.format_exc())
    r.duration_s = time.monotonic() - t0
    return r


# ---------------------------------------------------------------------------
# Test catalog — rich descriptions for the HTML report
# Maps test_id → {desc, inputs, outputs, code}
# ---------------------------------------------------------------------------
TEST_CATALOG: Dict[str, Dict[str, str]] = {
    # Stage 0: Bootstrap
    "0.1": {"desc": "Verify autograph package can be imported", "inputs": "Python module path", "outputs": "Module object"},
    "0.2": {"desc": "Version string follows semver (major.minor[.patch])", "inputs": "autograph.__version__", "outputs": "Validated version string"},
    "0.3": {"desc": "All public API symbols exist in autograph namespace", "inputs": "Expected symbol list (Flow, ApiFlow, Workflow, etc.)", "outputs": "All symbols found or list of missing",
            "code": "from autograph import Flow, ApiFlow, Workflow, NodeInfo, convert, map_strings"},
    "0.4": {"desc": "Bundled workflow.json can be loaded as a Flow object", "inputs": "examples/workflows/workflow.json", "outputs": "Flow object with nodes"},
    "0.5": {"desc": "Built-in node_info dict contains the 6 standard ComfyUI node classes", "inputs": "BUILTIN_NODE_INFO dict (KSampler, CLIPTextEncode, etc.)", "outputs": "NodeInfo object"},

    # Stage 1: Load + Access
    "1.1": {"desc": "Load workflow from a filesystem path string", "inputs": "Path string → workflow.json", "outputs": "Flow object",
            "code": "f = Flow.load('/path/to/workflow.json')"},
    "1.2": {"desc": "Load workflow from a pathlib.Path object", "inputs": "Path object", "outputs": "Flow object",
            "code": "f = Flow.load(Path('workflow.json'))"},
    "1.3": {"desc": "Load workflow from an in-memory dict", "inputs": "Python dict (parsed JSON)", "outputs": "Flow object",
            "code": "f = Flow.load({'nodes': [...], 'links': [...]})"},
    "1.4": {"desc": "Load workflow from a raw JSON string", "inputs": "JSON string", "outputs": "Flow object"},
    "1.5": {"desc": "Load workflow from bytes (UTF-8 encoded JSON)", "inputs": "bytes object", "outputs": "Flow object"},
    "1.6": {"desc": "Enumerate all nodes in the workflow graph", "inputs": "Flow object", "outputs": "NodeSet collection"},
    "1.7": {"desc": "Access a node by its class_type using Python dot notation", "inputs": "flow.nodes.KSampler", "outputs": "Node or NodeSet",
            "code": "ks = flow.nodes.KSampler"},
    "1.8": {"desc": "Access multiple instances of the same node type via indexing", "inputs": "flow.nodes.CLIPTextEncode[0]", "outputs": "Individual node instances",
            "code": "clip_pos = flow.nodes.CLIPTextEncode[0]\nclip_neg = flow.nodes.CLIPTextEncode[1]"},
    "1.9": {"desc": "Read widget values on a converted API node using dot notation", "inputs": "api.KSampler.seed", "outputs": "Widget value (int/float/str)",
            "code": "api = ApiFlow('wf.json', node_info=ni)\nseed = api.KSampler.seed"},
    "1.10": {"desc": "List all widget attribute names for a node", "inputs": "node.attrs()", "outputs": "List of widget names ['seed', 'steps', ...]",
             "code": "attrs = api.KSampler.attrs()  # ['seed', 'steps', 'cfg', ...]"},
    "1.11": {"desc": "Set a widget value via dot notation and verify it persists", "inputs": "api.KSampler.seed = 42", "outputs": "Updated seed value = 42",
             "code": "api.KSampler.seed = 42\nassert api.KSampler.seed == 42"},
    "1.16": {"desc": "Serialize Flow back to JSON string", "inputs": "Flow object", "outputs": "Valid JSON string",
             "code": "j = flow.to_json()"},
    "1.17": {"desc": "Load → serialize → reload → serialize produces identical JSON", "inputs": "Flow object", "outputs": "Two identical JSON dicts"},
    "1.18": {"desc": "Save to file, reload, and verify content matches", "inputs": "flow.save(path)", "outputs": "Reloaded Flow matches original"},
    "1.19": {"desc": "Build the internal DAG (directed acyclic graph) of node connections", "inputs": "Flow object", "outputs": "DAG structure"},
    "1.20": {"desc": "Tab completion support: dir(flow.nodes) lists node class_types", "inputs": "dir(flow.nodes)", "outputs": "['KSampler', 'CLIPTextEncode', ...]"},
    "1.21": {"desc": "Tab completion support: dir(api.KSampler) lists widget names", "inputs": "dir(api.KSampler)", "outputs": "['seed', 'steps', 'cfg', ...]"},

    # Stage 2: Convert + Metadata
    "2.1": {"desc": "Convert a Flow workflow to API format using node_info", "inputs": "workflow.json + node_info", "outputs": "ApiFlow (API-format dict with inputs resolved)",
            "code": "api = ApiFlow('wf.json', node_info=node_info)"},
    "2.2": {"desc": "Non-API nodes (MarkdownNote) are stripped during conversion", "inputs": "11-node workflow (4 MarkdownNote + 7 real)", "outputs": "7 API nodes (MarkdownNotes removed)"},
    "2.3": {"desc": "Access converted node widgets via dot notation", "inputs": "api.KSampler.seed", "outputs": "Seed value from API dict"},
    "2.4": {"desc": "Access raw API node dict by node ID string", "inputs": "api['3']", "outputs": "Node dict with class_type, inputs"},
    "2.5": {"desc": "Convert workflow and serialize to JSON in one step", "inputs": "ApiFlow(path, node_info)", "outputs": "JSON string ready for ComfyUI /prompt API"},
    "2.6": {"desc": "Convert with error reporting — returns ok, data, errors, warnings", "inputs": "Flow + node_info", "outputs": "ConvertResult with .ok, .data, .errors",
            "code": "result = convert_with_errors(flow, node_info=ni)\nif result.ok: api = result.data"},
    "2.7": {"desc": "Access _meta dict on API nodes (autograph metadata)", "inputs": "api.KSampler._meta", "outputs": "Dict or None"},
    "2.9": {"desc": "Metadata written to _meta persists through to_json() serialization", "inputs": "node['_meta'] = {...}", "outputs": "_meta present in JSON output"},
    "2.14": {"desc": "Widget introspection: query available choices for combo widgets", "inputs": "api.KSampler.sampler_name.choices()", "outputs": "['euler', 'euler_ancestral', ...]",
             "code": "choices = api.KSampler.sampler_name.choices()"},
    "2.15": {"desc": "Widget introspection: get tooltip text for a widget", "inputs": "widget.tooltip()", "outputs": "Tooltip string or None"},
    "2.16": {"desc": "Widget introspection: get full spec (type, default, min, max)", "inputs": "widget.spec()", "outputs": "Spec dict with type constraints"},

    # Stage 3: Find + Navigate
    "3.1": {"desc": "Find nodes by exact class_type match", "inputs": "find(type='KSampler')", "outputs": "1 matching node",
            "code": "results = flow.nodes.find(type='KSampler')"},
    "3.2": {"desc": "Find nodes case-insensitively", "inputs": "find(type='ksampler')", "outputs": "1 matching node (case-insensitive)"},
    "3.3": {"desc": "Find nodes using regex pattern matching", "inputs": "find(type=re.compile('CLIP.*'))", "outputs": "2 CLIPTextEncode nodes",
            "code": "import re\nresults = flow.nodes.find(type=re.compile('CLIP.*'))"},
    "3.4": {"desc": "Find nodes by their display title", "inputs": "find(title='Note: Prompt')", "outputs": "1 matching node"},
    "3.5": {"desc": "Find nodes by title using regex", "inputs": "find(title=re.compile('Note:.*'))", "outputs": "≥3 matching MarkdownNote nodes"},
    "3.6": {"desc": "Multi-filter AND: type + widget value must both match", "inputs": "find(type='KSampler', seed=696969)", "outputs": "Matching nodes (AND logic)"},
    "3.7": {"desc": "OR operator: any filter criterion can match", "inputs": "find(type='KSampler', operator='or')", "outputs": "≥1 matching node"},
    "3.8": {"desc": "Find a specific node by its numeric ID", "inputs": "find(node_id=3)", "outputs": "1 node (node 3 = KSampler)"},
    "3.9": {"desc": "Get the hierarchical path of a found node", "inputs": "result.path()", "outputs": "Path string like 'KSampler'"},
    "3.10": {"desc": "Get the addressable location of a found node", "inputs": "result.address()", "outputs": "Address string"},
    "3.11": {"desc": "Find on ApiFlow by class_type", "inputs": "api.find(class_type='KSampler')", "outputs": "Matching API nodes",
             "code": "api = ApiFlow('wf.json', node_info=ni)\nresults = api.find(class_type='KSampler')"},
    "3.13": {"desc": "Direct node lookup by string ID on ApiFlow", "inputs": "api.by_id('3')", "outputs": "Node dict for ID '3'"},

    # Stage 4: Mapping
    "4.1": {"desc": "Replace literal string values across the entire API dict", "inputs": "map_strings(api_dict, {'literal': {'Default': 'REPLACED'}})", "outputs": "All 'Default' → 'REPLACED'",
            "code": "result = map_strings(dict(api.unwrap()), spec)"},
    "4.5": {"desc": "Force all cached nodes to recompute (change seeds)", "inputs": "force_recompute(api)", "outputs": "Modified API dict with fresh seeds",
            "code": "result = force_recompute(api)"},
    "4.7": {"desc": "api_mapping calls user callback for every node+param pair", "inputs": "api_mapping(api, callback)", "outputs": "Callback receives {node_id, class_type, param, value}",
            "code": "def cb(ctx):\n    print(ctx['class_type'], ctx['param'], ctx['value'])\napi_mapping(api, cb, node_info=ni)"},
    "4.8": {"desc": "api_mapping callback can return a value to override a parameter", "inputs": "Return 999999 when param == 'seed'", "outputs": "All KSampler seeds changed to 999999",
            "code": "def cb(ctx):\n    if ctx['param'] == 'seed': return 999999\napi_mapping(api, cb, node_info=ni)"},

    # Stage 5: Fixtures
    "5.1": {"desc": "Scan fixtures directory for fixture.json manifests", "inputs": "fixtures/ directory path", "outputs": "List of discovered fixture cases"},

    # Stage 6: Server
    "6.1": {"desc": "Verify ComfyUI server is reachable via HTTP", "inputs": "Server URL (e.g. http://localhost:8188)", "outputs": "HTTP 200 response"},
    "6.2": {"desc": "Fetch live node_info from running ComfyUI server", "inputs": "NodeInfo.fetch(server_url=...)", "outputs": "Full node_info dict (~100+ node types)",
            "code": "ni = NodeInfo.fetch(server_url='http://localhost:8188')"},
    "6.3": {"desc": "Convert workflow using live server node_info", "inputs": "ApiFlow(path, server_url=...)", "outputs": "ApiFlow with live node specs"},

    # Stage 7: Tools
    "7.1": {"desc": "Verify PIL/Pillow can create, save, and reload images", "inputs": "PIL.Image.new('RGB', (64,64))", "outputs": "64×64 red PNG image"},
}


# ---------------------------------------------------------------------------
# Console reporting
# ---------------------------------------------------------------------------
def _print_stage_summary(collector: ResultCollector, stage: str) -> None:
    results = [r for r in collector.results if r.stage == stage]
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    errors = sum(1 for r in results if r.status == "ERROR")
    skipped = sum(1 for r in results if r.status == "SKIP")

    for r in results:
        icon = {"PASS": "✅", "FAIL": "❌", "ERROR": "💥", "SKIP": "⏭️"}.get(r.status, "?")
        line = f"  {icon} [{r.test_id}] {r.name}"
        if r.status in ("FAIL", "ERROR") and r.message:
            first_line = r.message.strip().split("\n")[0][:100]
            line += f" — {first_line}"
        print(line)

    print(f"\n  Summary: {passed} passed, {failed} failed, {errors} errors, {skipped} skipped\n")


# ---------------------------------------------------------------------------
# HTML report generator
# ---------------------------------------------------------------------------
def _build_run_config_html(run_config: Optional[Dict[str, Any]],
                           version: str = "", python_ver: str = "",
                           platform: str = "", date_str: str = "",
                           total: int = 0) -> str:
    """Build a collapsible environment details panel.

    The summary bar shows version / python / OS / date / total.
    Clicking expands a vertical grid of all detected environment fields.
    """
    # Summary bar items
    summary_parts = []
    if version:
        summary_parts.append(f"<strong>Version:</strong> {html_mod.escape(version)}")
    if python_ver:
        summary_parts.append(f"<strong>Python:</strong> {html_mod.escape(python_ver)}")
    if platform:
        summary_parts.append(f"<strong>OS:</strong> {html_mod.escape(platform)}")
    if date_str:
        summary_parts.append(f"<strong>Date:</strong> {html_mod.escape(date_str)}")
    summary_parts.append(f"<strong>Total:</strong> {total} tests")
    summary_html = " &nbsp;|&nbsp; ".join(summary_parts)

    # Detail rows
    if not run_config:
        return f'<details class="env-panel"><summary class="env-summary">{summary_html}</summary></details>\n'

    labels = [
        ("python_version", "🐍 Python"),
        ("pip_version",    "📦 pip"),
        ("has_pil",        "🖼️ PIL/Pillow"),
        ("server_url",     "🌐 ComfyUI Server"),
        ("comfyui_root",   "🖥️ ComfyUI Modules"),
        ("ffmpeg_path",    "🎬 ffmpeg"),
        ("magick_path",    "🪄 magick"),
        ("fixtures_dir",   "📁 Fixtures"),
        ("output_dir",     "📂 Output"),
    ]
    rows = ""
    for key, label in labels:
        val = run_config.get(key)
        if val is None:
            display = '<span class="env-val env-missing">not set</span>'
        elif isinstance(val, bool):
            if val:
                display = '<span class="env-val env-ok">✓ available</span>'
            else:
                display = '<span class="env-val env-missing">✗ unavailable</span>'
        else:
            display = f'<span class="env-val env-ok">{html_mod.escape(str(val))}</span>'
        rows += f'<div class="env-row"><span class="env-key">{label}</span>{display}</div>\n'

    return f"""<details class="env-panel">
<summary class="env-summary">{summary_html}</summary>
<div class="env-grid">
{rows}</div>
</details>
"""


def generate_html_report(collector: ResultCollector, output_path: str,
                         fixtures: Optional[List[FixtureCase]] = None,
                         run_config: Optional[Dict[str, Any]] = None) -> str:
    """Generate an HTML investigation dashboard with test details, images, and progress."""
    import autograph
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stages = collector.by_stage()
    out_dir = Path(output_path).parent

    total = len(collector.results)
    passed = sum(1 for r in collector.results if r.status == "PASS")
    failed = sum(1 for r in collector.results if r.status == "FAIL")
    errors = sum(1 for r in collector.results if r.status == "ERROR")
    skipped = sum(1 for r in collector.results if r.status == "SKIP")
    overall_color = "#2d5016" if collector.all_passed else "#8b1a1a"

    # --- Build stage sections with expandable rows ---
    stage_sections = ""
    for stage_name, results in stages.items():
        stage_passed = sum(1 for r in results if r.status == "PASS")
        stage_total = len(results)
        stage_icon = "✅" if stage_passed == stage_total else "❌"

        rows = ""
        for r in results:
            color = {
                "PASS": "#2d5016", "FAIL": "#8b1a1a",
                "ERROR": "#8b4513", "SKIP": "#4a4a00"
            }.get(r.status, "#333")
            icon = {"PASS": "✅", "FAIL": "❌", "ERROR": "💥", "SKIP": "⏭️"}.get(r.status, "?")
            msg_html = html_mod.escape(r.message) if r.message else ""

            # Merge catalog (static) with runtime detail (from test return value)
            cat = TEST_CATALOG.get(r.test_id, {}).copy()
            if r.detail:
                cat.update(r.detail)
            desc = cat.get("desc", "")
            # Prefer runtime singular keys over static catalog plural keys
            inputs = cat.get("input", cat.get("inputs", ""))
            outputs = cat.get("output", cat.get("outputs", ""))
            result_val = cat.get("result", "")
            code = cat.get("code", "")

            preview = cat.get("preview", "")
            preview_type = cat.get("preview_type", "")  # "mermaid", "dot", or ""

            has_detail = bool(desc or inputs or outputs or result_val or code or msg_html or preview)
            clickable = ' class="expandable" onclick="toggleDetail(this)"' if has_detail else ''

            # Tooltip text
            tooltip = html_mod.escape(desc) if desc else ""

            rows += f"""
            <tr style="background: {color}22;"{clickable} title="{tooltip}">
                <td class="id-col">{html_mod.escape(r.test_id)}</td>
                <td>{icon} {html_mod.escape(r.name)}</td>
                <td><strong>{r.status}</strong></td>
                <td>{r.duration_s:.3f}s</td>
                <td class="arrow-col">{'▶' if has_detail else ''}</td>
            </tr>"""

            if has_detail:
                detail_parts = []
                if desc:
                    detail_parts.append(f'<div class="detail-desc">{html_mod.escape(desc)}</div>')
                if inputs or outputs or result_val:
                    io_html = '<div class="detail-io">'
                    if inputs:
                        io_html += f'<div class="io-box"><span class="io-label">INPUT</span> {html_mod.escape(str(inputs))}</div>'
                    if outputs:
                        io_html += f'<div class="io-box"><span class="io-label">OUTPUT</span> {html_mod.escape(str(outputs))}</div>'
                    if result_val:
                        io_html += f'<div class="io-box"><span class="io-label io-result">RESULT</span> {html_mod.escape(str(result_val))}</div>'
                    io_html += '</div>'
                    detail_parts.append(io_html)
                if preview:
                    escaped = html_mod.escape(preview)
                    if preview_type == "mermaid":
                        detail_parts.append(
                            f'<div class="detail-preview"><div class="mermaid">{escaped}</div></div>')
                    else:
                        detail_parts.append(
                            f'<div class="detail-code"><pre>{escaped}</pre></div>')
                if code:
                    detail_parts.append(f'<div class="detail-code"><pre>{html_mod.escape(code)}</pre></div>')
                if msg_html and r.status != "PASS":
                    detail_parts.append(f'<div class="detail-msg"><strong>Message:</strong><pre>{msg_html}</pre></div>')

                rows += f"""
            <tr class="detail-row" style="display:none;">
                <td colspan="5">
                    <div class="detail-content">{"".join(detail_parts)}</div>
                </td>
            </tr>"""

        stage_sections += f"""
    <div class="stage-section">
        <h2 class="stage-header" onclick="toggleStage(this)">
            {stage_icon} {html_mod.escape(stage_name)}
            <span class="stage-count">{stage_passed}/{stage_total}</span>
            <span class="stage-toggle">▼</span>
        </h2>
        <div class="stage-body">
            <table>
            <thead><tr><th style="width:60px">ID</th><th>Test</th><th style="width:70px">Status</th><th style="width:70px">Time</th><th style="width:30px"></th></tr></thead>
            <tbody>{rows}</tbody>
            </table>
        </div>
    </div>"""

    # --- Build image comparison sections ---
    image_sections = ""
    if fixtures:
        for fx in fixtures:
            if not fx.ground_truth_images and not fx.generated_images:
                continue
            section = f'<div class="fixture-card">\n'
            section += f'<h2>🖼️ {html_mod.escape(fx.name)}</h2>\n'
            section += '<div class="image-comparison">\n'

            if fx.ground_truth_images:
                section += '<div class="image-col">\n<h3>Ground Truth</h3>\n'
                for img in fx.ground_truth_images:
                    rel = os.path.relpath(str(out_dir / fx.directory.name / "ground-truth" / img.name), str(out_dir))
                    section += f'<a href="{rel}" target="_blank" class="img-link">'
                    section += f'<img src="{rel}" alt="{html_mod.escape(img.name)}" />'
                    section += f'</a>\n<span class="img-label">{html_mod.escape(img.name)}</span>\n'
                section += '</div>\n'

            section += '<div class="image-col">\n<h3>Generated</h3>\n'
            if fx.generated_images:
                for img in fx.generated_images:
                    rel = os.path.relpath(str(img), str(out_dir))
                    section += f'<a href="{rel}" target="_blank" class="img-link">'
                    section += f'<img src="{rel}" alt="{html_mod.escape(img.name)}" />'
                    section += f'</a>\n<span class="img-label">{html_mod.escape(img.name)}</span>\n'
            else:
                section += '<div class="no-image-placeholder">'
                section += '<p>⏳ No generated images</p>'
                section += '<p class="img-label">Run with --server-url to generate output images</p>'
                section += '</div>\n'
            section += '</div>\n'
            section += '</div>\n'  # end image-comparison

            if fx.progress_log:
                progress_steps = [e for e in fx.progress_log if e.get("type") == "progress"]
                if progress_steps:
                    last = progress_steps[-1]
                    data = last.get("data", {})
                    max_val = data.get("max", 1)
                    cur_val = data.get("value", 0)
                    pct = int(cur_val / max_val * 100) if max_val else 100
                    elapsed = last.get("elapsed_s", 0)
                    section += f'<div class="progress-info">\n'
                    section += f'<strong>Progress:</strong> {cur_val}/{max_val} steps ({pct}%) — {elapsed:.1f}s\n'
                    section += f'<div class="progress-bar"><div class="progress-fill" style="width:{pct}%"></div></div>\n'

                    section += '<div class="progress-timeline">\n'
                    for step in progress_steps:
                        s_data = step.get("data", {})
                        s_val = s_data.get("value", 0)
                        s_max = s_data.get("max", 1)
                        s_time = step.get("elapsed_s", 0)
                        section += f'<div class="timeline-step" title="Step {s_val}/{s_max} at {s_time:.1f}s">'
                        section += f'<span class="timeline-dot"></span>'
                        section += f'</div>\n'
                    section += '</div>\n'  # end timeline
                    section += '</div>\n'  # end progress-info

                all_events = fx.progress_log
                if all_events:
                    section += '<details class="events-log">\n'
                    section += f'<summary>📋 Raw events ({len(all_events)} captured)</summary>\n'
                    section += '<pre class="events-pre">'
                    for evt in all_events:
                        section += html_mod.escape(json.dumps(evt, indent=None)) + '\n'
                    section += '</pre>\n</details>\n'

            section += '</div>\n'  # end fixture-card
            image_sections += section

    # --- Assemble HTML ---
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>autograph Test Dashboard</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: 'Inter', system-ui, -apple-system, sans-serif; background: #0d1117; color: #c9d1d9; margin: 0; padding: 2rem; line-height: 1.6; }}
  h1 {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 0.5rem; margin-bottom: 0.5rem; }}
  h2 {{ color: #8b949e; margin-top: 1.5rem; margin-bottom: 0.5rem; }}
  h3 {{ color: #58a6ff; margin: 0.5rem 0; font-size: 0.95em; }}

  /* Summary stats */
  .summary {{ display: flex; gap: 1rem; margin: 1rem 0; flex-wrap: wrap; }}
  .stat {{ padding: 1rem 1.5rem; border-radius: 8px; text-align: center; min-width: 100px; }}
  .stat-label {{ font-size: 0.8em; color: #8b949e; }}
  .stat-value {{ font-size: 2em; font-weight: bold; }}
  .pass {{ background: #2d501622; border: 1px solid #2d5016; }}
  .pass .stat-value {{ color: #3fb950; }}
  .fail {{ background: #8b1a1a22; border: 1px solid #8b1a1a; }}
  .fail .stat-value {{ color: #f85149; }}
  .skip {{ background: #4a4a0022; border: 1px solid #4a4a00; }}
  .skip .stat-value {{ color: #d29922; }}
  .error {{ background: #8b451322; border: 1px solid #8b4513; }}
  .error .stat-value {{ color: #db6d28; }}
  .overall {{ padding: 1rem; border-radius: 8px; background: {overall_color}44; border: 2px solid {overall_color}; margin-bottom: 1rem; text-align: center; font-size: 1.2em; }}
  .env-info {{ color: #8b949e; font-size: 0.9em; margin-bottom: 1rem; }}
  .env-info strong {{ color: #c9d1d9; }}

  /* Collapsible env panel */
  .env-panel {{ margin-bottom: 1rem; border: 1px solid #21262d; border-radius: 8px; overflow: hidden; }}
  .env-summary {{ padding: 0.75rem 1rem; background: #161b22; cursor: pointer; color: #8b949e; font-size: 0.9em; user-select: none; display: flex; align-items: center; gap: 0.5rem; list-style: none; }}
  .env-summary::-webkit-details-marker {{ display: none; }}
  .env-summary::before {{ content: '▶'; font-size: 0.7em; color: #484f58; transition: transform 0.2s; display: inline-block; }}
  .env-panel[open] .env-summary::before {{ transform: rotate(90deg); }}
  .env-summary strong {{ color: #c9d1d9; }}
  .env-summary:hover {{ background: #1c2128; }}
  .env-grid {{ display: flex; flex-direction: column; padding: 0; background: #0d1117; }}
  .env-row {{ display: flex; align-items: center; gap: 0.75rem; padding: 0.5rem 1.25rem; border-top: 1px solid #21262d; font-size: 0.88em; }}
  .env-key {{ color: #8b949e; min-width: 150px; }}
  .env-val {{ font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 0.9em; }}
  .env-ok {{ color: #3fb950; }}
  .env-missing {{ color: #f85149; }}

  /* Preview (mermaid, dot) */
  .detail-preview {{ background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 1rem; margin-top: 0.5rem; overflow-x: auto; }}
  .detail-preview .mermaid {{ background: transparent; }}

  /* Stage sections */
  .stage-section {{ margin-bottom: 1rem; border: 1px solid #21262d; border-radius: 8px; overflow: hidden; }}
  .stage-header {{ background: #161b22; margin: 0; padding: 0.75rem 1rem; cursor: pointer; display: flex; align-items: center; gap: 0.5rem; user-select: none; font-size: 1em; }}
  .stage-header:hover {{ background: #1c2128; }}
  .stage-count {{ margin-left: auto; font-size: 0.85em; color: #8b949e; }}
  .stage-toggle {{ font-size: 0.7em; color: #484f58; transition: transform 0.2s; }}
  .stage-body {{ padding: 0; }}
  .stage-body.collapsed {{ display: none; }}

  /* Test table */
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid #21262d; }}
  th {{ background: #0d1117; color: #8b949e; font-weight: 600; font-size: 0.85em; }}
  pre {{ color: #f0f0f0; margin: 0; }}
  .id-col {{ font-family: monospace; font-size: 0.85em; color: #8b949e; }}
  .arrow-col {{ text-align: center; color: #484f58; font-size: 0.8em; transition: transform 0.2s; }}

  /* Expandable rows */
  .expandable {{ cursor: pointer; }}
  .expandable:hover {{ background: #161b2244 !important; }}
  .expandable:hover .arrow-col {{ color: #58a6ff; }}
  .detail-row td {{ padding: 0; background: #161b22; }}
  .detail-content {{ padding: 0.75rem 1rem 0.75rem 4rem; border-left: 3px solid #58a6ff; animation: slideDown 0.15s ease-out; }}
  @keyframes slideDown {{ from {{ opacity: 0; max-height: 0; }} to {{ opacity: 1; max-height: 500px; }} }}

  .detail-desc {{ color: #c9d1d9; margin-bottom: 0.5rem; font-size: 0.9em; }}
  .detail-io {{ display: flex; gap: 1rem; margin-bottom: 0.5rem; flex-wrap: wrap; }}
  .io-box {{ background: #21262d; border-radius: 6px; padding: 0.4rem 0.75rem; font-size: 0.85em; flex: 1; min-width: 200px; }}
  .io-label {{ display: inline-block; background: #30363d; color: #58a6ff; padding: 0.1rem 0.4rem; border-radius: 3px; font-size: 0.75em; font-weight: 600; margin-right: 0.5rem; letter-spacing: 0.05em; }}
  .io-result {{ color: #3fb950; }}
  .detail-code {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 0.5rem 0.75rem; margin-top: 0.5rem; }}
  .detail-code pre {{ font-size: 0.85em; color: #79c0ff; white-space: pre-wrap; word-break: break-word; }}
  .detail-msg {{ margin-top: 0.5rem; }}
  .detail-msg pre {{ font-size: 0.8em; color: #f85149; white-space: pre-wrap; word-break: break-word; }}

  /* Fixture cards */
  .fixture-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1.5rem; margin-top: 1.5rem; }}
  .fixture-card h2 {{ margin-top: 0; color: #c9d1d9; }}

  /* Image comparison */
  .image-comparison {{ display: flex; gap: 2rem; margin: 1rem 0; flex-wrap: wrap; }}
  .image-col {{ flex: 1; min-width: 280px; }}
  .image-col img {{ max-width: 100%; border-radius: 8px; border: 1px solid #30363d; cursor: pointer; transition: transform 0.2s; }}
  .image-col img:hover {{ transform: scale(1.02); box-shadow: 0 0 20px rgba(88,166,255,0.3); }}
  .img-link {{ display: block; margin-bottom: 0.5rem; }}
  .img-label {{ font-size: 0.8em; color: #8b949e; display: block; margin-bottom: 1rem; }}
  .no-image-placeholder {{ border: 2px dashed #30363d; border-radius: 8px; padding: 3rem 2rem; text-align: center; color: #484f58; min-height: 200px; display: flex; flex-direction: column; align-items: center; justify-content: center; }}
  .no-image-placeholder p {{ margin: 0.25rem 0; }}

  /* Progress */
  .progress-info {{ background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 1rem; margin-top: 1rem; }}
  .progress-bar {{ background: #21262d; border-radius: 4px; height: 8px; margin-top: 0.5rem; overflow: hidden; }}
  .progress-fill {{ background: linear-gradient(90deg, #3fb950, #58a6ff); height: 100%; border-radius: 4px; transition: width 0.3s; }}
  .progress-timeline {{ display: flex; gap: 2px; margin-top: 0.5rem; flex-wrap: wrap; }}
  .timeline-step {{ position: relative; }}
  .timeline-dot {{ display: inline-block; width: 6px; height: 6px; background: #3fb950; border-radius: 50%; }}
  .timeline-step:hover .timeline-dot {{ background: #58a6ff; transform: scale(1.5); }}

  /* Events log */
  .events-log {{ margin-top: 0.75rem; }}
  .events-log summary {{ cursor: pointer; color: #8b949e; font-size: 0.85em; }}
  .events-log summary:hover {{ color: #58a6ff; }}
  .events-pre {{ max-height: 300px; overflow-y: auto; font-size: 0.75em; padding: 0.5rem; background: #0d1117; border: 1px solid #21262d; border-radius: 4px; }}

  /* Lightbox */
  .lightbox {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.92); z-index: 1000; align-items: center; justify-content: center; cursor: pointer; }}
  .lightbox.active {{ display: flex; }}
  .lightbox img {{ max-width: 95vw; max-height: 95vh; object-fit: contain; border-radius: 8px; }}
</style>
</head>
<body>
<h1>🧪 autograph Test Dashboard</h1>
<div class="overall">{'🎉 ALL TESTS PASSED' if collector.all_passed else '⚠️ SOME TESTS FAILED'}</div>
{_build_run_config_html(run_config,
    version=autograph.__version__,
    python_ver=sys.version.split()[0],
    platform=sys.platform,
    date_str=now,
    total=total)}

<div class="summary">
  <div class="stat pass"><div class="stat-value">{passed}</div><div class="stat-label">Passed</div></div>
  <div class="stat fail"><div class="stat-value">{failed}</div><div class="stat-label">Failed</div></div>
  <div class="stat error"><div class="stat-value">{errors}</div><div class="stat-label">Errors</div></div>
  <div class="stat skip"><div class="stat-value">{skipped}</div><div class="stat-label">Skipped</div></div>
</div>

{stage_sections}

{image_sections}

<div id="lightbox" class="lightbox" onclick="this.classList.remove('active')">
  <img id="lightbox-img" src="" alt="Full resolution" />
</div>

<script>
// Toggle detail row
function toggleDetail(row) {{
  const detail = row.nextElementSibling;
  if (detail && detail.classList.contains('detail-row')) {{
    const arrow = row.querySelector('.arrow-col');
    if (detail.style.display === 'none') {{
      detail.style.display = 'table-row';
      if (arrow) arrow.textContent = '▼';
    }} else {{
      detail.style.display = 'none';
      if (arrow) arrow.textContent = '▶';
    }}
  }}
}}

// Toggle stage collapse
function toggleStage(header) {{
  const body = header.nextElementSibling;
  const toggle = header.querySelector('.stage-toggle');
  if (body) {{
    body.classList.toggle('collapsed');
    if (toggle) toggle.textContent = body.classList.contains('collapsed') ? '▶' : '▼';
  }}
}}

// Lightbox for images
document.querySelectorAll('.image-col img').forEach(img => {{
  img.addEventListener('click', function(e) {{
    e.preventDefault();
    e.stopPropagation();
    const lb = document.getElementById('lightbox');
    const lbImg = document.getElementById('lightbox-img');
    lbImg.src = this.parentElement.href || this.src;
    lb.classList.add('active');
  }});
}});

// Auto-expand failed tests
document.querySelectorAll('.expandable').forEach(row => {{
  const statusCell = row.querySelector('td:nth-child(3) strong');
  if (statusCell && (statusCell.textContent === 'FAIL' || statusCell.textContent === 'ERROR')) {{
    toggleDetail(row);
  }}
}});
</script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{ startOnLoad: true, theme: 'dark' }});</script>

<p style="color:#484f58;margin-top:2rem;font-size:0.85em;">Generated by autograph test suite — click any test row for details</p>
</body>
</html>"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html_content, encoding="utf-8")
    return output_path
