"""autoflow package

Public API is re-exported here for convenience.

.. deprecated:: 1.5.1
    ``comfyui-autoflow`` has been renamed to ``comfyui-autograph``.
    Install the new package: ``pip install comfyui-autograph``
"""

import warnings as _warnings

_warnings.warn(
    "comfyui-autoflow has been renamed to comfyui-autograph. "
    "This package (comfyui-autoflow) will no longer receive updates. "
    "Please switch: pip install comfyui-autograph",
    DeprecationWarning,
    stacklevel=2,
)

from .version import __version__
from .model_layer import (  # noqa: F401
    ApiFlow,
    Flow,
    Workflow,
    NodeInfo,
)
from .models import WidgetValue  # noqa: F401
from .connection import Connection  # noqa: F401
from .convert import (  # noqa: F401
    ConvertResult,
    WorkflowConverterError,
    NodeInfoError,
    ErrorSeverity,
    ErrorCategory,
    ConversionError,
    ConversionResult,
    comfyui_available,
)
from .results import (  # noqa: F401
    SubmissionResult,
    ImagesResult,
    ImageResult,
)
from .convert import (  # noqa: F401
    convert,
    convert_with_errors,
)
from .api import main  # noqa: F401

from .ws import (  # noqa: F401
    WsEvent,
    ProgressPrinter,
    chain_callbacks,
)
from .map import (  # noqa: F401
    api_mapping,
    map_strings,
    map_paths,
    force_recompute,
)


__all__ = [
    "__version__",
    "ApiFlow",
    "Flow",
    "Workflow",
    "NodeInfo",
    "ConvertResult",
    "SubmissionResult",
    "ImagesResult",
    "ImageResult",
    "WorkflowConverterError",
    "NodeInfoError",
    "ErrorSeverity",
    "ErrorCategory",
    "ConversionError",
    "ConversionResult",
    "comfyui_available",
    "convert",
    "convert_with_errors",
    "main",
    "WsEvent",
    "ProgressPrinter",
    "chain_callbacks",
    "api_mapping",
    "map_strings",
    "map_paths",
    "force_recompute",
    "WidgetValue",
    "Connection",
]
