from __future__ import annotations

import pytest

from educator_agency.schemas import ParseError
from educator_agency.schemas.course import parse

VALID = """\
---
title: Introduction to Cryptographic Hashing
description: A foundational course on hash functions.
target_audience: Second-year undergraduate CS students.
lesson_count: 6
lesson_duration_minutes: 50
language: en
---

# Learning objectives

1. **LO-1:** Define cryptographic hash functions and articulate the three core security properties.
2. **LO-2:** Compare major hash function families and justify the choice of one over another.
3. **LO-3:** Apply hash functions to construct higher-level cryptographic primitives.

# Lessons

## L1: Introduction to hashing

- **LO-1.1:** State the definition of a cryptographic hash function.
- **LO-1.2:** Describe preimage resistance, second-preimage resistance, and collision resistance informally.
- **LO-1.3:** Recognise three categories of real-world applications that depend on hashing.

## L2: The three security properties formally

- **LO-1.2:** Define preimage, second-preimage, and collision resistance with precise probabilistic statements.
- **LO-1.3:** Explain why these properties are not equivalent and which implies which.
"""


def test_parses_valid_course() -> None:
    doc = parse(VALID)
    assert doc.frontmatter.title == "Introduction to Cryptographic Hashing"
    assert doc.frontmatter.lesson_count == 6
    assert doc.frontmatter.language == "en"
    assert [o.id for o in doc.course_objectives] == ["LO-1", "LO-2", "LO-3"]
    assert [l.number for l in doc.lessons] == [1, 2]
    assert doc.lessons[0].title == "Introduction to hashing"
    assert [m.id for m in doc.lessons[0].micro_objectives] == [
        "LO-1.1",
        "LO-1.2",
        "LO-1.3",
    ]


def test_language_defaults_to_en() -> None:
    body = VALID.replace("language: en\n", "")
    doc = parse(body)
    assert doc.frontmatter.language == "en"


def test_missing_frontmatter_raises() -> None:
    with pytest.raises(ParseError, match="missing YAML frontmatter"):
        parse("# Learning objectives\n\n1. **LO-1:** ...\n")


def test_missing_learning_objectives_section() -> None:
    body = VALID.replace("# Learning objectives", "# Goals")
    with pytest.raises(ParseError, match="Learning objectives"):
        parse(body)


def test_missing_lessons_section() -> None:
    body = VALID.replace("# Lessons", "# Modules")
    with pytest.raises(ParseError, match="Lessons"):
        parse(body)


def test_duplicate_course_lo_rejected() -> None:
    body = VALID.replace("**LO-2:**", "**LO-1:**")
    with pytest.raises(ParseError, match="duplicate course objective LO-1"):
        parse(body)


def test_lesson_with_no_micros_rejected() -> None:
    body = VALID.replace(
        "## L2: The three security properties formally\n\n"
        "- **LO-1.2:** Define preimage, second-preimage, and collision resistance with precise probabilistic statements.\n"
        "- **LO-1.3:** Explain why these properties are not equivalent and which implies which.\n",
        "## L2: Empty lesson\n",
    )
    with pytest.raises(ParseError, match="no micro-objectives"):
        parse(body)


def test_invalid_lesson_count_rejected() -> None:
    body = VALID.replace("lesson_count: 6", "lesson_count: 0")
    with pytest.raises(ParseError, match="invalid course frontmatter"):
        parse(body)


def test_missing_required_field_rejected() -> None:
    body = VALID.replace("target_audience: Second-year undergraduate CS students.\n", "")
    with pytest.raises(ParseError, match="invalid course frontmatter"):
        parse(body)
