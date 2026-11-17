"""Node knowledge and graph checks for the MCP tools, in plain JSON.

An assistant building a workflow needs three things the raw ComfyUI data does
not give it directly:

* **a searchable, compact node catalog** (``Catalog``): which node types
  exist, what each input takes (type, default, range, choices) and which
  inputs are widgets and which are connections;
* **a readable view of a graph** (``summarize``): every node with its values
  by name and its links in ComfyUI's API notation;
* **a real pre-flight** (``validate``): unknown node types, missing required
  inputs, links between unrelated types, values that are not among a
  widget's choices or outside its range, graphs without an output. This is
  what ComfyUI checks when a prompt is queued, done before queueing and with
  suggestions attached.

Everything here works on plain dicts, so it can be tested without a server.
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from autograph.connection import (
    AUTOGROW_TYPE,
    DYNAMIC_COMBO_TYPE,
    default_for_spec,
    dynamic_combo_options,
    is_queue_time_spec,
    is_widget_spec,
    option_input_specs,
)

ANNOTATED_FOLDERS = ("[output]", "[input]", "[temp]")
# Problems with a value. ComfyUI decides these when a job is queued (a node may
# check its own inputs), so they are reported but do not stop a run by themselves.
VALUE_CODES = frozenset({"value_not_in_list", "no_options", "out_of_range", "wrong_type"})
# ComfyUI's name for each problem, to match its answer with what was found here.
SERVER_CODES = {
    "value_not_in_list": "value_not_in_list", "required_input_missing": "missing_input",
    "return_type_mismatch": "type_mismatch", "value_smaller_than_min": "out_of_range",
    "value_bigger_than_max": "out_of_range", "invalid_input_type": "wrong_type",
    "bad_linked_input": "bad_output_index", "custom_validation_failed": "value_not_in_list",
}
# Core nodes that check some of their inputs themselves (a validate_inputs of their own), so ComfyUI
# does not hold those inputs to the list or range in the node list. "*" is every input. The file
# loaders do this too, but their own check is "the file exists", which is what the list says.
SELF_CHECKED = {
    "CustomCombo": "*", "HeyGenImageToVideoNode": "*", "HeyGenReferenceToVideoNode": "*", "Kling4EditVideoNode": "*",
    "FluxProUltraImageNode": ("aspect_ratio",), "ModelAttentionBackend": ("attention",),
    "SelectCLIPDevice": ("device",), "SelectModelDevice": ("device",), "SelectVAEDevice": ("device",),
}
# Node types that exist only in the editor (notes); they are not part of what runs.
EDITOR_ONLY = frozenset({"Note", "MarkdownNote"})
MAX_OPTIONS = 25            # choices listed per widget before the list is cut short
MAX_TOOLTIP = 160


# ---------------------------------------------------------------------------
# Plain data
# ---------------------------------------------------------------------------


def plain(obj: Any) -> Any:
    """A deep copy made of dict / list / str / number / bool / None only.

    autograph hands out views (``DictView``, ``ListView``, ``WidgetValue``)
    that behave like containers but are not JSON types.
    """
    if obj is None or isinstance(obj, (str, bool, int, float)):
        return obj
    if isinstance(obj, dict) or (hasattr(obj, "keys") and hasattr(obj, "__getitem__")):
        try:
            items = dict.items(obj) if isinstance(obj, dict) else [(k, obj[k]) for k in obj.keys()]
        except Exception:
            items = [(k, obj[k]) for k in obj.keys()]
        return {str(k): plain(v) for k, v in items}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [plain(v) for v in obj]
    unwrap = getattr(obj, "unwrap", None)
    if callable(unwrap):
        try:
            return plain(unwrap())
        except Exception:
            pass
    if hasattr(obj, "__iter__") and hasattr(obj, "__len__"):
        try:
            return [plain(v) for v in obj]
        except Exception:
            pass
    value = getattr(obj, "value", None)
    if value is not None and isinstance(value, (str, bool, int, float)):
        return value
    return str(obj)


def is_link(value: Any) -> bool:
    """True for a link in ComfyUI's API notation: ``[node_id, output_index]``."""
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and not isinstance(value[0], bool)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def types_connect(out_type: Any, in_type: Any) -> bool:
    """Whether an output of one type can feed an input of another (ComfyUI's editor rule).

    Equal types, wildcards and comma-separated lists sharing a member connect,
    without regard to case. The schema's generic types (match-type, autogrow,
    dynamic combo) are resolved per node by ComfyUI; they are let through.
    """
    a = "" if out_type in (None, 0) else str(out_type)
    b = "" if in_type in (None, 0) else str(in_type)
    if not a or not b or a == b:
        return True
    outs = {t.strip().upper() for t in a.split(",")}
    ins = {t.strip().upper() for t in b.split(",")}
    if any(t in ("*", "") or t.startswith("COMFY_") for t in outs | ins):
        return True
    return bool(outs & ins)


# ---------------------------------------------------------------------------
# Input specs
# ---------------------------------------------------------------------------


def _spec_parts(spec: Any) -> Tuple[str, Dict[str, Any], Optional[List[Any]]]:
    """(type name, options dict, choices or None) of a node_info input spec."""
    if not isinstance(spec, (list, tuple)) or not spec:
        return "*", {}, None
    head = spec[0]
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if isinstance(head, (list, tuple)):
        return "COMBO", opts, list(head)
    name = str(head)
    if name.upper() == "COMBO":
        options = opts.get("options")
        return "COMBO", opts, list(options) if isinstance(options, (list, tuple)) else []
    return name, opts, None


def describe_input(name: str, spec: Any, required: bool, max_options: int = MAX_OPTIONS) -> Dict[str, Any]:
    """One input of a node type, as an assistant needs it to fill the input in."""
    type_name, opts, choices = _spec_parts(spec)
    widget = bool(is_widget_spec(spec))
    out: Dict[str, Any] = {"name": name, "type": type_name, "required": required,
                           "kind": "widget" if widget else "connection"}
    if type_name == AUTOGROW_TYPE:
        template = opts.get("template") if isinstance(opts.get("template"), dict) else {}
        member = None
        for section in ("required", "optional"):
            for _n, member_spec in ((template.get("input") or {}).get(section) or {}).items():
                member = member_spec
                break
            if member is not None:
                break
        names = template.get("names")
        out["kind"] = "connection_group"
        out["member_type"] = _spec_parts(member)[0] if member is not None else "*"
        out["members"] = (list(names) if isinstance(names, (list, tuple))
                          else f"{name}.{template.get('prefix', '')}0, {name}.{template.get('prefix', '')}1, ...")
        if isinstance(template.get("min"), int):
            out["min_members"] = template["min"]
        if isinstance(template.get("max"), int):
            out["max_members"] = template["max"]
    elif type_name == DYNAMIC_COMBO_TYPE:
        # A dropdown whose choice decides which further inputs the node has: they are
        # addressed as "<name>.<input>".
        options: Dict[str, Any] = {}
        for option in dynamic_combo_options(spec):
            key = option.get("key")
            required_names = set(((option.get("inputs") or {}).get("required") or {}).keys())
            options[str(key)] = [
                describe_input(f"{name}.{sub}", sub_spec, sub in required_names, max_options)
                for sub, sub_spec in option_input_specs(option)
            ]
        out["kind"] = "widget"
        out["options"] = list(options)
        out["inputs_per_option"] = options
        out["default"] = default_for_spec(spec)
    elif widget:
        if is_queue_time_spec(spec):
            out["queue_time"] = True        # filled in by the ComfyUI editor when it queues; needs a value from the caller
        default = default_for_spec(spec)
        if default is not None:
            out["default"] = default
        for key in ("min", "max", "step"):
            if isinstance(opts.get(key), (int, float)) and not isinstance(opts.get(key), bool):
                out[key] = opts[key]
        if choices is not None:
            out["options_total"] = len(choices)
            out["options"] = choices[:max_options] if max_options and len(choices) > max_options else choices
        if opts.get("multiline"):
            out["multiline"] = True
        if opts.get("control_after_generate"):
            out["seed"] = True
        for key in ("image_upload", "video_upload", "audio_upload", "file_upload"):
            if opts.get(key):
                out["upload"] = key.split("_")[0]       # takes a file uploaded with upload_file
    elif opts.get("forceInput"):
        out["accepts_value"] = True         # a number or text may be given directly instead of a link
    tooltip = opts.get("tooltip")
    if isinstance(tooltip, str) and tooltip.strip():
        out["tooltip"] = tooltip.strip() if len(tooltip) <= MAX_TOOLTIP else tooltip.strip()[:MAX_TOOLTIP - 1] + "…"
    return out


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class Catalog:
    """The node types of one ComfyUI server (its ``/object_info``), searchable."""

    def __init__(self, node_info: Dict[str, Any], *, server_url: str = "", from_cache: bool = False):
        self.node_info: Dict[str, Any] = {
            str(k): v for k, v in node_info.items() if isinstance(k, str) and isinstance(v, dict)
        }
        self.server_url = server_url
        self.from_cache = from_cache
        self._lower = {k.lower(): k for k in self.node_info}
        self._specs: Dict[str, List[Dict[str, Any]]] = {}

    def __len__(self) -> int:
        return len(self.node_info)

    def __contains__(self, class_type: Any) -> bool:
        return isinstance(class_type, str) and class_type in self.node_info

    # -- lookup ---------------------------------------------------------

    def suggest(self, name: str, limit: int = 5) -> List[str]:
        """Node types whose class or display name is close to ``name``."""
        wanted = str(name).lower()
        display = {str(v.get("display_name") or "").lower(): k for k, v in self.node_info.items()}
        hits: List[str] = []
        for key in difflib.get_close_matches(wanted, list(self._lower), n=limit, cutoff=0.6):
            hits.append(self._lower[key])
        for key in difflib.get_close_matches(wanted, [d for d in display if d], n=limit, cutoff=0.6):
            if display[key] not in hits:
                hits.append(display[key])
        for key, real in self._lower.items():
            if len(hits) >= limit:
                break
            if wanted and wanted in key and real not in hits:
                hits.append(real)
        return hits[:limit]

    def resolve(self, class_type: str) -> str:
        """The exact class_type for a name given in any case; KeyError with suggestions otherwise."""
        if class_type in self.node_info:
            return class_type
        real = self._lower.get(str(class_type).lower())
        if real is not None:
            return real
        for key, info in self.node_info.items():
            if str(info.get("display_name") or "").lower() == str(class_type).lower():
                return key
        close = self.suggest(class_type)
        hint = f" Did you mean: {', '.join(close)}?" if close else " Search with list_node_types(query=...)."
        raise LookupError(f"Unknown node type {class_type!r}.{hint}")

    def inputs(self, class_type: str, max_options: int = MAX_OPTIONS) -> List[Dict[str, Any]]:
        """The inputs of a node type, in the node's own order (hidden inputs left out)."""
        cache_key = f"{class_type}/{max_options}"
        if cache_key in self._specs:
            return self._specs[cache_key]
        info = self.node_info.get(class_type) or {}
        sections = info.get("input") if isinstance(info.get("input"), dict) else {}
        order = info.get("input_order") if isinstance(info.get("input_order"), dict) else {}
        out: List[Dict[str, Any]] = []
        for section in ("required", "optional"):
            specs = sections.get(section) if isinstance(sections.get(section), dict) else {}
            names = [n for n in (order.get(section) or []) if n in specs] or list(specs)
            names += [n for n in specs if n not in names]
            for name in names:
                out.append(describe_input(name, specs[name], section == "required", max_options))
        self._specs[cache_key] = out
        return out

    def input_spec(self, class_type: str, name: str,
                   selected: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """One input by name; dotted names reach the inputs of a dropdown's option or a group member.

        Options of one dropdown may have inputs of the same name with different
        choices. ``selected`` (input name -> value, e.g. the node's current
        values) says which option is meant; without it the dropdown's default
        option is looked at first.
        """
        return _find_spec(self.inputs(class_type, 0), name, selected)

    def outputs(self, class_type: str) -> List[Dict[str, Any]]:
        info = self.node_info.get(class_type) or {}
        types = info.get("output") or []
        names = info.get("output_name") or []
        lists = info.get("output_is_list") or []
        out = []
        for index, kind in enumerate(types):
            row = {"index": index,
                   "name": str(names[index]) if index < len(names) else str(kind),
                   "type": kind if isinstance(kind, str) else "COMBO"}
            if index < len(lists) and lists[index]:
                row["is_list"] = True
            out.append(row)
        return out

    def is_output_node(self, class_type: str) -> bool:
        return bool((self.node_info.get(class_type) or {}).get("output_node"))

    # -- search ---------------------------------------------------------

    def _row(self, class_type: str) -> Dict[str, Any]:
        info = self.node_info[class_type]
        row: Dict[str, Any] = {"class_type": class_type}
        display = info.get("display_name")
        if isinstance(display, str) and display and display != class_type:
            row["display_name"] = display
        row["category"] = info.get("category") or ""
        connections = [f"{s['name']}:{s['type']}{'' if s['required'] else '?'}"
                       for s in self.inputs(class_type, 0) if s["kind"] != "widget"]
        if connections:
            row["takes"] = connections
        gives = [o["type"] for o in self.outputs(class_type)]
        if gives:
            row["gives"] = gives
        for flag in ("output_node", "deprecated", "experimental", "api_node"):
            if info.get(flag):
                row[flag] = True
        return row

    def search(
        self,
        query: Optional[str] = None,
        *,
        category: Optional[str] = None,
        input_type: Optional[str] = None,
        output_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        include_deprecated: bool = False,
    ) -> Dict[str, Any]:
        """Node types matching every given filter, best match first.

        ``query`` words are looked for in the class name, display name, search
        aliases, category and description. ``input_type`` / ``output_type``
        find nodes that take or give a connection of that type ("what can I
        plug this LATENT into", "what produces CONDITIONING").
        """
        words = [w for w in str(query or "").lower().replace("_", " ").split() if w]
        cat = str(category).lower() if category else None
        scored: List[Tuple[float, str]] = []
        for class_type, info in self.node_info.items():
            if info.get("deprecated") and not include_deprecated:
                continue
            if cat and cat not in str(info.get("category") or "").lower():
                continue
            if input_type and not any(
                s["kind"] != "widget" and _type_listed(input_type, s.get("member_type") or s["type"])
                for s in self.inputs(class_type, 0)
            ):
                continue
            if output_type and not any(_type_listed(output_type, o["type"]) for o in self.outputs(class_type)):
                continue
            score = 1.0
            if words:
                name = class_type.lower()
                spaced = _spaced(class_type)
                display = str(info.get("display_name") or "").lower()
                aliases = " ".join(str(a).lower() for a in (info.get("search_aliases") or []))
                rest = f"{info.get('category') or ''} {info.get('description') or ''}".lower()
                score = 0.0
                joined = "".join(words)
                if joined in (name, display.replace(" ", "")):
                    score += 100.0
                for word in words:
                    if word in name or word in spaced:
                        score += 10.0 + (1.0 if word in spaced.split() else 0.0)
                    elif word in display:
                        score += 9.0
                    elif word in aliases:
                        score += 6.0
                    elif word in rest:
                        score += 2.0
                    else:
                        score = 0.0
                        break
                if score <= 0.0:
                    continue
                score -= len(name) / 200.0          # shorter names first among equals
            if info.get("api_node"):
                score -= 0.5                        # hosted (paid) nodes after local ones
            scored.append((score, class_type))
        scored.sort(key=lambda item: (-item[0], item[1].lower()))
        offset = max(0, int(offset))
        limit = max(1, int(limit))
        page = scored[offset:offset + limit]
        out: Dict[str, Any] = {"total": len(scored), "count": len(page), "offset": offset,
                               "node_types": [self._row(class_type) for _score, class_type in page]}
        if offset + len(page) < len(scored):
            out["next_offset"] = offset + len(page)
        return out

    def describe(self, class_type: str, max_options: int = MAX_OPTIONS) -> Dict[str, Any]:
        """Everything needed to add a node of this type and wire it."""
        real = self.resolve(class_type)
        info = self.node_info[real]
        out: Dict[str, Any] = {"class_type": real}
        for key in ("display_name", "category", "description"):
            value = info.get(key)
            if isinstance(value, str) and value.strip():
                out[key] = value.strip()
        for flag in ("output_node", "deprecated", "experimental", "api_node"):
            if info.get(flag):
                out[flag] = True
        out["inputs"] = self.inputs(real, max_options)
        out["outputs"] = self.outputs(real)
        tips = info.get("output_tooltips")
        if isinstance(tips, (list, tuple)):
            for row, tip in zip(out["outputs"], tips):
                if isinstance(tip, str) and tip.strip():
                    row["tooltip"] = tip.strip()[:MAX_TOOLTIP]
        return out

    def options(self, class_type: str, input_name: str, query: Optional[str] = None,
                limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """The choices of one dropdown input, searchable (model lists can be long)."""
        real = self.resolve(class_type)
        spec = self.input_spec(real, input_name)
        if spec is None:
            names = [s["name"] for s in self.inputs(real, 0)]
            raise LookupError(f"{real} has no input {input_name!r}. Inputs: {', '.join(names)}")
        choices = spec.get("options")
        if not isinstance(choices, list):
            raise ValueError(f"{real}.{input_name} is not a dropdown (type {spec['type']}).")
        wanted = str(query).lower() if query else None
        hits = [c for c in choices if wanted is None or wanted in str(c).lower()]
        page = hits[max(0, offset):max(0, offset) + max(1, limit)]
        out: Dict[str, Any] = {"class_type": real, "input": input_name, "total": len(hits), "options": page}
        if max(0, offset) + len(page) < len(hits):
            out["next_offset"] = max(0, offset) + len(page)
        return out


def _find_spec(specs: List[Dict[str, Any]], name: str, selected: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for spec in specs:
        if spec["name"] == name:
            return spec
    for spec in specs:
        if not name.startswith(spec["name"] + "."):
            continue
        if spec["kind"] == "connection_group":
            return {"name": name, "type": spec.get("member_type", "*"), "required": False, "kind": "connection"}
        per_option = spec.get("inputs_per_option") or {}
        chosen = (selected or {}).get(spec["name"], spec.get("default"))
        pools = ([per_option[str(chosen)]] if str(chosen) in per_option else []) + \
            [subs for key, subs in per_option.items() if key != str(chosen)]
        if selected is not None and spec["name"] in selected and str(chosen) in per_option:
            pools = pools[:1]            # the caller said which option: only its inputs exist
        for subs in pools:
            hit = _find_spec(subs, name, selected)
            if hit is not None:
                return hit
    return None


def _spaced(class_type: str) -> str:
    """'CLIPTextEncode' -> 'clip text encode', so a query word matches a part of the name."""
    out = []
    for index, ch in enumerate(class_type):
        if ch.isupper() and index and (class_type[index - 1].islower()
                                       or (index + 1 < len(class_type) and class_type[index + 1].islower())):
            out.append(" ")
        out.append(ch)
    return "".join(out).replace("_", " ").lower()


def _type_listed(wanted: str, have: Any) -> bool:
    """A filter type against a socket type: exact member match, so 'IMAGE' does not match everything generic."""
    want = str(wanted).strip().upper()
    members = {t.strip().upper() for t in str(have).split(",")}
    return want in members or "*" in members


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def check_value(spec: Dict[str, Any], value: Any) -> Tuple[Any, Optional[Dict[str, Any]]]:
    """Check a widget value against its input spec.

    Returns the value to store (numbers given as text are converted) and, when
    it does not fit, a problem ``{code, message, ...}``.
    """
    kind = str(spec.get("type", "*")).split(",")[0].strip().upper()
    name = spec.get("name", "")
    if "options" in spec and "inputs_per_option" not in spec and kind == "COMBO":
        choices = spec["options"]
        if value in choices:
            return value, None
        if spec.get("upload") and isinstance(value, str) and value.rstrip().endswith(ANNOTATED_FOLDERS):
            return value, None          # "name.png [output]": a file in another ComfyUI folder, not in the list
        if spec.get("upload"):
            fix = ("Send the file with upload_file and use its `as_input`, or pick one that is there "
                   "(list_input_options).")
        else:
            fix = ("list_input_options lists what the server has. If this is a model file, it has to be "
                   "installed in ComfyUI: tell the user which one is missing.")
        if not choices:
            return value, {
                "code": "no_options",
                "message": f"{name}: {value!r} is not available; the server lists no choices for this input.",
                "hint": fix,
            }
        close = difflib.get_close_matches(str(value), [str(c) for c in choices], n=3, cutoff=0.6)
        return value, {
            "code": "value_not_in_list",
            "message": f"{name}: {value!r} is not one of this input's choices.",
            "closest": close,
            "options_total": len(choices),
            "hint": ("One of `closest` may be what was meant. " if close else "") + fix,
        }
    if "inputs_per_option" in spec:
        if str(value) in spec["inputs_per_option"]:
            return value, None
        return value, {
            "code": "value_not_in_list",
            "message": f"{name}: {value!r} is not one of this input's choices.",
            "closest": difflib.get_close_matches(str(value), list(spec["inputs_per_option"]), n=3, cutoff=0.3),
            "options": list(spec["inputs_per_option"]),
        }
    if kind in ("INT", "FLOAT"):
        number = value
        if isinstance(number, str):
            try:
                number = float(number) if kind == "FLOAT" or "." in number or "e" in number.lower() else int(number)
            except ValueError:
                return value, {"code": "wrong_type", "message": f"{name}: expected a number, got {value!r}."}
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            return value, {"code": "wrong_type", "message": f"{name}: expected a number, got {value!r}."}
        if kind == "INT" and isinstance(number, float):
            if number != int(number):
                return value, {"code": "wrong_type", "message": f"{name}: expected a whole number, got {value!r}."}
            number = int(number)
        low, high = spec.get("min"), spec.get("max")
        if isinstance(low, (int, float)) and number < low:
            return number, {"code": "out_of_range", "message": f"{name}: {number} is below the minimum {low}.",
                            "min": low, "max": high}
        if isinstance(high, (int, float)) and number > high:
            return number, {"code": "out_of_range", "message": f"{name}: {number} is above the maximum {high}.",
                            "min": low, "max": high}
        return number, None
    if kind == "BOOLEAN":
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true", None
        return value, {"code": "wrong_type", "message": f"{name}: expected true or false, got {value!r}."}
    if kind == "STRING":
        if isinstance(value, str):
            return value, None
        return value, {"code": "wrong_type", "message": f"{name}: expected text, got {type(value).__name__}."}
    return value, None


# ---------------------------------------------------------------------------
# A workspace graph, readable
# ---------------------------------------------------------------------------

_MODES = {0: "active", 2: "muted", 4: "bypassed"}


def _widget_values(node: Dict[str, Any], node_info: Dict[str, Any]) -> Dict[str, Any]:
    """A workspace node's widget values by input name."""
    from autograph.convert import expand_widget_names, get_widget_input_names

    values = node.get("widgets_values")
    class_type = str(node.get("type") or "")
    if isinstance(values, dict):
        return {str(k): plain(v) for k, v in values.items()}
    if not isinstance(values, list) or class_type not in node_info:
        return {}
    try:
        names = get_widget_input_names(class_type, node_info, use_api=True)
        names, vals, _positions = expand_widget_names(class_type, values, names, node_info)
    except Exception:
        return {}
    linked = {slot.get("name") for slot in node.get("inputs") or []
              if isinstance(slot, dict) and slot.get("link") is not None}
    return {name: plain(value) for name, value in zip(names, vals) if name not in linked}


def summarize(flow: Dict[str, Any], catalog: Optional[Catalog], node_ids: Optional[Sequence[Any]] = None,
              brief: bool = False) -> List[Dict[str, Any]]:
    """Every node of a workspace graph with its inputs by name, links as ``[node_id, output_index]``.

    The same notation ComfyUI's API format uses, so what is read here can be
    written back with ``add_node`` / ``set_workflow_values`` / ``connect_nodes``.
    ``missing`` lists required inputs that have neither a value nor a link.
    """
    node_info = catalog.node_info if catalog is not None else {}
    links: Dict[Any, Tuple[str, int]] = {}
    for link in flow.get("links") or []:
        if isinstance(link, (list, tuple)) and len(link) >= 5:
            links[link[0]] = (str(link[1]), int(link[2]))
        elif isinstance(link, dict) and "id" in link:
            links[link["id"]] = (str(link.get("origin_id")), int(link.get("origin_slot") or 0))
    consumers: Dict[Tuple[str, int], List[str]] = {}
    for node in flow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        for slot in node.get("inputs") or []:
            if isinstance(slot, dict) and slot.get("link") in links:
                consumers.setdefault(links[slot["link"]], []).append(f"{node.get('id')}.{slot.get('name')}")
    wanted = {str(i) for i in node_ids} if node_ids else None
    subgraphs = {str(d.get("id")): d.get("name") for d in ((flow.get("definitions") or {}).get("subgraphs") or [])
                 if isinstance(d, dict)}
    out: List[Dict[str, Any]] = []
    for node in flow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id"))
        if wanted is not None and node_id not in wanted:
            continue
        class_type = str(node.get("type") or "")
        row: Dict[str, Any] = {"id": node_id, "class_type": class_type}
        if class_type in subgraphs:
            row["subgraph"] = subgraphs[class_type]
        title = node.get("title")
        if isinstance(title, str) and title and title != class_type:
            row["title"] = title
        mode = _MODES.get(node.get("mode", 0), "active")
        if mode != "active":
            row["mode"] = mode
        inputs: Dict[str, Any] = {}
        if not brief:
            inputs.update(_widget_values(node, node_info))
        for slot in node.get("inputs") or []:
            if isinstance(slot, dict) and slot.get("link") in links:
                src, index = links[slot["link"]]
                inputs[str(slot.get("name"))] = [src, index]
        if inputs:
            row["inputs"] = inputs
        if catalog is not None and class_type in catalog:
            missing = [f"{spec['name']}:{spec['type']}" for spec in catalog.inputs(class_type, 0)
                       if spec["required"] and spec["kind"] == "connection" and spec["name"] not in inputs
                       and not spec.get("accepts_value")]
            if missing:
                row["missing"] = missing
            outputs = catalog.outputs(class_type)
        else:
            if class_type not in subgraphs and class_type not in EDITOR_ONLY and class_type not in ("Reroute", "PrimitiveNode"):
                row["unknown_type"] = True
            outputs = [{"index": i, "name": str(o.get("name")), "type": str(o.get("type"))}
                       for i, o in enumerate(node.get("outputs") or []) if isinstance(o, dict)]
        if outputs:
            row["outputs"] = [
                (f"{o['name']}:{o['type']}" if o["name"] != o["type"] else str(o["type"]))
                + (f" -> {', '.join(consumers[(node_id, o['index'])])}" if (node_id, o["index"]) in consumers else "")
                for o in outputs
            ]
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------


def _issue(code: str, message: str, node_id: Optional[str] = None, class_type: Optional[str] = None,
           input_name: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"code": code, "message": message}
    if node_id is not None:
        out["node_id"] = str(node_id)
    if class_type:
        out["class_type"] = class_type
    if input_name:
        out["input"] = input_name
    out.update({k: v for k, v in extra.items() if v not in (None, [], "")})
    return out


def unknown_type_hint(close: Sequence[str]) -> str:
    if close:
        return "Use one of `closest` if that is the node meant, or find it with list_node_types."
    return ("No node type has a similar name: it comes from a custom node that is not installed on this server "
            "(ask the user to install it), or use another node found with list_node_types.")


def _feeders(api: Dict[str, Any], catalog: Catalog, wanted_type: str, exclude: str) -> List[str]:
    """Outputs already in the graph that could feed an input of this type: 'node_id.OUTPUT'."""
    out: List[str] = []
    for node_id, node in api.items():
        if node_id == exclude or not isinstance(node, dict):
            continue
        for output in catalog.outputs(str(node.get("class_type") or "")):
            if _type_listed(wanted_type, output["type"]) and output["type"] != "*":
                out.append(f"[\"{node_id}\", {output['index']}] ({node.get('class_type')}.{output['name']})")
    return out[:6]


def _how_to_feed(api: Dict[str, Any], catalog: Catalog, wanted_type: str, node_id: str) -> Dict[str, Any]:
    """What to do about an unconnected input: outputs in the graph that fit, or how to find a node that gives the type."""
    candidates = _feeders(api, catalog, wanted_type, node_id)
    if candidates:
        return {"candidates": candidates, "hint": "Connect one of `candidates` with connect_nodes."}
    return {"hint": f"Nothing in the graph gives {wanted_type}. Find a node that does with "
                    f"list_node_types(output_type=\"{wanted_type}\"), add it and connect it."}


def validate(api: Dict[str, Any], catalog: Catalog) -> Dict[str, Any]:
    """Check an API-format graph the way ComfyUI does when it is queued, before queueing.

    Returns ``{"errors": [...], "warnings": [...]}``. Each entry has a ``code``,
    a ``message``, the ``node_id`` / ``input`` it is about and, where there is
    one, what to do about it (``closest``, ``candidates``, ``hint``).
    """
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    nodes = {str(k): v for k, v in api.items() if isinstance(v, dict) and "class_type" in v}

    for node_id, node in nodes.items():
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if class_type not in catalog:
            close = catalog.suggest(class_type)
            errors.append(_issue(
                "unknown_node_type", f"Node {node_id}: the server has no node type {class_type!r}.",
                node_id, class_type, closest=close, hint=unknown_type_hint(close),
            ))
            continue
        specs = catalog.inputs(class_type, 0)
        known: Set[str] = set()
        for spec in specs:
            known.add(spec["name"])
            name = spec["name"]
            value = inputs.get(name)
            present = name in inputs and value is not None
            if spec["kind"] == "connection_group":
                continue
            if name in inputs and value is None and spec["kind"] == "widget":
                continue        # a widget the editor fills in (a comparison view, a preview): nothing to check
            if not present:
                if spec["required"]:
                    if spec["kind"] == "connection":
                        errors.append(_issue(
                            "missing_input",
                            f"Node {node_id} ({class_type}): required input '{name}' ({spec['type']}) is not connected.",
                            node_id, class_type, name, type=spec["type"],
                            **_how_to_feed(nodes, catalog, spec["type"], node_id),
                        ))
                    else:
                        errors.append(_issue(
                            "missing_value", f"Node {node_id} ({class_type}): required input '{name}' has no value.",
                            node_id, class_type, name, default=spec.get("default"),
                        ))
                continue
            _check_input(errors, warnings, nodes, catalog, node_id, class_type, spec, value)
            if "inputs_per_option" in spec and not is_link(value):
                for sub in spec["inputs_per_option"].get(str(value), []):
                    known.add(sub["name"])
                    sub_value = inputs.get(sub["name"])
                    if sub["name"] in inputs and sub_value is not None:
                        _check_input(errors, warnings, nodes, catalog, node_id, class_type, sub, sub_value)
                    elif sub["required"] and sub["kind"] == "connection":
                        errors.append(_issue(
                            "missing_input",
                            f"Node {node_id} ({class_type}): '{sub['name']}' ({sub['type']}) is required when "
                            f"'{name}' is {value!r} and is not connected.",
                            node_id, class_type, sub["name"], type=sub["type"],
                            candidates=_feeders(nodes, catalog, sub["type"], node_id),
                        ))
        groups = [s["name"] for s in specs if s["kind"] == "connection_group" or "inputs_per_option" in s]
        for name, value in inputs.items():
            if name in known or any(name.startswith(g + ".") for g in groups):
                if is_link(value) and name not in known:
                    _check_link(errors, nodes, catalog, node_id, class_type, name,
                                (catalog.input_spec(class_type, name) or {}).get("type", "*"), value)
                continue
            warnings.append(_issue(
                "unknown_input", f"Node {node_id} ({class_type}) has no input '{name}'; ComfyUI ignores it.",
                node_id, class_type, name,
                closest=difflib.get_close_matches(name, sorted(known), n=3, cutoff=0.5),
            ))

    # What runs: only what an output node needs.
    roots = [nid for nid, node in nodes.items() if catalog.is_output_node(str(node.get("class_type") or ""))]
    # A node of an unknown type may well be an output: what feeds it is not called unused.
    unknown = [nid for nid, node in nodes.items() if str(node.get("class_type") or "") not in catalog]
    if nodes and not roots and not unknown:
        errors.append(_issue(
            "no_output_node", "The graph has no output node, so ComfyUI has nothing to run.",
            hint="Add a node that saves or previews a result (SaveImage, PreviewImage, ...) and connect it.",
        ))
    elif roots or unknown:
        used: Set[str] = set()
        stack = list(roots) + unknown
        while stack:
            current = stack.pop()
            if current in used or current not in nodes:
                continue
            used.add(current)
            for value in (nodes[current].get("inputs") or {}).values():
                if is_link(value):
                    stack.append(str(value[0]))
        unused = sorted((nid for nid in nodes if nid not in used), key=lambda n: (len(n), n))
        if unused:
            warnings.append(_issue(
                "unused_nodes",
                f"{len(unused)} node(s) do not feed any output node and will not run: {', '.join(unused[:20])}.",
                node_ids=unused[:50],
            ))
            # Problems on nodes that never run do not stop the rest.
            keep, moved = [], []
            for issue in errors:
                (moved if issue.get("node_id") in unused and issue["code"] != "unknown_node_type" else keep).append(issue)
            errors = keep
            for issue in moved:
                issue["note"] = "on a node that does not feed any output"
                warnings.append(issue)
    return {"errors": errors, "warnings": warnings}


def _check_link(errors: List[Dict[str, Any]], nodes: Dict[str, Any], catalog: Catalog, node_id: str,
                class_type: str, name: str, wanted_type: str, value: Any) -> None:
    src_id, index = str(value[0]), int(value[1])
    source = nodes.get(src_id)
    if source is None:
        errors.append(_issue(
            "dangling_link", f"Node {node_id} ({class_type}): '{name}' is linked to node {src_id}, which is not in the graph.",
            node_id, class_type, name,
        ))
        return
    src_type = str(source.get("class_type") or "")
    if src_type not in catalog:
        return
    outputs = catalog.outputs(src_type)
    if index < 0 or index >= len(outputs):
        errors.append(_issue(
            "bad_output_index",
            f"Node {node_id} ({class_type}): '{name}' is linked to output {index} of node {src_id} ({src_type}), "
            f"which has {len(outputs)} output(s).",
            node_id, class_type, name, outputs=[f"{o['index']}: {o['name']} ({o['type']})" for o in outputs],
        ))
        return
    if not types_connect(outputs[index]["type"], wanted_type):
        fitting = [o for o in outputs if types_connect(o["type"], wanted_type)]
        errors.append(_issue(
            "type_mismatch",
            f"Node {node_id} ({class_type}): '{name}' takes {wanted_type}, but it is linked to "
            f"{src_type}.{outputs[index]['name']}, which gives {outputs[index]['type']}.",
            node_id, class_type, name, expected=wanted_type, got=outputs[index]["type"],
            candidates=([f"[\"{src_id}\", {o['index']}] ({src_type}.{o['name']})" for o in fitting]
                        or _feeders(nodes, catalog, wanted_type, node_id)),
        ))


def _check_input(errors: List[Dict[str, Any]], warnings: List[Dict[str, Any]], nodes: Dict[str, Any],
                 catalog: Catalog, node_id: str, class_type: str, spec: Dict[str, Any], value: Any) -> None:
    name = spec["name"]
    if is_link(value):
        _check_link(errors, nodes, catalog, node_id, class_type, name, spec["type"], value)
        return
    if spec["kind"] == "connection":
        if spec.get("accepts_value") or str(spec["type"]).upper().startswith("COMFY_") or spec["type"] == "*":
            return          # takes a value as it is, or any type at all
        errors.append(_issue(
            "value_on_connection",
            f"Node {node_id} ({class_type}): '{name}' takes a {spec['type']} connection, not a value ({value!r}).",
            node_id, class_type, name, type=spec["type"],
            candidates=_feeders(nodes, catalog, spec["type"], node_id),
        ))
        return
    if spec.get("queue_time") and value in ("", None):
        warnings.append(_issue(
            "queue_time_input",
            f"Node {node_id} ({class_type}): '{name}' is filled in by the ComfyUI editor when it queues "
            "(a webcam frame, a recording, the 3D view) and has no value here.",
            node_id, class_type, name,
        ))
        return
    if isinstance(value, dict):
        return          # wrapped values ({"__value__": [...]}, curves): left to the server
    _fixed, problem = check_value(spec, value)
    if problem is not None:
        problem = dict(problem)
        code = problem.pop("code")
        message = problem.pop("message")
        sink = errors
        own = SELF_CHECKED.get(class_type, ())
        if own == "*" or name.split(".")[0] in own:
            # ComfyUI does not hold this input to the list: the node checks it itself.
            sink = warnings
            problem["note"] = "This node checks the value itself, so ComfyUI may accept it."
        sink.append(_issue(code, f"Node {node_id} ({class_type}): {message}", node_id, class_type, name, **problem))


# ---------------------------------------------------------------------------
# API format -> workspace graph
# ---------------------------------------------------------------------------


def looks_like_api(data: Any) -> bool:
    """True for a graph in ComfyUI's API format: ``{node_id: {"class_type": ..., "inputs": {...}}}``."""
    if not isinstance(data, dict) or not data or "nodes" in data:
        return False
    nodes = [v for v in data.values() if isinstance(v, dict)]
    return bool(nodes) and all("class_type" in v for v in nodes)


def _order(api: Dict[str, Any]) -> List[str]:
    """Node ids with sources before the nodes they feed (stable for cycles and missing ids)."""
    seen: Set[str] = set()
    out: List[str] = []

    def visit(node_id: str, trail: Tuple[str, ...]) -> None:
        if node_id in seen or node_id not in api or node_id in trail:
            return
        for value in (api[node_id].get("inputs") or {}).values():
            if is_link(value):
                visit(str(value[0]), trail + (node_id,))
        seen.add(node_id)
        out.append(node_id)

    for node_id in sorted(api, key=lambda n: (not str(n).isdigit(), int(n) if str(n).isdigit() else 0, str(n))):
        visit(str(node_id), ())
    return out


def build_flow(api: Dict[str, Any], catalog: Catalog) -> Tuple[Any, Dict[str, str], List[Dict[str, Any]]]:
    """Build a workspace graph from an API-format graph with autograph's builder.

    This is how an assistant writes a whole workflow in one go: it knows the
    API format (``{"1": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "steps": 20}}}``),
    and the result is a real workspace graph that opens in ComfyUI.

    Returns the Flow, a map from the given node ids to the ids in the Flow, and
    the problems met (unknown node types, inputs that could not be set).
    """
    import warnings as _warnings

    import autograph

    api = {str(k): v for k, v in api.items() if isinstance(v, dict) and "class_type" in v}
    flow = autograph.Flow.create(node_info=catalog.node_info)
    problems: List[Dict[str, Any]] = []
    refs: Dict[str, Any] = {}
    id_map: Dict[str, str] = {}
    literals: List[Tuple[str, str, str, Any]] = []
    for node_id in _order(api):
        node = api[node_id]
        try:
            class_type = catalog.resolve(str(node.get("class_type") or ""))
        except LookupError as exc:
            problems.append(_issue("unknown_node_type", str(exc).strip("'\""), node_id, str(node.get("class_type")),
                                   closest=catalog.suggest(str(node.get("class_type") or ""))))
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        values: Dict[str, Any] = {}
        for name, value in inputs.items():
            if is_link(value) or value is None:
                continue
            spec = catalog.input_spec(class_type, name, inputs)
            if spec is None:
                problems.append(_issue(
                    "unknown_input", f"Node {node_id} ({class_type}) has no input '{name}'; left out.",
                    node_id, class_type, name,
                    closest=difflib.get_close_matches(name, [s["name"] for s in catalog.inputs(class_type, 0)], n=3),
                ))
                continue
            if spec["kind"] != "widget":
                primitive = primitive_for(spec, value, catalog) if spec.get("accepts_value") else None
                if primitive is None:
                    problems.append(_issue(
                        "value_on_connection",
                        f"Node {node_id} ({class_type}): '{name}' takes a {spec['type']} connection, not a value; left out.",
                        node_id, class_type, name,
                    ))
                else:
                    literals.append((node_id, name, primitive, value))
                continue
            values[name] = value
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            ref = flow.add_node(class_type, **values)
        for warning in caught:
            problems.append(_issue("builder_warning", str(warning.message), node_id, class_type))
        title = (node.get("_meta") or {}).get("title") if isinstance(node.get("_meta"), dict) else None
        if isinstance(title, str) and title and title != class_type:
            ref._data_ref()["title"] = title
        refs[node_id] = ref
        id_map[node_id] = str(ref.addr)
    for node_id, ref in refs.items():
        class_type = str(ref._data_ref().get("type"))
        for name, value in (api[node_id].get("inputs") or {}).items():
            if not is_link(value):
                continue
            source = refs.get(str(value[0]))
            if source is None:
                problems.append(_issue(
                    "dangling_link", f"Node {node_id} ({class_type}): '{name}' is linked to node {value[0]}, "
                    "which is not in the graph; left unconnected.", node_id, class_type, name,
                ))
                continue
            try:
                ref.connect(name, source, int(value[1]))
            except Exception as exc:
                problems.append(_issue("cannot_connect", f"Node {node_id} ({class_type}): '{name}': {exc}",
                                       node_id, class_type, name))
    # A value given for an input that only takes a connection (forceInput) is held by a primitive node.
    for node_id, name, primitive, value in literals:
        if node_id not in refs:
            continue
        try:
            source = flow.add_node(primitive, value=value)
            refs[node_id].connect(name, source, 0)
        except Exception as exc:
            problems.append(_issue("cannot_connect", f"Node {node_id}: '{name}': {exc}", node_id, None, name))
    id_map = _keep_ids(flow, id_map)
    try:
        flow.auto_layout()
    except Exception:
        pass
    return flow, id_map, problems


def _keep_ids(flow: Any, id_map: Dict[str, str]) -> Dict[str, str]:
    """Give the built nodes the ids they had in the API graph, where those are whole numbers.

    What was written as node "9" is then node 9 in the session, in ComfyUI's
    errors and in its results. Nodes added on top (primitives) get ids after them.
    """
    if not id_map or not all(old.isdigit() for old in id_map):
        return id_map
    raw = flow._flow if hasattr(flow, "_flow") else flow
    wanted = {int(new): int(old) for old, new in id_map.items()}
    if all(now == then for now, then in wanted.items()):
        return id_map
    spare = max(wanted.values()) + 1
    for node in raw.get("nodes") or []:
        if isinstance(node, dict) and node.get("id") not in wanted:
            wanted[node.get("id")] = spare
            spare += 1
    for node in raw.get("nodes") or []:
        if isinstance(node, dict):
            node["id"] = wanted[node["id"]]
    for link in raw.get("links") or []:
        if isinstance(link, list) and len(link) >= 5:
            link[1] = wanted.get(link[1], link[1])
            link[3] = wanted.get(link[3], link[3])
    raw["last_node_id"] = max(wanted.values())
    try:
        object.__delattr__(raw, "_AUTOGRAPH_dag_cache")
    except (AttributeError, TypeError):
        pass
    return {old: old for old in id_map}


_PRIMITIVES = (("BOOLEAN", bool, "PrimitiveBoolean"), ("INT", int, "PrimitiveInt"),
               ("FLOAT", (int, float), "PrimitiveFloat"), ("STRING", str, "PrimitiveString"))


def primitive_for(spec: Dict[str, Any], value: Any, catalog: Catalog) -> Optional[str]:
    """The primitive node type that can hold ``value`` for a connection input of this type, if any."""
    wanted = {t.strip().upper() for t in str(spec.get("type", "")).split(",")}
    for type_name, python_type, class_type in _PRIMITIVES:
        if type_name not in wanted and "*" not in wanted:
            continue
        if isinstance(value, bool) and type_name != "BOOLEAN":
            continue
        if isinstance(value, python_type) and class_type in catalog:
            if class_type == "PrimitiveString" and "\n" in value and "PrimitiveStringMultiline" in catalog:
                return "PrimitiveStringMultiline"
            return class_type
    return None
