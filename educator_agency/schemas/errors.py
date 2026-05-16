from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParseError(Exception):
    """Raised when a course/plan/research markdown file fails to parse.

    The message is surfaced to LLM agents so they can self-correct. Keep it
    specific: what was expected, what was found, where in the file.
    """

    message: str
    line: int | None = None
    section: str | None = None

    def __str__(self) -> str:
        parts = [self.message]
        if self.section:
            parts.append(f"section: {self.section}")
        if self.line is not None:
            parts.append(f"line: {self.line}")
        return " | ".join(parts)
