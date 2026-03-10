# Declarative mapping (map_strings / map_paths)

Use declarative mapping when you want a JSON-friendly spec to patch string/path inputs.

```mermaid
flowchart LR
  apiFlow["ApiFlow (API payload)"] --> mapStrings["map_strings(spec)"]
  mapStrings --> apiFlow2["ApiFlow (patched)"]
  apiFlow --> mapPaths["map_paths(spec)"]
  mapPaths --> apiFlow3["ApiFlow (patched paths)"]
```

## map_strings(flow, spec)
- Rewrites **string values** under each node `inputs`
- `spec` supports:
  - literal replacements (ordered)
  - regex replacements (list)
  - optional rules to target specific nodes/params/values
- Rules expand env vars and can be inline regex or **a file path** to a regex

```python
# api
from autograph import ApiFlow
from autograph.map import map_strings

api = ApiFlow("workflow.json")

spec = {
    "replacements": {
        "literal": {"${ROOT}": "/data/project"},
        "regex": [{"pattern": r"\\{FRAME\\}", "replace": "0001"}],
    },
    "rules": {
        "mode": "and",
        "node": {"regex": r"LoadImage|SaveImage"},
        "param": {"regex": r"path|filename|image"},
    },
}

api2 = map_strings(api, spec)
```

## map_paths(flow, spec)
- Convenience wrapper around `map_strings(...)`
- Adds a conservative default `rules.param` filter (path-like keys)

```python
# api
from autograph.map import map_paths

from autograph import ApiFlow

api = ApiFlow("workflow.json")
spec = {
    "replacements": {
        "literal": {"${ROOT}": "/data/project"},
    },
}
api2 = map_paths(api, spec)
```

## CLI
- CLI note: mapping helpers are currently Python-only.

```bash
# Put your spec in JSON, then load + apply it in Python.
```


