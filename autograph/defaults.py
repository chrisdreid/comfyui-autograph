"""autoflow.defaults

Centralized defaults and environment-variable helpers.

Precedence rule: args -> env var -> default.
"""

from __future__ import annotations

import os


def _env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if isinstance(v, str) and v != "" else default


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if not isinstance(v, str) or v.strip() == "":
        return default
    try:
        return int(v.strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if not isinstance(v, str) or v.strip() == "":
        return default
    try:
        return float(v.strip())
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if not isinstance(v, str) or v.strip() == "":
        return default
    s = v.strip().lower()
    if s in ("1", "true", "t", "yes", "y", "on"):
        return True
    if s in ("0", "false", "f", "no", "n", "off"):
        return False
    return default


# Env vars (library-level defaults; args still win via function parameters)
ENV_TIMEOUT_S = "AUTOFLOW_TIMEOUT_S"
ENV_POLL_INTERVAL_S = "AUTOFLOW_POLL_INTERVAL_S"
ENV_WAIT_TIMEOUT_S = "AUTOFLOW_WAIT_TIMEOUT_S"
ENV_OUTPUT_PATH = "AUTOFLOW_OUTPUT_PATH"
ENV_SUBMIT_CLIENT_ID = "AUTOFLOW_SUBMIT_CLIENT_ID"
ENV_SUBGRAPH_MAX_DEPTH = "AUTOFLOW_SUBGRAPH_MAX_DEPTH"
ENV_FIND_MAX_DEPTH = "AUTOFLOW_FIND_MAX_DEPTH"
ENV_POLL_QUEUE = "AUTOFLOW_POLL_QUEUE"
ENV_QUEUE_POLL_INTERVAL_S = "AUTOFLOW_QUEUE_POLL_INTERVAL_S"
ENV_NODE_INFO_SOURCE = "AUTOFLOW_NODE_INFO_SOURCE"


# Hard defaults (computed once at import)
DEFAULT_HTTP_TIMEOUT_S = _env_int(ENV_TIMEOUT_S, 30)
DEFAULT_POLL_INTERVAL_S = _env_float(ENV_POLL_INTERVAL_S, 0.5)
DEFAULT_WAIT_TIMEOUT_S = _env_int(ENV_WAIT_TIMEOUT_S, 60)
DEFAULT_SUBMIT_CLIENT_ID = _env_str(ENV_SUBMIT_CLIENT_ID, "autoflow")
DEFAULT_OUTPUT_PATH = _env_str(ENV_OUTPUT_PATH, "./")
DEFAULT_POLL_QUEUE = _env_bool(ENV_POLL_QUEUE, False)
DEFAULT_QUEUE_POLL_INTERVAL_S = _env_float(ENV_QUEUE_POLL_INTERVAL_S, 1.0)

DEFAULT_SUBMIT_WAIT = False
DEFAULT_FETCH_IMAGES = False
DEFAULT_GET_IMAGES_WAIT = True
DEFAULT_GET_IMAGES_INCLUDE_IMAGE_BYTES = True
DEFAULT_USE_API = False
DEFAULT_INCLUDE_META = True

DEFAULT_JSON_INDENT = 2
DEFAULT_JSON_ENSURE_ASCII = False

# Depth limits (subgraph + search)
DEFAULT_SUBGRAPH_MAX_DEPTH = _env_int(ENV_SUBGRAPH_MAX_DEPTH, 99)
DEFAULT_FIND_DEEP = True
# Unified find depth:
# - used both for subgraph traversal (when deep=True) and attribute recursion into dict/list structures
DEFAULT_FIND_MAX_DEPTH = _env_int(ENV_FIND_MAX_DEPTH, 8)


