"""Tool: generate educator-style slides from HTML fragments and propose via FileOpsBackend.

Replaces the themed `BuildPptxFromHtmlSlides` + `ModifySlide` pipeline with a
simpler flow: the LLM generates HTML body content per slide, this tool wraps
each in a full HTML document using the course's style.css, calls the existing
`html2pptx_runner.js` pipeline, then proposes the resulting bytes via the
`ApprovalGatingBackend`. No sub-agent spawning.

Per `.claude/plans/i-want-to-start-stateless-oasis.md` §6.8.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from agency_swarm.tools import BaseTool
from pydantic import Field

from educator_agency.runtime._context import get_backend
from educator_agency.runtime.file_ops import Accepted, Failed, Pending, Rejected

RUNNER_JS = Path(__file__).parent.parent.parent.parent / "slides_agent" / "tools" / "html2pptx_runner.js"

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1280, height=720">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{
  width: 1280px;
  height: 720px;
  overflow: hidden;
  font-family: "Helvetica Neue", Arial, sans-serif;
}}
{css}
/* Speaker notes are hidden in the slide render */
.speaker-notes {{ display: none; }}
</style>
</head>
<body>
<div class="slide">
{body}
</div>
</body>
</html>
"""


class GenerateEducatorSlides(BaseTool):
    """Generate a PPTX lecture deck from per-slide HTML body fragments.

    Each string in `slides_html` should be the body content of one slide
    (i.e. the content that goes inside `<div class="slide">…</div>`).
    Include a `<div class="speaker-notes">` element at the end of each slide
    body for the post-processing pass that embeds notes into the PPTX.

    The tool wraps each fragment in a minimal full HTML document using
    `css_content` (the course's style.css), runs `html2pptx_runner.js`
    (Playwright + dom-to-pptx), and proposes the resulting file via the
    `ApprovalGatingBackend`. The proposal is NOT committed until the user
    replies `/approve <proposal_id>`.

    Returns the proposal description including proposal_id, path, and diff.
    """

    slides_html: list[str] = Field(
        ...,
        description=(
            "List of slide body HTML fragments, one per slide, in presentation order. "
            "Each fragment is the content inside <div class='slide'>...</div>. "
            "Include <div class='speaker-notes'>3-5 sentence notes</div> at the end of each."
        ),
    )
    css_content: str = Field(
        ...,
        description="Full content of style.css for this course.",
    )
    output_path: str = Field(
        ...,
        description=(
            "Destination path for the PPTX, relative to the course root. "
            "E.g. 'lessons/L1-intro/slides.pptx'."
        ),
    )
    layout: str = Field(
        default="LAYOUT_16x9_1280",
        description="Presentation layout. Default LAYOUT_16x9_1280 (1280x720).",
    )

    def run(self) -> str:
        if not self.slides_html:
            return "Error: slides_html is empty."
        if not RUNNER_JS.exists():
            return (
                f"Error: html2pptx_runner.js not found at {RUNNER_JS}. "
                "Ensure node_modules is installed at the repo root."
            )
        node_modules = RUNNER_JS.parent.parent.parent / "node_modules"
        if not node_modules.exists():
            return "Error: node_modules not found. Run 'npm install' at the repo root."

        with tempfile.TemporaryDirectory(prefix="educator_slides_") as tmp:
            tmp_path = Path(tmp)
            html_files = self._write_html_files(tmp_path)
            pptx_path = tmp_path / "slides.pptx"
            error = self._run_converter(html_files, pptx_path)
            if error:
                return error

            try:
                pptx_bytes = pptx_path.read_bytes()
            except OSError as exc:
                return f"Failed to read generated PPTX: {exc}"

            pptx_bytes = self._add_speaker_notes(pptx_bytes)

        backend = get_backend()
        outcome = backend.write_file(Path(self.output_path), pptx_bytes)

        if isinstance(outcome, Pending):
            return (
                f"Slide deck generated ({len(self.slides_html)} slides) — pending approval.\n"
                f"proposal_id: {outcome.proposal_id}\n"
                f"path: {outcome.path}\n\n"
                f"{outcome.diff}\n"
                f"Reply `/approve {outcome.proposal_id}` to save, or "
                f"`/reject {outcome.proposal_id} <feedback>` to discard."
            )
        if isinstance(outcome, Accepted):
            return f"Slide deck saved: {outcome.path} ({len(self.slides_html)} slides)"
        if isinstance(outcome, Rejected):
            return f"Proposal rejected: {outcome.reason}"
        if isinstance(outcome, Failed):
            return f"Write failed: {outcome.error}"
        return f"Unexpected outcome: {outcome!r}"

    def _write_html_files(self, tmp_path: Path) -> list[str]:
        paths = []
        for i, body in enumerate(self.slides_html):
            html = _HTML_TEMPLATE.format(css=self.css_content, body=body)
            slide_file = tmp_path / f"slide_{i + 1:03d}.html"
            slide_file.write_text(html, encoding="utf-8")
            paths.append(str(slide_file))
        return paths

    def _run_converter(self, html_files: list[str], output_path: Path) -> str | None:
        """Run html2pptx_runner.js. Returns an error string on failure, None on success."""
        cmd = [
            "node",
            str(RUNNER_JS),
            "--output", str(output_path),
            "--layout", self.layout,
            "--",
            *html_files,
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(RUNNER_JS.parent.parent.parent),
            )
        except subprocess.TimeoutExpired:
            return "Error: PPTX conversion timed out after 300 s."
        except FileNotFoundError:
            return "Error: Node.js not found. Please install Node.js."

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            return f"Error converting HTML to PPTX:\n{err}"
        return None

    def _add_speaker_notes(self, pptx_bytes: bytes) -> bytes:
        """Extract speaker-notes divs from slide HTML and embed in PPTX.

        Uses python-pptx to add a notesSlide to each slide. If a slide has no
        speaker-notes div, the notes slide is left empty.
        """
        from io import BytesIO
        import re

        from pptx import Presentation
        from pptx.util import Pt

        _notes_re = re.compile(
            r'<div[^>]*class=["\']speaker-notes["\'][^>]*>(.*?)</div>',
            re.DOTALL | re.IGNORECASE,
        )

        notes_texts: list[str] = []
        for body in self.slides_html:
            match = _notes_re.search(body)
            if match:
                text = re.sub(r"<[^>]+>", "", match.group(1)).strip()
                notes_texts.append(text)
            else:
                notes_texts.append("")

        prs = Presentation(BytesIO(pptx_bytes))
        for i, slide in enumerate(prs.slides):
            if i >= len(notes_texts) or not notes_texts[i]:
                continue
            notes_slide = slide.notes_slide
            tf = notes_slide.notes_text_frame
            tf.text = notes_texts[i]

        out = BytesIO()
        prs.save(out)
        return out.getvalue()
