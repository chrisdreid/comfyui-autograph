"""Scan a node_info source for unusual patterns and edge cases.

Usage:
    python audit_node_info.py                        # uses BUILTIN_NODE_INFO
    python audit_node_info.py path/to/node_info.json # uses a file
    python audit_node_info.py fetch                  # fetches from server

Outputs a summary of patterns that may need special handling in the builder API.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def load_node_info(source=None):
    """Load node_info from a file, server, or built-in fallback."""
    if source and source != "builtin":
        if source == "fetch":
            from autograph import NodeInfo
            ni = NodeInfo("fetch")
            return dict(ni)
        p = Path(source)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"Not found: {source}")

    # Fallback to BUILTIN_NODE_INFO
    from harness import BUILTIN_NODE_INFO
    return BUILTIN_NODE_INFO


def audit(ni: dict, verbose: bool = True) -> dict:
    """Scan node_info and return a summary of edge case patterns.

    Returns a dict with pattern names as keys and lists of (node, input, detail)
    tuples as values.
    """
    results = {
        "control_after_generate": [],
        "combo_type_inputs": [],
        "force_input": [],
        "file_upload": [],
        "display_flag": [],
        "hidden_inputs": [],
        "many_outputs": [],
        "output_is_list": [],
        "no_inputs": [],
        "no_outputs": [],
        "unusual_spec": [],
        "optional_inputs": [],
        "unusual_flags": Counter(),
    }

    known_flags = {
        "default", "min", "max", "step", "round", "tooltip",
        "multiline", "dynamicPrompts", "placeholder",
        "forceInput", "lazy", "rawLink",
        "control_after_generate", "display",
        "image_upload", "video_upload", "audio_upload", "file_upload",
        "multiselect", "options",
    }

    for ct, info in ni.items():
        inputs = info.get("input", {})

        # Hidden inputs
        hidden = inputs.get("hidden", {})
        if isinstance(hidden, dict) and hidden:
            results["hidden_inputs"].append((ct, list(hidden.keys())))

        # No inputs
        if not inputs.get("required") and not inputs.get("optional"):
            results["no_inputs"].append(ct)

        # Has optional
        if inputs.get("optional"):
            results["optional_inputs"].append(ct)

        # No outputs
        if not info.get("output"):
            results["no_outputs"].append(ct)

        # Many outputs
        outputs = info.get("output", [])
        if len(outputs) > 5:
            results["many_outputs"].append(
                (ct, len(outputs), info.get("output_name", [])[:6])
            )

        # output_is_list
        oil = info.get("output_is_list", [])
        if any(oil):
            results["output_is_list"].append((ct, oil))

        # Scan inputs
        for sec in ["required", "optional"]:
            for name, spec in inputs.get(sec, {}).items():
                if not isinstance(spec, list) or len(spec) == 0:
                    results["unusual_spec"].append((ct, name, sec, f"not a list or empty"))
                    continue

                # COMBO type string
                if spec[0] == "COMBO":
                    opts = spec[1] if len(spec) >= 2 and isinstance(spec[1], dict) else {}
                    results["combo_type_inputs"].append((ct, name, opts.get("options", [])[:5]))

                # forceInput
                if len(spec) >= 2 and isinstance(spec[1], dict):
                    if spec[1].get("forceInput"):
                        results["force_input"].append((ct, name, sec, spec[0]))

                    # control_after_generate
                    if spec[1].get("control_after_generate"):
                        results["control_after_generate"].append((ct, name, spec[0]))

                    # file upload
                    for flag in ("image_upload", "video_upload", "audio_upload", "file_upload"):
                        if spec[1].get(flag):
                            results["file_upload"].append((ct, name, flag))

                    # display
                    if spec[1].get("display"):
                        results["display_flag"].append((ct, name, spec[1]["display"]))

                    # unusual flags
                    for key in spec[1]:
                        if key not in known_flags:
                            results["unusual_flags"][key] += 1

    return results


def print_report(results: dict, total_nodes: int):
    """Print a human-readable report."""

    def _header(title):
        print(f"\n{'=' * 60}")
        print(f"  {title}")
        print(f"{'=' * 60}")

    print(f"\nTotal node types scanned: {total_nodes}\n")

    _header(f"control_after_generate ({len(results['control_after_generate'])} nodes)")
    for ct, name, ptype in results["control_after_generate"][:10]:
        print(f"  {ct}.{name} (parent type: {ptype})")
    if len(results["control_after_generate"]) > 10:
        print(f"  ...and {len(results['control_after_generate']) - 10} more")

    _header(f"COMBO type inputs ({len(results['combo_type_inputs'])} nodes)")
    for ct, name, opts in results["combo_type_inputs"][:10]:
        print(f"  {ct}.{name}: options={opts}")
    if len(results["combo_type_inputs"]) > 10:
        print(f"  ...and {len(results['combo_type_inputs']) - 10} more")

    _header(f"forceInput ({len(results['force_input'])} nodes)")
    for ct, name, sec, stype in results["force_input"][:10]:
        print(f"  {ct}.{name} ({sec}): original type={stype}")
    if len(results["force_input"]) > 10:
        print(f"  ...and {len(results['force_input']) - 10} more")

    _header(f"File upload widgets ({len(results['file_upload'])} nodes)")
    for ct, name, flag in results["file_upload"]:
        print(f"  {ct}.{name}: {flag}")

    _header(f"display flag ({len(results['display_flag'])} nodes)")
    display_types = Counter(d for _, _, d in results["display_flag"])
    for dtype, count in display_types.most_common():
        print(f"  display={dtype!r}: {count} nodes")

    _header(f"Many outputs >5 ({len(results['many_outputs'])} nodes)")
    for ct, count, names in sorted(results["many_outputs"], key=lambda x: -x[1]):
        print(f"  {ct}: {count} outputs — {names}")

    _header(f"output_is_list ({len(results['output_is_list'])} nodes)")
    for ct, flags in results["output_is_list"][:10]:
        print(f"  {ct}: {flags}")

    _header(f"Unusual option flags")
    for flag, count in results["unusual_flags"].most_common(20):
        print(f"  {flag:35s} {count:4d} nodes")

    _header("Summary")
    print(f"  Nodes with optional inputs:    {len(results['optional_inputs']):4d}")
    print(f"  Nodes with no inputs at all:   {len(results['no_inputs']):4d}")
    print(f"  Nodes with no outputs:         {len(results['no_outputs']):4d}")
    print(f"  Nodes with hidden inputs:      {len(results['hidden_inputs']):4d}")
    print(f"  Unusual input specs:           {len(results['unusual_spec']):4d}")


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else None
    ni = load_node_info(source)
    results = audit(ni)
    print_report(results, len(ni))


if __name__ == "__main__":
    main()
