from __future__ import annotations

import yaml

from .errors import ParseError


def split_frontmatter(text: str) -> tuple[dict, str, int]:
    """Split a markdown document with YAML frontmatter.

    Returns (frontmatter_dict, body_text, body_first_line_number).
    Raises ParseError if the frontmatter is missing or malformed.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ParseError(
            "missing YAML frontmatter (file must start with a '---' line)",
            line=1,
        )

    close_idx: int | None = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            close_idx = i
            break
    if close_idx is None:
        raise ParseError(
            "unterminated YAML frontmatter (missing closing '---' line)",
            line=len(lines),
        )

    yaml_block = "\n".join(lines[1:close_idx])
    try:
        data = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError as exc:
        raise ParseError(f"invalid YAML in frontmatter: {exc}", line=1) from None
    if not isinstance(data, dict):
        raise ParseError(
            "frontmatter must be a YAML mapping (key: value pairs)",
            line=1,
        )

    body_lines = lines[close_idx + 1 :]
    return data, "\n".join(body_lines), close_idx + 2
