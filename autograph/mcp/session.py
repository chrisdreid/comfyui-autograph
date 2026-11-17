"""In-memory workflow session store with disk auto-checkpoints.

The MCP server exposes a stateful, conversation-friendly view of workflows:
the LLM gets back a ``workflow_id`` from ``load_workflow`` / ``create_workflow``
and uses it across subsequent ``add_node`` / ``connect_nodes`` / ``run_workflow``
calls. Every mutation auto-saves a snapshot to ``~/.comfyui-autograph/sessions/``
so a crash or IDE restart does not lose work-in-progress.

This module is intentionally tiny — autograph's :class:`Flow` already owns all
the workflow-mutation logic. We just wrap it in a stable id and a save side-effect.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

import autograph


SESSION_DIR_ENV = "AUTOGRAPH_MCP_SESSION_DIR"


def default_session_dir() -> Path:
    override = os.environ.get(SESSION_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".comfyui-autograph" / "sessions"


@dataclass
class WorkflowSession:
    """A single live workflow tracked by the MCP."""

    id: str
    flow: autograph.Flow
    source_path: Optional[str] = None        # original load path, if any
    label: Optional[str] = None
    checkpoint_path: Optional[Path] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class SessionStore:
    """Thread-safe registry of live :class:`WorkflowSession` instances."""

    def __init__(self, session_dir: Optional[Path] = None):
        self._dir = session_dir or default_session_dir()
        self._sessions: Dict[str, WorkflowSession] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        flow: Optional[autograph.Flow] = None,
        *,
        source_path: Optional[str] = None,
        label: Optional[str] = None,
    ) -> WorkflowSession:
        wf = flow if flow is not None else autograph.Flow.create()
        sid = "wf_" + uuid.uuid4().hex[:8]
        session = WorkflowSession(id=sid, flow=wf, source_path=source_path, label=label)
        with self._lock:
            self._sessions[sid] = session
        self._checkpoint(session)
        return session

    def load_from(
        self,
        source: Union[str, Path, Dict[str, Any], bytes],
        *,
        label: Optional[str] = None,
    ) -> WorkflowSession:
        """Load a workflow into a new session (path / JSON string / dict / bytes)."""
        if isinstance(source, (str, Path)) and Path(str(source)).expanduser().exists():
            flow = autograph.Flow(Path(str(source)).expanduser())
            src_str = str(Path(str(source)).expanduser().resolve())
        else:
            flow = autograph.Flow(source)
            src_str = None
        return self.create(flow=flow, source_path=src_str, label=label)

    def get(self, workflow_id: str) -> WorkflowSession:
        with self._lock:
            try:
                return self._sessions[workflow_id]
            except KeyError as exc:
                # LookupError, not KeyError: str(KeyError) wraps the message in quotes.
                raise LookupError(
                    f"No active workflow session with id {workflow_id!r}. "
                    f"Use `list_sessions` to see live ids, or `load_workflow`/`create_workflow` to start one."
                ) from exc

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            out: List[Dict[str, Any]] = []
            for s in self._sessions.values():
                try:
                    nodes = list(s.flow.nodes)
                    node_count = len(nodes)
                except Exception:
                    node_count = -1
                out.append(
                    {
                        "workflow_id": s.id,
                        "label": s.label,
                        "source_path": s.source_path,
                        "checkpoint_path": str(s.checkpoint_path) if s.checkpoint_path else None,
                        "node_count": node_count,
                    }
                )
            return out

    def close(self, workflow_id: str, *, delete_checkpoint: bool = False) -> Dict[str, Any]:
        with self._lock:
            session = self._sessions.pop(workflow_id, None)
        if session is None:
            return {"workflow_id": workflow_id, "ok": False, "error": "not found"}
        result: Dict[str, Any] = {"workflow_id": workflow_id, "ok": True}
        if delete_checkpoint and session.checkpoint_path and session.checkpoint_path.exists():
            try:
                session.checkpoint_path.unlink()
                result["checkpoint_deleted"] = str(session.checkpoint_path)
            except OSError as exc:
                result["checkpoint_delete_error"] = str(exc)
        return result

    # ------------------------------------------------------------------
    # Save / checkpoint
    # ------------------------------------------------------------------

    def save(
        self,
        workflow_id: str,
        path: Optional[Union[str, Path]] = None,
    ) -> Path:
        """Persist a workflow. With no path, writes to its checkpoint location."""
        session = self.get(workflow_id)
        target: Path
        if path is None:
            target = session.checkpoint_path or (self._dir / f"{session.id}.json")
        else:
            target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        # Use Flow.to_json() which handles WidgetValue and friends correctly.
        target.write_text(session.flow.to_json(), encoding="utf-8")
        return target

    def touch(self, workflow_id: str) -> None:
        """Mark a session dirty and write its checkpoint. Call after every mutation."""
        session = self.get(workflow_id)
        self._checkpoint(session)

    def _checkpoint(self, session: WorkflowSession) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            target = self._dir / f"{session.id}.json"
            target.write_text(session.flow.to_json(), encoding="utf-8")
            session.checkpoint_path = target
        except OSError:
            # Disk failure should not abort the user's edit; just leave the snapshot stale.
            pass


# ---------------------------------------------------------------------------
# Polymorphic resolver
# ---------------------------------------------------------------------------


WorkflowArg = Union[str, Dict[str, Any]]


def resolve_flow(
    store: SessionStore,
    workflow: Optional[WorkflowArg] = None,
    workflow_id: Optional[str] = None,
) -> "autograph.Flow":
    """Resolve a tool's ``workflow`` / ``workflow_id`` argument to a live :class:`Flow`.

    Tools can take EITHER ``workflow_id`` (a session id) OR an inline ``workflow``
    (path/JSON/dict). If both are given, ``workflow_id`` wins.
    """
    if workflow_id:
        return store.get(workflow_id).flow
    if workflow is None:
        raise ValueError("Provide either `workflow_id` or `workflow`.")
    if isinstance(workflow, dict):
        return autograph.Flow(workflow)
    if isinstance(workflow, str):
        if workflow.lstrip().startswith(("{", "[")):
            return autograph.Flow(json.loads(workflow))
        p = Path(workflow).expanduser()
        if p.exists() and p.is_file():
            return autograph.Flow(p)
        return autograph.Flow(json.loads(workflow))
    raise TypeError(
        f"workflow must be a JSON string, file path, or dict — got {type(workflow).__name__}"
    )


def session_or_inline(
    store: SessionStore,
    workflow: Optional[WorkflowArg] = None,
    workflow_id: Optional[str] = None,
) -> Optional[WorkflowSession]:
    """Like :func:`resolve_flow` but returns the underlying session if one is live (else None)."""
    if workflow_id:
        return store.get(workflow_id)
    return None
