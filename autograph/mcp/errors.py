"""Submission-error parsing for the MCP layer.

ComfyUI returns rich, structured error info on failures:

* ``POST /prompt`` returns HTTP 400 with a JSON body shaped like
  ``{"error": {...}, "node_errors": {<node_id>: {"errors": [...], "class_type": "..."}}}``
  when validation fails.
* During execution, ``/history/{prompt_id}`` may include a ``status.messages``
  list with ``execution_error`` entries naming the offending node.

Tools surface raw exceptions as last resort. Wherever possible we parse these
shapes into a flat ``{node_id, class_type, error_type, message, hint?}`` list
the LLM can act on.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any, Dict, List, Optional


def _string(v: Any) -> str:
    if isinstance(v, str):
        return v
    if v is None:
        return ""
    try:
        return json.dumps(v, default=str)
    except Exception:
        return str(v)


def parse_prompt_error(exc: BaseException) -> List[Dict[str, Any]]:
    """Turn an exception raised by ``/prompt`` into a structured error list."""
    errors: List[Dict[str, Any]] = []

    # Best case: HTTPError carrying a JSON body.
    if isinstance(exc, urllib.error.HTTPError):
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8")
        except Exception:
            body_text = ""
        if not body_text.strip():
            # autograph's http_json has already read the body and re-raised with
            # it appended to the reason ("Bad Request: {...}").
            msg = str(getattr(exc, "msg", "") or exc)
            if "{" in msg:
                body_text = msg[msg.index("{"):]
            elif ":" in msg:
                body_text = msg.split(":", 1)[-1].strip()
        return parse_prompt_error_body(body_text, default_status=exc.code)

    # autograph re-raises HTTPError with the body inlined into .msg, so the
    # exception's str() includes the JSON body in many cases.
    raw = str(exc)
    if "{" in raw and "}" in raw:
        start = raw.index("{")
        candidate = raw[start:]
        return parse_prompt_error_body(candidate)

    errors.append(
        {
            "node_id": None,
            "class_type": None,
            "error_type": type(exc).__name__,
            "message": raw,
        }
    )
    return errors


def parse_prompt_error_body(body: str, *, default_status: Optional[int] = None) -> List[Dict[str, Any]]:
    """Parse a ``/prompt`` 400 response body."""
    errors: List[Dict[str, Any]] = []
    if not body or not body.strip():
        if default_status is not None:
            errors.append(
                {
                    "node_id": None,
                    "class_type": None,
                    "error_type": "http",
                    "message": f"HTTP {default_status} from /prompt with empty body",
                }
            )
        return errors

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        errors.append(
            {
                "node_id": None,
                "class_type": None,
                "error_type": "non-json-body",
                "message": body.strip(),
            }
        )
        return errors

    if not isinstance(data, dict):
        errors.append(
            {
                "node_id": None,
                "class_type": None,
                "error_type": "unexpected-shape",
                "message": _string(data),
            }
        )
        return errors

    top = data.get("error")
    if isinstance(top, dict):
        errors.append(
            {
                "node_id": None,
                "class_type": None,
                "error_type": _string(top.get("type")) or "prompt-validation",
                "message": _string(top.get("message")) or _string(top),
                "details": top.get("details"),
            }
        )

    node_errors = data.get("node_errors")
    if isinstance(node_errors, dict):
        for node_id, payload in node_errors.items():
            if not isinstance(payload, dict):
                continue
            class_type = _string(payload.get("class_type")) or None
            for sub in payload.get("errors") or []:
                if not isinstance(sub, dict):
                    continue
                errors.append(
                    {
                        "node_id": _string(node_id),
                        "class_type": class_type,
                        "error_type": _string(sub.get("type")) or "node-validation",
                        "message": _string(sub.get("message")),
                        "details": sub.get("details"),
                    }
                )
    return errors or [
        {
            "node_id": None,
            "class_type": None,
            "error_type": "prompt-error",
            "message": _string(data),
        }
    ]


def parse_history_errors(history_entry: Any) -> List[Dict[str, Any]]:
    """Extract ``execution_error`` messages from a single history entry."""
    out: List[Dict[str, Any]] = []
    if not isinstance(history_entry, dict):
        return out
    status = history_entry.get("status")
    if not isinstance(status, dict):
        return out
    messages = status.get("messages")
    if not isinstance(messages, list):
        return out
    for m in messages:
        if not isinstance(m, list) or len(m) < 2:
            continue
        kind = m[0]
        payload = m[1] if len(m) > 1 else None
        if kind != "execution_error" or not isinstance(payload, dict):
            continue
        out.append(
            {
                "node_id": _string(payload.get("node_id")),
                "class_type": _string(payload.get("node_type")),
                "error_type": _string(payload.get("exception_type")) or "execution-error",
                "message": _string(payload.get("exception_message")) or _string(payload.get("message")),
                "details": {k: payload.get(k) for k in ("traceback", "current_inputs") if k in payload},
            }
        )
    return out
