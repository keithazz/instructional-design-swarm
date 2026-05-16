from __future__ import annotations

from pathlib import Path

import pytest

from educator_agency.runtime.file_ops import (
    Accepted,
    ApprovalBuffer,
    ApprovalGatingBackend,
    LocalFsBackend,
    Pending,
    Rejected,
)
from educator_agency.runtime.slash_commands import DirectResponse, Passthrough, dispatch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gating(tmp_path: Path):
    buf = ApprovalBuffer()
    inner = LocalFsBackend(tmp_path)
    return ApprovalGatingBackend(inner, buf), buf


# ---------------------------------------------------------------------------
# Non-slash messages pass through unchanged
# ---------------------------------------------------------------------------


def test_plain_text_is_passthrough():
    result = dispatch("generate lesson 1", approval_gating=None)
    assert isinstance(result, Passthrough)
    assert result.message == "generate lesson 1"


def test_empty_string_is_passthrough():
    result = dispatch("", approval_gating=None)
    assert isinstance(result, Passthrough)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------


def test_help_returns_direct_response():
    result = dispatch("/help", approval_gating=None)
    assert isinstance(result, DirectResponse)
    assert "/approve" in result.text
    assert "/reject" in result.text


# ---------------------------------------------------------------------------
# /approve
# ---------------------------------------------------------------------------


def test_approve_commits_write(tmp_path: Path):
    gating, buf = _make_gating(tmp_path)
    pending = gating.write_file(Path("COURSE.md"), "content\n")
    assert isinstance(pending, Pending)

    result = dispatch(f"/approve {pending.proposal_id}", gating)
    assert isinstance(result, DirectResponse)
    assert "accepted" in result.text.lower()
    assert (tmp_path / "COURSE.md").read_text() == "content\n"
    assert buf.get(pending.proposal_id) is None


def test_approve_unknown_id_returns_failed_message(tmp_path: Path):
    gating, _ = _make_gating(tmp_path)
    result = dispatch("/approve deadbeef", gating)
    assert isinstance(result, DirectResponse)
    assert "failed" in result.text.lower()


def test_approve_no_id_returns_usage(tmp_path: Path):
    gating, _ = _make_gating(tmp_path)
    result = dispatch("/approve", gating)
    assert isinstance(result, DirectResponse)
    assert "Usage" in result.text


def test_approve_no_backend_returns_error():
    result = dispatch("/approve abc123", approval_gating=None)
    assert isinstance(result, DirectResponse)
    assert "not configured" in result.text.lower()


# ---------------------------------------------------------------------------
# /reject
# ---------------------------------------------------------------------------


def test_reject_removes_proposal_without_writing(tmp_path: Path):
    gating, buf = _make_gating(tmp_path)
    pending = gating.write_file(Path("COURSE.md"), "content\n")
    assert isinstance(pending, Pending)

    result = dispatch(f"/reject {pending.proposal_id} too long", gating)
    assert isinstance(result, DirectResponse)
    assert "rejected" in result.text.lower()
    assert "too long" in result.text
    assert not (tmp_path / "COURSE.md").exists()
    assert buf.get(pending.proposal_id) is None


def test_reject_default_feedback(tmp_path: Path):
    gating, _ = _make_gating(tmp_path)
    pending = gating.write_file(Path("PLAN.md"), "plan\n")
    assert isinstance(pending, Pending)
    result = dispatch(f"/reject {pending.proposal_id}", gating)
    assert isinstance(result, DirectResponse)
    assert "rejected" in result.text.lower()


# ---------------------------------------------------------------------------
# Workflow commands (translated to Passthrough with structured prompt)
# ---------------------------------------------------------------------------


def test_init_translates_to_passthrough():
    result = dispatch("/init", approval_gating=None)
    assert isinstance(result, Passthrough)
    assert "new course" in result.message.lower()


def test_regenerate_slides_translates():
    result = dispatch("/regenerate-slides L4", approval_gating=None)
    assert isinstance(result, Passthrough)
    assert "L4" in result.message
    assert "slides" in result.message.lower()


def test_regenerate_slides_case_insensitive():
    result = dispatch("/regenerate-slides l2", approval_gating=None)
    assert isinstance(result, Passthrough)
    assert "L2" in result.message


def test_regenerate_slides_missing_arg():
    result = dispatch("/regenerate-slides", approval_gating=None)
    assert isinstance(result, DirectResponse)
    assert "Usage" in result.text


def test_regenerate_lesson_translates():
    result = dispatch("/regenerate-lesson L3", approval_gating=None)
    assert isinstance(result, Passthrough)
    assert "L3" in result.message


# ---------------------------------------------------------------------------
# Unknown commands
# ---------------------------------------------------------------------------


def test_unknown_command_returns_direct_response():
    result = dispatch("/frobnicate", approval_gating=None)
    assert isinstance(result, DirectResponse)
    assert "Unknown command" in result.text
    assert "/help" in result.text
