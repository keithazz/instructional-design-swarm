"""FileOpsBackend-routed tools for educator-agency agents.

These are drop-in replacements for `open()`-based file tools. Every agent
that produces course artifacts (COURSE.md, PLAN.md, research.md, slides.pptx)
uses these instead of writing files directly. The runtime decides whether the
write lands immediately (`LocalFsBackend`) or is gated through diff approval
(`ApprovalGatingBackend`); the tool surface and the contract the agent reasons
about are identical in either case.

The backend is retrieved from the process-level context (set by the runtime at
startup). All paths are relative to the course root registered with the backend.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agency_swarm.tools import BaseTool
from pydantic import Field

from educator_agency.runtime._context import get_backend
from educator_agency.runtime.file_ops import Accepted, Failed, Pending, Rejected


class ReadFileTool(BaseTool):
    """Read a file from the course directory.

    Returns the file contents as a string. Raises if the file does not exist.
    """

    path: str = Field(..., description="Path to the file, relative to the course root.")

    def run(self) -> str:
        backend = get_backend()
        return backend.read_file(Path(self.path))


class WriteFileTool(BaseTool):
    """Write a file to the course directory.

    Returns a response that tells you what actually happened. React to it —
    do not assume a particular outcome:

    - File written: tell the user the file is saved and mention the path.
    - Pending user approval: the response includes a diff and the exact
      instructions for the user to follow. Relay both verbatim.
    - Rejected: read the feedback in the response and call `write_file` again
      with revised content. Never retry the same content.
    - Failed: surface the OS error to the user and stop.
    """

    path: str = Field(..., description="Path to the file, relative to the course root.")
    content: str = Field(..., description="Full content to write (UTF-8 text).")

    def run(self) -> str:
        backend = get_backend()
        outcome = backend.write_file(Path(self.path), self.content)

        if isinstance(outcome, Pending):
            return (
                f"Proposal pending user approval.\n"
                f"proposal_id: {outcome.proposal_id}\n"
                f"path: {outcome.path}\n\n"
                f"Diff:\n```diff\n{outcome.diff}\n```\n\n"
                f"Show the user this diff and ask them to reply "
                f"`/approve {outcome.proposal_id}` or "
                f"`/reject {outcome.proposal_id} <feedback>`."
            )
        if isinstance(outcome, Accepted):
            return f"File written: {outcome.path}"
        if isinstance(outcome, Rejected):
            return (
                f"Proposal rejected. Feedback: {outcome.reason}\n"
                "Please revise the content and call write_file again with the updated version."
            )
        if isinstance(outcome, Failed):
            return f"Write failed: {outcome.error}"
        return f"Unexpected outcome: {outcome!r}"


class WriteFileBytesTool(BaseTool):
    """Write a binary file (e.g. slides.pptx) to the course directory.

    Same contract as `WriteFileTool` — the response tells you whether the
    write landed, is pending user approval, was rejected, or failed.
    Accepts a hex-encoded byte string for the content. For binary files the
    diff (if any) is a size summary rather than a textual diff.
    """

    path: str = Field(..., description="Destination path relative to the course root.")
    content_hex: str = Field(
        ...,
        description=(
            "File contents encoded as a hex string (use bytes.hex() to produce this). "
            "The hex string is decoded back to bytes before writing."
        ),
    )

    def run(self) -> str:
        backend = get_backend()
        try:
            content = bytes.fromhex(self.content_hex)
        except ValueError as exc:
            return f"Invalid hex content: {exc}"

        outcome = backend.write_file(Path(self.path), content)

        if isinstance(outcome, Pending):
            return (
                f"Binary proposal pending user approval.\n"
                f"proposal_id: {outcome.proposal_id}\n"
                f"path: {outcome.path}\n\n"
                f"{outcome.diff}\n"
                f"Ask the user to reply `/approve {outcome.proposal_id}` or `/reject {outcome.proposal_id} <feedback>`."
            )
        if isinstance(outcome, Accepted):
            return f"File written: {outcome.path}"
        if isinstance(outcome, Rejected):
            return f"Proposal rejected. Feedback: {outcome.reason}"
        if isinstance(outcome, Failed):
            return f"Write failed: {outcome.error}"
        return f"Unexpected outcome: {outcome!r}"


class ListFilesTool(BaseTool):
    """List all files under a directory in the course root."""

    path: str = Field(
        default=".",
        description="Directory path relative to the course root. Defaults to '.' (the entire course root).",
    )

    def run(self) -> str:
        backend = get_backend()
        files = backend.list_files(Path(self.path))
        if not files:
            return f"No files found under {self.path}."
        return "\n".join(str(p) for p in files)
