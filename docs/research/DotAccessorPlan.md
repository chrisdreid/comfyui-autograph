# Dot Accessor Improvements — Implementation Plan

## Overview

Improve the Flow API so that accessing nodes and their widgets is intuitive, discoverable, and informative. All changes target the `flowtree` model layer.

**Version bump:** 1.2.x → 1.3.0

---

## 1. Auto-Resolve `node_info` (was `node_info`)

### Problem

`AUTOGRAPH_COMFYUI_SERVER_URL` is set but `Flow` doesn't auto-fetch from it. The resolution chain skips this env var unless `AUTOGRAPH_NODE_INFO_SOURCE` is also set.

### Fix

Add `AUTOGRAPH_COMFYUI_SERVER_URL` as a final fallback in `resolve_node_info_with_origin()` before returning `None`:

```python
# convert.py — resolution chain (after existing checks):
if allow_env:
    env_server = os.environ.get("AUTOGRAPH_COMFYUI_SERVER_URL", "").strip()
    if env_server:
        return fetch_node_info(env_server, timeout), True, ...
```

When node_info still resolves to `None`, emit a warning:

```python
warnings.warn(
    "Flow created without node_info — widget access and tab completion limited.\n"
    "Options:\n"
    "  • Set AUTOGRAPH_COMFYUI_SERVER_URL env var (auto-fetches)\n"
    "  • Pass node_info= to Flow()\n"
    "  • Call flow.fetch_node_info(server_url=...)\n"
    "See: docs/troubleshooting.md",
    UserWarning, stacklevel=2,
)
```

### Files

- `autograph/convert.py` — add server URL fallback in resolution chain
- `autograph/flowtree.py` — emit warning in `Flow.__init__` when degraded

---

## 2. Rename `node_info` → `node_info`

Mechanical rename across the entire codebase. Nobody is using this externally yet.

| Old | New |
|---|---|
| `NodeInfo` class | `NodeInfo` |
| `AUTOGRAPH_NODE_INFO_SOURCE` env var | `AUTOGRAPH_NODE_INFO_SOURCE` |
| `node_info=` params | `node_info=` |
| `flow.node_info` | `flow.node_info` |
| `fetch_node_info()` | `fetch_node_info()` |
| All internal function/variable names | `node_info` equivalent |

Old env var `AUTOGRAPH_NODE_INFO_SOURCE` kept as a fallback alias for transition.

### Files

All files in `autograph/`, all tests in `examples/unittests/`, all docs, example code.

---

## 3. `WidgetValue` Wrapper — `.choices()` and `.tooltip()`

Widget attribute access returns a smart wrapper that **behaves like the raw value** but adds introspection methods.

### API

```python
n = f.nodes.ksampler[0]

# Transparent value behavior (subclasses int/str/float):
n.seed              # → 200
n.seed == 200       # → True
n.seed + 1          # → 201
print(n.seed)       # → 200

# Introspection methods:
n.seed.choices()    # → {'type': 'INT', 'default': 0, 'min': 0, 'max': 18446744073709551615}
n.seed.tooltip()    # → "The random seed used for creating the noise."

n.sampler_name              # → "euler"
n.sampler_name.choices()    # → ['euler', 'euler_ancestral', 'heun', ...]
n.sampler_name.tooltip()    # → "The algorithm used for..."

# Assignment unchanged:
n.seed = 42
```

### Implementation

`WidgetValue` uses `__new__` to subclass the value's native Python type:
- `int` value → subclass of `(WidgetValue, int)`
- `float` value → subclass of `(WidgetValue, float)`
- `str` value → subclass of `(WidgetValue, str)`
- Other → plain wrapper with `._value`

Carries `_spec` (the node_info input spec) and `_name` (widget name) as hidden attrs.

`choices()` reads `_spec`:
- `spec[0]` is a list → combo choices (return the list)
- `spec[0]` is a type string → return constraint dict (`{'type': 'INT', 'min': 0, ...}`)

`tooltip()` reads `_spec[1].get("tooltip")`.

### Hook Point

`FlowNodeProxy.__getattr__` — when resolving a widget value, wrap in `WidgetValue(val, spec=spec, name=name)` instead of returning the raw value. Lookup spec via input definitions in node_info.

### Files

- `autograph/models.py` — `WidgetValue` class, modify `FlowNodeProxy.__getattr__`
- `autograph/flowtree.py` — expose via `NodeRef.__getattr__` delegation (already works)

---

## 4. `__repr__` and `__str__` — Path-Keyed Dict

Both `repr()` and `str()` return the same format: a dict keyed by the node's dot-path, containing widget attrs.

### Format

```python
n = f.nodes.ksampler[0]

repr(n)
# {'nodes.KSampler[0]': {'seed': 200, 'steps': 20, 'cfg': 8.0, 'sampler_name': 'euler', 'scheduler': 'normal', 'denoise': 1.0}}

print(n)
# same output

# NodeSet with multiple nodes:
print(f.nodes.ksampler)
# [{'nodes.KSampler[0]': {'seed': 200, ...}}, {'nodes.KSampler[1]': {'seed': 999, ...}}]
```

### Fallback

When `node_info` is unavailable, falls back to showing raw node keys (current behavior).

### Files

- `autograph/flowtree.py` — `NodeRef.__repr__`, `NodeRef.__str__`, `NodeSet.__repr__`, `NodeSet.__str__`

---

## 5. Filtered `__dir__` — Clean Tab Completion

### Show in tab completion

- **Widget names:** `seed`, `steps`, `cfg`, `sampler_name`, `scheduler`, `denoise`
- **Metadata:** `id`, `type`, `title`
- **Methods:** `choices`, `tooltip`, `tree`, `unwrap`, `to_dict`, `attrs`, `meta`

### Hide from tab (still accessible via direct attribute access)

- `widgets_values`, `pos`, `size`, `flags`, `order`, `mode`, `outputs`, `inputs`, `properties`

These remain fully accessible (e.g. `node.inputs` works), just not shown in tab completion. This is important for future `connect()`/`disconnect()` functionality that will use `inputs`/`outputs`.

### Files

- `autograph/models.py` — `FlowNodeProxy.__dir__()` / `FlowNodeProxy.attrs()`
- `autograph/flowtree.py` — `NodeRef.__dir__()`

---

## 6. Node Metadata Accessor

Clean API for per-node metadata that auto-stores to workspace `extra`:

```python
n = f.nodes.ksampler[0]
n.meta.my_tag = "production"      # stores to extra.autograph.meta.nodes["7"]["my_tag"]
n.meta.priority = 1
print(n.meta.my_tag)              # "production"
```

Currently `NodeRef.__getattr__("meta")` returns a `DictView` of `node["_meta"]`. Extend to auto-create the storage path in `workflow["extra"]["autograph"]["meta"]["nodes"][str(node_id)]` on write.

### Files

- `autograph/flowtree.py` — `NodeRef.__getattr__` meta handling

---

## Execution Order

| Phase | Feature | Difficulty |
|---|---|---|
| 1 | Rename `node_info` → `node_info` | Medium (mechanical) |
| 2 | Auto-resolve from `AUTOGRAPH_COMFYUI_SERVER_URL` + warning | Low |
| 3 | `WidgetValue` wrapper (`.choices()`, `.tooltip()`) | Medium |
| 4 | `__repr__`/`__str__` = path-keyed dict | Low |
| 5 | Filtered `__dir__` | Low |
| 6 | Node metadata auto-storage | Low-Med |

## Verification

- Run existing test suite: `python3 -m pytest examples/unittests/`
- New tests for `choices()`, `tooltip()`, repr format, and node_info auto-resolution
- Interactive REPL verification of tab completion
