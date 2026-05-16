"""Parser for lessons/L<N>-<slug>/research.md.

Schema is intentionally loose (POC §3.3). Required only: frontmatter
(lesson_id, generated_at), one H1 (lesson title), and a '# References' H1
at the end with a footnote-style source list.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, ValidationError

from ._frontmatter import split_frontmatter
from .errors import ParseError


class ResearchFrontmatter(BaseModel):
    lesson_id: str = Field(pattern=r"^L\d+$")
    generated_at: datetime


class ResearchDoc(BaseModel):
    frontmatter: ResearchFrontmatter
    title: str
    body: str  # everything between the title H1 and # References
    references: str  # everything under # References (raw)
    footnote_ids: list[int]


_H1 = re.compile(r"^#\s+(.+?)\s*$")
_FOOTNOTE_DEF = re.compile(r"^\[\^(\d+)\]:\s*.+$")


def parse(text: str) -> ResearchDoc:
    fm_dict, body, body_start = split_frontmatter(text)
    try:
        frontmatter = ResearchFrontmatter(**fm_dict)
    except ValidationError as exc:
        raise ParseError(f"invalid research frontmatter: {exc.errors()}") from None

    lines = body.splitlines()
    h1_indices: list[tuple[int, str]] = []  # (line_index_in_body, heading_text)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            h1_indices.append((i, stripped[2:].strip()))

    if not h1_indices:
        raise ParseError("missing H1 (expected a '# <lesson title>' heading)")

    title_idx, title = h1_indices[0]
    refs_idx: int | None = None
    for idx, heading in h1_indices:
        if heading.strip().lower() == "references":
            refs_idx = idx
            break
    if refs_idx is None:
        raise ParseError("missing '# References' H1 at end of document")
    if refs_idx == title_idx:
        raise ParseError(
            "the first H1 must be the lesson title, not '# References'",
            line=body_start + title_idx,
        )

    body_text = "\n".join(lines[title_idx + 1 : refs_idx]).strip()
    refs_text = "\n".join(lines[refs_idx + 1 :]).strip()

    footnote_ids = sorted(
        {int(m.group(1)) for line in refs_text.splitlines() if (m := _FOOTNOTE_DEF.match(line.strip()))}
    )
    if not footnote_ids:
        raise ParseError(
            "no '[^N]: ...' footnote definitions in '# References' section",
            section="References",
        )

    return ResearchDoc(
        frontmatter=frontmatter,
        title=title,
        body=body_text,
        references=refs_text,
        footnote_ids=footnote_ids,
    )
