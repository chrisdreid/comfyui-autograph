"""autoflow.origin

Small metadata objects for tracking where inputs were resolved from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class NodeInfoOrigin:
    """
    Metadata describing how an node_info dict was obtained.

    - requested: what the caller/environment requested (token, URL, path, etc.)
    - resolved: what we actually loaded from ("dict", "file", "url", "modules", "server", or None)
    - via_env: whether AUTOFLOW_NODE_INFO_SOURCE was used
    - effective_server_url: when resolved from a server, the effective base URL used
    - note: extra context (e.g. fallback details)
    """

    requested: Optional[str] = None
    resolved: Optional[str] = None
    via_env: bool = False
    effective_server_url: Optional[str] = None
    modules_root: Optional[str] = None
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested": self.requested,
            "resolved": self.resolved,
            "via_env": self.via_env,
            "effective_server_url": self.effective_server_url,
            "modules_root": self.modules_root,
            "note": self.note,
        }

