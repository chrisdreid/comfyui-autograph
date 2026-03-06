"""autoflow.map

Stdlib-only helpers to post-process API-prompt workflows (dict of node_id -> node).

Primary use cases:
- templating / string replacement across node input values
- path remapping (Windows/macOS/Linux) for file-based nodes
- opt-in force recompute for repeatable pipeline runs

These helpers operate on *API payload* format (the dict returned by `convert(...)`).
"""

from __future__ import annotations

import copy
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union


# A conservative default set, only used when use_defaults=True.
# These have been tested I am sure there are many more that will work
_DEFAULT_CACHE_BUSTER_NODE_TYPES = [
    "KSampler",
    "EmptyLatentImage",
    "VAEDecode",
    "VAEEncode",
    "KSamplerAdvanced",
]


def _expand(s: str) -> str:
    # Expand env vars and ~
    return os.path.expandvars(os.path.expanduser(s))


def _read_text_if_file(value: str) -> str:
    """If value points to an existing file, return its text; otherwise return value.

    Rule values can be a literal string or a filepath.
    We treat *existing files* as file paths.
    """

    expanded = _expand(value)
    p = Path(expanded)
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return expanded


def _compile_regex(maybe_pattern_or_path: Optional[str]) -> Optional[re.Pattern]:
    if not maybe_pattern_or_path:
        return None
    pattern = _read_text_if_file(maybe_pattern_or_path).strip()
    if not pattern:
        return None
    return re.compile(pattern)


def _node_names(node: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    ct = node.get("class_type")
    if isinstance(ct, str) and ct:
        out.append(ct)
    meta = node.get("_meta")
    if isinstance(meta, dict):
        title = meta.get("title")
        if isinstance(title, str) and title:
            out.append(title)
    return out


def _rule_match(
    *,
    node: Dict[str, Any],
    param: str,
    value: str,
    rules: Optional[Dict[str, Any]],
) -> bool:
    """Return True if this (node, param, value) should be processed."""

    if not rules:
        return True

    mode = rules.get("mode", "and")
    if mode not in ("and", "or"):
        mode = "and"

    node_re = _compile_regex((rules.get("node") or {}).get("regex") if isinstance(rules.get("node"), dict) else rules.get("node"))
    param_re = _compile_regex((rules.get("param") or {}).get("regex") if isinstance(rules.get("param"), dict) else rules.get("param"))
    value_re = _compile_regex((rules.get("value") or {}).get("regex") if isinstance(rules.get("value"), dict) else rules.get("value"))

    checks: List[bool] = []

    if node_re is not None:
        checks.append(any(node_re.search(name) for name in _node_names(node)))
    if param_re is not None:
        checks.append(bool(param_re.search(param)))
    if value_re is not None:
        checks.append(bool(value_re.search(value)))

    if not checks:
        return True

    return all(checks) if mode == "and" else any(checks)


def _apply_literal(value: str, literal: Dict[str, Any]) -> str:
    # Preserve dict insertion order (py3.7+).
    out = value
    for k, v in literal.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, (str, Path)):
            rep = _expand(str(v))
        else:
            rep = str(v)
        if k:
            out = out.replace(_expand(k), rep)
    return out


def _apply_regex(value: str, rules: List[Dict[str, Any]]) -> str:
    out = value
    for r in rules:
        if not isinstance(r, dict):
            continue
        pat = r.get("pattern")
        rep = r.get("replace")
        if not isinstance(pat, str) or rep is None:
            continue
        pat_s = _read_text_if_file(pat)
        rep_s = _expand(str(rep))
        try:
            out = re.sub(pat_s, rep_s, out)
        except re.error:
            # Ignore bad patterns rather than blowing up the whole mapping.
            continue
    return out


def map_strings(flow: Dict[str, Any], spec: Dict[str, Any], *, in_place: bool = False) -> Dict[str, Any]:
    """Replace string values under each node's `inputs` based on a JSON-friendly spec.

    `spec`:
      {
        "replacements": {
          "literal": {"${X}": "y"},
          "regex": [{"pattern": "...", "replace": "..."}],
        },
        "rules": {
          "mode": "and"|"or",
          "node": {"regex": "..."} | "...",
          "param": {"regex": "..."} | "...",
          "value": {"regex": "..."} | "...",
        }
      }

    Rule regex values may be inline strings or file paths (and env vars are expanded).
    """

    if not hasattr(flow, "items"):
        raise TypeError("flow must be a dict-like mapping (API payload format)")
    if not isinstance(spec, dict):
        raise TypeError("spec must be a dict")

    out = flow if in_place else copy.deepcopy(flow)

    repl = spec.get("replacements") if isinstance(spec.get("replacements"), dict) else {}
    literal = repl.get("literal") if isinstance(repl.get("literal"), dict) else {}
    regex_rules = repl.get("regex") if isinstance(repl.get("regex"), list) else []

    rules = spec.get("rules") if isinstance(spec.get("rules"), dict) else None

    for _node_id, node in out.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        for param, val in list(inputs.items()):
            if not isinstance(param, str):
                continue
            if not isinstance(val, str):
                continue

            if not _rule_match(node=node, param=param, value=val, rules=rules):
                continue

            new_val = val
            if literal:
                new_val = _apply_literal(new_val, literal)
            if regex_rules:
                new_val = _apply_regex(new_val, regex_rules)

            if new_val != val:
                inputs[param] = new_val

    return out


def map_paths(
    flow: Dict[str, Any],
    spec: Dict[str, Any],
    *,
    in_place: bool = False,
    path_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Convenience wrapper around map_strings, tuned for file-path params.

    It injects a default param filter if one isn't provided:
    - matches common path-like input names (case-insensitive)

    Users can override `path_keys` to tighten the filter.
    """

    if not isinstance(spec, dict):
        raise TypeError("spec must be a dict")

    spec2 = spec if in_place else copy.deepcopy(spec)

    rules = spec2.get("rules")
    if not isinstance(rules, dict):
        rules = {}
        spec2["rules"] = rules

    # If the user didn't specify a param rule, add a conservative default.
    if "param" not in rules:
        keys = path_keys or [
            "path",
            "file",
            "filename",
            "image",
            "mask",
            "video",
            "dir",
            "directory",
            "output",
            "input",
            "ckpt",
            "checkpoint",
            "vae",
            "lora",
            "model",
        ]
        pat = "(?i)(" + "|".join(re.escape(k) for k in keys) + ")"
        rules["param"] = {"regex": pat}

    return map_strings(flow, spec2, in_place=in_place)


def force_recompute(
    flow: Any,
    *,
    node_types: Optional[List[str]] = None,
    key: str = "_autoflow_force_recompute_",
    use_defaults: bool = False,
    in_place: bool = False,
) -> Dict[str, Any]:
    """Inject a random UUID into node inputs to force recompute.

    This is opt-in. If node_types is None:
    - use_defaults=False: do nothing
    - use_defaults=True: uses a small conservative default list
    """

    if not hasattr(flow, "items"):
        raise TypeError("flow must be a dict-like mapping (API payload format)")

    out = flow if in_place else copy.deepcopy(flow)

    effective = node_types
    if effective is None:
        effective = _DEFAULT_CACHE_BUSTER_NODE_TYPES if use_defaults else []

    want = set([t for t in effective if isinstance(t, str)])
    if not want:
        return out

    for _node_id, node in out.items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type")
        if ct not in want:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        inputs[key] = str(uuid.uuid4())

    return out


def _get_param_spec(
    node_info: Optional[Dict[str, Any]],
    class_type: Optional[str],
    param: str,
) -> Optional[List[Any]]:
    """
    Return the raw node_info spec list for a param, e.g. ["INT", {...}] or ["MODEL"].
    """
    if not node_info or not isinstance(node_info, dict):
        return None
    if not class_type or not isinstance(class_type, str):
        return None
    info = node_info.get(class_type)
    if not isinstance(info, dict):
        return None
    inp = info.get("input")
    if not isinstance(inp, dict):
        return None
    for section in ("required", "optional"):
        sec = inp.get(section)
        if not isinstance(sec, dict):
            continue
        spec = sec.get(param)
        if isinstance(spec, list):
            return spec
    return None


def api_mapping(
    flow: Any,
    callbacks: Union[Callable[[Dict[str, Any]], Any], Iterable[Callable[[Dict[str, Any]], Any]]],
    *,
    node_info: Optional[Dict[str, Any]] = None,
    in_place: bool = False,
) -> Dict[str, Any]:
    """
    Callback-first mapping over an API payload (dict of node_id -> node dict).

    Callbacks receive a context dict with (best effort):
      - node_id, node, class_type
      - param, value
      - meta (node['_meta'] dict or {})
      - workflow_extra (best-effort workflow['extra'] dict, usually attached during convert)
      - upstream_node_id, upstream_slot, upstream_node (when value is a link like ['12', 0])
      - param_spec, param_type (from node_info if available)

    Callback return values:
      - None: no change
      - any value: replace this param's value with that value (typed overwrite)
      - dict op (optional): {"set": v} | {"delete": True} | {"rename": "new"} | {"meta": {...}}
      - list of the above: applied in order
    """
    # Accept dict-like objects (e.g. ApiFlow) as long as they behave like a mapping.
    if not hasattr(flow, "items"):
        raise TypeError("flow must be a dict-like mapping (API payload format)")

    if callable(callbacks):
        cb_list = [callbacks]
    else:
        cb_list = [c for c in callbacks if callable(c)]

    if not cb_list:
        return flow if in_place else copy.deepcopy(flow)

    # Read attached metadata from the ORIGINAL flow before any copy, since these
    # may be @property on wrapper classes (e.g. flowtree ApiFlow) that won't
    # survive deepcopy (no __deepcopy__/__reduce__ on MutableMapping wrappers).
    if node_info is None:
        node_info = getattr(flow, "node_info", None)
    workflow_extra_attr = getattr(flow, "workflow_meta", None)

    if in_place:
        out = flow
    else:
        # Flowtree ApiFlow overrides items()/keys() with class_type-based keys,
        # so dict(flow) or flow.items() won't produce raw node-id keyed data.
        # Use unwrap() to get the underlying legacy dict when available.
        raw = getattr(flow, "unwrap", lambda: flow)()
        out = copy.deepcopy(dict(raw))

    for node_id, node in out.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        class_type = node.get("class_type") if isinstance(node.get("class_type"), str) else None
        meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
        workflow_extra = workflow_extra_attr
        if not isinstance(workflow_extra, dict):
            workflow_extra = meta.get("extra") if isinstance(meta.get("extra"), dict) else None

        for param, value in list(inputs.items()):
            if not isinstance(param, str):
                continue

            upstream_node_id = None
            upstream_slot = None
            upstream_node = None
            if isinstance(value, (list, tuple)) and len(value) == 2:
                upstream_node_id = str(value[0])
                upstream_slot = value[1]
                upstream_node = out.get(upstream_node_id) if upstream_node_id is not None else None

            param_spec = _get_param_spec(node_info, class_type, param)
            param_type = None
            if isinstance(param_spec, list) and param_spec and isinstance(param_spec[0], str):
                param_type = param_spec[0]

            ctx = {
                "node_id": str(node_id),
                "node": node,
                "class_type": class_type,
                "param": param,
                "value": value,
                "meta": meta,
                "workflow_extra": workflow_extra,
                "upstream_node_id": upstream_node_id,
                "upstream_slot": upstream_slot,
                "upstream_node": upstream_node,
                "param_spec": param_spec,
                "param_type": param_type,
            }

            def _apply_ret(ret: Any) -> None:
                nonlocal param
                if ret is None:
                    return
                if isinstance(ret, list):
                    for item in ret:
                        _apply_ret(item)
                    return
                if isinstance(ret, dict):
                    if ret.get("delete") is True:
                        inputs.pop(param, None)
                        return
                    if "rename" in ret and isinstance(ret.get("rename"), str) and ret["rename"]:
                        new_param = ret["rename"]
                        if new_param != param:
                            inputs[new_param] = inputs.pop(param, value)
                            param = new_param
                        return
                    if "meta" in ret and isinstance(ret.get("meta"), dict):
                        m = node.get("_meta")
                        if not isinstance(m, dict):
                            m = {}
                        ns = m.get("meta")
                        if not isinstance(ns, dict):
                            ns = {}
                        ns.update(ret["meta"])
                        m["meta"] = ns
                        node["_meta"] = m
                        return
                    if "set" in ret:
                        inputs[param] = ret["set"]
                        return
                    # Unknown dict: ignore
                    return

                # Plain value overwrite (typed).
                inputs[param] = ret

            for cb in cb_list:
                _apply_ret(cb(ctx))

    return out
