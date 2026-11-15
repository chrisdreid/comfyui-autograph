"""autograph.connection

Shared primitives for workflow connection inspection and manipulation.

Provides:
- Connection dataclass — represents one wired connection
- get_connection_input_names() — input names that need connections (not widgets)
- get_output_slots() — output slot definitions from node_info
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional, Tuple, Union


def _is_dict_like(x: Any) -> bool:
    """True for dict, DictView, or any Mapping-like object."""
    return hasattr(x, 'get') and hasattr(x, 'items')


@dataclasses.dataclass
class Connection:
    """Represents a single wired connection between two nodes.

    For *input* connections (node.connections):
        input_name, from_node_id, from_output, from_class_type are populated.

    For *output/downstream* connections (node.downstream):
        to_node_id, to_input_name, to_class_type are also populated.
    """

    input_name: str                         # destination input slot name
    from_node_id: str                       # source node ID
    from_output: Union[int, str]            # source output slot (index or name)
    from_class_type: str = ""               # source node's class_type

    to_node_id: Optional[str] = None        # destination node ID (for downstream)
    to_input_name: Optional[str] = None     # destination input name (for downstream)
    to_class_type: Optional[str] = None     # destination class_type (for downstream)

    def __repr__(self) -> str:
        parts = [f"input={self.input_name!r}"]
        parts.append(f"from={self.from_node_id}[{self.from_output}]")
        if self.from_class_type:
            parts.append(f"type={self.from_class_type!r}")
        if self.to_node_id is not None:
            parts.append(f"to={self.to_node_id}")
        return f"Connection({', '.join(parts)})"


# Type names that render as widgets in the frontend (everything else with a
# plain string type is a connection slot).
WIDGET_TYPE_NAMES = {"INT", "FLOAT", "BOOLEAN", "STRING", "COMBO", "COMFY_DYNAMICCOMBO_V3"}

# Option-dict keys that only widget specs carry (legacy/custom widget types
# like "INT:seed" or "EASY_COMBO" declare widget config instead of a
# recognizable type name).
WIDGET_OPTION_KEYS = {
    "min", "max", "step", "round", "multiline", "placeholder",
    "dynamicPrompts", "control_after_generate", "options",
    "image_upload", "video_upload", "audio_upload", "file_upload", "text_upload",
}

AUTOGROW_TYPE = "COMFY_AUTOGROW_V3"
DYNAMIC_COMBO_TYPE = "COMFY_DYNAMICCOMBO_V3"

# Values of the combo the frontend adds after an INT widget that declares
# "control_after_generate" (seeds). It is serialized in widgets_values but is
# not an input of the node.
CONTROL_AFTER_GENERATE_CHOICES = ("fixed", "increment", "decrement", "randomize")


def _is_sequence(x: Any) -> bool:
    """True for list, tuple, or a ListView-like wrapper."""
    return isinstance(x, (list, tuple)) or (
        not isinstance(x, (str, bytes, dict)) and hasattr(x, "__getitem__") and hasattr(x, "__len__") and not _is_dict_like(x)
    )


def _spec_options(spec: Any) -> Any:
    """The options dict of an input spec ({} when it has none)."""
    if _is_sequence(spec) and len(spec) >= 2 and _is_dict_like(spec[1]):
        return spec[1]
    return {}


def is_widget_spec(spec: Any) -> bool:
    """True if a node_info input spec is a widget (a value stored in ``widgets_values``).

    This is the one classification shared by the converter and the builder;
    every input that is not a widget is a connection.

    Widgets:
    - [[choice1, choice2, ...], {...}]            — classic combo
    - ["INT" | "FLOAT" | "BOOLEAN" | "STRING" | "COMBO" | "COMFY_DYNAMICCOMBO_V3", ...]
      (V3 schemas emit plain widgets as e.g. ["BOOLEAN", {}])
    - [TYPE, {"socketless": ...}] / [TYPE, {"widgetType": ...}]
      (COLOR, CURVE, custom editor types; multi-type inputs like "FLOAT,INT")
    - legacy/custom widget types ("INT:seed", "EASY_COMBO") whose options
      carry widget config: a non-null default, min/max/step, options, upload flags

    Connections:
    - anything with ``forceInput: true``
    - plain data types (IMAGE, LATENT, ...), COMFY_MATCHTYPE_V3,
      COMFY_AUTOGROW_V3, unmarked multi-types ("BOUNDING_BOX,STRING")
    """
    if not _is_sequence(spec) or len(spec) == 0:
        return False
    head = spec[0]
    opts = _spec_options(spec)
    if opts.get("forceInput"):
        return False
    if _is_sequence(head):
        return True
    if "socketless" in opts or opts.get("widgetType"):
        return True
    if isinstance(head, str) and head.upper() in WIDGET_TYPE_NAMES:
        return True
    if opts.get("default") is not None or any(k in opts for k in WIDGET_OPTION_KEYS):
        return True
    return False


def _is_connection_only_input(spec: list) -> bool:
    """True if a node_info input spec is a connection-only input (not a widget).

    The complement of :func:`is_widget_spec` for well-formed specs.
    """
    if not _is_sequence(spec) or len(spec) == 0:
        return False
    return not is_widget_spec(spec)


def get_connection_input_names(
    class_type: str,
    node_info: Dict[str, Any],
) -> List[str]:
    """Return input names that require connections (not widget values).

    This is the complement of ``get_widget_input_names(class_type, node_info, use_api=True)``.

    Args:
        class_type: The node class type (e.g. "KSampler").
        node_info: The full node_info dict (all class types).

    Returns:
        List of input names that are connection-only inputs, in definition order.

    Raises:
        KeyError: If class_type is not found in node_info.
    """
    type_info = node_info.get(class_type)
    if type_info is None:
        raise KeyError(f"Node class '{class_type}' not found in node_info")

    inputs_def = type_info.get("input", {})
    if not _is_dict_like(inputs_def):
        return []

    connection_names: List[str] = []
    for section in ["required", "optional"]:
        section_inputs = inputs_def.get(section, {})
        if not _is_dict_like(section_inputs):
            continue
        for name, spec in section_inputs.items():
            if not isinstance(spec, (list, tuple)) or len(spec) == 0:
                continue
            if _is_connection_only_input(spec):
                connection_names.append(name)
    return connection_names


def get_output_slots(
    class_type: str,
    node_info: Dict[str, Any],
) -> List[Tuple[int, str, str]]:
    """Return output slot definitions for a node type.

    Args:
        class_type: The node class type.
        node_info: The full node_info dict.

    Returns:
        List of (slot_index, output_name, output_type) tuples.

    Raises:
        KeyError: If class_type is not found in node_info.
    """
    type_info = node_info.get(class_type)
    if type_info is None:
        raise KeyError(f"Node class '{class_type}' not found in node_info")

    outputs = type_info.get("output", [])
    output_names = type_info.get("output_name", [])

    slots: List[Tuple[int, str, str]] = []
    for i, out_type in enumerate(outputs):
        name = output_names[i] if i < len(output_names) else str(out_type)
        slots.append((i, str(name), str(out_type)))
    return slots


def get_all_input_names(
    class_type: str,
    node_info: Dict[str, Any],
) -> List[str]:
    """Return all input names (both connections and widgets), in definition order.

    Args:
        class_type: The node class type.
        node_info: The full node_info dict.

    Returns:
        List of all input names.

    Raises:
        KeyError: If class_type is not found in node_info.
    """
    type_info = node_info.get(class_type)
    if type_info is None:
        raise KeyError(f"Node class '{class_type}' not found in node_info")

    inputs_def = type_info.get("input", {})
    if not _is_dict_like(inputs_def):
        return []

    names: List[str] = []
    for section in ["required", "optional"]:
        section_inputs = inputs_def.get(section, {})
        if not _is_dict_like(section_inputs):
            continue
        for name in section_inputs:
            names.append(name)
    return names


def default_for_spec(spec: Any) -> Any:
    """The value a freshly created widget holds: its declared default, else what the frontend shows.

    A choice widget starts on its first option, a text widget empty, a number
    at zero (or its minimum), a toggle off. Only custom widget types without a
    default yield None. A widget never starts as None when its type is known:
    ``widgets_values`` is positional, and a hole there shifts every later value
    when the workflow is converted.
    """
    if not _is_sequence(spec) or len(spec) == 0:
        return None
    head = spec[0]
    opts = _spec_options(spec)
    if opts.get("default") is not None:
        return opts["default"]
    if _is_sequence(head):
        return head[0] if len(head) > 0 else None
    if not isinstance(head, str):
        return None
    kind = opts.get("widgetType") if isinstance(opts.get("widgetType"), str) else head
    kind = kind.split(",")[0].strip().upper()
    if kind == "COMBO":
        options = opts.get("options") or []
        return options[0] if len(options) > 0 else None
    if kind == DYNAMIC_COMBO_TYPE:
        for option in opts.get("options") or []:
            if _is_dict_like(option):
                return option.get("key")
        return None
    if kind == "STRING":
        return ""
    if kind == "BOOLEAN":
        return False
    if kind in ("INT", "FLOAT"):
        low = opts.get("min")
        value = low if isinstance(low, (int, float)) and not isinstance(low, bool) and low > 0 else 0
        return float(value) if kind == "FLOAT" else int(value)
    return None


def _find_top_level_spec(class_type: str, input_name: str, node_info: Dict[str, Any]) -> Any:
    type_info = node_info.get(class_type)
    if type_info is None:
        return None
    inputs_def = type_info.get("input", {})
    if not _is_dict_like(inputs_def):
        return None
    for section in ["required", "optional"]:
        section_inputs = inputs_def.get(section, {})
        if _is_dict_like(section_inputs) and input_name in section_inputs:
            return section_inputs[input_name]
    return None


def get_input_default(
    class_type: str,
    input_name: str,
    node_info: Dict[str, Any],
) -> Any:
    """Return the default value for a widget input, or None for connection inputs.

    For combo inputs (list of choices), returns the first choice.
    """
    spec = _find_top_level_spec(class_type, input_name, node_info)
    if not _is_sequence(spec) or len(spec) == 0 or not is_widget_spec(spec):
        return None
    return default_for_spec(spec)


def dynamic_combo_options(spec: Any) -> List[Any]:
    """The option dicts of a COMFY_DYNAMICCOMBO_V3 spec ([] for anything else)."""
    if not _is_sequence(spec) or len(spec) == 0 or spec[0] != DYNAMIC_COMBO_TYPE:
        return []
    options = _spec_options(spec).get("options")
    if not _is_sequence(options):
        return []
    return [o for o in options if _is_dict_like(o)]


def option_input_specs(option: Any) -> List[Tuple[str, Any]]:
    """(name, spec) pairs a dynamic-combo option contributes, required first."""
    out: List[Tuple[str, Any]] = []
    inputs_def = option.get("inputs") if _is_dict_like(option) else None
    if _is_dict_like(inputs_def):
        for section in ("required", "optional"):
            section_inputs = inputs_def.get(section)
            if _is_dict_like(section_inputs):
                out.extend(section_inputs.items())
    return out


def autogrow_member_spec(spec: Any) -> Any:
    """The spec each member of a COMFY_AUTOGROW_V3 group has (None for anything else)."""
    if not _is_sequence(spec) or len(spec) == 0 or spec[0] != AUTOGROW_TYPE:
        return None
    template = _spec_options(spec).get("template")
    inputs_def = template.get("input") if _is_dict_like(template) else None
    if _is_dict_like(inputs_def):
        for section in ("required", "optional"):
            section_inputs = inputs_def.get(section)
            if _is_dict_like(section_inputs):
                for _name, member in section_inputs.items():
                    return member
    return None


def autogrow_member_names(spec: Any, count: Optional[int] = None) -> List[str]:
    """Member names of a COMFY_AUTOGROW_V3 group: its ``names``, or ``<prefix><index>``.

    Without ``count``, as many as a new node shows (``min``, at least one).
    """
    if not _is_sequence(spec) or len(spec) == 0 or spec[0] != AUTOGROW_TYPE:
        return []
    template = _spec_options(spec).get("template")
    if not _is_dict_like(template):
        return []
    low = template.get("min")
    wanted = count if count is not None else max(int(low) if isinstance(low, int) else 0, 1)
    names = template.get("names")
    if _is_sequence(names):
        return [str(n) for n in names][:wanted]
    prefix = template.get("prefix")
    if isinstance(prefix, str):
        return [f"{prefix}{i}" for i in range(wanted)]
    return []


def _is_autogrow_member(spec: Any, member: str) -> bool:
    template = _spec_options(spec).get("template")
    if not _is_dict_like(template):
        return False
    names = template.get("names")
    if _is_sequence(names):
        return member in [str(n) for n in names]
    prefix = template.get("prefix")
    if isinstance(prefix, str) and member.startswith(prefix) and member[len(prefix):].isdigit():
        high = template.get("max")
        return not isinstance(high, int) or int(member[len(prefix):]) < high
    return False


def _resolve_dotted(specs: List[Tuple[str, Any]], name: str) -> Any:
    lookup = dict(specs)
    if name in lookup:
        return lookup[name]
    # Longest matching parent first: input names may themselves contain dots.
    parts = name.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        parent, rest = ".".join(parts[:cut]), ".".join(parts[cut:])
        spec = lookup.get(parent)
        if spec is None:
            continue
        if _is_sequence(spec) and len(spec) > 0 and spec[0] == AUTOGROW_TYPE:
            return autogrow_member_spec(spec) if _is_autogrow_member(spec, rest) else None
        for option in dynamic_combo_options(spec):
            found = _resolve_dotted(option_input_specs(option), rest)
            if found is not None:
                return found
    return None


def resolve_input_spec(class_type: str, input_name: str, node_info: Dict[str, Any]) -> Any:
    """The spec of any input a node can have, including the ones that only exist by name.

    Besides the inputs listed in node_info this resolves

    - members of an autogrow group: ``"values.a"``, ``"images.image0"``
    - inputs of a dynamic-combo option: ``"resize_type.width"``, ``"model.mask"``
    - and their combinations: ``"model.images.image_1"``

    Returns None when the node has no such input.
    """
    type_info = node_info.get(class_type)
    if type_info is None:
        return None
    inputs_def = type_info.get("input", {})
    if not _is_dict_like(inputs_def):
        return None
    specs: List[Tuple[str, Any]] = []
    for section in ["required", "optional"]:
        section_inputs = inputs_def.get(section, {})
        if _is_dict_like(section_inputs):
            specs.extend(section_inputs.items())
    return _resolve_dotted(specs, input_name)


def slot_type_for_spec(spec: Any) -> str:
    """The type string an input slot for this spec carries in a workspace file."""
    if _is_sequence(spec) and len(spec) > 0:
        head = spec[0]
        if isinstance(head, str):
            return head
        if _is_sequence(head):
            return "COMBO"
    return "*"


def get_promotable_attr_names(
    class_type: str,
    node_info: Dict[str, Any],
) -> Dict[str, str]:
    """Return attr names that can be promoted to input slots, mapped to their types.

    These are the widget/attr inputs (not connection-only) that can be
    converted to connectable input slots via ``to_input()`` or auto-promotion.

    Args:
        class_type: The node class type.
        node_info: The full node_info dict.

    Returns:
        Dict mapping promotable attr name → slot type (e.g. ``{"width": "INT", "seed": "INT"}``).
    """
    type_info = node_info.get(class_type)
    if type_info is None:
        return {}

    inputs_def = type_info.get("input", {})
    if not _is_dict_like(inputs_def):
        return {}

    promotable: Dict[str, str] = {}
    for section in ["required", "optional"]:
        section_inputs = inputs_def.get(section, {})
        if not _is_dict_like(section_inputs):
            continue
        for name, spec in section_inputs.items():
            if not isinstance(spec, (list, tuple)) or len(spec) == 0:
                continue
            if _is_connection_only_input(spec):
                continue
            # Widget/attr — determine its type string
            head = spec[0]
            if isinstance(head, str):
                promotable[name] = head
            elif isinstance(head, (list, tuple)):
                # Classic combo: [[choice1, choice2], ...]
                promotable[name] = "COMBO"
            else:
                promotable[name] = "STRING"
    return promotable
