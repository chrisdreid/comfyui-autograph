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


def _is_connection_only_input(spec: list) -> bool:
    """True if a node_info input spec represents a connection-only input (not a widget).

    Connection-only patterns:
    - [TYPE_NAME]                           — single string, no options
    - [TYPE_NAME, {"tooltip": "..."}]       — string + tooltip-only dict
    - [TYPE_NAME, {}]                       — string + empty dict
    - Any input with forceInput: true       — widget forced to connection slot

    NOT connection-only (these are widgets):
    - ["COMBO", {"options": [...]}]         — new-style combo widget
    - [[choice1, choice2, ...], {...}]      — classic combo widget
    - [TYPE, {"default": val, ...}]         — scalar widget
    """
    if not isinstance(spec, (list, tuple)) or len(spec) == 0:
        return False

    # forceInput: true → always a connection, even if it looks like a widget
    if len(spec) >= 2 and _is_dict_like(spec[1]) and spec[1].get("forceInput"):
        return True

    l = len(spec)
    if l == 1 and isinstance(spec[0], str):
        # "COMBO" alone is ambiguous but treat as connection (no options provided)
        return spec[0] != "COMBO"
    if l == 2 and isinstance(spec[0], str) and _is_dict_like(spec[1]):
        # "COMBO" with options → widget, not connection
        if spec[0] == "COMBO":
            return False
        opts = spec[1]
        if not opts or (len(opts) == 1 and "tooltip" in opts):
            return True
    return False


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


def get_input_default(
    class_type: str,
    input_name: str,
    node_info: Dict[str, Any],
) -> Any:
    """Return the default value for a widget input, or None for connection inputs.

    For combo inputs (list of choices), returns the first choice.
    """
    type_info = node_info.get(class_type)
    if type_info is None:
        return None

    inputs_def = type_info.get("input", {})
    if not _is_dict_like(inputs_def):
        return None

    for section in ["required", "optional"]:
        section_inputs = inputs_def.get(section, {})
        if not _is_dict_like(section_inputs):
            continue
        if input_name not in section_inputs:
            continue
        spec = section_inputs[input_name]
        if not isinstance(spec, (list, tuple)) or len(spec) == 0:
            return None
        if _is_connection_only_input(spec):
            return None
        # New-style COMBO: ["COMBO", {"options": [...], "default": "..."}]
        if isinstance(spec[0], str) and spec[0] == "COMBO":
            if len(spec) >= 2 and _is_dict_like(spec[1]):
                opts = spec[1]
                if "default" in opts:
                    return opts["default"]
                options = opts.get("options", [])
                return options[0] if options else None
            return None
        # Classic combo widget: [[choice1, choice2, ...], {...}]
        if isinstance(spec[0], (list, tuple)) and len(spec[0]) > 0:
            return spec[0][0]
        # Scalar widget with default: [TYPE, {"default": val, ...}]
        if len(spec) >= 2 and _is_dict_like(spec[1]):
            return spec[1].get("default")
        return None
    return None


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
