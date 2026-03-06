"""autoflow.comfyproc

Tiny stdlib-only helpers to run a local ComfyUI server as a subprocess.

This is intentionally explicit and opt-in: nothing here runs unless you call it.

Primary use case: Flow/ApiFlow.execute(backend="server", start_server=True) for quick parity
with HTTP (/prompt + /history + /ws) without requiring the user to manually start ComfyUI.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .net import comfy_url, http_json

__all__ = [
    "looks_like_comfyui_root",
    "find_comfyui_root",
    "default_comfyui_cmd",
    "start_comfyui_server",
    "stop_comfyui_server",
    "wait_until_ready",
]


def looks_like_comfyui_root(root: Union[str, Path]) -> bool:
    """
    Best-effort marker check for a ComfyUI repo root.
    """
    try:
        p = Path(root)
        return p.exists() and (p / "nodes.py").is_file() and (p / "comfy").is_dir()
    except Exception:
        return False


def find_comfyui_root(*, roots: Optional[List[Union[str, Path]]] = None) -> Optional[Path]:
    """
    Try to locate a ComfyUI root from a short list of candidates.

    We intentionally keep this conservative and predictable:
    - prefer explicit `roots` if provided
    - then try cwd
    - then try entries from sys.path
    """
    candidates: List[Path] = []
    if roots:
        for r in roots:
            try:
                candidates.append(Path(r))
            except Exception:
                pass

    candidates.append(Path.cwd())
    for p in sys.path:
        if not isinstance(p, str) or not p:
            continue
        try:
            candidates.append(Path(p))
        except Exception:
            pass

    for c in candidates:
        if looks_like_comfyui_root(c):
            return c
    return None


def default_comfyui_cmd(
    *,
    comfyui_root: Path,
    host: str,
    port: int,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    """
    Default command for starting ComfyUI 0.9.x from its repo root.

    This is best-effort: ComfyUI CLI flags can change. If this doesn't match your
    installation, pass `comfyui_cmd=[...]` explicitly to `start_comfyui_server()`.
    """
    main_py = comfyui_root / "main.py"
    if not main_py.is_file():
        raise FileNotFoundError(f"ComfyUI entrypoint not found: {main_py}")
    cmd = [sys.executable, str(main_py), "--listen", str(host), "--port", str(int(port))]
    if extra_args:
        cmd.extend([str(x) for x in extra_args])
    return cmd


def wait_until_ready(
    server_url: str,
    *,
    timeout_s: float = 30.0,
    poll_interval_s: float = 0.25,
) -> bool:
    """
    Poll a lightweight ComfyUI endpoint until it responds.

    We use GET /object_info because it's stable and exists in the HTTP flow this repo already supports.
    """
    deadline = time.time() + max(0.1, float(timeout_s))
    url = comfy_url(server_url, "/object_info")
    while time.time() < deadline:
        try:
            _ = http_json(url, payload=None, timeout=5, method="GET")
            return True
        except Exception:
            time.sleep(max(0.05, float(poll_interval_s)))
    return False


def start_comfyui_server(
    *,
    server_url: Optional[str] = None,
    host: str = "127.0.0.1",
    port: int = 8188,
    comfyui_root: Optional[Union[str, Path]] = None,
    comfyui_cmd: Optional[List[str]] = None,
    extra_args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    stdout: Any = subprocess.DEVNULL,
    stderr: Any = subprocess.DEVNULL,
    ready_timeout_s: float = 30.0,
) -> Tuple[str, subprocess.Popen]:
    """
    Start a ComfyUI HTTP server as a subprocess and wait until it becomes ready.

    Returns (server_url, proc). Caller should eventually call `stop_comfyui_server(proc)`.

    Notes:
    - This function does not attempt to manage GPU/model config; it assumes the environment is ready.
    - For exact command control, pass `comfyui_cmd=[...]`.
    """
    if server_url is None:
        server_url = f"http://{host}:{int(port)}"

    root: Optional[Path] = None
    if comfyui_root is not None:
        root = Path(comfyui_root)
    else:
        root = find_comfyui_root()

    if comfyui_cmd is None:
        if root is None:
            raise FileNotFoundError(
                "Could not locate ComfyUI root. Pass comfyui_root=... or comfyui_cmd=[...] explicitly."
            )
        comfyui_cmd = default_comfyui_cmd(comfyui_root=root, host=host, port=port, extra_args=extra_args)
    else:
        comfyui_cmd = [str(x) for x in comfyui_cmd]
        if extra_args:
            comfyui_cmd.extend([str(x) for x in extra_args])

    # Env: inherit parent env; allow small overrides.
    proc_env = os.environ.copy()
    if env:
        for k, v in env.items():
            if isinstance(k, str) and isinstance(v, str):
                proc_env[k] = v

    cwd = str(root) if root is not None else None
    proc = subprocess.Popen(comfyui_cmd, cwd=cwd, env=proc_env, stdout=stdout, stderr=stderr)

    ok = wait_until_ready(server_url, timeout_s=ready_timeout_s)
    if not ok:
        try:
            stop_comfyui_server(proc)
        except Exception:
            pass
        raise TimeoutError(f"ComfyUI server did not become ready within {ready_timeout_s}s at {server_url}")

    return server_url, proc


def stop_comfyui_server(proc: subprocess.Popen, *, timeout_s: float = 5.0) -> None:
    """
    Stop a subprocess started by `start_comfyui_server()`.
    """
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=max(0.1, float(timeout_s)))
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=1.0)
    except Exception:
        pass