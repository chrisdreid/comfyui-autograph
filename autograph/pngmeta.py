"""autoflow.pngmeta

Stdlib-only PNG metadata helpers for ComfyUI.

ComfyUI embeds JSON in PNG metadata under keys:
- "prompt"   (API payload / ApiFlow format)
- "workflow" (workspace Flow format)
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, Dict, Union


def parse_png_metadata_from_bytes(png_bytes: bytes) -> Dict[str, Any]:
    """
    Parse ComfyUI workflow metadata from PNG bytes (stdlib-only).

    Returns dict with "prompt" and/or "workflow" keys (parsed JSON).
    """
    if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Not valid PNG data")

    metadata: Dict[str, Any] = {}
    offset = 8  # Skip signature

    while offset < len(png_bytes):
        if offset + 8 > len(png_bytes):
            break
        length, chunk_type = struct.unpack(">I4s", png_bytes[offset : offset + 8])
        chunk_type_str = chunk_type.decode("ascii", errors="replace")
        offset += 8

        if offset + length > len(png_bytes):
            break
        chunk_data = png_bytes[offset : offset + length]
        offset += length + 4  # skip data + CRC

        if chunk_type_str == "tEXt":
            # tEXt: keyword\x00text
            key_bytes, _, value_bytes = chunk_data.partition(b"\x00")
            key = key_bytes.decode("latin-1")
            if key in ("prompt", "workflow"):
                try:
                    metadata[key] = json.loads(value_bytes.decode("utf-8"))
                except json.JSONDecodeError:
                    pass

        elif chunk_type_str == "iTXt":
            # iTXt: keyword\x00compression_flag\x00compression_method\x00lang\x00translated\x00text
            key_bytes, _, rest = chunk_data.partition(b"\x00")
            key = key_bytes.decode("utf-8", errors="replace")
            if key in ("prompt", "workflow"):
                parts = rest.split(b"\x00", 4)
                if len(parts) >= 5:
                    try:
                        metadata[key] = json.loads(parts[4].decode("utf-8"))
                    except json.JSONDecodeError:
                        pass

        elif chunk_type_str == "IEND":
            break

    return metadata


def extract_png_comfyui_metadata(png_path: Union[str, Path]) -> Dict[str, Any]:
    """Extract ComfyUI metadata dict from a PNG file path."""
    with open(png_path, "rb") as f:
        return parse_png_metadata_from_bytes(f.read())


def looks_like_json(s: str) -> bool:
    """Heuristic: string looks like JSON if it contains {, }, and :."""
    return "{" in s and "}" in s and ":" in s


def looks_like_path(s: str) -> bool:
    """
    Heuristic: treat strings as file paths (not JSON) when they look like a path/filename.

    Prevents confusing behavior like json.loads(\"workflow.json\") when a relative file
    doesn't exist in the current working directory.
    """
    if not isinstance(s, str) or not s:
        return False
    suf = Path(s).suffix.lower()
    if suf in (".json", ".png"):
        return True
    if "/" in s or "\\" in s:
        return True
    return False


def is_png_bytes(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n")


def is_png_path(x: Union[str, Path]) -> bool:
    p = Path(x) if isinstance(x, str) else x
    return p.suffix.lower() == ".png" and p.exists()



