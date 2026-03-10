# Live progress events (WebSocket)

If you pass `wait=True` and `on_event=...`, autograph opens ComfyUI's WebSocket and streams events into your callback.

```mermaid
flowchart  LR
  apiFlow["ApiFlow (API payload)"] --> submitFn["submit(wait=True, on_event=...)"]
  submitFn --> ws["/ws (WebSocket)"]
  ws --> cb["on_event(event)"]
  submitFn --> comfyServer["ComfyUI server"]
```

## Basic progress printer

```mermaid
flowchart LR
  apiFlow["ApiFlow"] --> submit["submit(wait=True, on_event=ProgressPrinter())"]
  submit --> ws["/ws"]
  ws --> printer["ProgressPrinter"]
```

## Completed events detected via history (WS silent / all-cached / queued)

Sometimes ComfyUI completes without producing a terminal websocket frame (common when everything is cached, or when you're queued and the WS stays quiet).

In those cases, autograph will probe `GET /history/<prompt_id>` and emit a **synthetic** terminal event:

- `type="completed"` (still a normal completed event)
- `detected_by="history"` (marker so you can tell it wasn't a literal WS frame)
- `data` includes history-derived payload to make it feel like a real event:
  - `data["status"]` (including `completed`, `status_str`, `messages` when present)
  - `data["outputs"]` (output refs, when present)
  - `data["meta"]` / `data["prompt"]` (when present)
- `raw` will contain a synthetic object like `{"type":"history_completed","data": <history_item>}` for debugging (`ProgressPrinter(raw=True)`).

## WebSocket idle timeout (prevent hangs)

autograph includes an idle timeout so a silent websocket does not hang forever:

- Env var: `AUTOGRAPH_WS_IDLE_TIMEOUT_S` (default 5s)
- When the websocket is silent beyond the idle timeout, autograph falls back to `/history` polling.

```python
# api
from autograph import ApiFlow, ProgressPrinter

api = ApiFlow("workflow.json")
api.submit(server_url="http://localhost:8188", wait=True, on_event=ProgressPrinter())
```

```bash
# cli (default progress is enabled automatically when waiting)
python -m autograph --submit --input-path workflow.json --server-url http://localhost:8188 --save-images outputs_progress --filepattern frame.###.png

# Disable progress output:
python -m autograph --submit --input-path workflow.json --server-url http://localhost:8188 --no-progress --save-images outputs_no_progress --filepattern frame.###.png
```

## Combine callbacks

```mermaid
flowchart LR
  apiFlow["ApiFlow"] --> submit["submit(wait=True, on_event=chain_callbacks(...))"]
  submit --> ws["/ws"]
  ws --> cb1["ProgressPrinter"]
  ws --> cb2["my_cb"]
```

```python
# api
from autograph import ApiFlow, ProgressPrinter, chain_callbacks

def my_cb(ev):
    print(ev.get("type"), ev.get("prompt_id"))

api = ApiFlow("workflow.json")
api.submit(
    server_url="http://localhost:8188",
    wait=True,
    on_event=chain_callbacks(ProgressPrinter(), my_cb),
)
```

```bash
# CLI note: the CLI prints progress by default when waiting (use --no-progress to disable).
# Custom callbacks (on_event=...) are Python-only.
```


