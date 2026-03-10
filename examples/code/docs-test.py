#!/usr/bin/env python3
r"""
docs-test.py — docs example harness for autograph

This script is intentionally *docs-driven*: it parses every markdown file in `docs/`
and turns each fenced code block into a runnable Python function registered under a label.

It is meant to be both:
- a human-friendly “run all doc examples” tool, and
- LLM-friendly / self-regenerating.

---------------------------------------------------------------------------
HOW TO RUN THE SCRIPT (with ComfyUI server)
---------------------------------------------------------------------------
# Interactive prompt for everything (with editing/backspace supported by your terminal):
>> python examples/code/docs-test.py --prompt-env

# Online run with prompting + strict failures if server isn’t reachable:
>> python examples/code/docs-test.py --mode online --prompt-env --exec-python --run-cli --strict-network

# Scripted/non-interactive (no prompts) via args (args → env → defaults):
>> python examples/code/docs-test.py --mode online --server-url http://localhost:8188 --timeout 30 --wait-timeout 60 --poll-interval 0.5 --client-id autograph --exec-python --run-cli

# If you want to use your own workflows
# Pass paths:
# --workflow /path/to/your/workflow.json
# --node-info /path/to/node_info.json
# --image /path/to/a/comfyui_output.png

# Example:
>> python examples/code/docs-test.py \
    --workflow /abs/path/workflow.json \
    --node-info /abs/path/node_info.json \
    --image /abs/path/comfyui_output.png
---------------------------------------------------------------------------

-------------------------
LLM INSTRUCTIONS (meta)
-------------------------
If you are an LLM asked to regenerate or update this file:

Goal:
- Ensure **every fenced code block** in `docs/*.md` is represented by a Python function
  that can be invoked from:
  1) Python (import and call), and
  2) the CLI provided by this script.

Constraints:
- stdlib-only (do not add new dependencies)
- Do not make any ComfyUI server calls unless explicitly enabled (opt-in)
- Must work on Windows and Linux (avoid shell-only behavior; prefer `sys.executable -m ...`)

Algorithm:
- Walk `docs/*.md` (sorted).
- Parse fenced blocks (```lang ... ```) including indented fences.
- Ignore non-executable fences like `mermaid` (still record them if you want, but skip by default).
- For each fence, create a function named `doc_<docname>__<lang>__<n>(...)`.
- Register it in `EXAMPLES` under a stable label like:
  `docs/<docname>.md#<n>:<lang>`
- Implement per-language behavior:
  - **python**: always `compile()` (syntax check). Optionally `exec()` when `exec_python=True`
  - **bash**: optionally run safe python invocations when `run_cli=True` (no `shell=True`)
  - **json**: `json.loads()` and print a short summary
- Add a minimal sandbox for snippet execution:
  - create a temp dir
  - write `workflow.json`, `workflow-api.json`, `node_info.json`
  - provide a PNG (`ComfyUI_00001_.png` and `output.png`) from repo sample image
- Add gating:
  - If a block looks like it requires network (`localhost:8188`, `.submit(`, `NodeInfo.fetch`, `/object_info`, `--submit`),
    it must be **skipped unless `allow_network=True`**.
  - If a Python block looks like framework glue / pseudo-code (`@app.`, `FastAPI`, undefined `app`), default to compile-only.
- Expose CLI flags:
  - `--list`
  - `--only LABEL[,LABEL...]` and `--skip ...`
  - `--exec-python` (attempt exec for python blocks)
  - `--run-cli` (attempt to run bash blocks that are safe python invocations)
  - `--allow-network`
  - `--workflow`, `--node-info`, `--image`
  - `--out` (output dir for temp artifacts; default is under this script)

Keep output:
- Print a clear START/END label for each function.
- Let subprocess output stream directly to the terminal (so users can “see CLI output”).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
import urllib.error
import urllib.request
import hashlib
import time
import inspect
import zlib

# Optional: enables nicer interactive line editing on POSIX terminals.
try:
    import readline  # type: ignore  # noqa: F401
except Exception:
    readline = None  # type: ignore


# -----------------------------------------------------------------------------
# Registry
# -----------------------------------------------------------------------------

ExampleFn = Callable[..., Any]


@dataclass(frozen=True)
class Example:
    label: str
    fn_name: str
    fn: ExampleFn
    doc_file: str
    block_index: int
    lang: str
    needs_network: bool
    needs_comfyui_runtime: bool
    can_exec_python: bool
    can_run_cli: bool
    continued: bool  # True when block starts with "# continued"


EXAMPLES: Dict[str, Example] = {}


# -----------------------------------------------------------------------------
# Paths / defaults
# -----------------------------------------------------------------------------

HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[2]
DOCS_DIR = REPO_ROOT / "docs"

DEFAULT_WORKFLOW = REPO_ROOT / "default.json"
DEFAULT_NODE_INFO = REPO_ROOT / "node_info.json"
DEFAULT_IMAGE = REPO_ROOT / "comfyui-image.png"

# Known fixture fallback locations (checked in order)
_FIXTURE_CANDIDATES = [
    REPO_ROOT / "autograph-test-suite" / "fixtures" / "logo-basic",
    REPO_ROOT / "fixtures" / "logo-basic",
]

# Bundled workflow inside the package (last resort for default.json)
_BUNDLED_WORKFLOW = REPO_ROOT / "autograph" / "data" / "bundled-workflow.json"

# Ensure repo-local imports work when running this script directly.
# (When a script is executed, sys.path[0] is the script directory, not the repo root.)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _ensure_repo_defaults(*, interactive: bool = True) -> bool:
    """Check for missing default files and offer to symlink or copy them.

    Returns True if all required files exist (or were successfully created),
    False if a required file is still missing after the prompt.
    """
    import shutil

    # Find a fixture dir that has the files we need
    fixture_dir: Optional[Path] = None
    for candidate in _FIXTURE_CANDIDATES:
        if candidate.is_dir():
            fixture_dir = candidate
            break

    # Build a mapping of {target: [possible sources]}
    needs: list[tuple[Path, str, list[Path]]] = []

    # default.json  <-  fixture/workflow.json or examples fallback
    if not DEFAULT_WORKFLOW.exists():
        sources: list[Path] = []
        if fixture_dir:
            p = fixture_dir / "workflow.json"
            if p.is_file():
                sources.append(p)
        wf_examples = REPO_ROOT / "examples" / "workflows" / "workflow.json"
        if wf_examples.is_file():
            sources.append(wf_examples)
        if _BUNDLED_WORKFLOW.is_file():
            sources.append(_BUNDLED_WORKFLOW)
        needs.append((DEFAULT_WORKFLOW, "workflow", sources))

    # node_info.json  <-  fixture/node-info.json
    if not DEFAULT_NODE_INFO.exists():
        sources = []
        if fixture_dir:
            for name in ("node-info.json", "node_info.json", "object_info.json"):
                p = fixture_dir / name
                if p.is_file():
                    sources.append(p)
        needs.append((DEFAULT_NODE_INFO, "node_info", sources))

    # comfyui-image.png  <-  fixture/ground-truth/*.png
    if not DEFAULT_IMAGE.exists():
        sources = []
        if fixture_dir:
            gt_dir = fixture_dir / "ground-truth"
            if gt_dir.is_dir():
                for p in sorted(gt_dir.glob("*.png")):
                    sources.append(p)
                    break  # take first
        needs.append((DEFAULT_IMAGE, "image", sources))

    if not needs:
        return True  # everything already exists

    print("\n" + "=" * 70)
    print("  docs-test setup: some default files are missing from the repo root")
    print("=" * 70)
    if fixture_dir:
        print(f"  Fixture source: {fixture_dir}")
    print()

    all_ok = True
    for target, kind, sources in needs:
        rel_target = target.relative_to(REPO_ROOT)
        if not sources:
            print(f"  ✗ {rel_target} — no source found")
            if kind == "image":
                print(f"    (optional — PNG-based doc examples will be skipped)")
            else:
                all_ok = False
            continue

        src = sources[0]
        rel_src = src.relative_to(REPO_ROOT) if str(src).startswith(str(REPO_ROOT)) else src

        if not interactive or not sys.stdin.isatty():
            # Non-interactive: auto-copy
            shutil.copy2(str(src), str(target))
            print(f"  ✓ {rel_target} — copied from {rel_src}")
            continue

        print(f"  Missing: {rel_target}")
        print(f"  Source:  {rel_src}")
        choice = input("  Action — [s]ymlink / [c]opy / [S]kip? ").strip().lower()
        if choice == "s":
            target.symlink_to(src)
            print(f"    → symlinked")
        elif choice == "c":
            shutil.copy2(str(src), str(target))
            print(f"    → copied")
        else:
            print(f"    → skipped")
            if kind != "image":
                all_ok = False

    print()
    return all_ok


# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------

def _print_banner(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def _looks_networky(text: str) -> bool:
    t = text.lower()
    needles = [
        "http://localhost:8188",
        "ws://",
        "/object_info",
        "objectinfo.fetch",
        ".submit(",
        "--submit",
        "--download-node-info-path",
        "--server-url",
    ]
    return any(n in t for n in needles)

def _snippet_has_explicit_server_url(text: str, *, lang: str) -> bool:
    t = text.lower()
    if lang == "python":
        return "server_url=" in t
    if lang == "bash":
        return "--server-url" in t
    return False

def _extract_server_url_from_snippet(text: str, *, lang: str) -> Optional[str]:
    """
    Best-effort extraction of a server URL from a snippet so we can preflight reachability.
    """
    if not isinstance(text, str) or not text:
        return None
    if lang == "python":
        m = re.search(r"""server_url\s*=\s*["']([^"']+)["']""", text)
        if m:
            return m.group(1).strip()
        return None
    if lang == "bash":
        # Look for: --server-url http://...
        try:
            parts = shlex.split(text, posix=True)
        except Exception:
            return None
        for i, p in enumerate(parts):
            if p == "--server-url" and i + 1 < len(parts):
                return parts[i + 1]
        return None
    return None


def _server_reachable(server_url: str, *, timeout_s: float = 1.0) -> bool:
    """
    Quick reachability probe. Uses /object_info which is a known ComfyUI endpoint.
    """
    if not isinstance(server_url, str) or not server_url:
        return False
    base = server_url.rstrip("/")
    url = base + "/object_info"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            # Any HTTP response implies server is reachable (even if not 200).
            return True
    except Exception:
        return False


def _fetch_node_info(server_url: str, *, timeout_s: float = 10.0) -> Dict[str, Any]:
    """
    Fetch ComfyUI /object_info (stdlib-only).
    """
    base = server_url.rstrip("/")
    url = base + "/object_info"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _download_json(url: str, *, timeout_s: float = 20.0) -> Dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _looks_pseudo_or_frameworky_python(text: str) -> bool:
    t = text
    if re.search(r"^\s*@app\.", t, flags=re.M):
        return True
    if "FastAPI" in t or "HTTPException" in t or "JSONResponse" in t:
        # Usually shown as wiring example; may be incomplete in docs.
        return True
    # Doc snippets that use autograph.xxx() without importing the module
    # (illustrative API patterns, not self-contained snippets).
    if re.search(r"\bautograph\.\w+\(", t):
        return True
    return False


def _looks_needs_comfyui_runtime(text: str) -> bool:
    """Return True if snippet requires in-process ComfyUI modules."""
    needles = [
        ".execute(",
        "from_comfyui_modules",
        "import comfy",
        'node_info="modules"',
        "node_info='modules'",
        'NodeInfo("modules")',
        "NodeInfo('modules')",
    ]
    return any(n in text for n in needles)


def _looks_incomplete_snippet(text: str) -> bool:
    """Return True for very short illustrative snippets that reference
    variables not defined in the snippet (e.g. ``res = flow.execute()``
    appearing inside explanatory prose)."""
    lines = [ln for ln in text.strip().splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if len(lines) > 2:
        return False
    # No import statement → variables must come from somewhere else
    if any(ln.strip().startswith(("import ", "from ")) for ln in lines):
        return False
    return True


def _looks_needs_optional_deps(text: str) -> bool:
    """Return True if snippet requires optional dependencies like Pillow."""
    needles = [
        "to_pixels(",
        "to_pil(",
        "from PIL",
        "import PIL",
    ]
    return any(n in text for n in needles)


def _looks_needs_image_file(text: str) -> bool:
    """Return True if snippet reads image files (output.png, ComfyUI_*.png)."""
    needles = [
        'output.png',
        'ComfyUI_00001_.png',
        '.read_bytes()',
    ]
    # Needs both a PNG reference AND a read operation
    has_png = any(n in text for n in needles[:2])
    has_read = '.read_bytes()' in text or 'open(' in text
    return has_png and has_read


def _looks_data_specific_python(text: str) -> bool:
    """
    Return True for snippets that are valid, but assume specific IDs/labels that
    may not exist in the repo sample workflows.
    """
    # Common in docs as an illustrative nested-subgraph path example.
    if "18:17:3" in text:
        return True
    # Illustrative GUI rename in docs; sample workflows may not contain it.
    if "NewSubgraphName" in text:
        return True
    # Dict key access that depends on specific workflow structure.
    if "['meta']" in text or '["meta"]' in text:
        return True
    return False


def _safe_mkdir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _iter_doc_files(docs_dir: Path) -> List[Path]:
    return sorted([p for p in docs_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md"])


def _parse_fenced_blocks(md_text: str) -> List[Tuple[str, str]]:
    """
    Return list of (lang, code) for all fenced blocks in a markdown document.
    Supports indented fences (e.g. "   ```python").
    Dedents the code content.
    """
    out: List[Tuple[str, str]] = []
    in_block = False
    lang = ""
    buf: List[str] = []

    for line in md_text.splitlines(True):
        stripped = line.lstrip()
        if not in_block:
            if stripped.startswith("```"):
                lang = stripped[3:].strip().split()[0] if stripped[3:].strip() else ""
                in_block = True
                buf = []
        else:
            if stripped.startswith("```"):
                code = textwrap.dedent("".join(buf)).rstrip() + "\n"
                out.append((lang, code))
                in_block = False
                lang = ""
                buf = []
            else:
                buf.append(line)
    return out


@contextmanager
def _env_overlay(patch: Dict[str, Optional[str]]):
    """
    Temporarily overlay environment variables for the duration of the context.

    patch:
    - {"NAME": "value"} sets/overrides NAME
    - {"NAME": None} unsets NAME
    Restores the original environment after.
    """
    old: Dict[str, Optional[str]] = {}
    try:
        for k, v in patch.items():
            old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _prompt_env_var(name: str, *, default: Optional[str] = None, prompt: bool = True) -> Optional[str]:
    """
    Interactive prompt for env vars.
    - If env exists, user can press Enter to keep it.
    - If env missing but default provided, Enter uses default.
    - If prompt is False or stdin isn't a TTY, returns env or default without prompting.
    """
    existing = os.environ.get(name)
    if not prompt or not sys.stdin.isatty():
        return existing if existing is not None else default

    shown_default = existing if existing is not None else (default if default is not None else "")
    suffix = f" [{shown_default}]" if shown_default else ""
    try:
        s = input(f"Enter {name}{suffix}: ").strip()
    except EOFError:
        return existing if existing is not None else default

    if s == "":
        return existing if existing is not None else default
    return s


def _prompt_env_many(
    items: Sequence[Tuple[str, Optional[str]]],
    *,
    prompt: bool,
) -> Dict[str, str]:
    """
    Prompt for several env vars. Returns a dict of {name: value} for those with a resolved value.
    (Empty/None values are omitted.)
    """
    out: Dict[str, str] = {}
    for name, default in items:
        v = _prompt_env_var(name, default=default, prompt=prompt)
        if isinstance(v, str) and v != "":
            out[name] = v
    return out


def _make_sandbox(
    *,
    out_dir: Path,
    workflow_src: Path,
    node_info_src: Optional[Path],
    image_src: Optional[Path],
    server_url: Optional[str] = None,
    auto_node_info: bool = False,
    make_api_payload: bool = True,
    verbose: bool = False,
) -> Path:
    """
    Create a sandbox directory with canonical filenames used by docs snippets:
    - workflow.json
    - node_info.json
    - workflow-api.json (optional; generated offline)
    - ComfyUI_00001_.png and output.png (copy of repo sample)
    """
    _safe_mkdir(out_dir)
    sb = out_dir

    wf = sb / "workflow.json"
    oi = sb / "node_info.json"
    wf_api = sb / "workflow-api.json"
    png1 = sb / "ComfyUI_00001_.png"
    png2 = sb / "output.png"

    wf.write_text(_read_text(workflow_src), encoding="utf-8")

    # node_info.json:
    # - If user provided a file, copy it.
    # - Else if auto_node_info and server_url is set, fetch it.
    # - Else fall back to repo default (keeps offline behavior).
    if node_info_src is not None and node_info_src.exists():
        oi.write_text(_read_text(node_info_src), encoding="utf-8")
    elif auto_node_info and server_url:
        data = _fetch_node_info(server_url, timeout_s=10.0)
        oi.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        oi.write_text(_read_text(DEFAULT_NODE_INFO), encoding="utf-8")

    # PNG samples (optional)
    if image_src is not None and image_src.exists():
        png_bytes = image_src.read_bytes()
        png1.write_bytes(png_bytes)
        png2.write_bytes(png_bytes)

    if make_api_payload:
        # Create workflow-api.json using offline conversion.
        # Import locally to keep this script usable even if autograph isn't installed (repo-local usage).
        from autograph import ApiFlow  # type: ignore

        api = ApiFlow(str(wf), node_info=str(oi))
        api.save(str(wf_api))

    # Ensure PNG samples contain ComfyUI-style metadata so docs snippets that use Flow.load(ApiFlow.load)
    # from PNG work in offline mode. We embed both:
    # - tEXt "workflow" -> workspace workflow.json
    # - tEXt "prompt"   -> API payload workflow-api.json
    if png1.exists() and wf.exists() and wf_api.exists():
        try:
            wf_text = wf.read_text(encoding="utf-8")
            prompt_text = wf_api.read_text(encoding="utf-8")
            _write_png_with_text_chunks(png1, {"workflow": wf_text, "prompt": prompt_text})
            _write_png_with_text_chunks(png2, {"workflow": wf_text, "prompt": prompt_text})
        except Exception:
            # Best-effort; if this fails, PNG-based docs examples may be skipped/fail later.
            pass

    if verbose:
        print(f"[sandbox] {sb}")
    return sb


def _run_subprocess(
    argv: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
) -> int:
    # Stream output directly for visibility (user explicitly asked to see it).
    p = subprocess.run(list(argv), cwd=str(cwd) if cwd else None, env=env)
    return int(p.returncode)


def _write_png_with_text_chunks(dst_png: Path, text_map: Dict[str, str]) -> None:
    """
    Write a new PNG file at dst_png that includes tEXt chunks (keyword->text) before IEND.

    This is stdlib-only and designed for the docs-test sandbox.
    """
    src = dst_png.read_bytes()
    if not src.startswith(b"\x89PNG\r\n\x1a\n"):
        return

    sig = src[:8]
    pos = 8
    chunks: List[bytes] = []

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        ln = len(data).to_bytes(4, "big")
        crc = zlib.crc32(chunk_type)
        crc = zlib.crc32(data, crc) & 0xFFFFFFFF
        return ln + chunk_type + data + crc.to_bytes(4, "big")

    # Build new tEXt chunks
    new_chunks: List[bytes] = []
    for k, v in (text_map or {}).items():
        if not isinstance(k, str) or not k:
            continue
        if not isinstance(v, str):
            v = str(v)
        data = k.encode("latin-1", errors="ignore") + b"\x00" + v.encode("utf-8")
        new_chunks.append(_chunk(b"tEXt", data))

    # Copy all chunks, inserting new ones before IEND
    out = bytearray()
    out.extend(sig)
    while pos + 8 <= len(src):
        ln = int.from_bytes(src[pos : pos + 4], "big")
        ctype = src[pos + 4 : pos + 8]
        chunk_end = pos + 8 + ln + 4
        if chunk_end > len(src):
            break
        chunk_bytes = src[pos:chunk_end]
        if ctype == b"IEND":
            for nc in new_chunks:
                out.extend(nc)
            out.extend(chunk_bytes)
            dst_png.write_bytes(bytes(out))
            return
        out.extend(chunk_bytes)
        pos = chunk_end

    # Fallback: if parsing failed, keep original.
    dst_png.write_bytes(src)


def _bash_to_python_argv(line: str) -> Optional[List[str]]:
    """
    Convert a bash-ish command line into argv for subprocess, but only for safe patterns.

    We only accept:
    - `python -m autograph ...`
    - `python <somefile.py> ...`
    And we replace `python` with `sys.executable`.
    """
    s = line.strip()
    if not s or s.startswith("#"):
        return None

    # Drop line continuation backslashes.
    s = s.rstrip("\\").strip()

    # Skip env exports/sets (shell-specific).
    if s.startswith("export ") or s.startswith("set "):
        return None
    if s.startswith("$env:"):
        return None

    try:
        parts = shlex.split(s, posix=True)
    except Exception:
        return None

    if not parts:
        return None

    # Some docs use a standalone "\" token for line continuation.
    # Drop these so subprocess argv is valid.
    parts = [p for p in parts if p != "\\"]

    if parts[0] != "python":
        return None

    # Convert to current interpreter
    parts[0] = sys.executable
    return parts


def _call_with_supported_kwargs(fn: ExampleFn, kwargs: Dict[str, Any]) -> Any:
    """
    Call fn with only the kwargs it accepts.

    We use stdlib inspect so we can safely pass a superset of args to many different
    example functions (doc-derived + e2e).
    """
    try:
        sig = inspect.signature(fn)
    except Exception:
        return fn(**kwargs)

    params = sig.parameters
    # If fn accepts **kwargs, pass through everything.
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(**kwargs)

    filtered: Dict[str, Any] = {}
    for k, v in kwargs.items():
        if k in params:
            filtered[k] = v
    return fn(**filtered)


# -----------------------------------------------------------------------------
# Block runners
# -----------------------------------------------------------------------------

def _run_python_block(
    code: str,
    *,
    label: str,
    exec_python: bool,
    allow_network: bool,
    sandbox_dir: Path,
    env_patch: Optional[Dict[str, Optional[str]]] = None,
    strict_network: bool = False,
    shared_ns: Optional[Dict[str, Any]] = None,
) -> None:
    compile(code, filename=label, mode="exec")

    if not exec_python:
        print("[python] compile: ok (exec disabled)")
        return

    if _looks_networky(code) and not allow_network:
        print("[python] SKIP: looks like it needs network; rerun with --allow-network")
        return

    if _looks_pseudo_or_frameworky_python(code):
        print("[python] compile: ok (exec skipped: looks like pseudo/framework wiring)")
        return

    if _looks_data_specific_python(code):
        print("[python] compile: ok (exec skipped: data-specific example)")
        return

    if _looks_needs_comfyui_runtime(code):
        # Check if ComfyUI modules are actually available
        try:
            import comfy.samplers  # noqa: F401
        except ImportError:
            print("[python] compile: ok (exec skipped: needs ComfyUI environment)")
            return
        # Incomplete snippets (1-2 lines, no imports) are illustrative prose
        if _looks_incomplete_snippet(code):
            print("[python] compile: ok (exec skipped: illustrative snippet)")
            return

    if _looks_needs_optional_deps(code):
        # Check if Pillow is actually available
        try:
            import PIL  # noqa: F401
        except ImportError:
            print("[python] compile: ok (exec skipped: needs Pillow)")
            return

    if _looks_needs_image_file(code):
        # Check if sandbox actually has the image files
        if not (sandbox_dir / "output.png").exists() and not (sandbox_dir / "ComfyUI_00001_.png").exists():
            print("[python] compile: ok (exec skipped: needs image fixture)")
            return

    # Execute in a namespace.  When shared_ns is provided (per-page chaining),
    # reuse that namespace so variables carry over between blocks.
    old_cwd = Path.cwd()
    try:
        os.chdir(str(sandbox_dir))
        if shared_ns is not None:
            ns = shared_ns
        else:
            ns = {"__name__": "__docs_test__", "__file__": str(sandbox_dir / "_snippet_.py")}
        try:
            with _env_overlay(env_patch or {}):
                exec(code, ns, ns)
            print("[python] exec: ok")
        except Exception as e:
            # If this snippet is networky and the server isn't reachable, treat as SKIP unless strict.
            if (not strict_network) and _looks_networky(code) and isinstance(
                e, (ConnectionError, TimeoutError, urllib.error.URLError)
            ):
                print(f"[python] SKIP: network error ({type(e).__name__}: {e})")
                return
            raise
    finally:
        os.chdir(str(old_cwd))


def _run_json_block(code: str) -> None:
    obj = json.loads(code)
    if isinstance(obj, dict):
        print(f"[json] ok: dict with {len(obj)} keys")
    elif isinstance(obj, list):
        print(f"[json] ok: list with {len(obj)} items")
    else:
        print(f"[json] ok: {type(obj).__name__}")


def _run_bash_block(
    code: str,
    *,
    run_cli: bool,
    allow_network: bool,
    sandbox_dir: Path,
    server_url: Optional[str] = None,
    strict_network: bool = False,
) -> None:
    lines = [ln.strip() for ln in code.splitlines()]

    if not run_cli:
        print("[bash] parsed: ok (run disabled)")
        return

    if os.name == "nt":
        print("[bash] SKIP: bash snippets are not executed on Windows (use python examples instead)")
        return

    # Execute only safe python invocations.
    # Also handle simple multi-line continuations by concatenating consecutive lines ending with "\".
    merged: List[str] = []
    buf = ""
    for ln in lines:
        if not ln or ln.startswith("#"):
            continue
        if buf:
            buf = buf + " " + ln
        else:
            buf = ln
        if buf.endswith("\\"):
            continue
        merged.append(buf)
        buf = ""
    if buf:
        merged.append(buf)

    ran_any = False
    for cmd in merged:
        argv = _bash_to_python_argv(cmd)
        if not argv:
            continue
        if _looks_networky(cmd) and not allow_network:
            print("[bash] SKIP cmd (needs network):", cmd)
            continue

        # Choose cwd:
        # - commands that reference workflow.json/node_info.json should run in the sandbox
        # - commands that reference examples/ paths should run at repo root
        cmd_l = cmd.lower()
        if "examples/" in cmd_l or "examples\\" in cmd_l:
            cwd = REPO_ROOT
        elif "workflow.json" in cmd_l or "node_info.json" in cmd_l or "workflow-api.json" in cmd_l:
            cwd = sandbox_dir
        else:
            cwd = REPO_ROOT

        # Ensure `python -m autograph` works even when cwd is sandbox_dir.
        env = os.environ.copy()
        pp = env.get("PYTHONPATH", "")
        root_s = str(REPO_ROOT)
        if pp:
            if root_s not in pp.split(os.pathsep):
                env["PYTHONPATH"] = root_s + os.pathsep + pp
        else:
            env["PYTHONPATH"] = root_s

        # Optionally pass server URL via env (so docs snippets can omit --server-url).
        if server_url:
            env["AUTOGRAPH_COMFYUI_SERVER_URL"] = server_url
        else:
            # If we're not explicitly setting a server URL for this run, do not leak one into subprocesses.
            env.pop("AUTOGRAPH_COMFYUI_SERVER_URL", None)

        print("[bash] run:", cmd)
        rc = _run_subprocess(argv, cwd=cwd, env=env)
        ran_any = True
        if rc != 0:
            if (not strict_network) and _looks_networky(cmd):
                raise RuntimeError(f"bash cmd failed (network?) rc={rc}: {cmd}")
            raise RuntimeError(f"bash cmd failed rc={rc}: {cmd}")

    if not ran_any:
        print("[bash] no runnable python commands found in this block")


# -----------------------------------------------------------------------------
# E2E: docs correctness (server roundtrip)
# -----------------------------------------------------------------------------

def e2e_server_roundtrip(
    *,
    server_url: Optional[str] = None,
    prompt_env: bool = False,
    workflow: Path = DEFAULT_WORKFLOW,
    workflow_url: str = "",
    out: Optional[Path] = None,
    prompt_text: str = "A beautiful cinematic scene about autograph, high quality, detailed",
    fixed_seed: int = 123,
    client_id: str = "autograph-docs-test",
    timeout_s: int = 30,
    wait_timeout_s: int = 180,
    poll_interval_s: float = 0.5,
    strict: bool = True,
    verbose: bool = False,
) -> None:
    """
    End-to-end docs correctness check against a running ComfyUI server.

    Steps:
    1) Fetch /object_info from server (sandboxed node_info.json).
    2) Load a template workflow.json (repo default by default, or workflow_url if provided).
    3) Convert -> ApiFlow, set prompt to a autograph-themed scene, set fixed seed.
    4) Submit, wait, fetch output images, save first image.
    5) Extract Flow from the saved PNG, convert again, submit again.
    6) Save second image and print SHA256 hashes so a user can verify they match.

    Notes:
    - Exact byte-for-byte matches depend on server determinism. This prints both hashes and paths for manual inspection.
    """
    # This repo is often tested without a ComfyUI server; keep network/e2e explicitly opt-in.
    if not os.environ.get("AUTOGRAPH_DOCS_E2E"):
        if strict:
            raise RuntimeError("E2E docs tests are disabled by default (set AUTOGRAPH_DOCS_E2E=1 to enable).")
        print("[net] SKIP: e2e/server-roundtrip disabled (set AUTOGRAPH_DOCS_E2E=1 to enable).")
        return
    server_url2 = (server_url or os.environ.get("AUTOGRAPH_COMFYUI_SERVER_URL", "")).strip()
    if not server_url2:
        server_url2 = _prompt_env_var("AUTOGRAPH_COMFYUI_SERVER_URL", default="http://localhost:8188", prompt=prompt_env) or ""
    if not server_url2:
        raise ValueError("Missing ComfyUI server URL (set AUTOGRAPH_COMFYUI_SERVER_URL or pass --server-url)")

    if not _server_reachable(server_url2, timeout_s=1.0):
        msg = f"ComfyUI server not reachable: {server_url2}"
        if strict:
            raise RuntimeError(msg)
        print("[net] SKIP:", msg)
        return

    out_dir = out if out is not None else (HERE.parent / "_docs_test_out")
    _safe_mkdir(out_dir)
    run_dir = out_dir / ("e2e-" + time.strftime("%Y%m%d-%H%M%S"))
    _safe_mkdir(run_dir)

    if workflow_url.strip():
        wf_data = _download_json(workflow_url.strip(), timeout_s=20.0)
        wf_path = run_dir / "workflow.json"
        wf_path.write_text(json.dumps(wf_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        wf_path = Path(workflow).expanduser().resolve()

    # Prepare sandbox (we keep it, so user can inspect files).
    sb = run_dir / "sandbox"
    _safe_mkdir(sb)

    # Fetch node_info from server into sandbox.
    oi_data = _fetch_node_info(server_url2, timeout_s=10.0)
    oi_path = sb / "node_info.json"
    oi_path.write_text(json.dumps(oi_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if verbose:
        print(f"[e2e] saved node_info.json: {oi_path}")

    # Copy workflow into sandbox as workflow.json (to match docs snippets).
    sb_wf = sb / "workflow.json"
    sb_wf.write_text(_read_text(wf_path), encoding="utf-8")

    # Import locally (repo-local usage).
    from autograph import ApiFlow, Flow  # type: ignore

    def _apply_prompt_and_seed(api: Dict[str, Any]) -> Dict[str, Any]:
        a = api if isinstance(api, ApiFlow) else ApiFlow(api, node_info=str(oi_path))
        # Set prompt text on CLIPTextEncode nodes (heuristic: keep "watermark" style negatives intact).
        try:
            for node in a.cliptextencode:
                try:
                    txt = getattr(node, "text", None)
                except Exception:
                    txt = None
                if isinstance(txt, str) and ("watermark" in txt.lower() or txt.lower().strip().startswith("text,")):
                    continue
                try:
                    node.text = prompt_text
                except Exception:
                    pass
        except Exception:
            pass

        # Fixed seed for determinism (if any sampler exists).
        try:
            for node in a.ksampler:
                try:
                    node.seed = int(fixed_seed)
                except Exception:
                    pass
        except Exception:
            pass
        return a

    def _submit_and_first_image_bytes(api: ApiFlow) -> bytes:
        res = api.submit(
            server_url=server_url2,
            client_id=client_id,
            timeout=int(timeout_s),
            wait=True,
            wait_timeout=int(wait_timeout_s),
            poll_interval=float(poll_interval_s),
            fetch_outputs=True,
            include_bytes=True,
        )
        imgs = res.get("images") if isinstance(res, dict) else None
        if imgs is None:
            # Fallback: explicit fetch
            imgs = res.fetch_images(include_bytes=True)  # type: ignore[attr-defined]
        if not imgs:
            raise RuntimeError("No images returned from server (nothing to compare).")
        first = imgs[0]
        b = first.get("bytes") if isinstance(first, dict) else None
        if not isinstance(b, (bytes, bytearray)):
            raise RuntimeError("First image has no bytes (include_bytes=True required).")
        return bytes(b)

    # Run 1: template workflow -> api -> submit -> image
    api1 = ApiFlow(str(sb_wf), node_info=str(oi_path))
    api1 = _apply_prompt_and_seed(api1)  # type: ignore[assignment]
    b1 = _submit_and_first_image_bytes(api1)  # type: ignore[arg-type]
    img1_path = run_dir / "render-1.png"
    img1_path.write_bytes(b1)

    # Extract Flow from rendered image (if ComfyUI embedded workflow metadata)
    flow_from_png: Optional[Flow] = None
    try:
        flow_from_png = Flow.load(str(img1_path))
    except Exception as e:
        if strict:
            raise RuntimeError(f"Could not extract Flow from PNG (missing embedded workflow?): {e}")
        print(f"[e2e] SKIP: could not extract Flow from PNG: {e}")
        return

    # Run 2: extracted flow -> api -> submit -> image
    api2 = flow_from_png.convert(node_info=str(oi_path))  # type: ignore[union-attr]
    api2 = _apply_prompt_and_seed(api2)  # type: ignore[assignment]
    b2 = _submit_and_first_image_bytes(api2)  # type: ignore[arg-type]
    img2_path = run_dir / "render-2.png"
    img2_path.write_bytes(b2)

    h1 = _sha256_bytes(b1)
    h2 = _sha256_bytes(b2)

    print("[e2e] image1:", str(img1_path))
    print("[e2e] image2:", str(img2_path))
    print("[e2e] sha256-1:", h1)
    print("[e2e] sha256-2:", h2)
    print("[e2e] match:", "YES" if h1 == h2 else "NO (check images visually)")


# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------

def _register_doc_blocks(
    *,
    docs_dir: Path,
    include_langs: Optional[Sequence[str]] = None,
) -> None:
    include = set([s.lower() for s in include_langs]) if include_langs else None

    for doc_path in _iter_doc_files(docs_dir):
        blocks = _parse_fenced_blocks(_read_text(doc_path))
        per_doc_counter: Dict[str, int] = {}

        for i, (lang, code) in enumerate(blocks, start=1):
            lang2 = (lang or "").strip().lower()
            if include is not None and lang2 not in include:
                continue

            # Skip mermaid blocks by default (not code we can sensibly test).
            if lang2 == "mermaid":
                continue

            per_doc_counter[lang2] = per_doc_counter.get(lang2, 0) + 1
            n = per_doc_counter[lang2]

            doc_name = doc_path.name
            base = doc_path.stem.replace("-", "_")
            fn_name = f"doc_{base}__{lang2 or 'text'}__{n}"
            label = f"docs/{doc_name}#{n}:{lang2 or 'text'}"

            needs_network = _looks_networky(code)
            can_exec_python = (lang2 == "python")
            can_run_cli = (lang2 == "bash")
            continued = code.lstrip().startswith("# continued")

            def _make_fn(_code: str, _label: str, _lang: str) -> ExampleFn:
                def _fn(
                    *,
                    exec_python: bool = False,
                    run_cli: bool = False,
                    allow_network: bool = False,
                    server_url: Optional[str] = None,
                    prompt_env: bool = False,
                    strict_network: bool = False,
                    timeout_s: Optional[str] = None,
                    wait_timeout_s: Optional[str] = None,
                    poll_interval_s: Optional[str] = None,
                    submit_client_id: Optional[str] = None,
                    workflow: Path = DEFAULT_WORKFLOW,
                    node_info: Optional[Path] = DEFAULT_NODE_INFO,
                    image: Optional[Path] = DEFAULT_IMAGE,
                    out: Optional[Path] = None,
                    verbose: bool = False,
                    shared_ns: Optional[Dict[str, Any]] = None,
                ) -> None:
                    # Prompt for server URL if we're about to run network-y blocks.
                    server_url2 = server_url
                    if allow_network and needs_network and not _snippet_has_explicit_server_url(_code, lang=_lang):
                        if not server_url2 and not prompt_env:
                            print(
                                "[net] SKIP: needs server URL. Set AUTOGRAPH_COMFYUI_SERVER_URL, pass --server-url, "
                                "or rerun with --prompt-env."
                            )
                            return
                        if not server_url2:
                            server_url2 = _prompt_env_var(
                                "AUTOGRAPH_COMFYUI_SERVER_URL",
                                default="http://localhost:8188",
                                prompt=prompt_env,
                            )
                    # If we're about to run a network example, optionally preflight reachability.
                    if allow_network and needs_network and not strict_network:
                        url = server_url2 or _extract_server_url_from_snippet(_code, lang=_lang)
                        if url and not _server_reachable(url, timeout_s=1.0):
                            print(f"[net] SKIP: server not reachable: {url}")
                            return

                    out_dir = out if out is not None else (HERE.parent / "_docs_test_out")
                    # A per-example temp sandbox to avoid cross-test coupling.
                    with tempfile.TemporaryDirectory(dir=str(_safe_mkdir(out_dir))) as td:
                        sb = Path(td)
                        sandbox = _make_sandbox(
                            out_dir=sb,
                            workflow_src=workflow,
                            node_info_src=node_info,
                            image_src=image,
                            server_url=server_url2,
                            auto_node_info=bool(allow_network),
                            make_api_payload=True,
                            verbose=verbose,
                        )

                        env_patch: Dict[str, Optional[str]] = {}
                        if server_url2:
                            env_patch["AUTOGRAPH_COMFYUI_SERVER_URL"] = server_url2
                        if timeout_s:
                            env_patch["AUTOGRAPH_TIMEOUT_S"] = str(timeout_s)
                        if wait_timeout_s:
                            env_patch["AUTOGRAPH_WAIT_TIMEOUT_S"] = str(wait_timeout_s)
                        if poll_interval_s:
                            env_patch["AUTOGRAPH_POLL_INTERVAL_S"] = str(poll_interval_s)
                        if submit_client_id:
                            env_patch["AUTOGRAPH_SUBMIT_CLIENT_ID"] = str(submit_client_id)

                        if _lang == "python":
                            _run_python_block(
                                _code,
                                label=_label,
                                exec_python=exec_python,
                                allow_network=allow_network,
                                sandbox_dir=sandbox,
                                env_patch=env_patch,
                                strict_network=strict_network,
                                shared_ns=shared_ns,
                            )
                        elif _lang == "json":
                            _run_json_block(_code)
                        elif _lang == "bash":
                            _run_bash_block(
                                _code,
                                run_cli=run_cli,
                                allow_network=allow_network,
                                sandbox_dir=sandbox,
                                server_url=server_url2,
                                strict_network=strict_network,
                            )
                        else:
                            # Unknown fence lang: treat as plain text.
                            print(f"[{_lang or 'text'}] captured {len(_code)} bytes")
                return _fn

            fn = _make_fn(code, label, lang2)
            fn.__name__ = fn_name
            fn.__doc__ = f"Auto-generated from `{doc_name}` fenced block #{n} ({lang2 or 'text'})."

            # Expose as a module-global for interactive use:
            #   python -i examples/code/docs-test.py
            #   >>> doc_convert__python__1(exec_python=True)
            globals()[fn_name] = fn

            EXAMPLES[label] = Example(
                label=label,
                fn_name=fn_name,
                fn=fn,
                doc_file=doc_name,
                block_index=n,
                lang=lang2 or "text",
                needs_network=needs_network,
                needs_comfyui_runtime=_looks_needs_comfyui_runtime(code),
                can_exec_python=can_exec_python,
                can_run_cli=can_run_cli,
                continued=continued,
            )

    # Add a hand-written E2E test (not derived from docs fences).
    # Keep it explicitly opt-in so docs/tests never hit a live ComfyUI server by default.
    if os.environ.get("AUTOGRAPH_DOCS_E2E"):
        EXAMPLES["e2e/server-roundtrip"] = Example(
            label="e2e/server-roundtrip",
            fn_name="e2e_server_roundtrip",
            fn=e2e_server_roundtrip,
            doc_file="(e2e)",
            block_index=0,
            lang="python",
            needs_network=True,
            needs_comfyui_runtime=False,
            can_exec_python=True,
            can_run_cli=False,
            continued=False,
        )


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def _split_csv(s: Optional[str]) -> List[str]:
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def main(argv: Optional[Sequence[str]] = None) -> int:
    # Check for missing default files and offer to set them up
    _ensure_repo_defaults(interactive=(sys.stdin.isatty() and "--non-interactive" not in (argv or sys.argv)))

    _register_doc_blocks(docs_dir=DOCS_DIR, include_langs=["python", "bash", "json", "text", ""])

    p = argparse.ArgumentParser(
        prog="docs-test.py",
        description="Run fenced code examples from docs/*.md as labeled Python functions.",
    )
    p.add_argument("--list", action="store_true", help="List available example labels and exit")
    p.add_argument("--only", default="", help="Comma-separated list of labels to run")
    p.add_argument("--skip", default="", help="Comma-separated list of labels to skip")
    p.add_argument("--exec-python", action="store_true", help="Attempt to exec python blocks (compile always runs)")
    p.add_argument("--run-cli", action="store_true", help="Attempt to run safe bash blocks (python invocations only)")
    p.add_argument(
        "--mode",
        choices=["auto", "offline", "online"],
        default="auto",
        help="auto: online only if server URL is available; offline: never run network blocks; online: allow network blocks",
    )
    p.add_argument("--server-url", default="", help="ComfyUI server URL (sets AUTOGRAPH_COMFYUI_SERVER_URL for examples)")
    p.add_argument("--timeout", default="", help="Default HTTP timeout seconds (AUTOGRAPH_TIMEOUT_S)")
    p.add_argument("--wait-timeout", default="", help="Default wait timeout seconds (AUTOGRAPH_WAIT_TIMEOUT_S)")
    p.add_argument("--poll-interval", default="", help="Default poll interval seconds (AUTOGRAPH_POLL_INTERVAL_S)")
    p.add_argument("--client-id", default="", help="Default submit client_id (AUTOGRAPH_SUBMIT_CLIENT_ID)")
    p.add_argument("--workflow-url", default="", help="Optional URL to a workflow.json template (downloaded via stdlib)")
    p.add_argument("--prompt-text", default="", help="Prompt override for E2E test (autograph-themed)")
    p.add_argument("--seed", default="", help="Fixed seed for E2E test")
    p.add_argument(
        "--prompt-env",
        action="store_true",
        help="Interactively prompt for missing AUTOGRAPH_* env vars (TTY-only).",
    )
    p.add_argument(
        "--strict-network",
        action="store_true",
        help="Fail (do not skip) if network examples can't reach the server.",
    )
    p.add_argument("--workflow", default=str(DEFAULT_WORKFLOW), help="Path to workspace workflow.json sample")
    p.add_argument(
        "--node-info",
        default=str(DEFAULT_NODE_INFO),
        help="Path to node_info.json sample, or 'auto' to fetch from server in online mode",
    )
    p.add_argument(
        "--image",
        default=str(DEFAULT_IMAGE),
        help="Path to a ComfyUI PNG sample (optional). Pass empty string to disable.",
    )
    p.add_argument("--out", default=str(HERE.parent / "_docs_test_out"), help="Directory for temporary outputs")
    p.add_argument("--verbose", action="store_true", help="Verbose output")
    p.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop immediately on first failure (overrides --keep-going)",
    )

    ns = p.parse_args(list(argv) if argv is not None else None)

    if ns.list:
        for label in sorted(EXAMPLES.keys()):
            ex = EXAMPLES[label]
            flags = []
            if ex.needs_network:
                flags.append("needs_network")
            if ex.lang == "python":
                flags.append("python")
            elif ex.lang == "bash":
                flags.append("bash")
            elif ex.lang == "json":
                flags.append("json")
            else:
                flags.append(ex.lang)
            print(f"{label}  ({', '.join(flags)})")
        return 0

    only = set(_split_csv(ns.only)) if ns.only else None
    skip = set(_split_csv(ns.skip))

    workflow = Path(ns.workflow).expanduser().resolve()
    node_info: Optional[Path]
    if (ns.node_info or "").strip().lower() == "auto":
        node_info = None
    else:
        node_info = Path(ns.node_info).expanduser().resolve()

    image: Optional[Path]
    if (ns.image or "").strip() == "":
        image = None
    else:
        image = Path(ns.image).expanduser().resolve()
    out = Path(ns.out).expanduser().resolve()

    # Resolve defaults (args -> env -> hard default).
    # Note: We keep values as strings because autograph parses env vars itself.
    server_url_default = (ns.server_url or "").strip() or os.environ.get("AUTOGRAPH_COMFYUI_SERVER_URL", "").strip() or ""
    timeout_default = (ns.timeout or "").strip() or os.environ.get("AUTOGRAPH_TIMEOUT_S", "").strip() or "30"
    wait_timeout_default = (ns.wait_timeout or "").strip() or os.environ.get("AUTOGRAPH_WAIT_TIMEOUT_S", "").strip() or "60"
    poll_interval_default = (ns.poll_interval or "").strip() or os.environ.get("AUTOGRAPH_POLL_INTERVAL_S", "").strip() or "0.5"
    client_id_default = (ns.client_id or "").strip() or os.environ.get("AUTOGRAPH_SUBMIT_CLIENT_ID", "").strip() or "autograph"

    # Optional interactive prompting (TTY-only). If stdin isn't a TTY, this is a no-op fallback.
    prompted = _prompt_env_many(
        [
            ("AUTOGRAPH_COMFYUI_SERVER_URL", server_url_default or "http://localhost:8188"),
            ("AUTOGRAPH_TIMEOUT_S", timeout_default),
            ("AUTOGRAPH_WAIT_TIMEOUT_S", wait_timeout_default),
            ("AUTOGRAPH_POLL_INTERVAL_S", poll_interval_default),
            ("AUTOGRAPH_SUBMIT_CLIENT_ID", client_id_default),
        ],
        prompt=bool(ns.prompt_env),
    )

    server_url = (prompted.get("AUTOGRAPH_COMFYUI_SERVER_URL") or server_url_default).strip()
    timeout_s = prompted.get("AUTOGRAPH_TIMEOUT_S") or timeout_default
    wait_timeout_s = prompted.get("AUTOGRAPH_WAIT_TIMEOUT_S") or wait_timeout_default
    poll_interval_s = prompted.get("AUTOGRAPH_POLL_INTERVAL_S") or poll_interval_default
    submit_client_id = prompted.get("AUTOGRAPH_SUBMIT_CLIENT_ID") or client_id_default

    if ns.mode == "offline":
        allow_network = False
    elif ns.mode == "online":
        allow_network = True
    else:
        # auto
        allow_network = bool(server_url)

    labels = sorted(EXAMPLES.keys())
    ran = 0
    failures: List[Tuple[str, str]] = []
    # Keep-going is the default behavior for a docs harness.
    keep_going = not bool(ns.stop_on_error)

    for label in labels:
        if only is not None and label not in only:
            continue
        if label in skip:
            continue

        ex = EXAMPLES[label]
        _print_banner(f"START {label}  -> {ex.fn_name}")
        try:
            call_kwargs: Dict[str, Any] = dict(
                exec_python=bool(ns.exec_python),
                run_cli=bool(ns.run_cli),
                allow_network=bool(allow_network),
                # Avoid leaking server URL into offline runs.
                server_url=(server_url or None) if allow_network else None,
                prompt_env=bool(ns.prompt_env),
                strict_network=bool(ns.strict_network),
                timeout_s=timeout_s,
                wait_timeout_s=wait_timeout_s,
                poll_interval_s=poll_interval_s,
                submit_client_id=submit_client_id,
                workflow=workflow,
                node_info=node_info,
                image=image,
                out=out,
                verbose=bool(ns.verbose),
            )
            if (ns.workflow_url or "").strip():
                call_kwargs["workflow_url"] = (ns.workflow_url or "").strip()
            if (ns.prompt_text or "").strip():
                call_kwargs["prompt_text"] = (ns.prompt_text or "").strip()
            if (ns.seed or "").strip():
                call_kwargs["fixed_seed"] = int((ns.seed or "").strip())

            _call_with_supported_kwargs(ex.fn, call_kwargs)
            print(f"END   {label}  (ok)")
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            failures.append((label, msg))
            print(f"END   {label}  (error: {msg})")
            if not keep_going:
                return 1
        ran += 1

    if ran == 0:
        print("No examples selected. Use --list to see labels, or omit --only.")
        return 0

    print(f"\nRan {ran} examples.")
    if failures:
        _print_banner(f"FAILURES ({len(failures)})")
        for label, msg in failures:
            print(f"- {label}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


