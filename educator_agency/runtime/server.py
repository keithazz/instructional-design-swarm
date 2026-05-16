"""Educator-agency FastAPI server with slash-command middleware.

Entry point for the educator-agency mode. Sits alongside the original
`server.py` (which runs the full OpenSwarm agency) and is selected by
setting EDUCATOR_AGENCY=1 in the environment, or by running this module
directly.

Architecture:
  - Wraps `agency_swarm.integrations.fastapi.run_fastapi` with return_app=True
  - Injects a `SlashCommandMiddleware` that intercepts messages before they
    reach the agency (for /approve, /reject, /init, /regenerate-* commands)
  - The `ApprovalGatingBackend` and `ApprovalBuffer` are process-level
    singletons stored in `app.state`; agents receive them via dependency
    injection through the tool layer (wired in agency_def/agency.py)

Per `.claude/plans/i-want-to-start-stateless-oasis.md` §6.2, §6.3, §6.5.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _make_sse_text_response(text: str, thread_id: str | None = None) -> str:
    """Encode a plain text reply as an AG-UI SSE event stream."""
    from ag_ui.core import (
        TextMessageContentEvent,
        TextMessageEndEvent,
        TextMessageStartEvent,
        EventType,
        RunFinishedEvent,
        RunStartedEvent,
    )
    from ag_ui.encoder import EventEncoder

    encoder = EventEncoder()
    run_id = str(uuid.uuid4())
    msg_id = str(uuid.uuid4())
    tid = thread_id or str(uuid.uuid4())

    events = [
        RunStartedEvent(type=EventType.RUN_STARTED, thread_id=tid, run_id=run_id),
        TextMessageStartEvent(
            type=EventType.TEXT_MESSAGE_START, message_id=msg_id, role="assistant"
        ),
        TextMessageContentEvent(
            type=EventType.TEXT_MESSAGE_CONTENT, message_id=msg_id, delta=text
        ),
        TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=msg_id),
        RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=tid, run_id=run_id),
    ]
    return "".join(encoder.encode(e) for e in events)


def make_app(course_root: Path) -> Any:
    """Build and return the educator-agency FastAPI app.

    `course_root` is the directory that contains COURSE.md, PEDAGOGY.md,
    style.css, and the lessons/ subdirectory. All FileOpsBackend paths are
    resolved relative to this root.

    The agency factory is deferred to a later implementation step — the
    agency_def is populated as part of task §6.5 and §6.6 of the plan.
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import StreamingResponse
    from starlette.middleware.base import BaseHTTPMiddleware

    from .file_ops import ApprovalBuffer, ApprovalGatingBackend, LocalFsBackend
    from .slash_commands import DirectResponse, Passthrough, dispatch
    from ._context import set_backend

    # Process-level singletons — lost on restart (acceptable for Phase 1).
    _approval_buffer = ApprovalBuffer()
    _local_backend = LocalFsBackend(course_root)
    _approval_gating = ApprovalGatingBackend(_local_backend, _approval_buffer)

    # Make the backend available to all agent tools via the context module.
    set_backend(_approval_gating)

    # Deferred import so the agency isn't instantiated until needed.
    # Replaced with the real factory once educator_agency/agency_def/agency.py exists.
    try:
        from educator_agency.agency_def.agency import create_educator_agency as agency_factory
    except ImportError:
        logger.warning(
            "educator_agency.agency_def.agency not yet implemented — "
            "educator-agency is running without an agency (slash commands work, but chat won't)."
        )
        agency_factory = None

    from agency_swarm.integrations.fastapi import run_fastapi

    agencies = {}
    if agency_factory is not None:
        agencies["educator-agency"] = agency_factory

    app = run_fastapi(
        agencies=agencies if agencies else None,
        port=8080,
        enable_logging=True,
        allowed_local_file_dirs=[str(course_root)],
        return_app=True,
    )
    if app is None:
        # run_fastapi returns None when no agencies/tools are provided.
        # For development before agency_def exists, create a bare app.
        app = FastAPI()

    # Attach singletons to app state so they're accessible elsewhere.
    app.state.approval_buffer = _approval_buffer
    app.state.approval_gating = _approval_gating
    app.state.local_backend = _local_backend
    app.state.course_root = course_root

    class SlashCommandMiddleware(BaseHTTPMiddleware):
        """Intercept slash commands before they reach the agency."""

        WATCHED_PATHS = {
            "/educator-agency/get_response",
            "/educator-agency/get_response_stream",
        }

        async def dispatch(self, request: Request, call_next):
            if request.method != "POST" or request.url.path not in self.WATCHED_PATHS:
                return await call_next(request)

            body = await request.body()
            try:
                data: dict[str, Any] = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                request._body = body
                return await call_next(request)

            raw_message = data.get("message", "")
            if not isinstance(raw_message, str):
                # Structured message (list of content parts) — not a slash command.
                request._body = body
                return await call_next(request)

            result = dispatch(raw_message, _approval_gating)

            if isinstance(result, DirectResponse):
                thread_id = data.get("thread_id") or data.get("threadId")
                sse_body = _make_sse_text_response(result.text, thread_id=thread_id)
                return StreamingResponse(
                    iter([sse_body.encode()]),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )

            if isinstance(result, Passthrough) and result.message != raw_message:
                # Translated workflow command — replace message in body.
                data["message"] = result.message
                body = json.dumps(data).encode()

            # Re-inject body so the downstream handler can consume it.
            request._body = body
            return await call_next(request)

    app.add_middleware(SlashCommandMiddleware)
    return app


def run(course_root: Path, port: int = 8080) -> None:
    """Start the educator-agency server."""
    import uvicorn

    app = make_app(course_root)

    logger.info(f"Starting educator-agency server at http://localhost:{port}")
    logger.info(f"Course root: {course_root}")

    try:
        uvicorn.run(app, host="0.0.0.0", port=port, ws="websockets-sansio")
    except (TypeError, ValueError):
        uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Educator-agency server")
    parser.add_argument("course_root", type=Path, help="Path to the course root directory")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    run(args.course_root, port=args.port)
