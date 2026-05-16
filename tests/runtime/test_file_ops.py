from __future__ import annotations

from pathlib import Path

import pytest

from educator_agency.runtime.file_ops import (
    Accepted,
    ApprovalBuffer,
    ApprovalGatingBackend,
    ChaosBackend,
    Failed,
    LocalFsBackend,
    Pending,
    Rejected,
)


# ---------------------------------------------------------------------------
# LocalFsBackend
# ---------------------------------------------------------------------------


def test_local_write_then_read_roundtrip(tmp_path: Path) -> None:
    backend = LocalFsBackend(tmp_path)
    outcome = backend.write_file(Path("notes/x.md"), "hello\n")
    assert isinstance(outcome, Accepted)
    assert outcome.path == (tmp_path / "notes" / "x.md").resolve()
    assert backend.read_file(Path("notes/x.md")) == "hello\n"


def test_local_creates_parent_dirs(tmp_path: Path) -> None:
    backend = LocalFsBackend(tmp_path)
    outcome = backend.write_file(Path("a/b/c/d.txt"), "deep\n")
    assert isinstance(outcome, Accepted)
    assert (tmp_path / "a" / "b" / "c" / "d.txt").read_text() == "deep\n"


def test_local_writes_bytes(tmp_path: Path) -> None:
    backend = LocalFsBackend(tmp_path)
    outcome = backend.write_file(Path("img.bin"), b"\x00\x01\x02")
    assert isinstance(outcome, Accepted)
    assert (tmp_path / "img.bin").read_bytes() == b"\x00\x01\x02"


def test_local_rejects_path_escape(tmp_path: Path) -> None:
    backend = LocalFsBackend(tmp_path)
    outcome = backend.write_file(Path("../escape.txt"), "nope")
    assert isinstance(outcome, Failed)
    assert "escapes backend root" in outcome.error


def test_local_list_files_recurses(tmp_path: Path) -> None:
    backend = LocalFsBackend(tmp_path)
    backend.write_file(Path("lessons/L1-intro/PLAN.md"), "a")
    backend.write_file(Path("lessons/L1-intro/slides.pptx"), b"b")
    backend.write_file(Path("COURSE.md"), "c")
    found = backend.list_files(Path("."))
    assert {p.name for p in found} == {"PLAN.md", "slides.pptx", "COURSE.md"}


# ---------------------------------------------------------------------------
# ApprovalBuffer
# ---------------------------------------------------------------------------


def test_buffer_add_returns_unique_ids(tmp_path: Path) -> None:
    buffer = ApprovalBuffer()
    a = buffer.add(Path("a.md"), "1", "diff a")
    b = buffer.add(Path("b.md"), "2", "diff b")
    assert a.proposal_id != b.proposal_id
    assert len(buffer) == 2


def test_buffer_pop_removes(tmp_path: Path) -> None:
    buffer = ApprovalBuffer()
    p = buffer.add(Path("a.md"), "1", "diff")
    assert buffer.pop(p.proposal_id) is p
    assert buffer.get(p.proposal_id) is None
    assert len(buffer) == 0


def test_buffer_pop_missing_returns_none() -> None:
    buffer = ApprovalBuffer()
    assert buffer.pop("nonexistent") is None


# ---------------------------------------------------------------------------
# ApprovalGatingBackend
# ---------------------------------------------------------------------------


def test_gating_returns_pending_with_diff(tmp_path: Path) -> None:
    buffer = ApprovalBuffer()
    backend = ApprovalGatingBackend(LocalFsBackend(tmp_path), buffer)
    outcome = backend.write_file(Path("new.md"), "hello world\n")
    assert isinstance(outcome, Pending)
    assert outcome.proposal_id in {p.proposal_id for p in buffer.pending()}
    # New file: unified diff shows the insertion.
    assert "+hello world" in outcome.diff
    # And the file does not yet exist on disk.
    assert not (tmp_path / "new.md").exists()


def test_gating_diff_shows_modification(tmp_path: Path) -> None:
    inner = LocalFsBackend(tmp_path)
    inner.write_file(Path("x.md"), "alpha\nbeta\ngamma\n")
    backend = ApprovalGatingBackend(inner, ApprovalBuffer())
    outcome = backend.write_file(Path("x.md"), "alpha\nBETA\ngamma\n")
    assert isinstance(outcome, Pending)
    assert "-beta" in outcome.diff
    assert "+BETA" in outcome.diff


def test_gating_binary_diff_is_summary(tmp_path: Path) -> None:
    backend = ApprovalGatingBackend(LocalFsBackend(tmp_path), ApprovalBuffer())
    outcome = backend.write_file(Path("deck.pptx"), b"\x00" * 100)
    assert isinstance(outcome, Pending)
    assert "binary file" in outcome.diff
    assert "100 bytes" in outcome.diff


def test_gating_commit_writes_to_disk(tmp_path: Path) -> None:
    buffer = ApprovalBuffer()
    backend = ApprovalGatingBackend(LocalFsBackend(tmp_path), buffer)
    pending = backend.write_file(Path("x.md"), "content\n")
    assert isinstance(pending, Pending)
    result = backend.commit(pending.proposal_id)
    assert isinstance(result, Accepted)
    assert (tmp_path / "x.md").read_text() == "content\n"
    # Buffer is drained after commit.
    assert buffer.get(pending.proposal_id) is None


def test_gating_reject_returns_rejected_and_does_not_write(tmp_path: Path) -> None:
    buffer = ApprovalBuffer()
    backend = ApprovalGatingBackend(LocalFsBackend(tmp_path), buffer)
    pending = backend.write_file(Path("x.md"), "content\n")
    assert isinstance(pending, Pending)
    result = backend.reject(pending.proposal_id, "too verbose")
    assert isinstance(result, Rejected)
    assert result.reason == "too verbose"
    assert not (tmp_path / "x.md").exists()
    assert buffer.get(pending.proposal_id) is None


def test_gating_commit_missing_proposal_fails(tmp_path: Path) -> None:
    backend = ApprovalGatingBackend(LocalFsBackend(tmp_path), ApprovalBuffer())
    result = backend.commit("does-not-exist")
    assert isinstance(result, Failed)


# ---------------------------------------------------------------------------
# ChaosBackend
# ---------------------------------------------------------------------------


def test_chaos_always_rejects(tmp_path: Path) -> None:
    inner = LocalFsBackend(tmp_path)
    backend = ChaosBackend(inner, reject_reason="chaos says no")
    outcome = backend.write_file(Path("anything.md"), "content")
    assert isinstance(outcome, Rejected)
    assert outcome.reason == "chaos says no"
    assert not (tmp_path / "anything.md").exists()


def test_chaos_reads_delegate(tmp_path: Path) -> None:
    inner = LocalFsBackend(tmp_path)
    inner.write_file(Path("existing.md"), "i exist\n")
    backend = ChaosBackend(inner, reject_reason="-")
    assert backend.read_file(Path("existing.md")) == "i exist\n"


def test_local_read_missing_raises(tmp_path: Path) -> None:
    backend = LocalFsBackend(tmp_path)
    with pytest.raises(FileNotFoundError):
        backend.read_file(Path("nope.md"))
