#!/usr/bin/env python3
"""
Console (no FastAPI) example: load a workflow JSON from disk and convert it to API format.

This uses *file mode* by default (node_info JSON file), so it does NOT require a running
ComfyUI server.

Resolution order:
- CLI args override all
- else environment variables (AUTOGRAPH_*)
- else defaults
"""

import argparse
import os
from pathlib import Path
from typing import Optional

from autograph import Flow


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a ComfyUI workflow JSON to API format (console example).")
    parser.add_argument(
        "workflow",
        nargs="?",
        help="Path to workflow JSON (required)",
    )
    parser.add_argument(
        "--node-info", "-f",
        default=None,
        help=(
            "Path to node_info JSON. "
            "If omitted and AUTOGRAPH_COMFYUI_SERVER_URL is set, fetches from server."
        ),
    )
    parser.add_argument(
        "--output-path", "-o",
        default=None,
        dest="output_path",
        help="Output path for converted API JSON. Default: <workflow>-api.json",
    )
    parser.add_argument(
        "--include-meta", "-m",
        action="store_true",
        help="Include _meta fields in output.",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=30,
        help="Timeout in seconds (default: 30).",
    )
    args = parser.parse_args(argv)

    if not args.workflow:
        parser.error("workflow path is required")

    workflow_path = Path(args.workflow)
    if not workflow_path.exists():
        print(f"ERROR: workflow file not found: {workflow_path}")
        return 1

    # Determine node_info source
    node_info_path = None
    if args.node_info:
        node_info_path = Path(args.node_info)
    else:
        env_obj = os.environ.get("AUTOGRAPH_NODE_INFO_PATH")
        if env_obj:
            node_info_path = Path(env_obj)
        # If neither provided, will use AUTOGRAPH_COMFYUI_SERVER_URL if set

    # Determine output path
    if args.output_path:
        output_path = Path(args.output_path)
    else:
        output_path = workflow_path.with_name(f"{workflow_path.stem}-api{workflow_path.suffix}")

    flow = Flow.load(workflow_path)
    result = flow.convert_with_errors(
        node_info=node_info_path,
        timeout=args.timeout,
        include_meta=args.include_meta,
        output_path=output_path,
    )

    if not result.ok:
        print("Conversion failed.")
    else:
        print("Conversion succeeded.")

    print(f"Processed nodes: {result.processed_nodes}/{result.total_nodes} (skipped: {result.skipped_nodes})")
    if result.errors:
        print(f"Errors: {len(result.errors)}")
        for e in result.errors:
            print(f"  - {e.category.value}/{e.severity.value}: {e.message}")
    if result.warnings:
        print(f"Warnings: {len(result.warnings)}")
        for w in result.warnings:
            print(f"  - {w.category.value}/{w.severity.value}: {w.message}")

    if result.data is None:
        return 1
    print(f"Wrote: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
