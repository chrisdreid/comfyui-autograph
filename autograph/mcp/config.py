"""Configuration helpers for the autograph MCP server.

We deliberately reuse autograph's existing environment variables (so users do not
have to learn a new config surface), and add a small number of MCP-specific knobs
for the inline-image return policy.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


DEFAULT_MAX_INLINE_IMAGE_BYTES = 2_000_000   # ~2 MB per image inlined as base64
DEFAULT_MAX_INLINE_IMAGES = 4                # cap on number of images inlined per call
DEFAULT_LOCAL_FALLBACK_URL = "http://127.0.0.1:8188"
DEFAULT_WAIT_TIMEOUT_S = 120                 # how long run_workflow / wait_for_job wait before handing back the job id


@dataclass(frozen=True)
class McpConfig:
    server_url: Optional[str]
    timeout_s: int
    output_path: str
    max_inline_image_bytes: int
    max_inline_images: int
    wait_timeout_s: int = DEFAULT_WAIT_TIMEOUT_S

    def resolve_server_url(self, override: Optional[str] = None) -> str:
        """Pick a ComfyUI URL: explicit arg → env-configured → localhost fallback.

        Unlike autograph's stricter ``resolve_comfy_server_url`` (which raises if no URL
        is configured), the MCP server falls back to ``http://127.0.0.1:8188`` so a
        user who just dropped the IDE config in and is running ComfyUI locally with
        defaults gets a working setup without extra env vars.
        """
        if isinstance(override, str) and override:
            return override
        if isinstance(self.server_url, str) and self.server_url:
            return self.server_url
        return DEFAULT_LOCAL_FALLBACK_URL


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def load_config() -> McpConfig:
    return McpConfig(
        server_url=os.environ.get("AUTOGRAPH_COMFYUI_SERVER_URL") or None,
        timeout_s=_int_env("AUTOGRAPH_TIMEOUT_S", 30),
        output_path=os.environ.get("AUTOGRAPH_OUTPUT_PATH") or "./",
        max_inline_image_bytes=_int_env(
            "AUTOGRAPH_MCP_MAX_INLINE_IMAGE_BYTES", DEFAULT_MAX_INLINE_IMAGE_BYTES
        ),
        max_inline_images=_int_env(
            "AUTOGRAPH_MCP_MAX_INLINE_IMAGES", DEFAULT_MAX_INLINE_IMAGES
        ),
        wait_timeout_s=_int_env("AUTOGRAPH_MCP_WAIT_TIMEOUT_S", DEFAULT_WAIT_TIMEOUT_S),
    )
