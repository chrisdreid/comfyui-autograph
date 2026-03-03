# Contributing to comfyui-autoflow

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/chrisdreid/comfyui-autoflow.git
cd comfyui-autoflow
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

No external dependencies are required — the library is pure stdlib Python.

## Running Tests (offline)

### Master test suite (recommended)

```bash
# Run the full 154-test suite with HTML report (offline, no server needed)
python examples/unittests/main.py --non-interactive --no-browser

# Run a specific stage only
python examples/unittests/main.py --stage 8 --non-interactive --no-browser
```

What to expect:
- **154 tests** across 15 stages: conversion, node access, widgets, bypass, fixtures, and more
- **HTML report** generated at `autoflow-test-suite/outputs/index.html`
- **Exit code**: `0` on success, non-zero on failure
- Stages 5 (fixtures) and 6 (server) are skipped unless `--fixtures-dir` / `--server-url` are provided

### Legacy unit tests

```bash
# Run unittest discovery (subset of tests)
python -m unittest discover -s examples/unittests -v
```

What to expect:
- **Output**: test names + `... ok`, then a final `OK`
- **Exit code**: `0` on success, non-zero on failure

### Docs examples test harness (`docs-test.py`)

This runs the fenced code examples from `docs/*.md` in a sandbox.

```bash
# Offline run: compiles python blocks, optionally executes safe ones, and runs safe CLI blocks
python examples/code/docs-test.py --mode offline --exec-python --run-cli

# List available labeled examples
python examples/code/docs-test.py --list

# Run only a subset (labels come from --list)
python examples/code/docs-test.py --mode offline --only "docs/convert.md#1:python" --exec-python
```

What to expect:
- **Output**: `START ...` / `END ... (ok)` banners per doc block
- **Skips**: network-looking snippets print `SKIP` unless you run in online mode
- **Exit code**: `0` if all selected examples pass; `1` if any fail

Diagram: see [`docs/contributing-tests.md`](docs/contributing-tests.md)

## Code Style

- **Type hints** on all public functions
- **Docstrings** on every module and public class/function
- Keep network interactions **explicit and opt-in** (no surprise HTTP calls)
- Prefer **stdlib** over third-party packages

## Pull Requests

1. Fork the repo and create a branch from `main`
2. Make your changes and add/update tests as needed
3. Run both test suites (see above)
4. Open a PR with a clear description of what changed and why

## Reporting Issues

Please include:
- Python version and OS
- ComfyUI version (if relevant)
- Minimal reproduction steps or code snippet
- Full traceback (if applicable)

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
