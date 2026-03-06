#!/usr/bin/env python3
"""autoflow.api

Thin compatibility façade.

The implementation was split across:
- autoflow.models   (Flow/ApiFlow/NodeInfo/Workflow + drilling helpers)
- autoflow.convert  (conversion core + errors)
- autoflow.results  (submit + outputs + save helpers)
- autoflow.net/pngmeta/defaults (stdlib helpers)

This module re-exports the public API for backwards compatibility.
"""

from __future__ import annotations

from .version import __version__

# Public models
from .model_layer import (  # noqa: F401
    ApiFlow,
    Flow,
    Workflow,
    NodeInfo,
)
from .models import DictView  # noqa: F401
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

# Conversion functions
from .convert import (  # noqa: F401
    convert,
    convert_with_errors,
    convert_workflow,
    convert_workflow_with_errors,
    workflow_to_api_format,
    workflow_to_api_format_with_errors,
    validate_workflow_data,
    flatten_subgraphs,
    resolve_node_info,
    fetch_node_info,
    fetch_node_info_from_url,
    load_workflow_from_file,
    save_workflow_to_file,
    load_node_info_from_file,
    save_node_info_to_file,
    get_widget_input_names,
    align_widgets_values,
)

# Submission + outputs
from .results import (  # noqa: F401
    _sanitize_api_prompt,
    SubmissionResult,
    FilesResult,
    FileResult,
    ImagesResult,
    ImageResult,
)

# Helpers that were historically reachable from autoflow.api
from .defaults import (  # noqa: F401
    DEFAULT_HTTP_TIMEOUT_S,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_WAIT_TIMEOUT_S,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_SUBMIT_CLIENT_ID,
)
from .net import http_json, comfy_url, resolve_comfy_server_url  # noqa: F401
from .pngmeta import (  # noqa: F401
    parse_png_metadata_from_bytes,
    extract_png_comfyui_metadata,
    looks_like_json,
    looks_like_path,
    is_png_bytes,
    is_png_path,
)

# CLI entrypoint
from .cli import main  # noqa: F401

__all__ = [
    "__version__",
    # models
    "ApiFlow",
    "Flow",
    "Workflow",
    "NodeInfo",
    "ConvertResult",
    "SubmissionResult",
    "FilesResult",
    "FileResult",
    "ImagesResult",
    "ImageResult",
    "DictView",
    # conversion + errors
    "WorkflowConverterError",
    "NodeInfoError",
    "ErrorSeverity",
    "ErrorCategory",
    "ConversionError",
    "ConversionResult",
    "comfyui_available",
    "convert",
    "convert_with_errors",
    "convert_workflow",
    "convert_workflow_with_errors",
    "workflow_to_api_format",
    "workflow_to_api_format_with_errors",
    "validate_workflow_data",
    "flatten_subgraphs",
    "resolve_node_info",
    "fetch_node_info",
    "fetch_node_info_from_url",
    "load_workflow_from_file",
    "save_workflow_to_file",
    "load_node_info_from_file",
    "save_node_info_to_file",
    "get_widget_input_names",
    "align_widgets_values",
    # helpers
    "http_json",
    "comfy_url",
    "resolve_comfy_server_url",
    "parse_png_metadata_from_bytes",
    "extract_png_comfyui_metadata",
    "looks_like_json",
    "looks_like_path",
    "is_png_bytes",
    "is_png_path",
    # cli
    "main",
]


