# Error Handling Guide

## Overview
- Goal: service-friendly conversion that can return **partial results + structured errors**
- Primary API: `Flow.convert_with_errors(...)`
- Related: [`fastapi.md`](fastapi.md)

## Key Principles

### 1. Structured Error Responses
- Prefer returning a structured result over raising
- Result includes:
  - **ok/success**: boolean
  - **data**: converted API payload (if available)
  - **errors**: blocking or partial-failure issues
  - **warnings**: non-blocking issues
  - **stats**: processed/skipped/total nodes

### 2. Error Categories
Errors are categorized to help determine appropriate responses:

| Category | Description | Typical HTTP Status |
|----------|-------------|-------------------|
| `validation` | Invalid input data | 400 Bad Request |
| `io` | File system errors | 404 Not Found |
| `network` | Connection failures | 502 Bad Gateway |
| `node_processing` | Node-specific issues | 422 Unprocessable Entity |
| `conversion` | General failures | 500 Internal Server Error |

### 3. Severity Levels
Each error has a severity level:

| Severity | Impact | Response Strategy |
|----------|--------|------------------|
| `critical` | Prevents entire conversion | Return error immediately |
| `error` | Prevents specific nodes | Continue with partial results |
| `warning` | Non-blocking issues | Log and continue |

## Implementation Patterns

### Standard API Response Format

```json
{
    "success": false,
    "api_data": null,
    "processed_nodes": 0,
    "skipped_nodes": 0,
    "total_nodes": 0,
    "errors": [
        {
            "category": "validation",
            "severity": "critical",
            "message": "Workflow data missing 'nodes' field",
            "node_id": null,
            "details": null
        }
    ],
    "warnings": []
}
```

### HTTP Status Code Mapping

```python
from autograph import ErrorCategory, ErrorSeverity

def determine_http_status(result: ConversionResult) -> int:
    if not result.success:
        # Critical errors
        has_critical = any(error.severity == ErrorSeverity.CRITICAL for error in result.errors)
        if has_critical:
            for error in result.errors:
                if error.category == ErrorCategory.VALIDATION:
                    return 400  # Bad Request
                elif error.category == ErrorCategory.NETWORK:
                    return 502  # Bad Gateway
                elif error.category == ErrorCategory.IO:
                    return 404  # Not Found
            return 500  # Internal Server Error
        else:
            return 422  # Unprocessable Entity
    
    # Success cases
    if result.errors:
        return 206  # Partial Content
    elif result.warnings:
        return 200  # OK (with warnings)
    else:
        return 200  # OK
```

### Client error handling

```python
def handle_api_response(response_data: dict) -> None:
    """Example client-side error handling."""
    
    if response_data.get("success"):
        print(f"✓ Converted {response_data['processed_nodes']} nodes successfully")
        
        # Handle warnings
        warnings = response_data.get("warnings", [])
        if warnings:
            print(f"⚠ {len(warnings)} warnings:")
            for warning in warnings:
                print(f"  - {warning['message']}")
                
    else:
        print("✗ Conversion failed or partially succeeded")
        
        # Handle errors by category
        errors = response_data.get("errors", [])
        for error in errors:
            category = error["category"]
            severity = error["severity"]
            message = error["message"]
            
            if severity == "critical":
                print(f"💥 CRITICAL [{category}]: {message}")
                # Stop processing
                return
            else:
                print(f"❌ ERROR [{category}]: {message}")
        
        # Check if we have partial results
        api_data = response_data.get("api_data")
        if api_data:
            print(f"📊 Partial results available: {len(api_data)} nodes")
            # Process partial data
```

## Best practices

### For API Developers

1. **Use structured conversion**:
   ```python
   # ✓ Good
   from autograph import Flow
   workflow_data = "workflow.json"  # workspace workflow.json path
   result = Flow.load(workflow_data).convert_with_errors(node_info="node_info.json")
   
   # ✗ Avoid (for API endpoints)
   try:
       from autograph import ApiFlow
       data = ApiFlow(workflow_data, node_info="node_info.json")
   except Exception as e:
       # Exception handling
       pass
   ```

2. **Map errors to appropriate HTTP status codes**:
   - Use the error category and severity to determine status
   - Don't always return 500 for failures
   - Use 206 for partial success scenarios

3. **Provide detailed error context**:
   ```python
   def attach_error_context(response: dict, error) -> None:
       # Include node_id and details when available
       if getattr(error, "node_id", None):
           response["failed_node"] = error.node_id
       if getattr(error, "details", None):
           response["debug_info"] = error.details
   ```

4. **Handle partial success gracefully**:
   ```python
   def maybe_partial_response(result, response_data: dict) -> dict:
       if getattr(result, "data", None) and getattr(result, "processed_nodes", 0) > 0:
           return {"status_code": 206, "content": response_data}
       return {"status_code": 200, "content": response_data}
   ```

### For Client Developers

1. **Check both HTTP status and success field**:
   ```python
   def handle_http_response(status_code: int, response_data: dict) -> None:
       if status_code < 400 and response_data.get("success"):
           return
       if status_code == 206:
           return
       return
   ```

2. **Implement retry logic for network errors**:
   ```python
   def should_retry(errors: list) -> bool:
       return any(e.get("category") == "network" for e in (errors or []) if isinstance(e, dict))
   ```

3. **Log warnings for debugging**:
   ```python
   def log_warnings(logger, response_data: dict) -> None:
       for warning in response_data.get("warnings", []):
           if isinstance(warning, dict):
               logger.warning(f"Node {warning.get('node_id')}: {warning.get('message')}")
   ```

## Error Scenarios and Responses

### Scenario 1: Invalid Workflow Data
```json
{
    "success": false,
    "api_data": null,
    "errors": [
        {
            "category": "validation",
            "severity": "critical",
            "message": "Workflow data missing 'nodes' field"
        }
    ]
}
```
**HTTP Status**: 400 Bad Request

### Scenario 2: Network Connection Failure
```json
{
    "success": false,
    "api_data": null,
    "errors": [
        {
            "category": "network", 
            "severity": "critical",
            "message": "Could not connect to server http://localhost:8188",
            "details": {"server_url": "http://localhost:8188", "timeout": 30}
        }
    ]
}
```
**HTTP Status**: 502 Bad Gateway

### Scenario 3: Partial Success
```json
{
    "success": false,
    "api_data": {"1": {"class_type": "Example", "inputs": {}}, "2": {"class_type": "Example", "inputs": {}}},
    "processed_nodes": 2,
    "skipped_nodes": 1,
    "total_nodes": 3,
    "errors": [
        {
            "category": "node_processing",
            "severity": "error", 
            "message": "Failed to resolve link for input 'model'",
            "node_id": "3",
            "details": {"link_id": 999, "input_name": "model"}
        }
    ]
}
```
**HTTP Status**: 206 Partial Content

### Scenario 4: Success with Warnings
```json
{
    "success": true,
    "api_data": {"1": {"class_type": "Example", "inputs": {}}},
    "processed_nodes": 1,
    "total_nodes": 1,
    "warnings": [
        {
            "category": "node_processing",
            "severity": "warning",
            "message": "Mismatch in widgets_values length",
            "node_id": "1",
            "details": {"expected_count": 6, "actual_count": 7}
        }
    ]
}
```
**HTTP Status**: 200 OK

## Testing

Use the provided test scripts to verify error handling:

```bash
# Test various error scenarios
python examples/unittests/test_error_handling.py

# FastAPI/client examples are interactive / networked; see docs/fastapi.md
# python examples/code/fastapi_example.py
# python examples/code/client_example.py
```

## Migration from exception-based handling

If you're migrating from the old exception-based approach:

### Before (Exception-based)
```python
@app.post("/convert")
async def convert(request: WorkflowRequest):
    try:
        result = convert_workflow(request.workflow_data)
        return {"success": True, "data": result}
    except WorkflowConverterError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### After (Structured Error Handling)
```python
@app.post("/convert")
async def convert(request: WorkflowRequest):
    from autograph import Flow
    result = Flow.load(request.workflow_data).convert_with_errors(node_info="node_info.json")
    
    # Map to appropriate HTTP status
    if not result.success:
        status_code = 400 if any(e.severity == ErrorSeverity.CRITICAL for e in result.errors) else 422
    elif result.errors:
        status_code = 206  # Partial success
    else:
        status_code = 200
    
    return JSONResponse(
        status_code=status_code,
        content={
            "success": result.success,
            "data": result.data,
            "errors": [error._asdict() for error in result.errors],
            "warnings": [warning._asdict() for warning in result.warnings]
        }
    )
```

This approach provides much better error visibility and allows clients to make informed decisions about how to handle different types of failures.
