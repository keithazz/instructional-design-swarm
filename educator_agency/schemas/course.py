"""Parser for COURSE.md.

Schema is defined in
`.claude/instructional-design-swarm/tasks/001_presentation_poc/POC_DESIGN.md`
section 3.1.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, ValidationError

from ._frontmatter import split_frontmatter
from .errors import ParseError


class CourseFrontmatter(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    target_audience: str = Field(min_length=1)
    lesson_count: int = Field(ge=1)
    lesson_duration_minutes: int = Field(ge=1)
    language: str = "en"


class CourseObjective(BaseModel):
    id: str  # e.g. "LO-1"
    number: int
    text: str


class MicroObjective(BaseModel):
    id: str  # e.g. "LO-1.2"
    parent_number: int
    micro_number: int
    text: str


class Lesson(BaseModel):
    number: int
    title: str
    micro_objectives: list[MicroObjective]


class CourseDoc(BaseModel):
    frontmatter: CourseFrontmatter
    course_objectives: list[CourseObjective]
    lessons: list[Lesson]


_COURSE_LO_LINE = re.compile(r"^\s*\d+\.\s*\*\*LO-(\d+):\*\*\s*(.+?)\s*$")
_LESSON_H2 = re.compile(r"^##\s+L(\d+):\s*(.+?)\s*$")
_MICRO_BULLET = re.compile(r"^\s*-\s*\*\*LO-(\d+)\.(\d+):\*\*\s*(.+?)\s*$")


def parse(text: str) -> CourseDoc:
    """Parse a COURSE.md document. Raises ParseError on malformed input."""
    fm_dict, body, body_start = split_frontmatter(text)
    try:
        frontmatter = CourseFrontmatter(**fm_dict)
    except ValidationError as exc:
        raise ParseError(f"invalid course frontmatter: {exc.errors()}") from None

    sections = _split_h1_sections(body, body_offset=body_start)
    if "Learning objectives" not in sections:
        raise ParseError("missing '# Learning objectives' section")
    if "Lessons" not in sections:
        raise ParseError("missing '# Lessons' section")

    course_objectives = _parse_course_objectives(sections["Learning objectives"])
    lessons = _parse_lessons(sections["Lessons"])
    return CourseDoc(
        frontmatter=frontmatter,
        course_objectives=course_objectives,
        lessons=lessons,
    )


def _split_h1_sections(body: str, body_offset: int) -> dict[str, list[tuple[int, str]]]:
    """Group body lines under their preceding `# Heading`.

    Returns {heading_text: [(line_number, line_text), ...]}.
    Lines before the first H1 are discarded.
    """
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


def _parse_course_objectives(
    section: list[tuple[int, str]],
) -> list[CourseObjective]:
    objectives: list[CourseObjective] = []
    seen_numbers: set[int] = set()
    for line_no, line in section:
        match = _COURSE_LO_LINE.match(line)
        if not match:
            continue  # ignore blank lines, prose, etc.
        number = int(match.group(1))
        if number in seen_numbers:
            raise ParseError(
                f"duplicate course objective LO-{number}",
                line=line_no,
                section="Learning objectives",
            )
        seen_numbers.add(number)
        objectives.append(
            CourseObjective(id=f"LO-{number}", number=number, text=match.group(2))
        )
    if not objectives:
        raise ParseError(
            "no course objectives found (expected lines like '1. **LO-1:** ...')",
            section="Learning objectives",
        )
    return objectives


def _parse_lessons(section: list[tuple[int, str]]) -> list[Lesson]:
    lessons: list[Lesson] = []
    current_number: int | None = None
    current_title: str | None = None
    current_micros: list[MicroObjective] = []
    current_line: int | None = None
    seen_lesson_numbers: set[int] = set()

    def flush() -> None:
        nonlocal current_number, current_title, current_micros, current_line
        if current_number is None:
            return
        if not current_micros:
            raise ParseError(
                f"lesson L{current_number} has no micro-objectives "
                "(expected 3-5 bullets like '- **LO-N.M:** ...')",
                line=current_line,
                section="Lessons",
            )
        lessons.append(
            Lesson(
                number=current_number,
                title=current_title or "",
                micro_objectives=current_micros,
            )
        )
        current_number = None
        current_title = None
        current_micros = []
        current_line = None

    for line_no, line in section:
        h2 = _LESSON_H2.match(line)
        if h2:
            flush()
            num = int(h2.group(1))
            if num in seen_lesson_numbers:
                raise ParseError(
                    f"duplicate lesson L{num}",
                    line=line_no,
                    section="Lessons",
                )
            seen_lesson_numbers.add(num)
            current_number = num
            current_title = h2.group(2)
            current_line = line_no
            continue
        bullet = _MICRO_BULLET.match(line)
        if bullet:
            if current_number is None:
                raise ParseError(
                    "micro-objective bullet found before any '## L<N>: ...' lesson heading",
                    line=line_no,
                    section="Lessons",
                )
            current_micros.append(
                MicroObjective(
                    id=f"LO-{bullet.group(1)}.{bullet.group(2)}",
                    parent_number=int(bullet.group(1)),
                    micro_number=int(bullet.group(2)),
                    text=bullet.group(3),
                )
            )
    flush()

    if not lessons:
        raise ParseError(
            "no lessons found (expected '## L<N>: <Title>' headings)",
            section="Lessons",
        )
    return lessons
