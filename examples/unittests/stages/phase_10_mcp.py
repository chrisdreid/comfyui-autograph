"""Phase 10 — MCP server: optional sub-package, server build, tool registration,
session store, graft engine, error parser.

Skips cleanly when the optional ``mcp`` package is not installed (the core
``comfyui-autograph`` library is intentionally zero-dependency, so ``mcp`` only
ships as part of the ``[mcp]`` extra).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness import ResultCollector, _run_test  # noqa: E402

STAGE = "Phase 10: MCP server"

EXPECTED_TOOLS = {
    # Server / introspection
    "comfyui_status", "list_node_types", "describe_node_type", "list_models",
    # Inspection / editing
    "inspect_workflow", "convert_workflow", "validate_workflow", "set_workflow_values",
    # Builder
    "load_workflow", "create_workflow", "add_node", "connect_nodes",
    "disconnect_input", "remove_node", "merge_workflow", "save_workflow", "get_workflow",
    # Sessions
    "list_sessions", "close_session",
    # Library / sources
    "list_workflow_sources", "search_local_workflows", "load_local_workflow",
    # Execution
    "run_workflow", "queue_workflow", "get_history", "interrupt",
    # Files / outputs
    "upload_file", "fetch_outputs", "list_outputs",
    # Discovery, templates, jobs and housekeeping
    "list_input_options", "search_templates", "load_template", "set_node_mode",
    "get_job_status", "wait_for_job", "cancel_job", "free_memory", "get_logs",
}


def _mcp_node_info() -> dict:
    """The six standard nodes of the harness plus what the MCP tests need."""
    from harness import BUILTIN_NODE_INFO

    info = json.loads(json.dumps(BUILTIN_NODE_INFO))
    info["KSampler"]["input"]["required"].pop("control_after_generate", None)
    info["KSampler"]["input"]["required"]["seed"][1]["control_after_generate"] = True
    info["LoadImage"] = {
        "input": {"required": {"image": [["photo.png"], {"image_upload": True}]}},
        "output": ["IMAGE", "MASK"], "output_name": ["IMAGE", "MASK"], "output_is_list": [False, False],
        "display_name": "Load Image", "category": "image", "search_aliases": ["open picture"],
    }
    info["ImageBlur"] = {
        "input": {"required": {"image": ["IMAGE", {}],
                               "blur_radius": ["INT", {"default": 1, "min": 1, "max": 31}],
                               "sigma": ["FLOAT", {"default": 1.0, "min": 0.1, "max": 10.0}]}},
        "output": ["IMAGE"], "output_name": ["IMAGE"], "output_is_list": [False],
        "display_name": "Blur Image", "category": "image/filters", "description": "Soften an image.",
    }
    info["SaveAdvanced"] = {
        "input": {"required": {
            "images": ["IMAGE", {}],
            "format": ["COMFY_DYNAMICCOMBO_V3", {"options": [
                {"key": "png", "inputs": {"required": {"bit_depth": ["COMBO", {"options": ["8-bit", "16-bit"]}]}}},
                {"key": "jpeg", "inputs": {"required": {"quality": ["INT", {"default": 90, "min": 1, "max": 100}]}}},
            ]}],
        }},
        "output": [], "output_name": [], "output_is_list": [], "output_node": True,
        "display_name": "Save Image (Advanced)", "category": "image",
    }
    info["Scale"] = {
        "input": {"required": {"image": ["IMAGE", {}], "factor": ["FLOAT", {"forceInput": True}]}},
        "output": ["IMAGE"], "output_name": ["IMAGE"], "output_is_list": [False], "category": "image",
    }
    info["PrimitiveFloat"] = {
        "input": {"required": {"value": ["FLOAT", {"default": 0.0}]}},
        "output": ["FLOAT"], "output_name": ["FLOAT"], "output_is_list": [False], "category": "utils/primitive",
    }
    info["OldBlur"] = dict(info["ImageBlur"], deprecated=True, display_name="Blur (old)")
    return info


_TXT2IMG_API = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["1", 1]}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry", "clip": ["1", 1]}},
    "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "5": {"class_type": "KSampler", "inputs": {
        "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0],
        "seed": 7, "steps": 20, "cfg": 8.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
    "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
    "12": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "cat"}},
}

_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _FakeComfy:
    """A ComfyUI stand-in speaking the HTTP endpoints the MCP tools use. Jobs finish at once unless `hold` is set."""

    def __init__(self, node_info: dict):
        import http.server
        import threading
        import urllib.parse
        import uuid

        fake = self
        self.node_info = node_info
        self.history: dict = {}
        self.pending: list = []
        self.running: list = []
        self.hold = False
        self.fail_node = None
        self.prompts: list = []
        self.freed = 0

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, data, code=200, raw=False):
                body = data if raw else json.dumps(data).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "image/png" if raw else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                path = url.path
                if path == "/object_info":
                    return self._send(fake.node_info)
                if path == "/system_stats":
                    return self._send({"system": {"comfyui_version": "0.0-test", "ram_total": 2 ** 33, "ram_free": 2 ** 32},
                                       "devices": [{"name": "cpu", "type": "cpu", "vram_total": 2 ** 33, "vram_free": 2 ** 32}]})
                if path == "/queue":
                    return self._send({"queue_running": [[0, pid, {}, {}, []] for pid in fake.running],
                                       "queue_pending": [[i + 1, pid, {}, {}, []] for i, pid in enumerate(fake.pending)]})
                if path == "/history":
                    return self._send(fake.history)
                if path.startswith("/history/"):
                    pid = path.split("/", 2)[2]
                    return self._send({pid: fake.history[pid]} if pid in fake.history else {})
                if path == "/view":
                    return self._send(_PNG_1X1, raw=True)
                if path == "/models":
                    return self._send(["checkpoints", "loras"])
                if path == "/models/checkpoints":
                    return self._send(["sd_xl_base_1.0.safetensors", "v1-5-pruned-emaonly-fp16.safetensors"])
                if path == "/models/loras":
                    return self._send([])
                if path == "/internal/logs":
                    return self._send("line one\n\x1b[32m[INFO]\x1b[0m line two\nout of memory\n")
                if path == "/templates/index.json":
                    return self._send([
                        {"moduleName": "default", "title": "Image", "type": "image", "templates": [
                            {"name": "cat_t2i", "title": "Cat: Text to Image", "description": "Text to image.",
                             "mediaType": "image", "tags": ["Text to Image"], "models": ["SDXL"], "usage": 50,
                             "openSource": True, "size": 2 ** 31,
                             "io": {"inputs": [], "outputs": [{"nodeId": 12, "nodeType": "SaveImage"}]}},
                            {"name": "api_cat", "title": "Cat API", "description": "Paid text to image.",
                             "mediaType": "image", "tags": ["API"], "models": [], "usage": 90, "openSource": False}]},
                        {"moduleName": "default", "title": "Audio", "type": "audio", "templates": [
                            {"name": "song", "title": "Song", "description": "Text to music.", "mediaType": "audio",
                             "tags": [], "models": [], "usage": 5}]},
                    ])
                if path == "/templates/cat_t2i.json":
                    return self._send(fake.template)
                self._send({"error": "not found"}, 404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/prompt":
                    prompt = body.get("prompt") or {}
                    fake.prompts.append(body)
                    known = fake.node_info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
                    bad = {nid: n for nid, n in prompt.items()
                           if n.get("class_type") == "CheckpointLoaderSimple" and n["inputs"].get("ckpt_name") not in known}
                    if bad:
                        return self._send({
                            "error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
                            "node_errors": {nid: {"class_type": n["class_type"], "errors": [{
                                "type": "value_not_in_list", "message": "Value not in list",
                                "details": f"ckpt_name: '{n['inputs'].get('ckpt_name')}' not in {known}",
                                "extra_info": {"input_name": "ckpt_name"}}]} for nid, n in bad.items()}}, 400)
                    pid = uuid.uuid4().hex
                    if fake.hold:
                        fake.pending.append(pid)
                    else:
                        fake.finish(pid, prompt)
                    return self._send({"prompt_id": pid, "number": len(fake.prompts), "node_errors": {}})
                if self.path == "/queue":
                    for pid in body.get("delete") or []:
                        if pid in fake.pending:
                            fake.pending.remove(pid)
                    return self._send({})
                if self.path == "/interrupt":
                    for pid in list(fake.running):
                        fake.running.remove(pid)
                        fake.history[pid] = {"outputs": {}, "status": {"status_str": "error", "completed": False, "messages": [
                            ["execution_start", {"timestamp": 0}], ["execution_interrupted", {"timestamp": 10}]]}}
                    return self._send({})
                if self.path == "/free":
                    fake.freed += 1
                    return self._send({})
                self._send({"error": "not found"}, 404)

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.template: dict = {}

    def finish(self, pid: str, prompt: dict) -> None:
        if self.fail_node and any(n.get("class_type") == self.fail_node for n in prompt.values()):
            nid = next(k for k, n in prompt.items() if n.get("class_type") == self.fail_node)
            self.history[pid] = {"outputs": {}, "status": {"status_str": "error", "completed": False, "messages": [
                ["execution_start", {"timestamp": 0}],
                ["execution_error", {"timestamp": 900, "node_id": nid, "node_type": self.fail_node,
                                     "exception_type": "RuntimeError", "exception_message": "out of memory",
                                     "traceback": ["line 1\n", "line 2\n"], "current_inputs": {"big": "x" * 5000}}]]}}
            return
        outputs = {nid: {"images": [{"filename": f"{n['inputs'].get('filename_prefix', 'out')}_00001_.png",
                                     "subfolder": "", "type": "output"}]}
                   for nid, n in prompt.items() if n.get("class_type") in ("SaveImage", "SaveAdvanced")}
        self.history[pid] = {"outputs": outputs, "status": {"status_str": "success", "completed": True, "messages": [
            ["execution_start", {"timestamp": 0}], ["execution_success", {"timestamp": 1500}]]}}

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _mcp_available() -> bool:
    try:
        import mcp  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Synthetic flow fixtures
# ---------------------------------------------------------------------------


def _empty_workspace() -> dict:
    return {
        "last_node_id": 0,
        "last_link_id": 0,
        "nodes": [],
        "links": [],
        "groups": [],
        "config": {},
        "extra": {},
        "version": 0.4,
    }


def _make_loader_node(node_id: int) -> dict:
    return {
        "id": node_id,
        "type": "CheckpointLoaderSimple",
        "pos": [0, 0],
        "size": [315, 100],
        "flags": {},
        "order": 0,
        "mode": 0,
        "inputs": [],
        "outputs": [
            {"name": "MODEL", "type": "MODEL", "slot_index": 0, "links": []},
            {"name": "CLIP",  "type": "CLIP",  "slot_index": 1, "links": []},
            {"name": "VAE",   "type": "VAE",   "slot_index": 2, "links": []},
        ],
        "properties": {"Node name for S&R": "CheckpointLoaderSimple"},
        "widgets_values": ["model.safetensors"],
    }


def _make_save_node(node_id: int) -> dict:
    return {
        "id": node_id,
        "type": "SaveImage",
        "pos": [800, 0],
        "size": [315, 100],
        "flags": {},
        "order": 1,
        "mode": 0,
        "inputs": [
            {"name": "images", "type": "IMAGE", "link": None},
        ],
        "outputs": [],
        "properties": {"Node name for S&R": "SaveImage"},
        "widgets_values": ["ComfyUI"],
    }


def _make_decode_fragment() -> dict:
    """A tiny workflow fragment: one VAEDecode node with dangling LATENT/VAE inputs and an IMAGE output."""
    fragment = _empty_workspace()
    fragment["last_node_id"] = 99
    fragment["nodes"] = [
        {
            "id": 99,
            "type": "VAEDecode",
            "pos": [400, 0],
            "size": [210, 50],
            "flags": {},
            "order": 0,
            "mode": 0,
            "inputs": [
                {"name": "samples", "type": "LATENT", "link": None},
                {"name": "vae",     "type": "VAE",    "link": None},
            ],
            "outputs": [
                {"name": "IMAGE", "type": "IMAGE", "slot_index": 0, "links": []},
            ],
            "properties": {"Node name for S&R": "VAEDecode"},
            "widgets_values": [],
        }
    ]
    return fragment


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


def run(collector: ResultCollector, **kwargs) -> None:
    stage = STAGE
    print(f"\n{'='*60}")
    print(f"  {stage}")
    print(f"{'='*60}\n")

    # 10.1  Importing autograph alone must NOT pull in mcp.
    def t_10_1():
        import subprocess
        code = (
            "import sys; "
            "import autograph; "
            "leaked = [m for m in sys.modules if m == 'mcp' or m.startswith('mcp.')]; "
            "print('LEAKED:' + ','.join(leaked))"
        )
        out = subprocess.check_output(
            [sys.executable, "-c", code], stderr=subprocess.STDOUT, text=True
        ).strip()
        leaked = out.split("LEAKED:", 1)[-1]
        assert leaked == "", f"importing autograph leaked mcp modules: {leaked}"
        return {
            "input": "import autograph (subprocess)",
            "output": "no mcp.* modules in sys.modules",
            "result": "OK",
        }
    _run_test(collector, stage, "10.1", "core import does not load mcp", t_10_1)

    if not _mcp_available():
        def t_skip():
            return {
                "input": "import mcp",
                "output": "mcp not installed",
                "result": "SKIP — install with `pip install \"comfyui-autograph[mcp]\"`",
            }
        _run_test(collector, stage, "10.2", "mcp extra installed", t_skip)
        return

    # 10.2  autograph.mcp imports cleanly when [mcp] is present.
    def t_10_2():
        import autograph.mcp as _amcp
        assert hasattr(_amcp, "build_server"), "build_server missing"
        assert hasattr(_amcp, "main"), "main missing"
        return {
            "input": "import autograph.mcp",
            "output": "build_server / main present",
            "result": "OK",
        }
    _run_test(collector, stage, "10.2", "autograph.mcp imports", t_10_2)

    # 10.3  Server builds and registers every expected tool.
    def t_10_3():
        from autograph.mcp import build_server
        server = build_server()
        manager = server._tool_manager
        names = set(manager._tools.keys())
        missing = EXPECTED_TOOLS - names
        unexpected = names - EXPECTED_TOOLS
        assert not missing, f"missing tools: {sorted(missing)}"
        # Unexpected isn't fatal but we want to know if the surface drifts.
        return {
            "input": "build_server() tool registration",
            "output": f"{len(names)} tools registered (expected {len(EXPECTED_TOOLS)}); unexpected={sorted(unexpected)}",
            "result": "OK",
        }
    _run_test(collector, stage, "10.3", "all MCP tools registered", t_10_3)

    # 10.4  Resources and prompts registered.
    def t_10_4():
        from autograph.mcp import build_server
        server = build_server()
        rmgr = server._resource_manager
        pmgr = server._prompt_manager
        resources = list(getattr(rmgr, "_templates", {}).keys()) + list(getattr(rmgr, "_resources", {}).keys())
        prompts = list(getattr(pmgr, "_prompts", {}).keys())
        assert any("node-info" in r for r in resources)
        assert any("history" in r for r in resources)
        assert any("outputs" in r for r in resources)
        for required_prompt in ("text_to_image", "diagnose_workflow", "vibe_build_workflow"):
            assert required_prompt in prompts, f"prompt missing: {required_prompt}"
        return {
            "input": "build_server() resources + prompts",
            "output": f"resources={len(resources)}, prompts={sorted(prompts)}",
            "result": "OK",
        }
    _run_test(collector, stage, "10.4", "MCP resources and prompts registered", t_10_4)

    # 10.5  Console-script and python -m entry points agree.
    def t_10_5():
        from autograph.mcp import main as mcp_main
        from autograph.mcp.__main__ import main as dunder_main
        assert mcp_main is dunder_main, "console-script and __main__ diverge"
        return {
            "input": "console-script entry resolution",
            "output": "autograph.mcp.main is autograph.mcp.__main__.main",
            "result": "OK",
        }
    _run_test(collector, stage, "10.5", "console-script entry point", t_10_5)

    # 10.6  Session store: load workflow, write checkpoint, list, close.
    def t_10_6():
        from autograph.mcp.session import SessionStore
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(session_dir=Path(tmp))
            wf_path = Path(tmp) / "wf.json"
            wf_path.write_text(json.dumps(_empty_workspace()), encoding="utf-8")
            session = store.load_from(wf_path, label="test")
            wid = session.id
            assert session.checkpoint_path is not None and session.checkpoint_path.exists(), "checkpoint not written on load"
            session.flow._flow["nodes"].append(_make_loader_node(1))
            store.touch(wid)
            cp = json.loads(session.checkpoint_path.read_text(encoding="utf-8"))
            assert len(cp.get("nodes", [])) == 1, "checkpoint did not capture mutation"
            listed = store.list()
            assert any(s["workflow_id"] == wid for s in listed), "session not in list()"
            close_result = store.close(wid, delete_checkpoint=True)
            assert close_result.get("ok"), f"close failed: {close_result}"
            assert not session.checkpoint_path.exists(), "checkpoint file should have been deleted"
        return {
            "input": "SessionStore lifecycle",
            "output": "load → mutate → checkpoint → list → close (with checkpoint delete)",
            "result": "OK",
        }
    _run_test(collector, stage, "10.6", "session store load/checkpoint/close", t_10_6)

    # 10.7  Graft engine: insert a fragment with renumbering and auto-stitching.
    def t_10_7():
        import autograph
        from autograph.mcp.graft import merge_into_flow

        # Active flow: a CheckpointLoaderSimple (id=1) and a SaveImage (id=2).
        active = _empty_workspace()
        active["last_node_id"] = 2
        active["nodes"] = [_make_loader_node(1), _make_save_node(2)]
        flow = autograph.Flow(active)

        report = merge_into_flow(flow, _make_decode_fragment())

        # The fragment had id=99; with last_node_id=2 it should renumber to 99+2 = 101.
        added_ids = [int(n["node_id"]) for n in report["added_nodes"]]
        assert added_ids == [101], f"expected renumbered id 101, got {added_ids}"

        # VAEDecode has dangling LATENT (no LATENT producer in active) → still dangling.
        # VAEDecode has dangling VAE (CheckpointLoaderSimple has unique VAE output) → auto-wired.
        # VAEDecode IMAGE output → SaveImage has unique free IMAGE input → auto-wired.
        wires = report["auto_connected"]
        assert any(w["slot_type"] == "VAE" for w in wires), f"VAE not auto-wired: {wires}"
        assert any(w["slot_type"] == "IMAGE" for w in wires), f"IMAGE not auto-wired: {wires}"

        still_in = report["still_dangling_inputs"]
        assert any(d["input_type"] == "LATENT" for d in still_in), \
            f"LATENT input should still be dangling (no producer in active flow): {still_in}"

        return {
            "input": "merge_into_flow(VAEDecode fragment)",
            "output": (
                f"added={added_ids}, auto-wired={[w['slot_type'] for w in wires]}, "
                f"still_dangling={[d['input_type'] for d in still_in]}"
            ),
            "result": "OK",
        }
    _run_test(collector, stage, "10.7", "graft engine renumber + auto-stitch", t_10_7)

    # 10.8  Error parser: structured /prompt 400 body.
    def t_10_8():
        from autograph.mcp.errors import parse_prompt_error_body
        body = json.dumps(
            {
                "error": {"type": "prompt_outputs_failed", "message": "Validation failed"},
                "node_errors": {
                    "5": {
                        "class_type": "KSampler",
                        "errors": [
                            {"type": "value-not-in-list", "message": "seed not in range", "details": {}}
                        ],
                    }
                },
            }
        )
        errors = parse_prompt_error_body(body, default_status=400)
        assert len(errors) >= 2, f"expected top-level + node error, got {errors}"
        node_err = next((e for e in errors if e.get("node_id") == "5"), None)
        assert node_err is not None and node_err.get("class_type") == "KSampler", \
            f"missing structured KSampler error: {errors}"
        return {
            "input": "parse_prompt_error_body(<400 body>)",
            "output": f"{len(errors)} structured errors, including node 5/KSampler",
            "result": "OK",
        }
    _run_test(collector, stage, "10.8", "error parser handles /prompt 400", t_10_8)

    # 10.9  Workflow library + sources catalog.
    def t_10_9():
        from autograph.mcp import library as lib
        sources = lib.ONLINE_SOURCES
        assert isinstance(sources, list) and any("github.com/comfyanonymous" in s.get("url", "") for s in sources), \
            "expected the official ComfyUI examples GitHub URL in ONLINE_SOURCES"
        entries = lib.discover()
        names = [e.name for e in entries]
        assert any(n == "txt2img-basic" for n in names), f"bundled txt2img-basic missing from library: {names}"
        return {
            "input": "library.discover() + ONLINE_SOURCES",
            "output": f"{len(entries)} library entries; {len(sources)} curated sources",
            "result": "OK",
        }
    _run_test(collector, stage, "10.9", "library discovery + sources catalog", t_10_9)

    # ------------------------------------------------------------------
    # The tools themselves. Offline: a fixed node list, and a stand-in for
    # ComfyUI's HTTP API where a test needs the server to answer.
    # ------------------------------------------------------------------
    from autograph.mcp import graph as _graph
    from autograph.mcp import tools as _tools
    from autograph.mcp.config import McpConfig
    from autograph.mcp.session import SessionStore

    node_info = _mcp_node_info()
    tmp_root = Path(tempfile.mkdtemp(prefix="autograph_mcp_"))

    def _offline():
        """A config pointing at nothing, working from the fixed node list."""
        cfg = McpConfig(server_url="http://127.0.0.1:9", timeout_s=2, output_path=str(tmp_root / "out"),
                        max_inline_image_bytes=2_000_000, max_inline_images=4)
        _tools.use_node_info(cfg, node_info)
        return cfg, SessionStore(session_dir=tmp_root / "sessions")

    # 10.10  Node catalog: search by words, by type, and describe.
    def t_10_10():
        catalog = _graph.Catalog(node_info)
        names = lambda **kw: [n["class_type"] for n in catalog.search(**kw)["node_types"]]
        assert names(query="ksampler")[0] == "KSampler"
        assert names(query="load image")[0] == "LoadImage", names(query="load image")
        assert names(query="open picture") == ["LoadImage"], "search aliases are searched"
        assert names(query="soften") == ["ImageBlur"], "descriptions are searched"
        assert "OldBlur" not in names(query="blur") and "OldBlur" in names(query="blur", include_deprecated=True)
        assert set(names(output_type="CONDITIONING")) == {"CLIPTextEncode"}
        assert "KSampler" in names(input_type="LATENT") and "VAEDecode" in names(input_type="LATENT")
        assert names(category="filters") == ["ImageBlur"]
        page = catalog.search(limit=2)
        assert page["count"] == 2 and page["next_offset"] == 2 and page["total"] > 2
        row = catalog.search(query="ksampler")["node_types"][0]
        assert row["takes"] == ["model:MODEL", "positive:CONDITIONING", "negative:CONDITIONING", "latent_image:LATENT"]
        assert row["gives"] == ["LATENT"]
        d = catalog.describe("ksampler")                       # any case
        seed = next(i for i in d["inputs"] if i["name"] == "seed")
        assert seed["kind"] == "widget" and seed["seed"] is True and seed["min"] == 0 and seed["default"] == 0
        sampler = next(i for i in d["inputs"] if i["name"] == "sampler_name")
        assert sampler["type"] == "COMBO" and sampler["options_total"] == 8 and "euler" in sampler["options"]
        assert next(i for i in d["inputs"] if i["name"] == "model")["kind"] == "connection"
        assert d["outputs"] == [{"index": 0, "name": "LATENT", "type": "LATENT"}]
        assert catalog.describe("Load Image")["class_type"] == "LoadImage", "display names resolve"
        fmt = next(i for i in catalog.describe("SaveAdvanced")["inputs"] if i["name"] == "format")
        assert [i["name"] for i in fmt["inputs_per_option"]["jpeg"]] == ["format.quality"]
        json.dumps(d)                                           # plain JSON all the way down
        try:
            catalog.describe("KSamplr")
            raise AssertionError("an unknown node type must not resolve")
        except LookupError as exc:
            assert "KSampler" in str(exc), str(exc)
        assert catalog.options("KSampler", "scheduler", query="s")["options"] == ["karras", "sgm_uniform", "simple"]
        return {"input": "Catalog.search / describe / options", "output": "words, aliases, types, paging, suggestions",
                "result": "OK"}
    _run_test(collector, stage, "10.10", "node catalog: search and describe", t_10_10)

    # 10.11  Pre-flight: every kind of problem is found and says what to do.
    def t_10_11():
        catalog = _graph.Catalog(node_info)
        assert _graph.validate(_TXT2IMG_API, catalog) == {"errors": [], "warnings": []}

        def codes(change, key="errors"):
            api = json.loads(json.dumps(_TXT2IMG_API))
            change(api)
            return {(i["code"], i.get("node_id"), i.get("input")): i for i in _graph.validate(api, catalog)[key]}

        found = codes(lambda a: a["5"]["inputs"].pop("positive"))
        issue = found[("missing_input", "5", "positive")]
        assert issue["candidates"] and all("CLIPTextEncode" in c for c in issue["candidates"]), issue
        found = codes(lambda a: a["5"]["inputs"].update(model=["4", 0]))
        issue = found[("type_mismatch", "5", "model")]
        assert issue["expected"] == "MODEL" and issue["got"] == "LATENT" and "CheckpointLoaderSimple" in issue["candidates"][0]
        assert ("bad_output_index", "6", "vae") in codes(lambda a: a["6"]["inputs"].update(vae=["1", 9]))
        assert ("dangling_link", "6", "vae") in codes(lambda a: a["6"]["inputs"].update(vae=["99", 0]))
        found = codes(lambda a: a["5"]["inputs"].update(sampler_name="eulr"))
        assert found[("value_not_in_list", "5", "sampler_name")]["closest"][0] == "euler"
        found = codes(lambda a: a["5"]["inputs"].update(steps=0))
        assert found[("out_of_range", "5", "steps")]["min"] == 1
        assert ("wrong_type", "5", "cfg") in codes(lambda a: a["5"]["inputs"].update(cfg="high"))
        assert ("value_on_connection", "5", "positive") in codes(lambda a: a["5"]["inputs"].update(positive="a cat"))
        found = codes(lambda a: a["6"].update(class_type="VAEDecod"))
        assert found[("unknown_node_type", "6", None)]["closest"] == ["VAEDecode"]
        assert ("no_output_node", None, None) in codes(lambda a: a.pop("12"))
        assert ("unknown_input", "4", "widht") in codes(lambda a: a["4"]["inputs"].update(widht=1), "warnings")
        # A broken node that feeds nothing does not stop the rest.
        def spare(a):
            a["20"] = {"class_type": "KSampler", "inputs": {"seed": 1}}
        assert not codes(spare) and ("unused_nodes", None, None) in codes(spare, "warnings")
        # The file a previous run wrote is a valid value for a loader, though it is not in its list.
        def chain(a):
            a["30"] = {"class_type": "LoadImage", "inputs": {"image": "cat_00001_.png [output]"}}
            a["31"] = {"class_type": "SaveImage", "inputs": {"images": ["30", 0], "filename_prefix": "x"}}
        assert not codes(chain)
        # ComfyUI's link rule: same type in any case, a shared member of a list, wildcards.
        assert _graph.types_connect("IMAGE", "image") and _graph.types_connect("INT", "FLOAT,INT")
        assert _graph.types_connect("*", "MODEL") and not _graph.types_connect("IMAGE", "MASK")
        return {"input": "graph.validate on a txt2img prompt broken 12 ways", "output": "each found, with what would fit",
                "result": "OK"}
    _run_test(collector, stage, "10.11", "pre-flight finds each kind of problem", t_10_11)

    # 10.12  Build node by node: values and links in one call, refusals that say what fits.
    def t_10_12():
        cfg, store = _offline()
        w = _tools.create_workflow(cfg, store, label="build")["workflow_id"]
        add = lambda class_type, **inputs: _tools.add_node(cfg, store, w, class_type, inputs)
        ckpt = add("checkpointloadersimple", ckpt_name="sd_xl_base_1.0.safetensors")
        assert ckpt["class_type"] == "CheckpointLoaderSimple" and ckpt["node_id"] == "1"
        pos = add("CLIPTextEncode", text="a cat", clip=[ckpt["node_id"], 1])
        assert pos["inputs"] == {"text": "a cat", "clip": ["1", 1]} and "missing" not in pos
        neg = add("CLIPTextEncode", text="blurry", clip=["1", "CLIP"])            # an output by name
        latent = add("EmptyLatentImage", width=512, height=512)
        for wrong, said in (
            (dict(class_type="KSamplr"), "Did you mean: KSampler"),
            (dict(class_type="KSampler", sede=1), "no input 'sede' (did you mean seed"),
            (dict(class_type="KSampler", steps=0), "below the minimum 1"),
            (dict(class_type="KSampler", cfg="high"), "expected a number"),
            (dict(class_type="KSampler", positive="a cat"), "takes a CONDITIONING connection"),
            (dict(class_type="KSampler", model=[latent["node_id"], 0]), "gives LATENT"),
            (dict(class_type="KSampler", model=["1", 7]), "has no output 7"),
            (dict(class_type="KSampler", model=["42", 0]), "No node with id"),
        ):
            before = _tools.inspect_workflow(cfg, store, workflow_id=w)["node_count"]
            try:
                add(wrong.pop("class_type"), **wrong)
                raise AssertionError(f"should have been refused: {wrong}")
            except (LookupError, ValueError, TypeError) as exc:
                assert said in str(exc), f"{said!r} not in {exc}"
            assert _tools.inspect_workflow(cfg, store, workflow_id=w)["node_count"] == before, "a refused node stays out"
        sampler = add("KSampler", model=["1", 0], positive=[pos["node_id"], 0], negative=[neg["node_id"], 0],
                      latent_image=[latent["node_id"], 0], seed=7)
        assert sampler["node_id"] == "5", "refused nodes do not use up ids"
        decode = add("VAEDecode", samples=[sampler["node_id"], 0])
        assert decode["missing"] == ["vae:VAE"]
        save = _tools.add_node(cfg, store, w, "SaveImage", {"filename_prefix": "cat"}, title="Result")
        assert save["title"] == "Result"
        # connect_nodes: checks names and types, picks the one output that fits.
        for args, said in (
            (dict(from_node="1", to_node=decode["node_id"], to_input="vea"), "Did you mean vae?"),
            (dict(from_node="1", to_node=decode["node_id"], to_input="vae", from_output=0), "Outputs of node 1 that fit: 2 (VAE)"),
            (dict(from_node="4", to_node=decode["node_id"], to_input="vae"), "No output of node 4"),
        ):
            try:
                _tools.connect_nodes(cfg, store, w, **args)
                raise AssertionError(f"should have been refused: {args}")
            except (LookupError, ValueError, TypeError) as exc:
                assert said in str(exc), f"{said!r} not in {exc}"
        link = _tools.connect_nodes(cfg, store, w, from_node="1", to_node=decode["node_id"], to_input="vae")
        assert link["link"] == ["1", 2] and link["type"] == "VAE"
        _tools.connect_nodes(cfg, store, w, from_node=decode["node_id"], to_node=save["node_id"], to_input="images")
        check = _tools.validate_workflow(cfg, store, workflow_id=w)
        assert check["ok"] and not check["errors"] and not check["warnings"], check
        api = _tools.get_workflow(cfg, store, w, format="api")["workflow"]
        strip = lambda a: {k: {"class_type": v["class_type"], "inputs": v["inputs"]} for k, v in a.items()}
        expected = json.loads(json.dumps(_TXT2IMG_API))
        expected["7"] = expected.pop("12")
        assert strip(api) == expected, json.dumps(strip(api))
        saved = _tools.save_workflow(cfg, store, w, path=str(tmp_root / "built.json"))
        on_disk = json.loads(Path(saved["saved_to"]).read_text(encoding="utf-8"))
        spots = [tuple(n["pos"]) for n in on_disk["nodes"]]
        assert len(set(spots)) == len(spots), "a graph built here is laid out, not stacked on one spot"
        return {"input": "create_workflow + add_node x7 + connect_nodes", "output": "8 mistakes refused with what fits; "
                "the result converts to the expected prompt", "result": "OK"}
    _run_test(collector, stage, "10.12", "build node by node, with refusals that explain", t_10_12)

    # 10.13  A whole graph in one call, read back by name, edited, and still the same prompt.
    def t_10_13():
        cfg, store = _offline()
        made = _tools.create_workflow(cfg, store, nodes=_TXT2IMG_API)
        w = made["workflow_id"]
        assert made["valid"] is True and made["node_count"] == 7 and "node_ids" not in made, made
        view = _tools.inspect_workflow(cfg, store, workflow_id=w)
        assert [n["id"] for n in view["nodes"]] == ["1", "2", "3", "4", "5", "6", "12"], "the given ids are kept"
        assert view["output_nodes"] == ["12"]
        sampler = next(n for n in view["nodes"] if n["id"] == "5")
        assert sampler["inputs"]["seed"] == 7 and sampler["inputs"]["positive"] == ["2", 0]
        assert "control_after_generate" not in sampler["inputs"], "only real inputs are shown"
        assert sampler["outputs"] == ["LATENT -> 6.samples"]
        loader = next(n for n in view["nodes"] if n["id"] == "1")
        assert loader["outputs"] == ["MODEL -> 5.model", "CLIP -> 2.clip, 3.clip", "VAE -> 6.vae"]
        brief = _tools.inspect_workflow(cfg, store, workflow_id=w, brief=True, node_ids=["5"])
        assert len(brief["nodes"]) == 1 and "seed" not in brief["nodes"][0]["inputs"]
        api = _tools.get_workflow(cfg, store, w, format="api")["workflow"]
        strip = lambda a: {k: {"class_type": v["class_type"], "inputs": v["inputs"]} for k, v in a.items()}
        assert strip(api) == _TXT2IMG_API, "API -> session -> API is the identity"
        # Edits: by id, by class, by title; values, links, disconnect, rename.
        done = _tools.set_workflow_values(cfg, store, workflow_id=w, updates=[
            {"node_id": "5", "inputs": {"seed": 42, "steps": "30", "sampler_name": "heun"}},
            {"class_type": "CLIPTextEncode", "inputs": {"text": "a dog"}},
            {"node_id": "2", "set_title": "Positive"},
            {"node_id": "5", "inputs": {"stpes": 5, "cfg": 500, "scheduler": "karas", "negative": None}},
            {"node_id": "77", "inputs": {"seed": 1}},
        ])
        rows = {(r.get("node_id"), r.get("input")): r for r in done["applied"]}
        assert rows[("5", "steps")]["value"] == 30, "a number given as text is stored as a number"
        assert rows[("2", "text")]["ok"] and rows[("3", "text")]["ok"]
        assert rows[("5", "stpes")]["closest"] == ["steps"] and not rows[("5", "stpes")]["ok"]
        assert rows[("5", "cfg")]["max"] == 100.0 and not rows[("5", "cfg")]["ok"]
        assert rows[("5", "scheduler")]["ok"] and "karras" in rows[("5", "scheduler")]["closest"], "kept, with a warning"
        assert rows[("5", "negative")].get("disconnected")
        assert not done["ok"] and done["failed"] == 3
        view = {n["id"]: n for n in _tools.inspect_workflow(cfg, store, workflow_id=w)["nodes"]}
        assert view["5"]["inputs"]["seed"] == 42 and view["5"]["inputs"]["cfg"] == 8.0
        assert view["5"]["missing"] == ["negative:CONDITIONING"] and view["2"]["title"] == "Positive"
        back = _tools.set_workflow_values(cfg, store, workflow_id=w, updates=[
            {"title": "Positive", "inputs": {"text": "a cat"}},
            {"node_id": "5", "inputs": {"negative": ["3", 0], "scheduler": "normal"}}])
        assert back["ok"], back
        # Dropdown with inputs of its own: switching the choice swaps them.
        adv = _tools.add_node(cfg, store, w, "SaveAdvanced", {"images": ["6", 0], "format": "png", "format.bit_depth": "16-bit"})
        assert adv["inputs"]["format.bit_depth"] == "16-bit"
        switched = _tools.set_workflow_values(cfg, store, workflow_id=w, updates=[
            {"node_id": adv["node_id"], "inputs": {"format": "jpeg"}},
            {"node_id": adv["node_id"], "inputs": {"format.quality": 70, "format.bit_depth": "8-bit"}}])
        rows = {r["input"]: r for r in switched["applied"]}
        assert rows["format"]["ok"] and rows["format.quality"]["ok"] and not rows["format.bit_depth"]["ok"]
        now = _tools.inspect_workflow(cfg, store, workflow_id=w, node_ids=[adv["node_id"]])["nodes"][0]["inputs"]
        assert now["format"] == "jpeg" and now["format.quality"] == 70 and "format.bit_depth" not in now, now
        # A value for an input that only takes a connection is held by a primitive node.
        scale = _tools.add_node(cfg, store, w, "Scale", {"image": ["6", 0], "factor": 2.0})
        assert scale["inputs"]["factor"][1] == 0 and "missing" not in scale
        # Bypass, remove.
        assert _tools.set_node_mode(cfg, store, w, [scale["node_id"]], "bypassed")["mode"] == "bypassed"
        assert _tools.inspect_workflow(cfg, store, workflow_id=w, node_ids=[scale["node_id"]])["nodes"][0]["mode"] == "bypassed"
        gone = _tools.remove_node(cfg, store, w, "6")
        assert set(gone["inputs_left_unconnected"]) >= {"12.images", f"{adv['node_id']}.images"}, gone
        check = _tools.validate_workflow(cfg, store, workflow_id=w)
        assert not check["ok"] and {e["code"] for e in check["errors"]} == {"missing_input"}
        return {"input": "create_workflow(nodes=<API prompt>) + inspect + set_workflow_values + set_node_mode + remove_node",
                "output": "ids kept, inputs by name, round trip identical, bad edits refused one by one", "result": "OK"}
    _run_test(collector, stage, "10.13", "whole graph in one call; read and edit by name", t_10_13)

    # 10.14  Loading: workspace or API format, subgraphs flattened, nonsense refused, unknown types named.
    def t_10_14():
        cfg, store = _offline()
        made = _tools.create_workflow(cfg, store, nodes=_TXT2IMG_API)
        path = tmp_root / "roundtrip.json"
        _tools.save_workflow(cfg, store, made["workflow_id"], path=str(path))
        loaded = _tools.load_workflow(cfg, store, source=str(path))
        assert loaded["node_count"] == 7 and loaded["source_path"] == str(path.resolve())
        assert _tools.validate_workflow(cfg, store, workflow_id=loaded["workflow_id"])["ok"]
        as_text = _tools.load_workflow(cfg, store, source=json.dumps(_TXT2IMG_API))
        assert as_text["format"] == "api" and as_text["node_count"] == 7
        inline = _tools.validate_workflow(cfg, store, workflow=_TXT2IMG_API)
        assert inline["ok"] and inline["workflow_id"] is None, "a workflow can be checked without a session"
        for bad in ({"hello": 1}, str(tmp_root / "missing.json")):
            try:
                _tools.load_workflow(cfg, store, source=bad)
                raise AssertionError(f"should have been refused: {bad}")
            except (ValueError, FileNotFoundError):
                pass
        workspace = json.loads(path.read_text(encoding="utf-8"))
        workspace["nodes"].append({"id": 50, "type": "MyCustomNode", "pos": [0, 0], "size": [1, 1], "flags": {}, "order": 9,
                                   "mode": 0, "inputs": [], "outputs": [], "properties": {}, "widgets_values": []})
        workspace["nodes"].append({"id": 51, "type": "MarkdownNote", "pos": [0, 0], "size": [1, 1], "flags": {}, "order": 10,
                                   "mode": 0, "inputs": [], "outputs": [], "properties": {}, "widgets_values": ["hi"]})
        custom = _tools.load_workflow(cfg, store, source=workspace)
        check = _tools.validate_workflow(cfg, store, workflow_id=custom["workflow_id"])
        assert [(e["code"], e.get("node_id")) for e in check["errors"]] == [("unknown_node_type", "50")], check
        assert not check["warnings"], "a note is not a problem"
        # A graph with a subgraph: flattened, so each node has the id ComfyUI will use.
        inner = {"id": "sg-1", "name": "Encode", "nodes": [
            {"id": 3, "type": "CLIPTextEncode", "pos": [0, 0], "size": [1, 1], "flags": {}, "order": 0, "mode": 0,
             "inputs": [{"name": "clip", "type": "CLIP", "link": 1}],
             "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": [2]}],
             "properties": {}, "widgets_values": ["inside"]}],
            "links": [{"id": 1, "origin_id": -10, "origin_slot": 0, "target_id": 3, "target_slot": 0, "type": "CLIP"},
                      {"id": 2, "origin_id": 3, "origin_slot": 0, "target_id": -20, "target_slot": 0, "type": "CONDITIONING"}],
            "inputNode": {"id": -10}, "outputNode": {"id": -20},
            "inputs": [{"id": "in-1", "name": "clip", "type": "CLIP", "linkIds": [1]}],
            "outputs": [{"id": "out-1", "name": "CONDITIONING", "type": "CONDITIONING", "linkIds": [2]}], "widgets": []}
        outer = {"last_node_id": 2, "last_link_id": 1, "version": 0.4, "groups": [], "config": {}, "extra": {}, "nodes": [
            {"id": 1, "type": "CheckpointLoaderSimple", "pos": [0, 0], "size": [1, 1], "flags": {}, "order": 0, "mode": 0,
             "inputs": [], "outputs": [{"name": "MODEL", "type": "MODEL", "links": []}, {"name": "CLIP", "type": "CLIP", "links": [1]},
                                       {"name": "VAE", "type": "VAE", "links": []}],
             "properties": {}, "widgets_values": ["sd_xl_base_1.0.safetensors"]},
            {"id": 2, "type": "sg-1", "pos": [300, 0], "size": [1, 1], "flags": {}, "order": 1, "mode": 0,
             "inputs": [{"name": "clip", "type": "CLIP", "link": 1}],
             "outputs": [{"name": "CONDITIONING", "type": "CONDITIONING", "links": []}], "properties": {}, "widgets_values": []}],
            "links": [[1, 1, 1, 2, 0, "CLIP"]], "definitions": {"subgraphs": [inner]}}
        flat = _tools.load_workflow(cfg, store, source=outer)
        assert flat.get("subgraphs_flattened") == 1, flat
        nodes = _tools.inspect_workflow(cfg, store, workflow_id=flat["workflow_id"])["nodes"]
        encode = next(n for n in nodes if n["class_type"] == "CLIPTextEncode")
        assert encode["inputs"] == {"text": "inside", "clip": ["1", 1]}, encode
        assert all(n["class_type"] != "sg-1" for n in nodes)
        return {"input": "load_workflow: file, API JSON, inline, nonsense, custom node, subgraph",
                "output": "each loads or is refused; unknown node types are named; subgraphs are flattened", "result": "OK"}
    _run_test(collector, stage, "10.14", "loading workspace, API and subgraph workflows", t_10_14)

    # 10.15-10.18  Against a stand-in for ComfyUI's HTTP API.
    fake = _FakeComfy(node_info)
    try:
        def _online():
            cfg = McpConfig(server_url=fake.url, timeout_s=5, output_path=str(tmp_root / "out"),
                            max_inline_image_bytes=2_000_000, max_inline_images=4)
            _tools.forget_catalog(cfg)
            return cfg, SessionStore(session_dir=tmp_root / "sessions")

        # 10.15  Discovery tools read the server.
        def t_10_15():
            cfg, store = _online()
            status = _tools.comfyui_status(cfg)
            assert status["reachable"] and status["node_types"] == len(node_info) and status["queue"] == {"running": 0, "pending": 0}
            assert status["comfyui_version"] == "0.0-test" and status["devices"][0]["vram_free_gb"] == 4.0
            found = _tools.list_node_types(cfg, query="blur")
            assert [n["class_type"] for n in found["node_types"]] == ["ImageBlur"]
            assert _tools.list_node_types(cfg, query="zzz")["total"] == 0
            both = _tools.describe_node_type(cfg, ["KSampler", "Nope"])
            assert [n["class_type"] for n in both["node_types"]] == ["KSampler"] and both["unknown"][0]["class_type"] == "Nope"
            assert _tools.list_input_options(cfg, "CheckpointLoaderSimple", "ckpt_name", query="xl")["options"] == ["sd_xl_base_1.0.safetensors"]
            assert _tools.list_models(cfg)["folders"] == ["checkpoints", "loras"]
            assert _tools.list_models(cfg, folder="checkpoints", query="v1")["models"] == ["v1-5-pruned-emaonly-fp16.safetensors"]
            assert "hint" in _tools.list_models(cfg, folder="loras")
            log = _tools.get_logs(cfg, lines=2)
            assert log["log"] == "[INFO] line two\nout of memory", log
            assert _tools.free_memory(cfg)["ok"] and fake.freed == 1
            down = _tools.comfyui_status(McpConfig(server_url="http://127.0.0.1:9", timeout_s=1, output_path=".",
                                                   max_inline_image_bytes=1, max_inline_images=1))
            assert down["reachable"] is False and "hint" in down
            return {"input": "status, node search, options, models, logs against a server", "output": "compact answers",
                    "result": "OK"}
        _run_test(collector, stage, "10.15", "discovery tools against a server", t_10_15)

        # 10.16  Run: validate, queue, wait, outputs; failures come back with the node and what fits.
        def t_10_16():
            cfg, store = _online()
            w = _tools.create_workflow(cfg, store, nodes=_TXT2IMG_API)["workflow_id"]
            ticks = []
            done = _tools.run_workflow(cfg, store, workflow_id=w, on_progress=lambda *a: ticks.append(a))
            assert done["ok"] and done["status"] == "completed" and done["duration_s"] == 1.5, done
            out = done["outputs"][0]
            assert out["filename"] == "cat_00001_.png" and out["as_input"] == "cat_00001_.png [output]" and out["node_id"] == "12"
            assert Path(out["saved_path"]).read_bytes() == _PNG_1X1 and out["inlined"] is True
            assert [b["type"] for b in done["content_blocks"]] == ["image"]
            sent = fake.prompts[-1]
            assert sent["prompt"]["5"]["inputs"]["seed"] == 7
            assert sent["extra_data"]["extra_pnginfo"]["workflow"]["nodes"], "the workspace graph travels with the job"
            # Chain: the output of one run is a valid loader value in the next.
            chained = _tools.run_workflow(cfg, store, fetch_outputs=False, workflow={
                "1": {"class_type": "LoadImage", "inputs": {"image": out["as_input"]}},
                "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "again"}}})
            assert chained["ok"] and chained["outputs"][0]["filename"] == "again_00001_.png", chained
            # A graph that cannot run is not queued.
            queued = len(fake.prompts)
            _tools.disconnect_input(cfg, store, w, "5", "positive")
            stopped = _tools.run_workflow(cfg, store, workflow_id=w)
            assert stopped["ok"] is False and stopped["stage"] == "validation" and len(fake.prompts) == queued
            assert stopped["errors"][0]["code"] == "missing_input" and stopped["errors"][0]["candidates"]
            _tools.connect_nodes(cfg, store, w, "2", "5", "positive")
            # A value ComfyUI refuses: its error, with the near matches found here.
            _tools.set_workflow_values(cfg, store, workflow_id=w, updates=[
                {"node_id": "1", "inputs": {"ckpt_name": "sd_xl_base.safetensors"}}])
            refused = _tools.run_workflow(cfg, store, workflow_id=w)
            assert refused["ok"] is False and refused["stage"] == "submit", refused
            node_error = next(e for e in refused["errors"] if e.get("node_id") == "1")
            assert node_error["error_type"] == "value_not_in_list" and node_error["closest"][0] == "sd_xl_base_1.0.safetensors"
            _tools.set_workflow_values(cfg, store, workflow_id=w, updates=[
                {"node_id": "1", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}}])
            # A failure while running names the node; the raw inputs are not echoed back.
            fake.fail_node = "KSampler"
            failed = _tools.run_workflow(cfg, store, workflow_id=w)
            fake.fail_node = None
            assert failed["ok"] is False and failed["status"] == "failed"
            error = failed["errors"][0]
            assert error["node_id"] == "5" and error["class_type"] == "KSampler" and "out of memory" in error["message"]
            assert "line 2" in error["traceback_tail"] and len(json.dumps(failed)) < 2000
            return {"input": "run_workflow: ok, chained, invalid, refused by the server, failed while running",
                    "output": "outputs with as_input; each failure names the node and what fits", "result": "OK"}
        _run_test(collector, stage, "10.16", "run a workflow and report failures", t_10_16)

        # 10.17  Long jobs: the wait ends with the job id, and the job can be followed or cancelled.
        def t_10_17():
            cfg, store = _online()
            fake.hold = True
            try:
                first = _tools.run_workflow(cfg, store, workflow=_TXT2IMG_API, wait_timeout=0.5)
                assert first.get("timed_out") is True and first["status"] == "pending" and "ok" not in first, first
                pid = first["prompt_id"]
                second = _tools.queue_workflow(cfg, store, workflow=_TXT2IMG_API)
                assert second["status"] == "queued"
                assert _tools.get_job_status(cfg, second["prompt_id"])["queue_position"] == 2
                overview = _tools.get_job_status(cfg)
                assert overview["pending"] == [pid, second["prompt_id"]] and overview["running"] == []
                gone = _tools.cancel_job(cfg, second["prompt_id"])
                assert gone["ok"] and gone["was"] == "pending" and fake.pending == [pid]
                fake.pending.remove(pid)
                fake.running.append(pid)
                assert _tools.get_job_status(cfg, pid)["status"] == "running"
                stopped = _tools.cancel_job(cfg, pid)
                assert stopped["was"] == "running" and _tools.get_job_status(cfg, pid)["status"] == "cancelled"
                assert _tools.cancel_job(cfg, pid)["ok"] is False
                third = _tools.queue_workflow(cfg, store, workflow=_TXT2IMG_API)["prompt_id"]
                fake.pending.remove(third)
                fake.finish(third, _TXT2IMG_API)
                waited = _tools.wait_for_job(cfg, third, timeout=5)
                assert waited["ok"] and waited["outputs"][0]["as_input"] == "cat_00001_.png [output]"
            finally:
                fake.hold = False
            jobs = _tools.get_history(cfg, limit=5)["jobs"]
            assert jobs[0]["prompt_id"] == third and jobs[0]["status"] == "completed", "newest first"
            assert _tools.list_outputs(cfg, third)["outputs"][0]["resource_uri"].startswith("comfyui://outputs/")
            assert _tools.get_job_status(cfg, "nope")["status"] == "unknown"
            return {"input": "run_workflow(wait_timeout) / queue / status / cancel / wait_for_job / history",
                    "output": "timed_out with the id; pending and running jobs cancelled; outputs on completion", "result": "OK"}
        _run_test(collector, stage, "10.17", "long jobs: time out, follow, cancel", t_10_17)

        # 10.18  Templates served by ComfyUI.
        def t_10_18():
            cfg, store = _online()
            built = _tools.create_workflow(cfg, store, nodes=_TXT2IMG_API)
            fake.template = _tools.get_workflow(cfg, store, built["workflow_id"])["workflow"]
            found = _tools.search_templates(cfg, query="text image")
            assert [t["name"] for t in found["templates"]] == ["cat_t2i", "api_cat"], "a match in the title comes first"
            assert found["templates"][1]["uses_paid_api_nodes"] is True and found["templates"][0]["model_download_gb"] == 2.0
            assert [t["name"] for t in _tools.search_templates(cfg)["templates"]] == ["api_cat", "cat_t2i", "song"], "then most used"
            assert [t["name"] for t in _tools.search_templates(cfg, query="text image", local_only=True)["templates"]] == ["cat_t2i"]
            assert [t["name"] for t in _tools.search_templates(cfg, media_type="audio")["templates"]] == ["song"]
            loaded = _tools.load_template(cfg, store, "Cat: Text to Image")
            assert loaded["ready_to_run"] is True and loaded["node_count"] == 7 and loaded["outputs"][0]["node_id"] == "12"
            fake.node_info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0].clear()
            try:
                _tools.forget_catalog(cfg)
                missing = _tools.load_template(cfg, store, "cat_t2i")
                assert missing["ready_to_run"] is False and missing["problems"][0]["code"] == "no_options"
            finally:
                fake.node_info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0].extend(
                    ["sd_xl_base_1.0.safetensors", "v1-5-pruned-emaonly-fp16.safetensors"])
            try:
                _tools.load_template(cfg, store, "cat_t2")
                raise AssertionError("an unknown template must not load")
            except LookupError as exc:
                assert "cat_t2i" in str(exc)
            return {"input": "search_templates / load_template", "output": "ranked, filtered, loaded, with what is missing",
                    "result": "OK"}
        _run_test(collector, stage, "10.18", "ComfyUI's templates: search and load", t_10_18)

        # 10.19  The same over the MCP protocol: hints, errors, images, progress.
        def t_10_19():
            import asyncio
            try:
                from mcp.shared.memory import create_connected_server_and_client_session as connect
            except ImportError:
                from harness import SkipTest
                raise SkipTest("this mcp SDK has no in-memory client")
            from autograph.mcp import build_server
            cfg, store = _online()
            server = build_server(cfg, store)

            async def scenario():
                progress = []

                async def on_progress(done, total, message=None):
                    progress.append(message)

                async with connect(server._mcp_server) as client:
                    listed = (await client.list_tools()).tools
                    hints = {t.name: t.annotations for t in listed}
                    made = await client.call_tool("create_workflow", {"nodes": _TXT2IMG_API})
                    w = json.loads(made.content[0].text)["workflow_id"]
                    wrong = await client.call_tool("add_node", {"workflow_id": w, "class_type": "KSamplr"})
                    fake.hold = True
                    try:
                        slow = await client.call_tool("run_workflow", {"workflow_id": w, "wait_timeout": 1.2},
                                                      progress_callback=on_progress)
                    finally:
                        fake.hold = False
                        fake.pending.clear()
                    ran = await client.call_tool("run_workflow", {"workflow_id": w})
                    return listed, hints, wrong, slow, ran, progress

            listed, hints, wrong, slow, ran, progress = asyncio.run(scenario())
            assert {t.name for t in listed} >= EXPECTED_TOOLS
            assert all(t.description and len(t.description) > 40 for t in listed), "every tool says what it does"
            if hints["inspect_workflow"] is not None:
                assert hints["inspect_workflow"].readOnlyHint and not hints["add_node"].readOnlyHint
                assert hints["remove_node"].destructiveHint and not hints["add_node"].destructiveHint
                assert hints["add_node"].openWorldHint is False and hints["run_workflow"].openWorldHint is True
            assert wrong.isError and "Did you mean: KSampler" in wrong.content[0].text
            waiting = json.loads(slow.content[0].text)
            assert waiting["timed_out"] is True and waiting["prompt_id"]
            assert any(m and "queue" in m for m in progress), f"progress was reported while waiting: {progress}"
            assert [c.type for c in ran.content] == ["text", "image"] and json.loads(ran.content[0].text)["ok"] is True
            assert len(server.instructions) > 800 and "validate_workflow" in server.instructions
            return {"input": "list_tools / call_tool through an MCP client session",
                    "output": f"{len(listed)} tools with hints; an error result; progress; an inline image", "result": "OK"}
        _run_test(collector, stage, "10.19", "over the MCP protocol: hints, errors, progress, images", t_10_19)
    finally:
        fake.close()

    # 10.20  Without a server: the last node list saved on disk is used, and says so.
    def t_10_20():
        cfg = McpConfig(server_url="http://127.0.0.1:9", timeout_s=1, output_path=".",
                        max_inline_image_bytes=1, max_inline_images=1)
        saved = _tools._NODE_INFO_CACHE_PATH
        try:
            _tools._NODE_INFO_CACHE_PATH = tmp_root / "node_info_cache.json"
            _tools.forget_catalog(cfg)
            try:
                _tools.list_node_types(cfg, query="blur")
                raise AssertionError("no server and no saved node list must be an error")
            except RuntimeError as exc:
                assert "comfyui_status" in str(exc)
            store = SessionStore(session_dir=tmp_root / "sessions")
            empty = _tools.create_workflow(cfg, store)
            assert "note" in empty, "an empty session can still be created, and says what is missing"
            _tools._NODE_INFO_CACHE_PATH.write_text(json.dumps({"data": node_info}), encoding="utf-8")
            found = _tools.list_node_types(cfg, query="blur")
            assert found["from_cache"] is True and found["node_types"][0]["class_type"] == "ImageBlur"
            made = _tools.create_workflow(cfg, store, nodes=_TXT2IMG_API)
            assert made["valid"] is True
        finally:
            _tools._NODE_INFO_CACHE_PATH = saved
            _tools.forget_catalog(cfg)
        return {"input": "tools with ComfyUI unreachable", "output": "a clear error, then the saved node list",
                "result": "OK"}
    _run_test(collector, stage, "10.20", "offline: saved node list, clear errors", t_10_20)

    # 10.21  A /prompt error whose body autograph already read is still parsed.
    def t_10_21():
        import urllib.error
        from autograph.mcp.errors import parse_prompt_error
        body = json.dumps({"error": {"type": "prompt_outputs_failed_validation", "message": "failed"},
                           "node_errors": {"3": {"class_type": "KSampler", "errors": [
                               {"type": "value_not_in_list", "message": "Value not in list", "details": "x"}]}}})
        exc = urllib.error.HTTPError("http://x/prompt", 400, f"Bad Request: {body}", None, None)
        errors = parse_prompt_error(exc)
        assert any(e.get("node_id") == "3" and e["error_type"] == "value_not_in_list" for e in errors), errors
        return {"input": "HTTPError('Bad Request: {json}') with no body left to read", "output": "node errors parsed",
                "result": "OK"}
    _run_test(collector, stage, "10.21", "error parser reads a body already consumed", t_10_21)
