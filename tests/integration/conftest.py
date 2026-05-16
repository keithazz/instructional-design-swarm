"""Integration test fixtures.

Integration tests in this directory exercise the real DOM-to-PPTX pipeline
(Node.js + Playwright) and optionally the LLM. They are skipped automatically
when the required runtime tools are absent.

Mark tests that require LLM API calls with `@pytest.mark.llm` and run them
explicitly: `pytest -m llm tests/integration/`.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from educator_agency.runtime.file_ops import ApprovalBuffer, ApprovalGatingBackend, LocalFsBackend
from educator_agency.runtime._context import set_backend

# ---------------------------------------------------------------------------
# Shared course fixture files (mirrors POC §3 examples)
# ---------------------------------------------------------------------------

SAMPLE_COURSE_MD = """\
---
title: Introduction to Cryptographic Hashing
description: A foundational course on hash functions, their security properties, and their role in modern cryptographic systems.
target_audience: Second-year undergraduate computer science students with prior exposure to discrete mathematics and elementary algorithms.
lesson_count: 6
lesson_duration_minutes: 50
language: en
---

# Learning objectives

1. **LO-1:** Define cryptographic hash functions and articulate the three core security properties.
2. **LO-2:** Compare major hash function families and justify the choice of one over another for a given application.

# Lessons

## L1: Introduction to hashing

- **LO-1.1:** State the definition of a cryptographic hash function and contrast it with non-cryptographic hashes.
- **LO-1.2:** Describe preimage resistance, second-preimage resistance, and collision resistance informally.
- **LO-1.3:** Recognise three categories of real-world applications that depend on hashing.
"""

SAMPLE_PEDAGOGY_MD = """\
# Pedagogical guidance

## Voice

Authoritative but accessible. Clarity over comprehensiveness. Concrete examples over abstract definitions where the topic permits.

## Pedagogical approach

Traditional lecture format. The lecturer presents material in person; the goal is to distill and structure information clearly. No active-learning components, exercises, problem sets, or flipped-classroom expectations in the current version.

## Constraints

Slides should be readable from the back of a lecture hall. Favour short bullets, generous whitespace, clear typography. One main idea per slide. Speaker notes carry the prose; slides carry the scaffolding.
"""

SAMPLE_STYLE_CSS = """\
:root {
  --slide-bg: #ffffff;
  --slide-fg: #1a1a1a;
  --accent: #2c5aa0;
  --muted: #666666;
  --font-body: "Helvetica Neue", Arial, sans-serif;
  --font-heading: "Helvetica Neue", Arial, sans-serif;
  --font-mono: "Menlo", "Courier New", monospace;
}

.slide {
  background: var(--slide-bg);
  color: var(--slide-fg);
  font-family: var(--font-body);
  font-size: 28px;
  line-height: 1.45;
  padding: 60px;
  width: 1280px;
  height: 720px;
}

.slide h1 { font-family: var(--font-heading); font-size: 48px; color: var(--accent); margin: 0 0 0.5em 0; }
.slide h2 { font-family: var(--font-heading); font-size: 36px; margin: 0 0 0.5em 0; }
.slide ul, .slide ol { margin: 0.5em 0; padding-left: 1.2em; }
.slide li { margin-bottom: 0.4em; }
.slide code { font-family: var(--font-mono); background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-size: 0.85em; }
.footnote { font-size: 0.6em; color: var(--muted); position: absolute; bottom: 30px; right: 60px; }
"""

SAMPLE_PLAN_MD = """\
---
lesson_id: L1
title: Introduction to hashing
---

# Learning objectives

- **LO-1.1:** State the definition of a cryptographic hash function and contrast it with non-cryptographic hashes.
- **LO-1.2:** Describe preimage resistance, second-preimage resistance, and collision resistance informally.
- **LO-1.3:** Recognise three categories of real-world applications that depend on hashing.

# Lecture outline

This first lecture opens with a motivating example — the everyday password-check on a login screen — and uses it to introduce the idea of a function that is easy to compute forward but hard to invert. Building on this intuition, we distinguish cryptographic from non-cryptographic hashes by contrasting MD5 with Python's built-in hash(). We then introduce the three security properties informally and close with three real-world application categories.

# Key concepts and definitions

- **Hash function:** A function that maps inputs of arbitrary length to outputs of fixed length.
- **Cryptographic hash function:** A hash function additionally designed to resist computationally bounded adversaries.
- **Digest:** The output of a hash function for a given input.
- **Preimage resistance:** Given a hash output, it is computationally infeasible to find any input that hashes to it.

# Worked examples or illustrations

We will walk through SHA-256("hello") at a structural level (not bit-level), making the fixed-output property concrete. We will also contrast Python's hash() function (non-cryptographic, salted, not stable across runs) with hashlib.sha256() to make the distinction tangible.

# References

[^1]: Menezes, A., van Oorschot, P., & Vanstone, S. (1996). *Handbook of Applied Cryptography*. CRC Press. Chapter 9.
[^2]: Katz, J., & Lindell, Y. (2020). *Introduction to Modern Cryptography* (3rd ed.). Chapman and Hall/CRC.
"""


# ---------------------------------------------------------------------------
# Course-root fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def course_root(tmp_path: Path) -> Path:
    """Minimal valid course root with COURSE.md, PEDAGOGY.md, style.css, and L1 PLAN.md."""
    (tmp_path / "COURSE.md").write_text(SAMPLE_COURSE_MD, encoding="utf-8")
    (tmp_path / "PEDAGOGY.md").write_text(SAMPLE_PEDAGOGY_MD, encoding="utf-8")
    (tmp_path / "style.css").write_text(SAMPLE_STYLE_CSS, encoding="utf-8")
    lesson_dir = tmp_path / "lessons" / "L1-introduction-to-hashing"
    lesson_dir.mkdir(parents=True)
    (lesson_dir / "PLAN.md").write_text(SAMPLE_PLAN_MD, encoding="utf-8")
    return tmp_path


@pytest.fixture
def wired_backend(course_root: Path):
    """Create an ApprovalGatingBackend rooted at course_root and register it in context."""
    buf = ApprovalBuffer()
    inner = LocalFsBackend(course_root)
    gating = ApprovalGatingBackend(inner, buf)
    set_backend(gating)
    yield gating, buf, course_root
    # No teardown needed — tmp_path is cleaned up by pytest


# ---------------------------------------------------------------------------
# Runtime skip markers
# ---------------------------------------------------------------------------


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: requires a live LLM API key")
    config.addinivalue_line("markers", "node: requires Node.js + node_modules")


@pytest.fixture(autouse=False)
def require_node():
    if not shutil.which("node"):
        pytest.skip("Node.js not found")
    runner = Path(__file__).parents[2] / "slides_agent" / "tools" / "html2pptx_runner.js"
    if not runner.exists():
        pytest.skip("html2pptx_runner.js not found")
    node_modules = Path(__file__).parents[2] / "node_modules"
    if not node_modules.exists():
        pytest.skip("node_modules not found — run npm install")
