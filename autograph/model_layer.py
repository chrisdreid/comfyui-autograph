"""autoflow.model_layer

Model-layer selector.

The default layer is ``flowtree`` — navigation-first wrappers with Tree/DictView
drilling and polymorphic .load().  The ``models`` layer is the legacy dict-subclass
implementation.

Controlled by env var:
  AUTOFLOW_MODEL_LAYER=models|flowtree  (default: flowtree)
"""

from __future__ import annotations

import os
from typing import Any, Tuple, Type


def model_layer_name() -> str:
    v = os.environ.get("AUTOFLOW_MODEL_LAYER", "flowtree")
    v = (v or "flowtree").strip().lower()
    if v in ("model", "models", "legacy"):
        return "models"
    if v in ("flowtree", "tree", "nav"):
        return "flowtree"
    # Fail fast so users don't think they're in a mode they aren't.
    raise ValueError("AUTOFLOW_MODEL_LAYER must be 'models' or 'flowtree'")


def get_models():
    name = model_layer_name()
    if name == "flowtree":
        from .flowtree import ApiFlow, Flow, NodeInfo, Workflow  # noqa: F401

        return Flow, ApiFlow, NodeInfo, Workflow

    from .models import ApiFlow, Flow, NodeInfo, Workflow  # noqa: F401

    return Flow, ApiFlow, NodeInfo, Workflow


Flow, ApiFlow, NodeInfo, Workflow = get_models()

__all__ = ["model_layer_name", "get_models", "Flow", "ApiFlow", "NodeInfo", "Workflow"]


