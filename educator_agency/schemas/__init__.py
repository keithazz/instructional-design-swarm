"""File-format parsers for the educator-agency POC.

One module per schema. Each module exposes `parse(text)` returning the parsed
model, and raises `ParseError` (from `.errors`) on malformed input. All parsers
are pure functions and exercised by `tests/schemas/`.
"""

from .errors import ParseError

__all__ = ["ParseError"]
