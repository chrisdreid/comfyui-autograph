#!/usr/bin/env python3
"""
CLI entrypoint for the autoflow package.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .defaults import DEFAULT_OUTPUT_PATH
from .model_layer import Flow, ApiFlow, NodeInfo

__all__ = ["main"]


def _csv(s: str):
    return [p.strip() for p in (s or "").split(",") if p.strip()]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="autoflow", description="Convert ComfyUI workspace workflow.json to API payload, and optionally submit.")

    p.add_argument("--input-path", "-i", default=None, help="Input workflow.json (workspace) or a PNG with embedded workflow metadata")
    p.add_argument("--output-path", "-o", default=None, help="Output path for workflow-api.json")
    p.add_argument("--node-info-path", "-f", default=None, help="node_info.json path for offline conversion")

    p.add_argument("--server-url", default=None, help="ComfyUI server URL (e.g. http://localhost:8188)")
    p.add_argument("--download-node-info-path", default=None, help="Fetch /object_info and save to this path, then exit")

    p.add_argument("--submit", action="store_true", help="Submit converted API payload to ComfyUI")
    p.add_argument("--no-wait", action="store_true", help="Submit without waiting for completion")
    p.add_argument("--no-progress", action="store_true", help="Disable progress output when waiting")

    p.add_argument("--save-images", default=None, help="Directory to save fetched images")
    p.add_argument("--save-files", default=None, help="Directory to save fetched registered files")
    p.add_argument("--filepattern", default="frame.###.png", help="Filename pattern used when saving images")
    p.add_argument("--index-offset", type=int, default=0, help="Index offset for #### or %%0Nd patterns")
    p.add_argument("--output-types", default=None, help="Comma-separated output types when saving files (e.g. images,files)")

    args = p.parse_args(argv)

    # Download node_info and exit.
    if args.download_node_info_path:
        oi = NodeInfo.fetch(server_url=args.server_url, output_path=args.download_node_info_path)
        # Print the path for scripting.
        print(str(Path(args.download_node_info_path)))
        return 0

    if not args.input_path:
        p.error("--input-path is required (unless --download-node-info-path is used)")

    node_info = args.node_info_path

    # Convert-only mode.
    if not args.submit:
        out_path = args.output_path or DEFAULT_OUTPUT_PATH
        api = ApiFlow(args.input_path, node_info=node_info, auto_convert=True, server_url=args.server_url)
        api.save(out_path)
        print(str(Path(out_path)))
        return 0

    # Submit mode (convert then submit).
    api = ApiFlow(args.input_path, node_info=node_info, auto_convert=True, server_url=args.server_url)

    wait = not bool(args.no_wait)
    on_event = None
    if wait and not args.no_progress:
        try:
            from .ws import ProgressPrinter

            on_event = ProgressPrinter()
        except Exception:
            on_event = None

    res = api.submit(server_url=args.server_url, wait=wait, fetch_outputs=False, on_event=on_event)
    # Print job handle first.
    pid = getattr(res, "prompt_id", None) or res.get("prompt_id")
    if pid:
        print(str(pid))

    # In no-wait mode we stop here.
    if not wait:
        return 0

    # Save images/files if requested.
    if args.save_images:
        images = res.fetch_images()
        out_dir = Path(args.save_images)
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = images.save(output_path=out_dir, filename=args.filepattern, index_offset=int(args.index_offset))
        if isinstance(paths, Path):
            print(str(paths))
        else:
            for sp in paths:
                print(str(sp))

    if args.save_files:
        out_dir = Path(args.save_files)
        out_dir.mkdir(parents=True, exist_ok=True)
        kinds = _csv(args.output_types) if args.output_types else None
        files = res.fetch_files(output_types=kinds)
        paths = files.save(output_path=out_dir)
        for sp in paths:
            print(str(sp))

    return 0


