"""Tool: generate educator-style slides from structured slide briefs.

Per `.claude/plans/i-want-to-start-stateless-oasis.md` §6.8 and the
`002_presentation_styling` follow-up plan: the slides agent emits one
`SlideBrief` per slide; this tool calls a stateless HTML-writer sub-agent
per slide (driven by `html_writer_instructions.md` in this directory),
concatenates the resulting HTML fragments, wraps each in the course
style.css, runs `html2pptx_runner.js`, then writes the PPTX through the
active `FileOpsBackend`.

Architectural anchors:
- The tool's external contract stays one-shot and backend-gated.
- Sub-agents are internal, stateless, and never write to disk.
- All file writes route through `get_backend()`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from agency_swarm import Agent, ModelSettings, Reasoning
from agency_swarm.tools import BaseTool
from agents.extensions.models.litellm_model import LitellmModel
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from educator_agency.runtime._context import get_backend
from educator_agency.runtime.file_ops import Accepted, Failed, Pending, Rejected

RUNNER_JS = Path(__file__).parent.parent.parent.parent / "slides_agent" / "tools" / "html2pptx_runner.js"
_HTML_WRITER_INSTRUCTIONS = Path(__file__).with_name("html_writer_instructions.md")
_HTML_WRITER_MODEL_CLAUDE = "anthropic/claude-sonnet-4-6"
_HTML_WRITER_MODEL_OAI = "gpt-5.3-codex"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Logger is configured eagerly at import time so events flow regardless of
# how the host process set up logging. Two sinks:
#   1. stderr at INFO  — per-slide events visible in the TUI/server console.
#   2. <debug_dir>/slides.log at DEBUG — full prompts, raw responses, etc.
# Per-slide artifacts (prompt + raw response + extracted fragment) are also
# dumped to <debug_dir>/<run_timestamp>/slide_NNN.* so they can be diffed.
#
# debug_dir resolution:
#   $SLIDES_DEBUG_DIR if set, else Path.cwd() / ".slides_debug"
# To silence: set $SLIDES_LOG=quiet (drops the stderr handler).
# To go fully verbose on stderr too: set $SLIDES_LOG=debug.

logger = logging.getLogger("educator_agency.slides")


def _debug_dir() -> Path:
    base = os.getenv("SLIDES_DEBUG_DIR")
    return Path(base).expanduser() if base else Path.cwd() / ".slides_debug"


def _configure_logging() -> None:
    """Idempotent: only attaches handlers the first time it's called."""
    if getattr(logger, "_slides_configured", False):
        return
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # avoid double-logging via root

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
    )

    stderr_mode = os.getenv("SLIDES_LOG", "info").lower()
    if stderr_mode != "quiet":
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(
            logging.DEBUG if stderr_mode == "debug" else logging.INFO
        )
        stderr_handler.setFormatter(fmt)
        logger.addHandler(stderr_handler)

    try:
        debug_root = _debug_dir()
        debug_root.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(debug_root / "slides.log", mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError as exc:  # debug dir is optional; don't crash the tool
        logger.warning("Could not attach slides.log file handler: %s", exc)

    logger._slides_configured = True  # type: ignore[attr-defined]


_configure_logging()


def _make_run_artifact_dir() -> Path | None:
    """Create a per-run subdir under the debug dir, or return None on failure."""
    try:
        run_dir = _debug_dir() / datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir
    except OSError as exc:
        logger.warning("Could not create per-run artifact dir: %s", exc)
        return None


def _truncate(text: str, limit: int = 800) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated {len(text) - limit} chars]"


def _dump(run_dir: Path | None, slide_index: int, name: str, content: str) -> None:
    """Best-effort artifact dump. Silent on failure — logging is the fallback."""
    if run_dir is None:
        return
    try:
        (run_dir / f"slide_{slide_index:03d}_{name}").write_text(content, encoding="utf-8")
    except OSError as exc:
        logger.debug("Could not dump %s for slide %d: %s", name, slide_index, exc)


def _surface_runner_output(stdout: str, stderr: str) -> None:
    """Parse + log the Node runner's stdout/stderr.

    The runner emits structured prefixed lines we care about:
      [fonts] "Inter" — 78 KB           (font pre-download)
      [fonts] 3 font(s) ready for embedding
      [fa] Icons materialized in slide bodies
      [page error] ReferenceError: …    (browser-side error)
      [page console] Uncaught …         (browser console.error)
      [debug] Orchestrator HTML kept …  (our env-gated artifact)

    Anything else is logged at DEBUG. Page errors/warnings are logged at
    WARNING so they show up in the stderr console.
    """
    if not stdout and not stderr:
        return
    fonts_total: int | None = None
    fonts_downloaded: list[str] = []
    page_errors: list[str] = []
    other_warnings: list[str] = []

    for line in (stdout + "\n" + stderr).splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("[fonts] ") or s.startswith("[fa] "):
            # Font download log lines (info-worthy)
            logger.info("runner: %s", s)
            m = re.match(r"\[fonts\]\s+(\d+)\s+font\(s\)\s+ready", s)
            if m:
                fonts_total = int(m.group(1))
            m2 = re.match(r"\[fonts\]\s+\"([^\"]+)\"", s)
            if m2:
                fonts_downloaded.append(m2.group(1))
        elif s.startswith("[page error]") or s.startswith("[page console]"):
            page_errors.append(s)
            logger.warning("runner: %s", s)
        elif s.startswith("[debug]"):
            logger.info("runner: %s", s)
        elif s.startswith("Saved:") or s.startswith("Converted "):
            logger.debug("runner: %s", s)
        elif "warn" in s.lower() or "error" in s.lower():
            other_warnings.append(s)
            logger.warning("runner: %s", s)
        else:
            logger.debug("runner: %s", s)

    if fonts_total is not None:
        if fonts_total == 0:
            logger.warning(
                "runner embedded 0 fonts — PPTX will use PowerPoint's default fallback "
                "(usually Calibri/Times). Check the wrapped HTML has working "
                "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/...\"> tags."
            )
        else:
            logger.info(
                "runner embedded %d font(s): %s",
                fonts_total, ", ".join(fonts_downloaded) or "(names not logged)",
            )
    elif fonts_downloaded:
        logger.info("runner downloaded font(s): %s", ", ".join(fonts_downloaded))
    else:
        logger.warning(
            "runner output had no [fonts] log lines — pre-download may have failed "
            "or no Google Fonts <link> tags were emitted in the wrapped HTML."
        )

    if page_errors:
        logger.warning("runner reported %d page-level error(s) above", len(page_errors))

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1280, height=720">
{font_links}
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: 1280px; height: 720px; overflow: hidden; }}
{css}
</style>
</head>
<body>
<div class="slide">
{body}
</div>
</body>
</html>
"""


_GOOGLE_FONTS_IMPORT_RE = re.compile(
    r'@import\s+url\(\s*["\']?(https?://fonts\.googleapis\.com/[^"\')\s]+)["\']?\s*\)\s*;?',
    re.IGNORECASE,
)

# Image-related extensions the preprocessing helpers recognise.
# Mirrors slides_agent/tools/ModifySlide.py:166-170 so behaviour is consistent.
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif"}


def _is_image_path(src: str) -> bool:
    return Path(src.split("?")[0]).suffix.lower() in _IMAGE_EXTENSIONS


def _convert_css_bg_images_to_img_tags(html: str) -> str:
    """Convert CSS background-image url(...) to <img> tags.

    Ported from slides_agent/tools/ModifySlide.py:62-163. dom-to-pptx walks
    DOM nodes; CSS background paints aren't DOM nodes and silently disappear
    in the PPTX. Replacing them with an absolutely-positioned <img> as the
    element's first child preserves the visual.

    Handles two patterns:
      1. Inline style:  <div style="background-image: url(img.png)">
      2. Class-based:   .cls { background-image: url(img.png) } + <div class="cls">

    Leaves remote URLs (http://, https://, file://) and non-image data: URIs
    untouched. CSS gradients (linear-gradient, radial-gradient) are left
    untouched too — the runner rasterises those separately.
    """
    _BG_STRIP_RE = re.compile(
        r'\bbackground-image\s*:\s*url\([^)]*\)\s*;?\s*'
        r'|\bbackground-size\s*:\s*[^;]+;\s*'
        r'|\bbackground-position\s*:\s*[^;]+;\s*'
        r'|\bbackground-repeat\s*:\s*[^;]+;\s*',
        re.IGNORECASE,
    )

    def _img_tag(src: str) -> str:
        return (
            f'<img src="{src}" alt="" '
            f'style="position:absolute;top:0;left:0;width:100%;height:100%;'
            f'object-fit:cover;z-index:0;" />'
        )

    def _should_convert(url_arg: str) -> bool:
        if url_arg.startswith("data:image/"):
            return True
        if url_arg.startswith(("data:", "http://", "https://", "file://")):
            return False
        return _is_image_path(url_arg)

    # 1. Inline style="...background-image: url(...)..."
    inline_re = re.compile(
        r'(<[a-zA-Z][^>]*?style=["\'])([^"\']*?background-image\s*:\s*url\(([^)]+)\)[^"\']*?)(["\'][^>]*>)',
        re.IGNORECASE,
    )

    def rewrite_inline(m: re.Match) -> str:
        before, style_val, url_raw, after = m.group(1), m.group(2), m.group(3), m.group(4)
        url_arg = url_raw.strip("\"' ")
        if not _should_convert(url_arg):
            return m.group(0)
        clean = _BG_STRIP_RE.sub('', style_val).strip().rstrip(';')
        return f'{before}{clean}{after}{_img_tag(url_arg)}'

    html = inline_re.sub(rewrite_inline, html)

    # 2. Class-based rules in <style> blocks.
    style_block_re = re.compile(r'<style[^>]*>(.*?)</style>', re.IGNORECASE | re.DOTALL)
    css_class_bg_re = re.compile(
        r'\.([a-zA-Z_-][\w-]*)\s*\{([^}]*?background-image\s*:\s*url\(([^)]+)\)[^}]*?)\}',
        re.IGNORECASE | re.DOTALL,
    )

    class_to_url: dict[str, str] = {}
    for style_m in style_block_re.finditer(html):
        for rule_m in css_class_bg_re.finditer(style_m.group(1)):
            cls = rule_m.group(1)
            url_arg = rule_m.group(3).strip("\"' ")
            if _should_convert(url_arg):
                class_to_url[cls] = url_arg

    if not class_to_url:
        return html

    def rewrite_style_block(style_m: re.Match) -> str:
        css = style_m.group(1)

        def clean_rule(rule_m: re.Match) -> str:
            cls = rule_m.group(1)
            if cls not in class_to_url:
                return rule_m.group(0)
            cleaned_body = _BG_STRIP_RE.sub('', rule_m.group(2)).strip().rstrip(';')
            return f'.{cls} {{{cleaned_body}}}'

        return f'<style>{css_class_bg_re.sub(clean_rule, css)}</style>'

    html = style_block_re.sub(rewrite_style_block, html)

    class_pattern = '|'.join(re.escape(c) for c in class_to_url)
    element_re = re.compile(
        rf'(<[a-zA-Z][^>]*?class=["\'][^"\']*?(?:{class_pattern})[^"\']*?["\'][^>]*>)',
        re.IGNORECASE,
    )

    def inject_img(m: re.Match) -> str:
        opening = m.group(1)
        classes = re.search(r'class=["\']([^"\']+)["\']', opening, re.IGNORECASE)
        if not classes:
            return opening
        for cls in classes.group(1).split():
            if cls in class_to_url:
                return f'{opening}{_img_tag(class_to_url[cls])}'
        return opening

    html = element_re.sub(inject_img, html)
    return html


def _embed_local_images_as_base64(html: str, project_dir: Path) -> str:
    """Replace local image references with base64 data URIs.

    Ported from slides_agent/tools/ModifySlide.py:173-219. Encoding inline
    avoids any reliance on the runner's relative-path resolution and removes
    a class of silent failures where the renderer cannot find an asset.

    Handles HTML src=, CSS url(), SVG href/xlink:href, and <object data=>.
    Only processes paths with known image file extensions to avoid
    accidentally encoding scripts, stylesheets, or fonts.
    """
    import base64
    import mimetypes

    def _encode(src: str) -> str | None:
        if (
            src.startswith("data:")
            or src.startswith("http://")
            or src.startswith("https://")
            or src.startswith("file://")
            or not _is_image_path(src)
        ):
            return None
        img_path = (project_dir / src).resolve()
        if not img_path.exists():
            return None
        mime, _ = mimetypes.guess_type(str(img_path))
        mime = mime or "image/png"
        encoded = base64.b64encode(img_path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def replace_src(match: re.Match) -> str:
        quote, src = match.group(1), match.group(2)
        data_uri = _encode(src)
        return f"src={quote}{data_uri}{quote}" if data_uri else match.group(0)

    def replace_css_url(match: re.Match) -> str:
        quote, src = match.group(1), match.group(2)
        data_uri = _encode(src)
        return f"url({quote}{data_uri}{quote})" if data_uri else match.group(0)

    def replace_href(match: re.Match) -> str:
        attr, quote, src = match.group(1), match.group(2), match.group(3)
        data_uri = _encode(src)
        return f'{attr}={quote}{data_uri}{quote}' if data_uri else match.group(0)

    html = re.sub(r'src=(["\'])((?!data:|https?://|file://)[^"\']+)\1', replace_src, html)
    html = re.sub(r'url\((["\']?)((?!data:|https?://|file://)[^"\')\s]+)\1\)', replace_css_url, html)
    html = re.sub(
        r'(href|xlink:href|data)=(["\'])((?!data:|https?://|file://|#)[^"\']+)\2',
        replace_href,
        html,
    )
    return html


def _strip_base64_images(html: str) -> str:
    """Replace base64 data URI image references with short placeholders.

    Ported from slides_agent/tools/ModifySlide.py:48-59. Used when feeding
    a previous-attempt HTML back to the sub-agent on a retry, so the LLM
    sees structural context without spending tokens on multi-MB base64 blobs.
    """
    html = re.sub(r'src=(["\'])data:image/[^"\']+\1', r'src=\1[image]\1', html)
    html = re.sub(r'url\((["\']?)data:image/[^"\')\s]+\1\)', r'url(\1[image]\1)', html)
    html = re.sub(r'(href|xlink:href|data)=(["\'])data:image/[^"\']+\2', r'\1=\2[image]\2', html)
    return html


_VALID_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"}


def _collect_local_image_refs(html: str) -> list[str]:
    """Find local-looking image refs (img src / url()) in HTML."""
    refs: list[str] = []
    for m in re.finditer(r'<img[^>]+src\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE):
        refs.append(m.group(1).strip())
    for m in re.finditer(r'url\s*\(\s*["\']?([^"\')\s]+)["\']?\s*\)', html, re.IGNORECASE):
        refs.append(m.group(1).strip())
    local: list[str] = []
    for ref in refs:
        low = ref.lower()
        if low.startswith(("http://", "https://", "data:")):
            continue
        local.append(ref)
    return local


def _validate_image_refs(course_root: Path, html: str) -> list[str]:
    """Verify every local image ref exists under course_root and is a real image."""
    errors: list[str] = []
    course_root = course_root.resolve()
    seen: set[str] = set()
    for ref in _collect_local_image_refs(html):
        if not ref or ref in seen:
            continue
        seen.add(ref)
        normalized = ref.lstrip("/").replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        full = (course_root / normalized).resolve()
        try:
            full.relative_to(course_root)
        except (ValueError, TypeError):
            errors.append(f"Image path escapes course root: {ref}")
            continue
        if not full.exists():
            errors.append(f"Image file not found: {ref} (resolved to {full})")
            continue
        if full.suffix.lower() not in _VALID_IMAGE_EXTENSIONS:
            errors.append(
                f"Image '{ref}' has unsupported extension '{full.suffix}'. "
                f"Use one of: {', '.join(sorted(_VALID_IMAGE_EXTENSIONS))}"
            )
            continue
        try:
            with open(full, "rb") as f:
                header = f.read(50)
            if header.startswith(b"<") or b"<html" in header.lower():
                errors.append(
                    f"Image '{ref}' is not a valid image file (looks like HTML)."
                )
        except OSError:
            pass
    return errors


# Inline-badge regex: matches a <p>/<li> that contains plain text followed by
# a styled-background span/code/a — these split PPTX text boxes. Mirrors
# slides_agent/tools/slide_html_utils.py:181-192.
_INLINE_BADGE_IN_TEXT = re.compile(
    r'<(?:p|li)[^>]*>[^<]+<(?:span|code|a)[^>]+style=["\'][^"\']*background[^"\']*["\'][^>]*>[^<]+</(?:span|code|a)>',
    re.IGNORECASE | re.DOTALL,
)
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF]")
_EMPTY_DOT_RE = re.compile(
    r'<span[^>]*class=["\'][^"\']*\bdot\b[^"\']*["\'][^>]*>\s*</span>',
    re.IGNORECASE,
)


def _validate_slide(wrapped_html: str, course_root: Path) -> dict:
    """Run sync-Playwright validation on a fully-wrapped slide HTML.

    Ported from slides_agent/tools/slide_html_utils.py:163-339 (validate_html).
    Returns {"valid": bool, "error": str}.

    Checks:
      - Local image refs exist and are valid image files.
      - No emoji / Unicode pictographs (don't render reliably in PPTX).
      - No empty <span class="dot"></span> (invisible bullet markers).
      - No styled badges/pills inline within flowing <p>/<li> text
        (fragments PPTX text boxes).
      - Body dimensions match 1280x720 within 2px tolerance.
      - No horizontal/vertical content overflow.
      - No text within 3px of the bottom edge (descender clipping).
      - No naked text nodes inside <div> (dom-to-pptx mishandles them).

    Designed to be called via `asyncio.to_thread(...)` from the async tool
    `run()` so the sync Playwright API doesn't block the event loop.
    """
    errors: list[str] = []
    errors.extend(_validate_image_refs(course_root, wrapped_html))

    if _EMOJI_RE.search(wrapped_html):
        errors.append(
            "Emoji/Unicode symbols detected. Use inline SVG or image icons instead."
        )
    if _EMPTY_DOT_RE.search(wrapped_html):
        errors.append(
            "Empty <span class='dot'></span> bullets detected. Replace with "
            "inline SVG circles or image assets — empty spans render blank in PPTX."
        )
    if _INLINE_BADGE_IN_TEXT.search(wrapped_html):
        errors.append(
            "Styled badge/pill detected inline within <p> or <li> text. "
            "Inline elements with background-color split the surrounding sentence "
            "into separate PPTX text boxes. Move the badge to its own line / container."
        )

    # Write to a tempfile under the course root so relative ./assets/ paths
    # in the slide HTML resolve against the right base.
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".html",
        delete=False,
        encoding="utf-8",
        dir=str(course_root),
    ) as f:
        f.write(wrapped_html)
        temp_path = f.name

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.goto(f"file://{temp_path}", wait_until="load")

            dims = page.evaluate(
                """() => {
                    const body = document.body;
                    const slide = document.querySelector('.slide') || body;
                    const style = window.getComputedStyle(body);
                    return {
                        width: parseFloat(style.width),
                        height: parseFloat(style.height),
                        scrollWidth: Math.max(body.scrollWidth, slide.scrollWidth),
                        scrollHeight: Math.max(body.scrollHeight, slide.scrollHeight),
                    };
                }"""
            )

            if abs(dims["width"] - 1280) > 2:
                errors.append(f"Body width must be 1280px, got {dims['width']:.0f}px.")
            if abs(dims["height"] - 720) > 2:
                errors.append(f"Body height must be 720px, got {dims['height']:.0f}px.")

            w_over = max(0, dims["scrollWidth"] - dims["width"] - 1)
            h_over = max(0, dims["scrollHeight"] - dims["height"] - 1)
            if w_over > 0:
                errors.append(
                    f"Content overflows horizontally by {w_over:.0f}px. "
                    "Reduce content width, font size, or padding."
                )
            if h_over > 0:
                errors.append(
                    f"Content overflows vertically by {h_over:.0f}px. "
                    "Reduce content height, font size, or move elements up from the bottom."
                )

            descender_issues = page.evaluate(
                """() => {
                    const body = document.body;
                    const bodyRect = body.getBoundingClientRect();
                    const els = Array.from(body.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li, span'));
                    const issues = [];
                    for (const el of els) {
                        const r = el.getBoundingClientRect();
                        const dist = bodyRect.bottom - r.bottom;
                        if (dist >= -1 && dist < 3 && el.textContent.trim()) {
                            issues.push({ text: el.textContent.trim().substring(0, 30), dist });
                        }
                    }
                    return issues;
                }"""
            )
            if descender_issues:
                for issue in descender_issues[:2]:
                    errors.append(
                        f"Text '{issue['text']}…' is too close to the bottom edge "
                        f"({issue['dist']:.1f}px). Move it up at least 5-10px to avoid descender clipping."
                    )

            if w_over > 0 or h_over > 0:
                offenders = page.evaluate(
                    """() => {
                        const body = document.body;
                        const br = body.getBoundingClientRect();
                        const out = [];
                        for (const el of body.querySelectorAll('*')) {
                            const r = el.getBoundingClientRect();
                            const right = Math.max(0, r.right - br.right);
                            const bottom = Math.max(0, r.bottom - br.bottom);
                            const left = Math.max(0, br.left - r.left);
                            const top = Math.max(0, br.top - r.top);
                            if (right || bottom || left || top) {
                                out.push({
                                    tag: el.tagName.toLowerCase(),
                                    id: el.id || '',
                                    className: el.className || '',
                                    right, bottom, left, top,
                                    area: Math.max(0, r.width) * Math.max(0, r.height),
                                });
                            }
                        }
                        out.sort((a, b) => b.area - a.area);
                        return out.slice(0, 3);
                    }"""
                )
                if offenders:
                    errors.append("Top overflow offenders:")
                    for off in offenders:
                        ident = off["tag"]
                        if off["id"]:
                            ident += f"#{off['id']}"
                        if off["className"]:
                            ident += "." + str(off["className"]).strip().replace(" ", ".")
                        errors.append(
                            f"  - {ident} (R:{off['right']:.0f}px, B:{off['bottom']:.0f}px, "
                            f"L:{off['left']:.0f}px, T:{off['top']:.0f}px)"
                        )

            unwrapped = page.evaluate(
                """() => {
                    const issues = [];
                    // Skip elements that are hidden (display:none or visibility:hidden)
                    // since dom-to-pptx never sees them — most notably the
                    // .speaker-notes div, whose text is harvested separately for
                    // the PPTX notesSlide.
                    const isHidden = (el) => {
                        for (let cur = el; cur && cur !== document.body.parentElement; cur = cur.parentElement) {
                            const s = window.getComputedStyle(cur);
                            if (s.display === 'none' || s.visibility === 'hidden') return true;
                        }
                        return false;
                    };
                    document.querySelectorAll('div').forEach(div => {
                        if (isHidden(div)) return;
                        for (const n of div.childNodes) {
                            if (n.nodeType === Node.TEXT_NODE && n.textContent.trim()) {
                                const t = n.textContent.trim().substring(0, 50);
                                issues.push(t + (n.textContent.trim().length > 50 ? '…' : ''));
                            }
                        }
                    });
                    return issues;
                }"""
            )
            if unwrapped:
                errors.append("Naked text inside <div> (must be wrapped in <p>/<h*>/<li>):")
                for t in unwrapped[:3]:
                    errors.append(f"  - '{t}'")

            browser.close()
    except Exception as exc:
        errors.append(f"Validation error: {exc}")
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    if errors:
        return {"valid": False, "error": "\n".join(errors)}
    return {"valid": True, "error": ""}


def _backend_root() -> Path:
    """Resolve the backend's filesystem root (the course root).

    Duck-types `.root` (LocalFsBackend) and `.inner.root` (gating wrappers).
    Falls back to Path.cwd() so the helper never raises; the validation
    pass will simply skip image-existence checks for the wrong root.
    """
    backend = get_backend()
    root = getattr(backend, "root", None)
    if root is None:
        inner = getattr(backend, "inner", None)
        root = getattr(inner, "root", None)
    if root is None:
        logger.warning("Backend has no .root attribute; falling back to CWD for image preprocessing.")
        return Path.cwd()
    return Path(root)


_CSS_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
# Match @import url(...);  — must use [^)]+ (not [^;]+) because Google Fonts
# URLs contain `;` characters in their weight specs (e.g. wght@400;500;600;700).
_CSS_GOOGLE_FONT_IMPORT_RE = re.compile(
    r'@import\s+url\(\s*["\']?https?://fonts\.googleapis\.com/[^)]+\)\s*;?',
    re.IGNORECASE,
)


def _clean_css_for_runner(css: str) -> str:
    """Pre-process CSS before handing it to the Node runner.

    Two transformations:

    1. **Strip `/* ... */` comments.** The runner's `scopeSlideStyle()`
       (html2pptx_runner.js:84-120) extracts the selector text between
       `}` and the next `{` by raw substring. CSS comments between rules
       — and especially the `@import` declarations that often follow
       them — get glued onto the next rule's selector, producing invalid
       CSS that the browser silently drops. We strip comments here so
       the scoper sees clean rule sequences.

    2. **Strip `@import url("…fonts.googleapis.com…");`** declarations.
       We already promote these to explicit `<link rel="stylesheet">` tags
       in the wrapper template's `<head>` (see `_extract_google_fonts_links`),
       so leaving them in the inlined `<style>` block is redundant AND
       puts a brace-less `@import …;` statement between rules — which
       the scoper also mishandles for the same reason as (1).

    Limitation: naive comment stripping does not understand string-literal
    contexts (e.g. `content: "/* not a comment */"`). Slide CSS doesn't
    typically use that pattern; if a course ever needs it we'll switch
    to a proper CSS tokeniser.
    """
    css = _CSS_COMMENT_RE.sub("", css)
    css = _CSS_GOOGLE_FONT_IMPORT_RE.sub("", css)
    return css


_NOTES_SLIDE_PATH_RE = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")
_PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_DML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _set_notes_text(xml_bytes: bytes, notes_text: str) -> bytes | None:
    """Edit a single notesSlideN.xml to set the notes body text.

    Locates the `<p:sp>` whose `<p:ph type="body"/>` placeholder identifies
    the notes-body shape, then rewrites its `<a:txBody>` paragraphs to one
    `<a:p>` per newline-separated line of `notes_text`. Returns the new XML
    bytes, or `None` if the structure doesn't match (we leave the original
    unchanged in that case).
    """
    from lxml import etree

    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return None

    P = f"{{{_PML_NS}}}"
    A = f"{{{_DML_NS}}}"

    body_sp = None
    for sp in root.iter(f"{P}sp"):
        ph = sp.find(f".//{P}nvPr/{P}ph")
        if ph is not None and ph.get("type") == "body":
            body_sp = sp
            break
    if body_sp is None:
        return None

    # <p:txBody> is in the presentationML namespace (it lives on a <p:sp>),
    # though its children (<a:bodyPr>, <a:lstStyle>, <a:p>) are drawingML.
    tx_body = body_sp.find(f".//{P}txBody")
    if tx_body is None:
        return None

    # Preserve <a:bodyPr> and <a:lstStyle> (formatting prelude); replace <a:p>*.
    keep = [c for c in tx_body if c.tag in (f"{A}bodyPr", f"{A}lstStyle")]
    for c in list(tx_body):
        tx_body.remove(c)
    for c in keep:
        tx_body.append(c)

    for line in notes_text.split("\n"):
        p = etree.SubElement(tx_body, f"{A}p")
        if line.strip():
            r = etree.SubElement(p, f"{A}r")
            rPr = etree.SubElement(r, f"{A}rPr")
            rPr.set("lang", "en-US")
            rPr.set("dirty", "0")
            t = etree.SubElement(r, f"{A}t")
            t.text = line
        endParaRPr = etree.SubElement(p, f"{A}endParaRPr")
        endParaRPr.set("lang", "en-US")
        endParaRPr.set("dirty", "0")

    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _inject_speaker_notes_into_pptx(
    pptx_bytes: bytes,
    notes_per_slide: list[str],
) -> tuple[bytes, dict[str, int]]:
    """Inject speaker notes into the existing notesSlideN.xml parts of a PPTX.

    dom-to-pptx already creates a `notesSlideN.xml` for every slide and wires
    it up via slide rels + notesMaster + content-types — we just write the
    text into the empty placeholder. **Every other ZIP entry is copied
    byte-for-byte**, so the runner's embedded fonts (`<p:embeddedFontLst>`),
    custom shape XML, theme overrides, etc. are all preserved exactly.
    This is the deliberate alternative to round-tripping through
    `python-pptx`, which dropped those elements on save.

    Returns `(new_pptx_bytes, stats)` where stats has counts of injected /
    skipped-empty / no-body-placeholder / missing-notesSlide.
    """
    import zipfile
    from io import BytesIO

    stats = {
        "injected": 0,
        "skipped_empty": 0,
        "no_body_placeholder": 0,
        "missing_notes_part": 0,
    }
    note_paths = {
        i + 1: f"ppt/notesSlides/notesSlide{i + 1}.xml"
        for i in range(len(notes_per_slide))
    }
    expected_paths = set(note_paths.values())

    src_buf = BytesIO(pptx_bytes)
    out_buf = BytesIO()

    with zipfile.ZipFile(src_buf, "r") as src, zipfile.ZipFile(
        out_buf, "w", zipfile.ZIP_DEFLATED
    ) as dst:
        present_paths = set(src.namelist())
        # Slides whose notesSlide is missing entirely (runner skipped it).
        for idx, path in note_paths.items():
            if path not in present_paths and notes_per_slide[idx - 1].strip():
                stats["missing_notes_part"] += 1
                logger.warning(
                    "speaker notes: slide %d has notes but %s is missing; "
                    "skipping (runner did not generate it)",
                    idx, path,
                )

        for item in src.infolist():
            data = src.read(item.filename)
            match = _NOTES_SLIDE_PATH_RE.match(item.filename)
            if match:
                slide_idx = int(match.group(1))
                if 1 <= slide_idx <= len(notes_per_slide):
                    notes_text = notes_per_slide[slide_idx - 1].strip()
                    if not notes_text:
                        stats["skipped_empty"] += 1
                    else:
                        new_data = _set_notes_text(data, notes_text)
                        if new_data is None:
                            stats["no_body_placeholder"] += 1
                            logger.warning(
                                "speaker notes: %s has no <p:sp> with body "
                                "placeholder; leaving notes empty for slide %d",
                                item.filename, slide_idx,
                            )
                        else:
                            data = new_data
                            stats["injected"] += 1
            # Preserve original ZipInfo (compression, dates, attrs) by
            # constructing the entry from scratch with the same filename
            # but new bytes. Using item directly carries the original CRC.
            dst.writestr(item.filename, data)

    return out_buf.getvalue(), stats


def _extract_google_fonts_links(css_content: str) -> str:
    """Promote any @import url('...fonts.googleapis.com...') rules in the
    course CSS to explicit <link rel="stylesheet"> tags.

    The Node runner's font pre-download (html2pptx_runner.js:collectGoogleFontsHrefs)
    inspects HTML <link> tags only — it does NOT parse @import rules inside
    <style> blocks. Without this promotion, Google Fonts are fetched by
    Chromium at render time as WOFF2 and PowerPoint's WOFF2 embedding is
    unreliable, so the deck falls back to system fonts.

    Returns a string of <link> tags (one per unique href, in source order).
    Includes a <link rel="preconnect"> to fonts.gstatic.com for parity with
    the standard Google Fonts boilerplate.
    """
    hrefs: list[str] = []
    seen: set[str] = set()
    for match in _GOOGLE_FONTS_IMPORT_RE.finditer(css_content):
        href = match.group(1).strip()
        if href in seen:
            continue
        seen.add(href)
        hrefs.append(href)
    if not hrefs:
        return ""
    # Note: we deliberately do NOT emit <link rel="preconnect"> tags.
    # The runner's collectGoogleFontsHrefs filter (html2pptx_runner.js:564-574)
    # matches any <link> with `fonts.googleapis.com` in the href, so preconnect
    # tags get treated as stylesheet URLs and produce HTTP 404s in the runner
    # log. The preconnect-as-perf-hint is irrelevant in headless Chromium anyway.
    return "\n".join(f'<link rel="stylesheet" href="{href}">' for href in hrefs)


class SlideBrief(BaseModel):
    """Structured input for one slide. The HTML writer sub-agent renders this.

    Keep briefs small — the writer's job is to choose layout and compose
    the course's design vocabulary; the brief just supplies the pedagogical
    content.
    """

    title: str = Field(..., description="Slide title (becomes <h1>).")
    layout: str = Field(
        default="bullets",
        description=(
            "Layout hint for the writer (advisory). One of: bullets, objectives, "
            "two-col, grid, callout, hero, code, summary. The writer picks the "
            "actual CSS composition; this just signals intent."
        ),
    )
    key_points: list[str] = Field(
        default_factory=list,
        description=(
            "Primary content for the slide. For bullets/objectives/summary "
            "layouts these become list items; for grid layouts each becomes "
            "a card title; for two-col, the first two are the column headings."
        ),
    )
    body: str = Field(
        default="",
        description=(
            "Optional richer context (1-3 short sentences) the writer can use "
            "to flesh out callouts, hero text, or card bodies."
        ),
    )
    code: str = Field(
        default="",
        description=(
            "Optional code snippet for code layouts. Keep ≤8 lines, ≤60 chars/line."
        ),
    )
    citations: list[str] = Field(
        default_factory=list,
        description=(
            "Optional citation labels (e.g. ['[^1] Apiola et al., 2022']) "
            "rendered as footnote text on the slide."
        ),
    )
    speaker_notes: str = Field(
        ...,
        description=(
            "3-5 sentences of the lecturer's script. Embedded as the PPTX "
            "notesSlide for each slide (visible in PowerPoint's notes pane). "
            "Use newlines to split into paragraphs. Also captured in "
            ".slides_debug/<ts>/slide_NNN_brief.json for archival."
        ),
    )


# ---------------------------------------------------------------------------
# Sub-agent infrastructure (mirrors slides_agent/tools/ModifySlide.py but
# scoped to this tool: no template registry, no critique loop, one call per
# slide).
# ---------------------------------------------------------------------------


def _read_html_writer_instructions() -> str:
    try:
        return _HTML_WRITER_INSTRUCTIONS.read_text(encoding="utf-8").strip()
    except OSError:
        return "You generate slide body HTML. Return only the HTML fragment."


def _make_html_writer_agent() -> tuple[Agent, bool]:
    """Create a fresh, stateless HTML-writer sub-agent.

    Model priority:
      1. ANTHROPIC_API_KEY → Claude Sonnet 4.6 (best HTML quality)
      2. AsyncOpenAI default (env vars) → gpt-5.3-codex
    """
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    is_codex = False
    if anthropic_key:
        model = LitellmModel(model=_HTML_WRITER_MODEL_CLAUDE, api_key=anthropic_key)
        logger.info("HTML writer sub-agent: Claude (%s)", _HTML_WRITER_MODEL_CLAUDE)
    else:
        from agents import OpenAIResponsesModel

        client = AsyncOpenAI()
        is_codex = not str(client.base_url).startswith("https://api.openai.com")
        if is_codex:
            from dataclasses import replace

            class _CodexModel(OpenAIResponsesModel):
                async def _fetch_response(self, system_instructions, input, model_settings, *args, **kwargs):
                    model_settings = replace(model_settings, truncation=None)
                    return await super()._fetch_response(
                        system_instructions, input, model_settings, *args, **kwargs
                    )

            model = _CodexModel(model=_HTML_WRITER_MODEL_OAI, openai_client=client)
            logger.info(
                "HTML writer sub-agent: OpenAI/Codex (%s @ %s) — "
                "ANTHROPIC_API_KEY not set",
                _HTML_WRITER_MODEL_OAI, client.base_url,
            )
        else:
            model = OpenAIResponsesModel(model=_HTML_WRITER_MODEL_OAI, openai_client=client)
            logger.info(
                "HTML writer sub-agent: OpenAI (%s) — ANTHROPIC_API_KEY not set",
                _HTML_WRITER_MODEL_OAI,
            )

    instructions = _read_html_writer_instructions()
    logger.debug("HTML writer instructions loaded: %d chars", len(instructions))

    agent = Agent(
        name="Educator Slide HTML Writer",
        description="Generates one lecture slide's body HTML from a structured brief.",
        instructions=instructions,
        tools=[],
        model=model,
        model_settings=ModelSettings(
            reasoning=Reasoning(effort="medium", summary="auto"),
            verbosity="low",
            store=False if is_codex else None,
        ),
    )
    return agent, is_codex


async def _agent_get_response(agent: Agent, prompt: str, *, use_stream: bool) -> Any:
    if use_stream:
        stream = agent.get_response_stream(prompt)
        deltas: list[str] = []
        async for event in stream:
            data = getattr(event, "data", None)
            if data is not None:
                delta = getattr(data, "delta", None)
                if isinstance(delta, str):
                    deltas.append(delta)
        result = await stream.wait_final_result()
        if result is not None and not getattr(result, "final_output", None) and deltas:
            try:
                result.final_output = "".join(deltas)
            except Exception:
                pass
        return result
    return await agent.get_response(prompt)


def _build_writer_prompt(
    brief: SlideBrief,
    css_content: str,
    slide_index: int,
    total_slides: int,
    *,
    prev_error: str = "",
    prev_html: str | None = None,
) -> str:
    """Compose the per-slide prompt for the HTML-writer sub-agent.

    On retry attempts, `prev_error` (validation feedback) and `prev_html`
    (the failed attempt, base64-image-stripped to save tokens) are
    appended so the sub-agent can do a surgical fix instead of starting
    from scratch. Mirrors the structure of slides_agent/tools/ModifySlide.py
    `_build_sub_run_prompt` retry block.
    """
    parts = [
        f"You are generating slide {slide_index} of {total_slides} for a lecture deck.",
        "",
        "=== COURSE STYLE.CSS (authoritative design system) ===",
        css_content.strip(),
        "=== END STYLE.CSS ===",
        "",
        "=== SLIDE BRIEF ===",
        f"Title: {brief.title}",
        f"Layout hint: {brief.layout}",
    ]
    if brief.key_points:
        parts.append("Key points:")
        for kp in brief.key_points:
            parts.append(f"  - {kp}")
    if brief.body:
        parts.append(f"Body context: {brief.body}")
    if brief.code:
        parts.extend(["Code snippet:", "```", brief.code.rstrip(), "```"])
    if brief.citations:
        parts.append("Citations to surface on this slide:")
        for c in brief.citations:
            parts.append(f"  - {c}")
    parts.append("=== END BRIEF ===")

    if prev_error or prev_html:
        parts.extend(
            [
                "",
                "=== VALIDATION_FEEDBACK_FROM_PREVIOUS_ATTEMPT ===",
                prev_error.strip() or "(no error text — sub-agent returned nothing)",
                "=== END VALIDATION_FEEDBACK ===",
            ]
        )
        if prev_html:
            parts.extend(
                [
                    "",
                    "=== PREVIOUS_ATTEMPT (your last HTML; fix the issues above) ===",
                    prev_html.strip(),
                    "=== END PREVIOUS_ATTEMPT ===",
                ]
            )
        parts.append(
            "Make the smallest correction that addresses every issue above. "
            "Do NOT rewrite the slide from scratch unless the previous attempt "
            "was empty or fundamentally broken."
        )

    parts.extend(
        [
            "",
            "Return ONLY the HTML body fragment for this slide (the content inside "
            "<div class=\"slide\">…</div>). No markdown fences, no commentary, no "
            "<html>/<body> tags, no <style> blocks, no <div class=\"speaker-notes\">.",
        ]
    )
    return "\n".join(parts)


def _extract_html_fragment(text: str) -> tuple[str, list[str]]:
    """Pull the HTML fragment out of the sub-agent's raw output.

    Handles ```html fences, ``` fences, and bare HTML. Strips any stray
    <html>/<body>/<style> the model may have emitted despite instructions.

    Returns (fragment, debug_steps) where debug_steps lists each
    transformation applied — useful for diagnosing over-aggressive
    stripping. An empty fragment means the model emitted nothing useful.
    """
    steps: list[str] = []
    raw = (text or "").strip()
    if not raw:
        steps.append("input empty")
        return "", steps
    steps.append(f"input {len(raw)} chars")

    fence = re.search(r"```(?:html)?\s*(.*?)```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
        steps.append(f"unwrapped ``` fence → {len(raw)} chars")

    body_match = re.search(r"<body[^>]*>(.*?)</body>", raw, flags=re.IGNORECASE | re.DOTALL)
    if body_match:
        raw = body_match.group(1).strip()
        steps.append(f"peeled <body> → {len(raw)} chars")

    slide_match = re.search(
        r'<div[^>]*class=["\'][^"\']*\bslide\b[^"\']*["\'][^>]*>(.*)</div>',
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if slide_match:
        raw = slide_match.group(1).strip()
        steps.append(f"peeled outer .slide wrapper → {len(raw)} chars")

    pre = len(raw)
    raw = re.sub(r"<style[^>]*>.*?</style>", "", raw, flags=re.IGNORECASE | re.DOTALL)
    if len(raw) != pre:
        steps.append(f"stripped <style> blocks → {len(raw)} chars")

    pre = len(raw)
    raw = re.sub(
        r'<div[^>]*class=["\'][^"\']*\bspeaker-notes\b[^"\']*["\'][^>]*>.*?</div>',
        "",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if len(raw) != pre:
        steps.append(f"stripped speaker-notes div → {len(raw)} chars")

    raw = raw.strip()
    if not raw:
        steps.append("WARNING: nothing left after stripping")
    return raw, steps


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class GenerateEducatorSlides(BaseTool):
    """Generate a PPTX lecture deck from structured slide briefs.

    Pass `slides`: a list of `SlideBrief` objects in presentation order.
    For each brief, this tool calls an internal HTML-writer sub-agent
    (Claude Sonnet 4.6 by default, OpenAI fallback) that turns the brief
    into a styled HTML body using the course's `style.css`. The fragments
    are wrapped, rendered to PPTX via `html2pptx_runner.js`, and written
    through the active `FileOpsBackend`. React to the response as you
    would `write_file` — see shared instructions ("Writing files").
    """

    slides: list[SlideBrief] = Field(
        ...,
        description=(
            "Structured briefs, one per slide, in presentation order. Each "
            "brief carries the slide's title, key points, optional body/code/"
            "citations, and the lecturer's speaker notes."
        ),
    )
    css_content: str = Field(
        ...,
        description="Full content of the course's style.css.",
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

    # Per-slide attempt cap: initial + 1 retry. Bounds LLM cost when the
    # validation pass keeps rejecting the sub-agent's output.
    _MAX_ATTEMPTS = 2

    async def run(self) -> str:
        if not self.slides:
            logger.warning("GenerateEducatorSlides called with empty slides list.")
            return "Error: slides is empty."
        if not RUNNER_JS.exists():
            logger.error("html2pptx_runner.js missing at %s", RUNNER_JS)
            return (
                f"Error: html2pptx_runner.js not found at {RUNNER_JS}. "
                "Ensure node_modules is installed at the repo root."
            )
        node_modules = RUNNER_JS.parent.parent.parent / "node_modules"
        if not node_modules.exists():
            logger.error("node_modules missing at %s", node_modules)
            return "Error: node_modules not found. Run 'npm install' at the repo root."

        total = len(self.slides)
        run_dir = _make_run_artifact_dir()
        course_root = _backend_root()
        font_links = _extract_google_fonts_links(self.css_content)
        logger.info(
            "GenerateEducatorSlides: %d slides → %s (layout=%s); artifacts: %s",
            total, self.output_path, self.layout, run_dir or "(disabled)",
        )
        logger.debug("css_content: %d chars; course_root: %s", len(self.css_content), course_root)
        if font_links:
            n_links = font_links.count('rel="stylesheet"')
            logger.info("promoted %d @import Google Fonts URL(s) to <link> tag(s)", n_links)
        else:
            logger.debug("no @import Google Fonts URLs found in css_content")

        writer, is_codex = _make_html_writer_agent()
        wrapped_htmls: list[str] = []
        failures: list[str] = []

        for i, brief in enumerate(self.slides, start=1):
            logger.info(
                "slide %d/%d  layout=%-9s  title=%r  key_points=%d  code=%s",
                i, total, brief.layout, brief.title,
                len(brief.key_points), "yes" if brief.code else "no",
            )
            _dump(run_dir, i, "brief.json", brief.model_dump_json(indent=2))

            wrapped, error_text = await self._generate_one_slide(
                brief=brief,
                slide_index=i,
                total=total,
                writer=writer,
                is_codex=is_codex,
                font_links=font_links,
                course_root=course_root,
                run_dir=run_dir,
            )
            if wrapped is None:
                failures.append(f"slide {i} ({brief.title}): {error_text}")
                logger.warning(
                    "slide %d: all %d attempts failed; falling back to plain bullets",
                    i, self._MAX_ATTEMPTS,
                )
                fallback_body = _fallback_html(brief)
                wrapped = self._wrap_full_html(fallback_body, brief, font_links)
                _dump(run_dir, i, "FALLBACK_wrapped.html", wrapped)
            wrapped_htmls.append(wrapped)

        successes = total - len(failures)
        logger.info(
            "sub-agent loop done: %d ok, %d fell back to plain bullets",
            successes, len(failures),
        )
        failure_note = "\n".join(f"  - {f}" for f in failures) if failures else ""

        # Prefer a sticky directory under the run's debug dir so we can
        # inspect what the runner actually loaded (slide HTML, orchestrator
        # HTML, stdout/stderr) after the fact. Falls back to a TemporaryDirectory
        # only when artifact dumping is disabled (no run_dir).
        if run_dir is not None:
            runner_input = run_dir / "runner_input"
            runner_input.mkdir(parents=True, exist_ok=True)
            html_files = self._write_wrapped_to_tmp(runner_input, wrapped_htmls)
            pptx_path = runner_input / "slides.pptx"
            logger.debug(
                "running html2pptx_runner.js on %d files in %s",
                len(html_files), runner_input,
            )
            error = self._run_converter(html_files, pptx_path, run_dir=run_dir)
            if error:
                logger.error("html2pptx_runner.js failed: %s", error)
                return error
            try:
                pptx_bytes = pptx_path.read_bytes()
            except OSError as exc:
                logger.exception("could not read generated PPTX")
                return f"Failed to read generated PPTX: {exc}"
            logger.debug("PPTX generated: %d bytes", len(pptx_bytes))
        else:
            with tempfile.TemporaryDirectory(prefix="educator_slides_") as tmp:
                tmp_path = Path(tmp)
                html_files = self._write_wrapped_to_tmp(tmp_path, wrapped_htmls)
                pptx_path = tmp_path / "slides.pptx"
                logger.debug("running html2pptx_runner.js on %d files", len(html_files))
                error = self._run_converter(html_files, pptx_path)
                if error:
                    logger.error("html2pptx_runner.js failed: %s", error)
                    return error
                try:
                    pptx_bytes = pptx_path.read_bytes()
                except OSError as exc:
                    logger.exception("could not read generated PPTX")
                    return f"Failed to read generated PPTX: {exc}"
                logger.debug("PPTX generated: %d bytes", len(pptx_bytes))

        # Diagnostic dump: snapshot the runner's untouched output so we can
        # diff it against the final PPTX after notes injection. Useful for
        # verifying we only touched notesSlideN.xml parts.
        if run_dir is not None:
            try:
                (run_dir / "slides_pre_postprocess.pptx").write_bytes(pptx_bytes)
            except OSError as exc:
                logger.debug("Could not dump slides_pre_postprocess.pptx: %s", exc)

        # Inject speaker notes via ZIP/XML manipulation. Unlike the previous
        # python-pptx round-trip (which dropped embedded fonts and custom
        # shape XML), this only edits the existing notesSlideN.xml parts
        # in place. Every other ZIP entry is copied byte-for-byte.
        notes_per_slide = [s.speaker_notes for s in self.slides]
        if any(n.strip() for n in notes_per_slide):
            pptx_bytes, notes_stats = _inject_speaker_notes_into_pptx(
                pptx_bytes, notes_per_slide
            )
            logger.info(
                "speaker notes: %d injected, %d skipped-empty, "
                "%d missing-body-placeholder, %d missing-notesSlide",
                notes_stats["injected"],
                notes_stats["skipped_empty"],
                notes_stats["no_body_placeholder"],
                notes_stats["missing_notes_part"],
            )

        backend = get_backend()
        outcome = backend.write_file(Path(self.output_path), pptx_bytes)
        logger.info("write outcome: %s", type(outcome).__name__)

        if isinstance(outcome, Pending):
            msg = (
                f"Slide deck generated ({total} slides) — pending approval.\n"
                f"proposal_id: {outcome.proposal_id}\n"
                f"path: {outcome.path}\n\n"
                f"{outcome.diff}\n"
                f"Reply `/approve {outcome.proposal_id}` to save, or "
                f"`/reject {outcome.proposal_id} <feedback>` to discard."
            )
        elif isinstance(outcome, Accepted):
            msg = f"Slide deck saved: {outcome.path} ({total} slides)"
        elif isinstance(outcome, Rejected):
            msg = f"Proposal rejected: {outcome.reason}"
        elif isinstance(outcome, Failed):
            msg = f"Write failed: {outcome.error}"
        else:
            msg = f"Unexpected outcome: {outcome!r}"

        if failure_note:
            msg += (
                f"\n\nNote: {len(failures)} slide(s) used a plain fallback layout "
                f"because the HTML writer failed:\n{failure_note}"
            )
        return msg

    async def _generate_one_slide(
        self,
        *,
        brief: SlideBrief,
        slide_index: int,
        total: int,
        writer: Agent,
        is_codex: bool,
        font_links: str,
        course_root: Path,
        run_dir: Path | None,
    ) -> tuple[str | None, str]:
        """Run the up-to-MAX_ATTEMPTS sub-agent / preprocess / validate loop
        for one slide. Returns (wrapped_html, "") on success, or (None, reason)
        if all attempts were rejected by validation or the sub-agent failed.
        """
        last_error = ""
        last_fragment_stripped: str | None = None

        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            tag = f"attempt_{attempt}"
            logger.debug(
                "slide %d attempt %d/%d: building prompt (prev_error=%s)",
                slide_index, attempt, self._MAX_ATTEMPTS,
                "yes" if last_error else "no",
            )

            prompt = _build_writer_prompt(
                brief, self.css_content, slide_index, total,
                prev_error=last_error,
                prev_html=last_fragment_stripped,
            )
            _dump(run_dir, slide_index, f"{tag}_prompt.txt", prompt)
            logger.debug("slide %d %s prompt: %d chars", slide_index, tag, len(prompt))

            try:
                result = await _agent_get_response(writer, prompt, use_stream=is_codex)
            except Exception as exc:
                logger.exception(
                    "slide %d %s: sub-agent raised: %s", slide_index, tag, exc
                )
                last_error = f"sub-agent raised: {exc}"
                _dump(run_dir, slide_index, f"{tag}_FAILURE.txt", last_error)
                continue

            if result is None:
                logger.warning(
                    "slide %d %s: sub-agent returned None (likely API/rate-limit)",
                    slide_index, tag,
                )
                last_error = "sub-agent returned None"
                _dump(run_dir, slide_index, f"{tag}_FAILURE.txt", last_error)
                continue

            output_text = str(getattr(result, "final_output", "") or "")
            _dump(run_dir, slide_index, f"{tag}_raw_response.txt", output_text)
            logger.debug(
                "slide %d %s raw output: %d chars\n%s",
                slide_index, tag, len(output_text), _truncate(output_text),
            )

            fragment, extraction_steps = _extract_html_fragment(output_text)
            for step in extraction_steps:
                logger.debug("slide %d %s extract: %s", slide_index, tag, step)

            if not fragment:
                logger.warning(
                    "slide %d %s: empty fragment after extraction (raw starts: %r)",
                    slide_index, tag, output_text[:120],
                )
                last_error = (
                    "Your previous response was empty or stripped to nothing after "
                    "extraction. Return the slide body as plain HTML — no markdown "
                    "fences, no commentary."
                )
                _dump(run_dir, slide_index, f"{tag}_FAILURE.txt", last_error)
                continue

            _dump(run_dir, slide_index, f"{tag}_fragment.html", fragment)

            # Stage 1 preprocessing
            converted = _convert_css_bg_images_to_img_tags(fragment)
            if len(converted) != len(fragment):
                logger.info(
                    "slide %d %s: converted CSS background-image rules to <img> "
                    "(%d → %d chars)", slide_index, tag, len(fragment), len(converted)
                )
                _dump(run_dir, slide_index, f"{tag}_after_bg_convert.html", converted)
            fragment = converted

            embedded = _embed_local_images_as_base64(fragment, course_root)
            if len(embedded) != len(fragment):
                logger.info(
                    "slide %d %s: embedded local images as base64 (%d → %d chars)",
                    slide_index, tag, len(fragment), len(embedded),
                )
                _dump(run_dir, slide_index, f"{tag}_after_embed.html", embedded)
            fragment = embedded

            wrapped = self._wrap_full_html(fragment, brief, font_links)
            _dump(run_dir, slide_index, f"{tag}_wrapped.html", wrapped)

            # Stage 2 validation (sync Playwright; offloaded to a thread).
            try:
                validation = await asyncio.to_thread(
                    _validate_slide, wrapped, course_root
                )
            except Exception as exc:
                logger.exception(
                    "slide %d %s: validation crashed (%s) — skipping check",
                    slide_index, tag, exc,
                )
                validation = {"valid": True, "error": f"(validation skipped: {exc})"}

            if validation.get("valid"):
                logger.info(
                    "slide %d %s ok: %d chars of HTML (validation passed)",
                    slide_index, tag, len(fragment),
                )
                _dump(run_dir, slide_index, f"{tag}_validation.txt", "valid")
                return wrapped, ""

            error_text = validation.get("error", "unknown validation error")
            logger.warning(
                "slide %d %s: validation failed:\n%s",
                slide_index, tag, _truncate(error_text, 400),
            )
            _dump(run_dir, slide_index, f"{tag}_validation.txt", error_text)
            last_error = error_text
            # Strip base64 from the fragment before sending it back to the
            # sub-agent so we don't burn tokens on multi-MB image blobs.
            last_fragment_stripped = _strip_base64_images(fragment)

        return None, last_error or f"all {self._MAX_ATTEMPTS} attempts failed"

    def _wrap_full_html(
        self, body: str, brief: SlideBrief, font_links: str
    ) -> str:
        """Wrap a slide body in the _HTML_TEMPLATE with font links.

        Mirrors Pipeline A's input shape exactly — no speaker-notes div,
        no auxiliary elements. The `brief.speaker_notes` value is preserved
        in `slide_NNN_brief.json` debug artifacts but is not embedded in
        the PPTX (see SlideBrief.speaker_notes for why).

        The course CSS is cleaned before inlining — see
        `_clean_css_for_runner` for the (important) reason.
        """
        return _HTML_TEMPLATE.format(
            font_links=font_links,
            css=_clean_css_for_runner(self.css_content),
            body=body,
        )

    def _write_wrapped_to_tmp(
        self, tmp_path: Path, wrapped_htmls: list[str]
    ) -> list[str]:
        """Write pre-wrapped per-slide HTML to a temp dir for the Node runner."""
        paths: list[str] = []
        for i, html in enumerate(wrapped_htmls):
            slide_file = tmp_path / f"slide_{i + 1:03d}.html"
            slide_file.write_text(html, encoding="utf-8")
            paths.append(str(slide_file))
        return paths

    def _run_converter(
        self,
        html_files: list[str],
        output_path: Path,
        run_dir: Path | None = None,
    ) -> str | None:
        """Invoke html2pptx_runner.js. Captures stdout/stderr always and
        surfaces structured log lines so we can see exactly what the runner
        did — font downloads, page errors, FA materialization, embedded-font
        count, etc. When `run_dir` is given, full stdout/stderr are also
        dumped as `runner_stdout.log` / `runner_stderr.log` for offline
        inspection, and `KEEP_ORCHESTRATOR=1` is set so the runner leaves
        the orchestrator HTML on disk (it lives next to the slide files —
        look for `._orchestrator_*.html`).
        """
        cmd = [
            "node",
            str(RUNNER_JS),
            "--output", str(output_path),
            "--layout", self.layout,
            "--",
            *html_files,
        ]
        env = os.environ.copy()
        if run_dir is not None:
            env["KEEP_ORCHESTRATOR"] = "1"

        logger.debug("runner cmd: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(RUNNER_JS.parent.parent.parent),
                env=env,
            )
        except subprocess.TimeoutExpired:
            logger.error("runner timed out after 300s")
            return "Error: PPTX conversion timed out after 300 s."
        except FileNotFoundError:
            logger.error("node binary not found")
            return "Error: Node.js not found. Please install Node.js."

        # Always dump and parse the runner's output, even on success.
        stdout, stderr = result.stdout or "", result.stderr or ""
        if run_dir is not None:
            try:
                (run_dir / "runner_stdout.log").write_text(stdout, encoding="utf-8")
                (run_dir / "runner_stderr.log").write_text(stderr, encoding="utf-8")
            except OSError as exc:
                logger.debug("could not dump runner logs: %s", exc)
        _surface_runner_output(stdout, stderr)

        if result.returncode != 0:
            err = stderr.strip() or stdout.strip()
            return f"Error converting HTML to PPTX:\n{err}"
        return None


def _fallback_html(brief: SlideBrief) -> str:
    """Plain bullets layout when the sub-agent fails on a slide.

    The deck still ships; the user gets a clear note about which slides
    fell back so they can re-prompt for a regeneration.
    """
    parts = [f"<h1>{_escape(brief.title)}</h1>"]
    if brief.key_points:
        parts.append("<ul>")
        for kp in brief.key_points:
            parts.append(f"  <li>{_escape(kp)}</li>")
        parts.append("</ul>")
    if brief.body:
        parts.append(f"<p>{_escape(brief.body)}</p>")
    if brief.code:
        parts.append(f"<pre><code>{_escape(brief.code)}</code></pre>")
    if brief.citations:
        for c in brief.citations:
            parts.append(f'<div class="footnote">{_escape(c)}</div>')
    return "\n".join(parts)


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
