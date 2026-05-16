# Why the educator SlidesAgent produces visually dull decks

## Context

You're comparing two slide pipelines that live in the same repo:

- **Original (rich)** — [slides_agent/](slides_agent/) at the repo root. The general-purpose Agency Swarm slides agent that came with the upstream repo.
- **Educator (dull)** — [educator_agency/agency_def/slides_agent/](educator_agency/agency_def/slides_agent/). A stripped-down rewrite intended for the lecture-deck use case, per `.claude/plans/i-want-to-start-stateless-oasis.md` §6.8.

This is an explanation, not an implementation task. The plan file documents the diagnosis and lists the levers if you decide to recover visual richness.

## Root cause: the educator agent was *designed* to be minimal

The educator rewrite traded almost every visual-quality affordance of the original for simplicity and a one-shot, backend-gated write. The dullness is not a bug — it's the direct consequence of nine concurrent reductions.

### 1. Tool surface collapsed from ~16 to 3

Original tools ([slides_agent/slides_agent.py:60-90](slides_agent/slides_agent.py#L60-L90)):
`InsertNewSlides`, `ModifySlide`, `ManageTheme`, `DeleteSlide`, `SlideScreenshot`, `ReadSlide`, `BuildPptxFromHtmlSlides`, `RestoreSnapshot`, `CreatePptxThumbnailGrid`, `CheckSlideCanvasOverflow`, `CheckSlide`, `DownloadImage`, `EnsureRasterImage`, `GenerateImage`, `ImageSearch`, `IPythonInterpreter`, `PersistentShellTool`, `WebSearchTool`.

Educator tools ([educator_agency/agency_def/slides_agent/slides_agent.py:15](educator_agency/agency_def/slides_agent/slides_agent.py#L15)):
`ReadFileTool`, `ListFilesTool`, `GenerateEducatorSlides`.

No image search/generation/download, no web search, no theme management, no screenshot, no overflow check — so the agent has no way to *acquire* or *verify* visual content.

### 2. No HTML-writer sub-agent (no design-vocabulary prompt)

`ModifySlide` in the original spawns an isolated HTML writer sub-agent driven by [slides_agent/tools/html_writer_instructions.md](slides_agent/tools/html_writer_instructions.md). That prompt is *the* visual brain — it teaches Tailwind, Chart.js, ECharts, Font Awesome, Google Fonts, accent bars, kicker labels, grid-div backgrounds, glowing orbs, color-coded sections, hero layouts, split panels, etc.

[educator_agency/agency_def/slides_agent/generate_slides.py:56-94](educator_agency/agency_def/slides_agent/generate_slides.py#L56-L94) accepts a flat `slides_html: list[str]` and pipes it straight to `html2pptx_runner.js`. No sub-agent, no design prompt — whatever bullet HTML the parent agent emits is what you get.

### 3. The instructions explicitly forbid rich CSS

[educator_agency/agency_def/slides_agent/instructions.md:42-53](educator_agency/agency_def/slides_agent/instructions.md#L42-L53) advertises only six classes (`.slide`, `h1`, `h2`, `ul/ol`, `code`, `.footnote`, `.speaker-notes`) and adds: *"Do NOT use class names from the old themed system (`.slide-wrapper`, `.glass-panel`, `.canvas`, `.bg-grid`, `.content-safe-area`)."* The agent is being told not to be visual.

### 4. The course style.css is a generic black-on-white text style

[courses/intro_to_python/style.css](courses/intro_to_python/style.css) defines only: white background, single accent color (`#2c5aa0`), `Helvetica Neue` everywhere, a header rule, a list rule, an inline code rule, a footnote. No layout primitives, no spacing scale, no card/section/grid classes. Even if the agent wanted to compose a rich layout, the stylesheet doesn't give it the parts.

### 5. The HTML template hardcodes a system font

[educator_agency/agency_def/slides_agent/generate_slides.py:28-53](educator_agency/agency_def/slides_agent/generate_slides.py#L28-L53) injects `font-family: "Helvetica Neue", Arial, sans-serif` and provides no Google Fonts `<link>`. The original mandates Google Fonts so the PPTX exporter can embed them ([slides_agent/instructions.md:84-93](slides_agent/instructions.md#L84-L93)). Result: system-font typography with no embedded font in the PPTX.

### 6. No iterative critique loop

Original: `ModifySlide` auto-returns a screenshot; the agent inspects for overflow, contrast, broken layout and may issue up to ~3 corrective edits per slide ([slides_agent/instructions.md:136-138, 240](slides_agent/instructions.md#L136-L138)). Plus `CheckSlideCanvasOverflow` and `CheckSlide` as explicit verification tools.

Educator: one `generate_educator_slides` call, then done. Whatever HTML the LLM emitted on first try is the final deck.

### 7. No research / brand-asset extraction

Original spends meaningful effort in `WebSearchTool` + `IPythonInterpreter` to pull a brand palette, logo, and hero imagery before designing ([slides_agent/instructions.md:111-127](slides_agent/instructions.md#L111-L127)). Educator has no web tools and no image acquisition path, so every deck is text-only.

### 8. No theme/template registry

Original's `ManageTheme` writes a shared `_theme.css`, and `ModifySlide` supports `save_as_template_key` / `existing_template_key` for reusable layouts. Educator has none of this — every slide is whatever the LLM improvises in a single fragment of HTML.

### 9. Batch (all slides at once) vs. per-slide iteration

`generate_educator_slides` takes the whole deck in one shot. The original creates slides one at a time, letting the LLM concentrate per-slide reasoning, pick a layout that fits the content, and re-edit on screenshot feedback. Batching collapses all of that.

## What you can do about it (if you want to)

These are independent levers — pick the smallest set that gets you to acceptable quality:

| Lever | Effort | Expected impact |
|---|---|---|
| Expand `style.css` with layout primitives (cards, two-column grid, hero, accent bar, kicker, color-coded sections) and a real type scale | Low | Medium — the agent can compose richer layouts within the constraint of the existing 6-class allowlist |
| Add a Google Fonts `<link>` to `_HTML_TEMPLATE` and reference families from `style.css` | Trivial | Medium — typography immediately stops looking like a system-font document |
| Loosen [instructions.md:42-53](educator_agency/agency_def/slides_agent/instructions.md#L42-L53) to permit a small design vocabulary (accent bars, kicker badges, two-column flex, hero layouts), modeled on the relevant subset of [html_writer_instructions.md](slides_agent/tools/html_writer_instructions.md) | Low | High — gives the agent permission to design |
| Re-introduce a screenshot/critique pass (single round, max one redo) inside `generate_educator_slides` so the agent can see what it produced | Medium | Medium — catches blatant overflow/contrast issues |
| Re-introduce an HTML-writer sub-agent driven by a lecture-tuned variant of `html_writer_instructions.md`, invoked per slide | High | Highest — closest to recovering original visual quality, but reintroduces the complexity the rewrite was meant to remove |
| Allow `ImageSearch` + `DownloadImage` for hero/cover slides only | Medium | Medium — cover slide is the most visible "dullness" symptom |

The architectural tension to acknowledge: §6.8 of `.claude/plans/i-want-to-start-stateless-oasis.md` deliberately removed the sub-agent and the iterative tools to make the slide step stateless and backend-gateable. Recovering visual richness without re-introducing sub-agents requires getting more out of the parent agent's single-shot output — primarily via a richer `style.css` + permission to use it + a Google Fonts link. That's the cheapest path; the rest is opt-in.

## Verification (if you act on any of the levers)

1. Pick a lesson under `courses/intro_to_python/lessons/L*`.
2. Run [run_educator.py](run_educator.py) and ask the orchestrator to regenerate slides for that lesson.
3. Open the resulting `slides.pptx` in Keynote/PowerPoint and compare side-by-side with a deck produced by the original agent (e.g. via the [slides_agent/slides_agent.py:108](slides_agent/slides_agent.py#L108) terminal demo).
4. Check that: fonts are embedded (not substituted), titles use the accent color at the right weight, at least one non-bullet layout appears, no slide overflows the 1280×720 canvas.
