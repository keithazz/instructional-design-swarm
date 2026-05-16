"""Process-level FileOpsBackend singleton.

Server mode (educator_agency/runtime/server.py) sets an ApprovalGatingBackend
so every write becomes a diff proposal. TUI mode (run_educator.py) sets a plain
LocalFsBackend so writes happen immediately — appropriate for a terminal session.

Agent tools call `get_backend()` either way; they handle all WriteOutcome
variants so the mode is transparent to them.

Using a module global is acceptable for Phase 1 (single user, single process).
Phase 3 (concurrent Obsidian sessions) will replace this with a
`contextvars.ContextVar` keyed by request/session ID.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .file_ops import FileOpsBackend

_backend: "FileOpsBackend | None" = None


def set_backend(backend: "FileOpsBackend") -> None:
    global _backend
    _backend = backend


def get_backend() -> "FileOpsBackend":
    if _backend is None:
        raise RuntimeError(
            "FileOpsBackend is not set. "
            "Call set_backend() before invoking any agent tool that writes files. "
            "In server mode: educator_agency.runtime.server.make_app() does this. "
            "In TUI mode: run_educator.py does this."
        )
    return _backend
