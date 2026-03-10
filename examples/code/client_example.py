#!/usr/bin/env python3
"""
Client example showing how to use the FastAPI endpoint with proper error handling.

This demonstrates how a client application should handle the structured
error responses from the API.
"""

import json
import os
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, Optional, Union

DEFAULT_AUTOGRAPH_API_BASE_URL = "http://localhost:8000"

def resolve_api_base_url(cli_value: Optional[str] = None) -> str:
    """
    Resolve the API base URL using: CLI arg -> env -> hardcoded default.

    Env var: AUTOGRAPH_API_BASE_URL
    """
    return (cli_value or os.environ.get("AUTOGRAPH_API_BASE_URL", DEFAULT_AUTOGRAPH_API_BASE_URL)).rstrip("/")

class WorkflowConverterClient:
    """Client for the ComfyUI Workflow Converter API."""
    
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = resolve_api_base_url(base_url)
    
    @staticmethod
    def _load_json_if_file(value: Union[Dict[str, Any], str, Path]) -> Dict[str, Any]:
        """
        If a file path is provided, load JSON from disk; otherwise return the dict as-is.
        This keeps the API call consistent (the server receives JSON objects, not file paths).
        """
        if isinstance(value, dict):
            return value
        p = Path(value)
        if p.is_file():
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
        raise ValueError(f"Expected dict or existing JSON file path, got: {value!r}")

    def convert_workflow(
        self,
        workflow_data: Union[Dict[str, Any], str, Path],
        node_info: Optional[Union[Dict[str, Any], str, Path]] = None,
        server_url: Optional[str] = None,
        include_meta: bool = False,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """
        Convert workflow using the API endpoint.
        
        Returns:
            Dictionary with conversion results and error information
        """
        payload = {
            "workflow_data": self._load_json_if_file(workflow_data),
            "include_meta": include_meta,
            "timeout": timeout
        }
        
        if node_info is not None:
            payload["node_info"] = self._load_json_if_file(node_info)
        
        if server_url is not None:
            payload["server_url"] = server_url
        
        try:
            url = f"{self.base_url}/convert-workflow"
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
                status_code = getattr(resp, "status", None) or getattr(resp, "code", None)
                resp_body = resp.read().decode("utf-8")
                result = json.loads(resp_body) if resp_body else {}
                if not isinstance(result, dict):
                    result = {"raw_response": result}

                result["http_status"] = status_code
                result["http_success"] = (status_code is not None and status_code < 400)
                return result

        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as e:
            return {
                "success": False,
                "http_success": False,
                "http_status": None,
                "errors": [{
                    "category": "network",
                    "severity": "critical",
                    "message": f"Request failed: {str(e)}",
                    "details": {"error_type": type(e).__name__}
                }],
                "warnings": [],
                "api_data": None,
                "processed_nodes": 0,
                "skipped_nodes": 0,
                "total_nodes": 0
            }

def print_conversion_result(result: Dict[str, Any], title: str = "Conversion Result"):
    """Print conversion result in a readable format."""
    print(f"\n{title}")
    print("=" * len(title))
    
    print(f"HTTP Status: {result.get('http_status', 'N/A')}")
    print(f"Success: {result.get('success', False)}")
    print(f"Processed Nodes: {result.get('processed_nodes', 0)}/{result.get('total_nodes', 0)}")
    
    if result.get('skipped_nodes', 0) > 0:
        print(f"Skipped Nodes: {result.get('skipped_nodes', 0)}")
    
    # Print errors
    errors = result.get('errors', [])
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for error in errors:
            print(f"  [{error['category']}/{error['severity']}] {error['message']}")
            if error.get('node_id'):
                print(f"    Node: {error['node_id']}")
            if error.get('details'):
                print(f"    Details: {error['details']}")
    
    # Print warnings
    warnings = result.get('warnings', [])
    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for warning in warnings:
            print(f"  [{warning['category']}/{warning['severity']}] {warning['message']}")
            if warning.get('node_id'):
                print(f"    Node: {warning['node_id']}")
    
    # Print API data info
    api_data = result.get('api_data')
    if api_data:
        print(f"\nGenerated API data for {len(api_data)} nodes")
    else:
        print("\nNo API data generated")

def example_successful_conversion(client: WorkflowConverterClient):
    """Example of successful conversion."""
    print("Example 1: Successful Conversion")
    
    # Simple valid workflow
    workflow = {
        "nodes": [
            {
                "id": 1,
                "type": "TestNode",
                "widgets_values": [42],
                "inputs": []
            }
        ],
        "links": []
    }
    
    # Provide node info
    node_info = {
        "TestNode": {
            "input": {
                "required": {
                    "value": ["INT", {"default": 0}]
                },
                "optional": {}
            }
        }
    }
    
    result = client.convert_workflow(workflow, node_info=node_info)
    print_conversion_result(result)

def example_validation_error(client: WorkflowConverterClient):
    """Example of validation error."""
    print("Example 2: Validation Error")
    
    # Invalid workflow (missing required fields)
    invalid_workflow = {
        "nodes": "invalid"  # Should be a list
    }
    
    result = client.convert_workflow(invalid_workflow)
    print_conversion_result(result)

def example_partial_success(client: WorkflowConverterClient):
    """Example of partial success with some node failures."""
    print("Example 3: Partial Success")
    
    # Workflow with mix of valid and invalid nodes
    workflow = {
        "nodes": [
            {
                "id": 1,
                "type": "ValidNode",
                "widgets_values": [1],
                "inputs": []
            },
            {
                "id": 2,
                "type": "InvalidNode",
                "widgets_values": [],
                "inputs": [{"name": "bad_input", "link": 999}]
            }
        ],
        "links": []
    }
    
    node_info = {
        "ValidNode": {
            "input": {
                "required": {
                    "param": ["INT", {"default": 0}]
                },
                "optional": {}
            }
        }
    }
    
    result = client.convert_workflow(workflow, node_info=node_info)
    print_conversion_result(result)

def example_network_error(client: WorkflowConverterClient):
    """Example of network error handling."""
    print("Example 4: Network Error")
    
    workflow = {"nodes": [], "links": []}
    
    # Try to connect to invalid server
    result = client.convert_workflow(
        workflow, 
        server_url="http://invalid-server:9999"
    )
    print_conversion_result(result)

def example_client_side_error():
    """Example of client-side error (server not running)."""
    print("Example 5: Client-Side Error (Server Not Running)")
    
    # Use wrong port to simulate server not running
    client = WorkflowConverterClient("http://localhost:9999")
    
    workflow = {"nodes": [], "links": []}
    result = client.convert_workflow(workflow)
    print_conversion_result(result)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Client examples for the Workflow Converter API")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL for the Workflow Converter API. "
             "Resolution order: --base-url, then env AUTOGRAPH_API_BASE_URL, else default http://localhost:8000.",
    )
    args = parser.parse_args()

    print("ComfyUI Workflow Converter - Client Examples")
    print("=" * 50)
    effective_base_url = resolve_api_base_url(args.base_url)
    print(f"Note: Make sure the FastAPI server is running at: {effective_base_url}")
    print("Run: python fastapi_example.py")
    print()
    
    try:
        # Set the base URL once for the whole run (env fallback handled in resolve_api_base_url)
        client = WorkflowConverterClient(args.base_url)
        
        example_successful_conversion(client)
        example_validation_error(client)
        example_partial_success(client)
        example_network_error(client)
        
    except KeyboardInterrupt:
        print("\nExamples interrupted by user")
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        # Still try the client-side error example
        
    example_client_side_error()
    
    print("\n" + "=" * 50)
    print("Examples completed!")
