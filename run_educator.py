"""Educator-agency terminal launcher.

Runs the educator-agency in interactive TUI mode (same as `python swarm.py`
does for the full OpenSwarm). File writes happen immediately — no approval
gate — because the TUI is a direct in-process conversation.

The approval-gate diff flow (/approve, /reject) is a server-mode feature
(educator_agency/runtime/server.py) for use with the OpenCode TUI binary.
In TUI mode you get immediate writes; the agent still reports what it wrote.

Usage:
    python run_educator.py <course_root>
    python run_educator.py ~/courses/crypto-hashing

    # Create a starter course directory if you don't have one yet:
    python run_educator.py ~/courses/my-new-course --init
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.WARNING)

DEFAULT_PEDAGOGY_MD = """\
# Pedagogical guidance

## Voice

Authoritative but accessible. Clarity over comprehensiveness. Concrete examples
over abstract definitions where the topic permits.

## Pedagogical approach

Traditional lecture format. The lecturer presents material in person; the goal
is to distill and structure information clearly. No active-learning components,
exercises, problem sets, or flipped-classroom expectations in the current version.

## Constraints

Slides should be readable from the back of a lecture hall. Favour short bullets,
generous whitespace, clear typography. One main idea per slide. Speaker notes
carry the prose; slides carry the scaffolding.
"""

DEFAULT_STYLE_CSS = """\
/* Educator Agency: course visual style.
 * Edit this file to customize the look of all generated slide decks.
 * Slides are NOT auto-regenerated when this file changes —
 * re-prompt the SlidesAgent to apply updates.
 */
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


def scaffold_course_root(root: Path) -> None:
    """Create a minimal course root with starter files if they don't exist."""
    root.mkdir(parents=True, exist_ok=True)

    pedagogy = root / "PEDAGOGY.md"
    if not pedagogy.exists():
        pedagogy.write_text(DEFAULT_PEDAGOGY_MD, encoding="utf-8")
        print(f"  created {pedagogy}")

    style = root / "style.css"
    if not style.exists():
        style.write_text(DEFAULT_STYLE_CSS, encoding="utf-8")
        print(f"  created {style}")

    lessons = root / "lessons"
    lessons.mkdir(exist_ok=True)

    course_md = root / "COURSE.md"
    if not course_md.exists():
        print(f"  note: COURSE.md not found — ask the CourseDesigner to create it, or use /init")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the educator-agency in interactive TUI mode.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "course_root",
        type=Path,
        nargs="?",
        default=None,
        help="Path to the course root directory. Defaults to EDUCATOR_COURSE_ROOT env var or ./my-course.",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Scaffold starter files (PEDAGOGY.md, style.css, lessons/) if they don't exist.",
    )
    parser.add_argument(
        "--show-reasoning",
        action="store_true",
        default=False,
        help="Show chain-of-thought reasoning in the TUI.",
    )
    args = parser.parse_args()

    # Resolve course root
    course_root: Path = args.course_root or Path(
        os.getenv("EDUCATOR_COURSE_ROOT", "my-course")
    )
    course_root = course_root.expanduser().resolve()

    if args.init or not course_root.exists():
        print(f"Scaffolding course root: {course_root}")
        scaffold_course_root(course_root)
        print()

    if not course_root.exists():
        print(f"Error: course root does not exist: {course_root}", file=sys.stderr)
        print("Run with --init to create starter files.", file=sys.stderr)
        sys.exit(1)

    # Wire the backend — LocalFsBackend for tui() mode (immediate writes, no approval gate)
    from educator_agency.runtime.file_ops import LocalFsBackend
    from educator_agency.runtime._context import set_backend

    backend = LocalFsBackend(course_root)
    set_backend(backend)

    print(f"Educator Agency")
    print(f"Course root: {course_root}")
    print(f"Files in course root: {[p.name for p in course_root.iterdir()]}")
    print()
    print("Starting TUI... (Ctrl-C to exit)")
    print()

    from educator_agency.agency_def.agency import create_educator_agency

    agency = create_educator_agency()
    agency.tui(show_reasoning=True if args.show_reasoning else None, reload=False)


if __name__ == "__main__":
    main()
