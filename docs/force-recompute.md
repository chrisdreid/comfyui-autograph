# Force recompute (force_recompute)

Use this when you want repeated runs to avoid reusing cached results.

```mermaid
flowchart LR
  apiFlow["ApiFlow (API payload)"] --> force["force_recompute(...)"]
  force --> apiFlow2["ApiFlow (cache-busted)"]
```

## What it does
- Adds a random UUID to `inputs` for selected node types
- Default behavior is opt-in:
  - `use_defaults=False` does nothing
  - `use_defaults=True` uses a small conservative list (e.g. `KSampler`)

```python
# api
from autoflow import ApiFlow
from autoflow.map import force_recompute

api = ApiFlow("workflow.json")
api2 = force_recompute(api, use_defaults=True)
```

## CLI
- CLI note: `force_recompute(...)` is currently Python-only.

```bash
# Use Python to apply force_recompute before save/submit.
```


