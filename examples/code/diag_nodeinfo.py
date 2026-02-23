#!/usr/bin/env python3
"""
Diagnostic: compare NodeInfo from modules vs from server fetch.

Usage (from ComfyUI root, with autoflow on PYTHONPATH):
    PYTHONPATH=. python <autoflow>/examples/code/diag_nodeinfo.py [--server-url http://localhost:8188]

Also accepts AUTOFLOW_COMFYUI_SERVER_URL env var.
"""
import json
import os
import sys
from pathlib import Path

# Make sure autoflow is importable
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from autoflow.convert import node_info_from_comfyui_modules, fetch_node_info


def _deep_type_summary(v, depth=0, max_depth=3):
    """Produce a short type/shape summary of a value."""
    if depth > max_depth:
        return f"<{type(v).__name__}>"
    if isinstance(v, dict):
        keys = sorted(v.keys())
        inner = {k: _deep_type_summary(v[k], depth + 1, max_depth) for k in keys[:8]}
        if len(keys) > 8:
            inner["..."] = f"+{len(keys) - 8} more"
        return inner
    if isinstance(v, (list, tuple)):
        if len(v) == 0:
            return "[]"
        return [_deep_type_summary(v[0], depth + 1, max_depth), f"... ({len(v)} items)"]
    return f"{type(v).__name__}={v!r:.60s}" if isinstance(v, str) else f"{type(v).__name__}"


def compare_node_infos(modules_ni, fetch_ni):
    """Compare two node_info dicts and print differences."""
    mod_keys = set(modules_ni.keys())
    fetch_keys = set(fetch_ni.keys())

    print(f"\n{'='*70}")
    print(f"MODULES: {len(mod_keys)} node types")
    print(f"FETCH:   {len(fetch_keys)} node types")
    print(f"{'='*70}")

    only_modules = mod_keys - fetch_keys
    only_fetch = fetch_keys - mod_keys
    common = mod_keys & fetch_keys

    if only_modules:
        print(f"\n  Only in MODULES ({len(only_modules)}): {sorted(only_modules)[:20]}")
    if only_fetch:
        print(f"\n  Only in FETCH ({len(only_fetch)}):   {sorted(only_fetch)[:20]}")

    print(f"\n  Common node types: {len(common)}")

    # Compare structure of a well-known node (KSampler is always present)
    probe_types = ["KSampler", "CheckpointLoaderSimple", "SaveImage", "CLIPTextEncode"]
    for ct in probe_types:
        if ct not in common:
            print(f"\n  [{ct}] NOT in both — skipping")
            continue

        m = modules_ni[ct]
        f = fetch_ni[ct]

        m_keys = sorted(m.keys()) if isinstance(m, dict) else "NOT-DICT"
        f_keys = sorted(f.keys()) if isinstance(f, dict) else "NOT-DICT"

        print(f"\n  [{ct}] top-level keys:")
        print(f"    MODULES: {m_keys}")
        print(f"    FETCH:   {f_keys}")

        if isinstance(m, dict) and isinstance(f, dict):
            all_k = sorted(set(list(m.keys()) + list(f.keys())))
            for k in all_k:
                mv = m.get(k, "<MISSING>")
                fv = f.get(k, "<MISSING>")
                if k == "input":
                    # Deep dive into input structure
                    print(f"\n    [{ct}].input:")
                    if isinstance(mv, dict) and isinstance(fv, dict):
                        for sec in ("required", "optional", "hidden"):
                            ms = mv.get(sec, "<MISSING>")
                            fs = fv.get(sec, "<MISSING>")
                            if ms == "<MISSING>" and fs == "<MISSING>":
                                continue
                            ms_keys = sorted(ms.keys()) if isinstance(ms, dict) else repr(ms)[:100]
                            fs_keys = sorted(fs.keys()) if isinstance(fs, dict) else repr(fs)[:100]
                            print(f"      .{sec}:")
                            print(f"        MODULES keys: {ms_keys}")
                            print(f"        FETCH   keys: {fs_keys}")

                            # Compare a sample widget spec
                            if isinstance(ms, dict) and isinstance(fs, dict):
                                common_widgets = set(ms.keys()) & set(fs.keys())
                                for wname in sorted(common_widgets)[:3]:
                                    mw = ms[wname]
                                    fw = fs[wname]
                                    if mw != fw:
                                        print(f"        [{wname}] DIFFERS:")
                                        print(f"          MOD: {json.dumps(mw, default=str)[:200]}")
                                        print(f"          FET: {json.dumps(fw, default=str)[:200]}")
                                    else:
                                        print(f"        [{wname}] matches ✓")
                    else:
                        print(f"      MODULES: {_deep_type_summary(mv)}")
                        print(f"      FETCH:   {_deep_type_summary(fv)}")
                else:
                    eq = (mv == fv)
                    if not eq:
                        print(f"    .{k}: DIFFERS")
                        print(f"      MOD: {json.dumps(mv, default=str)[:200]}")
                        print(f"      FET: {json.dumps(fv, default=str)[:200]}")

    # Also test conversion with a sample workflow
    print(f"\n{'='*70}")
    print("CONVERSION TEST")
    print(f"{'='*70}")

    wf_candidates = [
        REPO / "examples" / "workflows" / "workflow.json",
        REPO / "examples" / "fixtures" / "workflow.json",
    ]
    # Also check parent test suite
    parent_suite = REPO.parent / "comfyui-autoflow-test-suite" / "fixtures" / "logo-basic" / "workflow.json"
    wf_candidates.append(parent_suite)

    wf_path = None
    for p in wf_candidates:
        if p.exists():
            wf_path = p
            break

    if wf_path is None:
        print("  No sample workflow found — skipping conversion test")
        return

    print(f"  Using workflow: {wf_path}")

    from autoflow.convert import workflow_to_api_format

    for label, ni in [("MODULES", modules_ni), ("FETCH", fetch_ni)]:
        wf_data = json.loads(wf_path.read_text())
        try:
            api = workflow_to_api_format(wf_data, node_info=ni, use_api=True)
            node_count = len(api)
            print(f"  {label}: converted OK -> {node_count} nodes")
            if node_count == 0:
                print(f"  {label}: ⚠️  EMPTY ApiFlow!")
            else:
                # Show first node's inputs
                first_id = next(iter(api))
                first = api[first_id]
                ct = first.get("class_type", "?")
                inputs = first.get("inputs", {})
                print(f"    first node: [{first_id}] {ct}, inputs keys: {sorted(inputs.keys())}")
        except Exception as e:
            print(f"  {label}: FAILED -> {type(e).__name__}: {e}")

    # Also dump raw structures for one node to a temp file for detailed comparison
    dump_type = "KSampler"
    if dump_type in modules_ni and dump_type in fetch_ni:
        dump_dir = Path("/tmp/diag_nodeinfo")
        dump_dir.mkdir(exist_ok=True)
        (dump_dir / f"{dump_type}_modules.json").write_text(
            json.dumps(modules_ni[dump_type], indent=2, default=str)
        )
        (dump_dir / f"{dump_type}_fetch.json").write_text(
            json.dumps(fetch_ni[dump_type], indent=2, default=str)
        )
        print(f"\n  Dumped {dump_type} details to {dump_dir}/")
        print(f"    diff {dump_dir}/{dump_type}_modules.json {dump_dir}/{dump_type}_fetch.json")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Compare NodeInfo from modules vs fetch")
    parser.add_argument("--server-url", default=None, help="ComfyUI server URL (default: AUTOFLOW_COMFYUI_SERVER_URL)")
    args = parser.parse_args()

    server_url = args.server_url or os.environ.get("AUTOFLOW_COMFYUI_SERVER_URL")
    if not server_url:
        print("ERROR: Need --server-url or AUTOFLOW_COMFYUI_SERVER_URL to fetch node_info from server")
        sys.exit(1)

    print(f"Fetching node_info from server: {server_url}")
    fetch_ni = fetch_node_info(server_url)
    print(f"  -> {len(fetch_ni)} node types")

    print(f"\nLoading node_info from ComfyUI modules...")
    modules_ni = node_info_from_comfyui_modules()
    print(f"  -> {len(modules_ni)} node types")

    compare_node_infos(modules_ni, fetch_ni)


if __name__ == "__main__":
    main()
