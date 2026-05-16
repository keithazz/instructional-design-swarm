"""Slash-command dispatcher for the educator-agency.

Intercepts user input before it reaches the agency. Two result types:

- `DirectResponse(text)` — return synthetic reply, do not invoke the agency.
  Used for: /approve, /reject, /help, and unknown commands.
- `Passthrough(message)` — forward `message` to the agency. The message may be
  the original user input (for non-slash text) or a translated prompt (for
  workflow commands like /init and /regenerate-slides L4).

Per `.claude/plans/i-want-to-start-stateless-oasis.md` §6.3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .file_ops import ApprovalGatingBackend, Accepted, Failed, Rejected

_HELP_TEXT = """\
Educator Agency — available commands:

  /approve <id>                    Accept a pending write proposal
  /reject  <id> [feedback]         Reject a pending write proposal (optional feedback returned to agent)
  /init                            Start creating a new course
  /regenerate-slides L<N>          Regenerate slides for lesson N (leaves research.md and PLAN.md unchanged)
  /regenerate-lesson L<N>          Regenerate research, plan, and slides for lesson N

Any other message is sent to the orchestrator as a normal chat turn.
"""

_LESSON_RE = re.compile(r"^L(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class DirectResponse:
    text: str


@dataclass(frozen=True)
class Passthrough:
    message: str


SlashResult = DirectResponse | Passthrough


def dispatch(
    message: str,
    approval_gating: ApprovalGatingBackend | None,
) -> SlashResult:
    """Parse `message` and return how to handle it.

    `approval_gating` may be None during tests that don't need the approval
    flow — approval commands will return an error in that case.
    """
    stripped = message.strip()
    if not stripped.startswith("/"):
        return Passthrough(message=message)

    parts = stripped.split(maxsplit=2)
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd == "/approve":
        return _approve(args, approval_gating)
    if cmd == "/reject":
        return _reject(args, approval_gating)
    if cmd == "/help":
        return DirectResponse(text=_HELP_TEXT)
    if cmd == "/init":
        return Passthrough(
            message=(
                "I want to create a new course. "
                "Please guide me through the process by asking about the topic, "
                "target audience, number of lessons, lesson duration, and any specific scope or depth constraints."
            )
        )
    if cmd in ("/regenerate-slides", "/regenerate_slides"):
        return _regenerate_slides(args)
    if cmd in ("/regenerate-lesson", "/regenerate_lesson"):
        return _regenerate_lesson(args)

    return DirectResponse(text=f"Unknown command: {parts[0]}. Type /help for available commands.")


# ---------------------------------------------------------------------------
# Approval commands
# ---------------------------------------------------------------------------


def _approve(args: list[str], gating: ApprovalGatingBackend | None) -> SlashResult:
    if gating is None:
        return DirectResponse(text="Approval backend is not configured.")
    if not args:
        return DirectResponse(text="Usage: /approve <proposal_id>")

    proposal_id = args[0]
    outcome = gating.commit(proposal_id)

    if isinstance(outcome, Accepted):
        return DirectResponse(
            text=(
                f"Proposal {proposal_id} accepted.\n"
                f"File written: {outcome.path}\n\n"
                "Send your next message to continue."
            )
        )
    if isinstance(outcome, Failed):
        return DirectResponse(
            text=f"Failed to commit proposal {proposal_id}: {outcome.error}"
        )
    return DirectResponse(text=f"Unexpected outcome for proposal {proposal_id}: {outcome!r}")


def _reject(args: list[str], gating: ApprovalGatingBackend | None) -> SlashResult:
    if gating is None:
        return DirectResponse(text="Approval backend is not configured.")
    if not args:
        return DirectResponse(text="Usage: /reject <proposal_id> [feedback]")

    proposal_id = args[0]
    feedback = args[1] if len(args) > 1 else "Rejected by user (no feedback given)."
    outcome = gating.reject(proposal_id, feedback)

    if isinstance(outcome, Rejected):
        return DirectResponse(
            text=(
                f"Proposal {proposal_id} rejected.\n"
                f"Feedback sent to agent: \"{feedback}\"\n\n"
                "The agent will be asked to revise and propose a new version."
            )
        )
    if isinstance(outcome, Failed):
        return DirectResponse(
            text=f"Failed to reject proposal {proposal_id}: {outcome.error}"
        )
    return DirectResponse(text=f"Unexpected outcome: {outcome!r}")


# ---------------------------------------------------------------------------
# Workflow commands (translated into orchestrator-directed prompts)
# ---------------------------------------------------------------------------


def _regenerate_slides(args: list[str]) -> SlashResult:
    if not args or not _LESSON_RE.match(args[0]):
        return DirectResponse(text="Usage: /regenerate-slides L<N>  (e.g. /regenerate-slides L3)")
    lesson = args[0].upper()
    return Passthrough(
        message=(
            f"Please regenerate the slides for lesson {lesson}. "
            f"Read the existing PLAN.md and PEDAGOGY.md for {lesson} but do not modify them. "
            f"Produce a new slides.pptx and show me the diff for approval."
        )
    )


def _regenerate_lesson(args: list[str]) -> SlashResult:
    if not args or not _LESSON_RE.match(args[0]):
        return DirectResponse(text="Usage: /regenerate-lesson L<N>  (e.g. /regenerate-lesson L3)")
    lesson = args[0].upper()
    return Passthrough(
        message=(
            f"Please fully regenerate lesson {lesson}: research, lesson plan, and slides. "
            f"Read the current COURSE.md for the updated learning objectives. "
            f"Show me each artifact for approval before writing the next one."
        )
    )
