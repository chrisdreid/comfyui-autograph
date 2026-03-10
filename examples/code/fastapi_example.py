#!/usr/bin/env python3
"""
FastAPI Integration Example with Enhanced Error Handling

This example demonstrates how to integrate the autograph module with FastAPI
to provide structured error responses and proper HTTP status codes.

Usage:
    pip install fastapi uvicorn
    python fastapi_example.py
    # Then visit http://localhost:8000/docs (or whatever AUTOGRAPH_PUBLIC_BASE_URL resolves to)
"""

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List, Union
import json
import os
import argparse
from pathlib import Path

# Import the converter with error handling
from autograph import __version__ as AUTOGRAPH_version
from autograph import Flow, ErrorCategory, ErrorSeverity

app = FastAPI(
    title="ComfyUI Workflow to API Converter",
    description="Convert ComfyUI workflow JSON to API format with comprehensive error handling",
    version=AUTOGRAPH_version
)

class ConversionErrorResponse(BaseModel):
    """Error information for API responses."""
    category: str
    severity: str
    message: str
    node_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

class WorkflowRequest(BaseModel):
    """Request model for workflow conversion."""
    workflow_data: Dict[str, Any] = Field(..., description="ComfyUI workflow data")
    node_info: Optional[Union[Dict[str, Any], str]] = Field(
        None,
        description="Node info (optional). Can be a JSON object, a file path, or an http(s) URL.",
    )
    server_url: Optional[str] = Field(None, description="ComfyUI server URL (optional)")
    include_meta: bool = Field(False, description="Include _meta fields in output")
    timeout: int = Field(30, description="HTTP timeout in seconds", ge=1, le=300)

class ConversionResponse(BaseModel):
    """Response model for successful conversions."""
    success: bool
    api_data: Optional[Dict[str, Any]] = None
    processed_nodes: int = 0
    skipped_nodes: int = 0
    total_nodes: int = 0
    errors: List[ConversionErrorResponse] = []
    warnings: List[ConversionErrorResponse] = []

def convert_error_to_response(error) -> ConversionErrorResponse:
    """Convert internal error format to API response format."""
    return ConversionErrorResponse(
        category=error.category.value,
        severity=error.severity.value,
        message=error.message,
        node_id=error.node_id,
        details=error.details
    )

def determine_http_status(result) -> int:
    """Determine appropriate HTTP status code based on conversion result."""
    if not result.success:
        # Check for critical errors
        has_critical = any(error.severity == ErrorSeverity.CRITICAL for error in result.errors)
        if has_critical:
            # Check error categories for specific status codes
            for error in result.errors:
                if error.category == ErrorCategory.VALIDATION:
                    return status.HTTP_400_BAD_REQUEST
                elif error.category == ErrorCategory.NETWORK:
                    return status.HTTP_502_BAD_GATEWAY
                elif error.category == ErrorCategory.IO:
                    return status.HTTP_404_NOT_FOUND
            return status.HTTP_500_INTERNAL_SERVER_ERROR
        else:
            # Non-critical errors but conversion failed
            return status.HTTP_422_UNPROCESSABLE_ENTITY
    
    # Success or partial success
    if result.errors:
        return status.HTTP_206_PARTIAL_CONTENT  # Partial success
    elif result.warnings:
        return status.HTTP_200_OK  # Success with warnings
    else:
        return status.HTTP_200_OK  # Complete success

@app.post("/convert-workflow", response_model=ConversionResponse)
async def convert_workflow_endpoint(request: WorkflowRequest):
    """
    Convert ComfyUI workflow to API format.
    
    Returns:
    - 200: Successful conversion
    - 206: Partial success (some nodes failed but others succeeded)
    - 400: Invalid workflow data or parameters
    - 404: File not found (when using file paths)
    - 422: Conversion failed but input was valid
    - 500: Internal server error
    - 502: Network error (when connecting to ComfyUI server)
    """
    try:
        # Convert workflow with structured error handling
        result = Flow.load(request.workflow_data).convert_with_errors(
            node_info=request.node_info,
            server_url=request.server_url,
            timeout=request.timeout,
            include_meta=request.include_meta,
        )
        
        # Convert errors and warnings to API format
        api_errors = [convert_error_to_response(error) for error in result.errors]
        api_warnings = [convert_error_to_response(warning) for warning in result.warnings]
        
        # Create response
        response = ConversionResponse(
            success=result.success,
            api_data=result.data,
            processed_nodes=result.processed_nodes,
            skipped_nodes=result.skipped_nodes,
            total_nodes=result.total_nodes,
            errors=api_errors,
            warnings=api_warnings
        )
        
        # Determine appropriate HTTP status code
        http_status = determine_http_status(result)
        
        return JSONResponse(
            status_code=http_status,
            content=response.dict()
        )
        
    except Exception as e:
        # Catch any unexpected errors
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Unexpected server error",
                "message": str(e),
                "type": type(e).__name__
            }
        )

@app.post("/convert-workflow-file")
async def convert_workflow_file_endpoint(
    workflow_file: str,
    node_info_file: Optional[str] = None,
    server_url: Optional[str] = None,
    include_meta: bool = False,
    timeout: int = 30
):
    """
    Convert workflow from file paths.
    
    This endpoint accepts file paths instead of JSON data,
    useful for server-side file processing.
    """
    try:
        result = Flow.load(workflow_file).convert_with_errors(
            node_info=node_info_file,
            server_url=server_url,
            timeout=timeout,
            include_meta=include_meta,
        )
        
        api_errors = [convert_error_to_response(error) for error in result.errors]
        api_warnings = [convert_error_to_response(warning) for warning in result.warnings]
        
        response = ConversionResponse(
            success=result.success,
            api_data=result.data,
            processed_nodes=result.processed_nodes,
            skipped_nodes=result.skipped_nodes,
            total_nodes=result.total_nodes,
            errors=api_errors,
            warnings=api_warnings
        )
        
        http_status = determine_http_status(result)
        
        return JSONResponse(
            status_code=http_status,
            content=response.dict()
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Unexpected server error",
                "message": str(e),
                "type": type(e).__name__
            }
        )

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "workflow-converter"}

@app.get("/")
async def root():
    """Root endpoint with basic information."""
    return {
        "service": "ComfyUI Workflow to API Converter",
        "version": AUTOGRAPH_version,
        "endpoints": {
            "convert": "/convert-workflow",
            "convert_file": "/convert-workflow-file",
            "docs": "/docs",
            "health": "/health"
        }
    }

# Error handler for validation errors
@app.exception_handler(422)
async def validation_exception_handler(request, exc):
    """Custom handler for validation errors."""
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": "Validation error",
            "details": exc.detail if hasattr(exc, 'detail') else str(exc)
        }
    )

if __name__ == "__main__":
    import uvicorn
    
    parser = argparse.ArgumentParser(description="FastAPI server example for autograph")
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host. Resolution order: --host, then env AUTOGRAPH_HOST, else default 0.0.0.0.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port. Resolution order: --port, then env AUTOGRAPH_PORT, else default 8000.",
    )
    parser.add_argument(
        "--public-base-url",
        default=None,
        help="Base URL used only for printing docs URLs. "
             "Resolution order: --public-base-url, then env AUTOGRAPH_PUBLIC_BASE_URL, "
             "else default http://localhost:8000.",
    )
    args = parser.parse_args()

    host = args.host or os.environ.get("AUTOGRAPH_HOST", "0.0.0.0")

    port = args.port
    if port is None:
        env_port = os.environ.get("AUTOGRAPH_PORT")
        if env_port:
            try:
                port = int(env_port)
            except ValueError:
                port = None
    if port is None:
        port = 8000

    public_base_url = (
        args.public_base_url
        or os.environ.get("AUTOGRAPH_PUBLIC_BASE_URL")
        or f"http://localhost:{port}"
    ).rstrip("/")

    print("Starting FastAPI server...")
    print(f"API documentation available at: {public_base_url}/docs")
    print(f"Interactive API at: {public_base_url}/redoc")
    
    uvicorn.run(
        "fastapi_example:app",
        host=host,
        port=port,
        reload=True,
        log_level="info"
    )
