"""Parser for lessons/L<N>-<slug>/PLAN.md.

Schema is defined in
`.claude/instructional-design-swarm/tasks/001_presentation_poc/POC_DESIGN.md`
section 3.2.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, ValidationError

from ._frontmatter import split_frontmatter
from .errors import ParseError


class PlanFrontmatter(BaseModel):
    lesson_id: str = Field(pattern=r"^L\d+$")
    title: str = Field(min_length=1)


class MicroObjective(BaseModel):
    id: str
    parent_number: int
    micro_number: int
    text: str


class PlanDoc(BaseModel):
    frontmatter: PlanFrontmatter
    objectives: list[MicroObjective]
    lecture_outline: str
    key_concepts: str
    worked_examples: str
    references: str


_REQUIRED_SECTIONS = (
    "Learning objectives",
    "Lecture outline",
    "Key concepts and definitions",
    "Worked examples or illustrations",
    "References",
)
_MICRO_BULLET = re.compile(r"^\s*-\s*\*\*LO-(\d+)\.(\d+):\*\*\s*(.+?)\s*$")


def parse(text: str) -> PlanDoc:
    """Parse a PLAN.md document. Raises ParseError on malformed input."""
    fm_dict, body, body_start = split_frontmatter(text)
    try:
        frontmatter = PlanFrontmatter(**fm_dict)
    except ValidationError as exc:
        raise ParseError(f"invalid plan frontmatter: {exc.errors()}") from None

    sections = _split_h1_sections(body, body_offset=body_start)
    for required in _REQUIRED_SECTIONS:
        if required not in sections:
            raise ParseError(f"missing '# {required}' section")

    objectives = _parse_objectives(sections["Learning objectives"])
    return PlanDoc(
        frontmatter=frontmatter,
        objectives=objectives,
        lecture_outline=_section_text(sections["Lecture outline"]),
        key_concepts=_section_text(sections["Key concepts and definitions"]),
        worked_examples=_section_text(sections["Worked examples or illustrations"]),
        references=_section_text(sections["References"]),
    )


def _split_h1_sections(body: str, body_offset: int) -> dict[str, list[tuple[int, str]]]:
    sections: dict[str, list[tuple[int, str]]] = {}
    current: str | None = None
    for i, line in enumerate(body.splitlines()):
        line_no = body_offset + i
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            current = stripped[2:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append((line_no, line))
    return sections


def _parse_objectives(section: list[tuple[int, str]]) -> list[MicroObjective]:
    objectives: list[MicroObjective] = []
    for line_no, line in section:
        match = _MICRO_BULLET.match(line)
        if not match:
            continue
        objectives.append(
            MicroObjective(
                id=f"LO-{match.group(1)}.{match.group(2)}",
                parent_number=int(match.group(1)),
                micro_number=int(match.group(2)),
                text=match.group(3),
            )
        )
    if not objectives:
        raise ParseError(
            "no micro-objectives in '# Learning objectives' "
            "(expected bullets like '- **LO-N.M:** ...')",
            section="Learning objectives",
        )
    return objectives


def _section_text(section: list[tuple[int, str]]) -> str:
    return "\n".join(line for _, line in section).strip()
