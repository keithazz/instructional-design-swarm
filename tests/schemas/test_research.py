from __future__ import annotations

import pytest

from educator_agency.schemas import ParseError
from educator_agency.schemas.research import parse

VALID = """\
---
lesson_id: L1
generated_at: 2026-05-15T14:32:01Z
---

# Research notes: Introduction to hashing

## Topic 1: Defining cryptographic hash functions

The standard textbook definition characterises a cryptographic hash function as a deterministic function $H: \\{0,1\\}^* \\to \\{0,1\\}^n$ that is efficiently computable [^1].

## Topic 2: Source comparisons

A different framing [^2].

# References

[^1]: Menezes, A., van Oorschot, P., & Vanstone, S. (1996). *Handbook of Applied Cryptography*. CRC Press.
[^2]: Rogaway, P., & Shrimpton, T. (2004). Cryptographic Hash-Function Basics. *FSE 2004*. https://eprint.iacr.org/2004/035
"""


def test_parses_valid_research() -> None:
    doc = parse(VALID)
    assert doc.frontmatter.lesson_id == "L1"
    assert doc.title.startswith("Research notes:")
    assert "Topic 1" in doc.body
    assert "Menezes" in doc.references
    assert doc.footnote_ids == [1, 2]


def test_missing_references_section_rejected() -> None:
    body = VALID.replace("# References", "# Sources")
    with pytest.raises(ParseError, match="missing '# References'"):
        parse(body)


def test_no_footnotes_rejected() -> None:
    body = VALID.replace("[^1]:", "*").replace("[^2]:", "*")
    with pytest.raises(ParseError, match="no '\\[\\^N\\]:"):
        parse(body)


def test_only_references_h1_rejected() -> None:
    body = """\
---
lesson_id: L1
generated_at: 2026-05-15T14:32:01Z
---

# References

[^1]: A source.
"""
    with pytest.raises(ParseError, match="first H1 must be the lesson title"):
        parse(body)


def test_invalid_generated_at_rejected() -> None:
    body = VALID.replace("generated_at: 2026-05-15T14:32:01Z", "generated_at: not-a-date")
    with pytest.raises(ParseError, match="invalid research frontmatter"):
        parse(body)
