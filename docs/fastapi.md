# FastAPI integration

This repo includes a full service + client example:
- server: `examples/code/fastapi_example.py`
- client: `examples/code/client_example.py`

```mermaid
flowchart  LR
  client["Client"] --> api["YourFastAPIService"]
  api --> convertFn["Flow.load(...) + .convert(...)"]
  convertFn --> submitFn["ApiFlow.submit(...)"]
  submitFn --> comfy["ComfyUI server"]
```

## Quick start

```python
from fastapi import FastAPI
import autograph

app = FastAPI()

@app.post("/convert")
async def convert(workflow: dict):
    result = autograph.convert_workflow_with_errors(workflow)
    return {
        "success": result.ok,
        "api_data": dict(result.data) if result.data else None,
        "errors":   [str(e) for e in result.errors],
        "warnings": [str(e) for e in result.warnings],
    }
```

## Service tips
- **Conversion**: use `Flow.load(...)` + `Flow.convert_with_errors(...)`
- **Errors**: return structured `errors`/`warnings` (see [`error-handling.md`](error-handling.md))
- **Network**: keep server calls explicit and opt-in
- **Status codes**: see `determine_http_status()` in the example for mapping
  `ErrorCategory` values to HTTP 4xx/5xx codes
- **Validation**: `WorkflowRequest` (Pydantic model in example) validates
  timeouts, server URLs, and workflow shape before conversion

## Example endpoints

The `fastapi_example.py` server provides:

| Endpoint | Method | Description |
|---|---|---|
| `/convert` | POST | Convert workflow JSON to API format |
| `/convert-file` | GET | Convert from server-side file paths |
| `/health` | GET | Health check |

See the full example for error handling, status code mapping, and Pydantic models.
