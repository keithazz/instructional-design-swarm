"""File-ops backend abstraction with diff-gated approval.

The architectural seam between agents and the filesystem. Per
`.claude/instructional-design-swarm/tasks/001_presentation_poc/POC_DESIGN.md`
§8 and `.claude/instructional-design-swarm/ARCHITECTURE.md` §4.2, extended
with the `Pending(proposal_id)` variant described in
`.claude/plans/i-want-to-start-stateless-oasis.md` §6.2.

Three concrete backends in this module:

- `LocalFsBackend` — real disk writes. Returns `Accepted` or `Failed`.
- `ApprovalGatingBackend` — wraps a `LocalFsBackend`, holds an
  `ApprovalBuffer` of pending proposals, returns `Pending`. The buffer is
  drained externally by the slash-command middleware once the user replies
  with `/approve` or `/reject`.
- `ChaosBackend` — test-only. Deterministically returns `Rejected` so the
  rejection-handling commitment from ARCHITECTURE.md §4.3 is falsifiable in
  Phase 1.
"""

from __future__ import annotations

import difflib
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol, Union


@dataclass(frozen=True)
class Accepted:
    path: Path


@dataclass(frozen=True)
class Rejected:
    reason: str


@dataclass(frozen=True)
class Failed:
    error: str


@dataclass(frozen=True)
class Pending:
    proposal_id: str
    path: Path
    diff: str


WriteOutcome = Union[Accepted, Rejected, Failed, Pending]


class FileOpsBackend(Protocol):
    """All agent file IO routes through implementations of this protocol."""

    def read_file(self, path: Path) -> str: ...

    def write_file(self, path: Path, content: str | bytes) -> WriteOutcome: ...

    def list_files(self, path: Path) -> list[Path]: ...


# ---------------------------------------------------------------------------
# LocalFsBackend
# ---------------------------------------------------------------------------


class LocalFsBackend:
    """Direct disk reads/writes, sandboxed to `root`.

    All paths passed in are resolved relative to `root`; absolute paths that
    escape the root raise `Failed`. Phase 1 keeps the sandboxing strict so
    that the eventual `ObsidianBridgeBackend` (Phase 3) can be a near
    drop-in.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def read_file(self, path: Path) -> str:
        resolved = self._resolve(path)
        return resolved.read_text(encoding="utf-8")

    def write_file(self, path: Path, content: str | bytes) -> WriteOutcome:
        try:
            resolved = self._resolve(path)
        except ValueError as exc:
            return Failed(str(exc))
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                resolved.write_text(content, encoding="utf-8")
            else:
                resolved.write_bytes(content)
        except OSError as exc:
            return Failed(f"{type(exc).__name__}: {exc}")
        return Accepted(path=resolved)

    def list_files(self, path: Path) -> list[Path]:
        resolved = self._resolve(path)
        if not resolved.exists():
            return []
        return sorted(p for p in resolved.rglob("*") if p.is_file())

    def _resolve(self, path: Path) -> Path:
        candidate = (self.root / path).resolve() if not path.is_absolute() else path.resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(
                f"path {candidate} escapes backend root {self.root}"
            ) from exc
        return candidate


# ---------------------------------------------------------------------------
# Approval buffer + gating backend
# ---------------------------------------------------------------------------


@dataclass
class Proposal:
    proposal_id: str
    path: Path
    content: str | bytes
    diff: str


@dataclass
class ApprovalBuffer:
    """In-memory pending-proposal store.

    Lost on server restart; per POC §2 cross-process persistence is deferred
    to Phase 2. Phase 1 acceptance: if the user `Ctrl-C`s mid-approval the
    pending proposal is dropped.
    """

    _store: dict[str, Proposal] = field(default_factory=dict)

    def add(self, path: Path, content: str | bytes, diff: str) -> Proposal:
        proposal_id = secrets.token_hex(4)
        proposal = Proposal(
            proposal_id=proposal_id, path=path, content=content, diff=diff
        )
        self._store[proposal_id] = proposal
        return proposal

    def get(self, proposal_id: str) -> Proposal | None:
        return self._store.get(proposal_id)

    def pop(self, proposal_id: str) -> Proposal | None:
        return self._store.pop(proposal_id, None)

    def pending(self) -> Iterator[Proposal]:
        return iter(list(self._store.values()))

    def __len__(self) -> int:
        return len(self._store)


class ApprovalGatingBackend:
    """Wraps a `LocalFsBackend` and turns every write into a Pending proposal.

    `commit` and `reject` are called by the slash-command middleware after
    the user responds, *not* by agents. Agents only see `Pending` (and
    sometimes `Rejected`, if a previous proposal for the same logical write
    was rejected and the agent is being asked to revise).
    """

    def __init__(self, inner: LocalFsBackend, buffer: ApprovalBuffer) -> None:
        self.inner = inner
        self.buffer = buffer

    def read_file(self, path: Path) -> str:
        return self.inner.read_file(path)

    def list_files(self, path: Path) -> list[Path]:
        return self.inner.list_files(path)

    def write_file(self, path: Path, content: str | bytes) -> WriteOutcome:
        diff = self._render_diff(path, content)
        proposal = self.buffer.add(path=path, content=content, diff=diff)
        return Pending(
            proposal_id=proposal.proposal_id, path=path, diff=diff
        )

    def commit(self, proposal_id: str) -> WriteOutcome:
        proposal = self.buffer.pop(proposal_id)
        if proposal is None:
            return Failed(f"no pending proposal with id {proposal_id!r}")
        return self.inner.write_file(proposal.path, proposal.content)

    def reject(self, proposal_id: str, feedback: str) -> WriteOutcome:
        proposal = self.buffer.pop(proposal_id)
        if proposal is None:
            return Failed(f"no pending proposal with id {proposal_id!r}")
        return Rejected(reason=feedback)

    def _render_diff(self, path: Path, content: str | bytes) -> str:
        # Binary: summary only — no meaningful textual diff.
        if isinstance(content, bytes):
            existing = b""
            try:
                existing = self.inner._resolve(path).read_bytes()
            except (FileNotFoundError, ValueError):
                pass
            marker = "new file" if not existing else "replacing existing file"
            return (
                f"[binary file, {marker}] {path}\n"
                f"  proposed size: {len(content)} bytes\n"
                f"  existing size: {len(existing)} bytes\n"
            )

        existing_text = ""
        try:
            existing_text = self.inner.read_file(path)
        except FileNotFoundError:
            pass

        diff_lines = difflib.unified_diff(
            existing_text.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
        rendered = "".join(diff_lines)
        if not rendered:
            return f"[no change] {path}\n"
        return rendered


# ---------------------------------------------------------------------------
# Chaos backend (test-only)
# ---------------------------------------------------------------------------


class ChaosBackend:
    """Test-only backend that rejects every write with a fixed reason.

    Used to exercise the `Rejected` path the production `LocalFsBackend`
    never produces, falsifying the dual-mode commitment from
    `.claude/instructional-design-swarm/ARCHITECTURE.md` §4.3.
    """

    def __init__(self, inner: FileOpsBackend, *, reject_reason: str) -> None:
        self.inner = inner
        self.reject_reason = reject_reason

    def read_file(self, path: Path) -> str:
        return self.inner.read_file(path)

    def list_files(self, path: Path) -> list[Path]:
        return self.inner.list_files(path)

    def write_file(self, path: Path, content: str | bytes) -> WriteOutcome:
        return Rejected(reason=self.reject_reason)
