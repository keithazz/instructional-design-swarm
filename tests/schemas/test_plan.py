from __future__ import annotations

import pytest

from educator_agency.schemas import ParseError
from educator_agency.schemas.plan import parse

VALID = """\
---
lesson_id: L1
title: Introduction to hashing
---

# Learning objectives

- **LO-1.1:** State the definition of a cryptographic hash function.
- **LO-1.2:** Describe preimage resistance, second-preimage resistance, and collision resistance informally.
- **LO-1.3:** Recognise three categories of real-world applications that depend on hashing.

# Lecture outline

This first lecture opens with a motivating example — the everyday password-check on a login screen — and uses it to introduce the idea of a function that is easy to compute forward but hard to invert.

# Key concepts and definitions

- **Hash function:** A function that maps inputs of arbitrary length to outputs of fixed length.
- **Digest:** The output of a hash function for a given input.

# Worked examples or illustrations

We will walk through three short examples during the lecture.

# References

[^1]: Menezes, A., van Oorschot, P., & Vanstone, S. (1996). *Handbook of Applied Cryptography*. CRC Press.
"""


def test_parses_valid_plan() -> None:
    doc = parse(VALID)
    assert doc.frontmatter.lesson_id == "L1"
    assert doc.frontmatter.title == "Introduction to hashing"
    assert [o.id for o in doc.objectives] == ["LO-1.1", "LO-1.2", "LO-1.3"]
    assert "motivating example" in doc.lecture_outline
    assert "Hash function" in doc.key_concepts
    assert "three short examples" in doc.worked_examples
    assert "Menezes" in doc.references


def test_invalid_lesson_id_format_rejected() -> None:
    body = VALID.replace("lesson_id: L1", "lesson_id: lesson1")
    with pytest.raises(ParseError, match="invalid plan frontmatter"):
        parse(body)


def test_missing_lecture_outline_rejected() -> None:
    body = VALID.replace("# Lecture outline", "# Outline")
    with pytest.raises(ParseError, match="Lecture outline"):
        parse(body)


def test_no_objectives_in_section_rejected() -> None:
    body = VALID.replace(
        "- **LO-1.1:** State the definition of a cryptographic hash function.\n"
        "- **LO-1.2:** Describe preimage resistance, second-preimage resistance, and collision resistance informally.\n"
        "- **LO-1.3:** Recognise three categories of real-world applications that depend on hashing.\n",
        "This lesson has no objectives listed.\n",
    )
    with pytest.raises(ParseError, match="no micro-objectives"):
        parse(body)
