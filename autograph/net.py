"""autoflow.net

Stdlib-only HTTP helpers for talking to a ComfyUI server.

This module intentionally keeps network interactions explicit and opt-in.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .defaults import DEFAULT_HTTP_TIMEOUT_S


def comfy_url(server_url: str, path: str) -> str:
    return f"{server_url.rstrip('/')}/{path.lstrip('/')}"


def http_json(
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = DEFAULT_HTTP_TIMEOUT_S,
    method: str = "POST",
) -> Dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            if not body:
                return {}
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                return parsed
            return {"raw": parsed}
    except urllib.error.HTTPError as e:
        # Best-effort capture response body for easier debugging (e.g. /prompt 400 errors).
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = ""
        raise urllib.error.HTTPError(
            e.url,
            e.code,
            f"{e.msg}{': ' + err_body if err_body else ''}",
            e.hdrs,
            e.fp,
        )


def resolve_comfy_server_url(server_url: Optional[str]) -> str:
    """
    Resolve ComfyUI server URL for submit operations.

    We intentionally do NOT default to localhost here; submit() should only run when the
    user explicitly provides a URL or sets AUTOFLOW_COMFYUI_SERVER_URL.
    """
    if server_url:
        return server_url
    env = os.environ.get("AUTOFLOW_COMFYUI_SERVER_URL")
    if env:
        return env
    raise ValueError("Missing server URL. Pass server_url= or set AUTOFLOW_COMFYUI_SERVER_URL.")



