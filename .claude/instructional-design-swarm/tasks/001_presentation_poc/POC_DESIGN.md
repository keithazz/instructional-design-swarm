# POC_DESIGN — Educator Agency Phase 1

> Phase 1 vertical-slice design. Pins down the file-format contracts, agent topology, and end-to-end flow for a working POC built as a fork of [VRSEN/OpenSwarm](https://github.com/VRSEN/OpenSwarm). Companion to [PRD.md](../../PRD.md), [ARCHITECTURE.md](../../ARCHITECTURE.md), and [PLAN.md](../../PLAN.md).

## 1. Scope and stance

This document specifies what the POC produces and consumes — the file conventions, the agent set, and the control flow — so that the integration test (PLAN.md Phase 1's "first thing to write") has something concrete to assert against.

**In scope for Phase 1.** A single-machine command-line tool, forked from OpenSwarm, that takes a course brief and produces a directory of stylistically consistent slide packs for an in-person lecturer. Five agents, four file types, one end-to-end flow with diff-style approvals on every write.

**Out of scope for Phase 1.** Obsidian plugin, WebSocket bridge, multi-agent parallelism, prerequisite tagging, active-learning components, per-LLM cost surfacing, multi-language beyond the `language` frontmatter field, hot-reload of agency definitions.

**Inherited from OpenSwarm.** Agency Swarm runtime scaffolding, the HTML-then-PPTX slide generation pipeline (Playwright rendering HTML and a Node.js `dom-to-pptx` runner — no LibreOffice involved, despite earlier drafts implying otherwise), `DeepResearchAgent`, `OrchestratorAgent` topology pattern, onboarding wizard for API keys.

**Built fresh.** `CourseDesignerAgent`, `LessonPlannerAgent`, the COURSE.md / PLAN.md / PEDAGOGY.md schemas, the LO-numbering convention, the style.css convention, a minimal `FileOpsBackend` wrapper around the file-writing tools.

## 2. Directory layout

The on-disk layout of a course produced by the POC:

```
<course-root>/
├── COURSE.md          # course-level outline, manually editable
├── PEDAGOGY.md        # voice and teaching-style notes, manually editable
├── style.css          # visual style spec for all generated decks
└── lessons/
    ├── L1-<slug>/
    │   ├── PLAN.md
    │   ├── research.md
    │   └── slides.pptx
    ├── L2-<slug>/
    │   └── ...
    └── L<N>-<slug>/
        └── ...
```

**Decisions worth flagging:**

- Lesson directories live under `lessons/`, not at the top level. Keeps the root readable.
- Directory naming is `L<N>-<slug>` where `<N>` is the canonical lesson ID and `<slug>` is a kebab-case derivative of the lesson title at creation time. The system locates a lesson by globbing `lessons/L<N>-*`, so the slug suffix is cosmetic and renaming the title does not require renaming the directory.
- The `.agency/sessions/` directory mentioned in the broader architecture (conversation persistence) is deferred to Phase 2.
- The agency definition itself does not live in the course directory in the POC — it remains in the source repo (the OpenSwarm fork). Migrating the agency into the vault is a Phase 3 concern.

## 3. File-format contracts

### 3.1 COURSE.md

The course-level outline. Co-authored by the user and `CourseDesignerAgent`. Manually editable; the canonical source of course-level intent.

```markdown
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
3. **LO-3:** Apply hash functions to construct higher-level cryptographic primitives.
4. **LO-4:** Identify common pitfalls and misuses of hashing in real-world systems.

# Lessons

## L1: Introduction to hashing

- **LO-1.1:** State the definition of a cryptographic hash function and contrast it with non-cryptographic hashes.
- **LO-1.2:** Describe preimage resistance, second-preimage resistance, and collision resistance informally.
- **LO-1.3:** Recognise three categories of real-world applications that depend on hashing.

## L2: The three security properties formally

- **LO-1.2:** Define preimage, second-preimage, and collision resistance with precise probabilistic statements.
- **LO-1.3:** Explain why these properties are not equivalent and which implies which.

## L3: ...

```

**Frontmatter fields** (all required for the POC):

- `title` — string. The course's display title.
- `description` — string. One-to-three sentence summary of the course.
- `target_audience` — string. Free-text description of who the course is for; consumed by all agents.
- `lesson_count` — integer. The intended number of lessons.
- `lesson_duration_minutes` — integer. Used by `SlidesAgent` to pace slide density.
- `language` — string. ISO 639-1 code. Defaults to `en` if omitted.

**Body structure** (strict):

- Exactly one `# Learning objectives` H1, containing a numbered list with `LO-<N>` IDs.
- Exactly one `# Lessons` H1, containing one H2 per lesson in the form `## L<N>: <Title>`.
- Each lesson H2 is followed by 3–5 bullets, each a micro-LO with ID `LO-<N>.<M>` (see §4).

The agents must read and write COURSE.md against this exact structure. If a user manually edits it into invalid form, the agents should refuse to proceed and surface the parse error rather than guessing.

### 3.2 PLAN.md

The per-lesson brief. Output of `LessonPlannerAgent`. Consumed by `SlidesAgent`. Lives at `lessons/L<N>-<slug>/PLAN.md`.

```markdown
---
lesson_id: L1
title: Introduction to hashing
---

# Learning objectives

- **LO-1.1:** State the definition of a cryptographic hash function and contrast it with non-cryptographic hashes.
- **LO-1.2:** Describe preimage resistance, second-preimage resistance, and collision resistance informally.
- **LO-1.3:** Recognise three categories of real-world applications that depend on hashing.

# Lecture outline

This first lecture opens with a motivating example — the everyday password-check on a login screen — and uses it to introduce the idea of a function that is easy to compute forward but hard to invert. Building on this intuition, we distinguish cryptographic from non-cryptographic hashes by contrasting MD5 with `hash()` in Python's standard library...

[200–500 words of prose, not slide-by-slide]

# Key concepts and definitions

- **Hash function:** A function that maps inputs of arbitrary length to outputs of fixed length.
- **Cryptographic hash function:** A hash function with additional security properties (defined formally in lesson 2).
- **Digest:** The output of a hash function for a given input.

# Worked examples or illustrations

We will walk through three short examples during the lecture:

1. Computing `SHA-256("hello")` by hand at a structural level (not bit-level), to make the input → fixed-output property concrete...

[prose description of examples the lecturer can use]

# References

[^1]: Menezes, A., van Oorschot, P., & Vanstone, S. (1996). *Handbook of Applied Cryptography*. CRC Press. Chapter 9. https://cacr.uwaterloo.ca/hac/
[^2]: Katz, J., & Lindell, Y. (2020). *Introduction to Modern Cryptography* (3rd ed.). Chapman and Hall/CRC.
```

**Decisions worth flagging:**

- The lecture outline is **prose, not slide-by-slide**. `SlidesAgent` decides the slide breakdown. This was the conscious choice from Q7 — pinning slide structure here would double the editing surface.
- The PLAN.md repeats the lesson's micro-LOs from COURSE.md. This is intentional: PLAN.md should be self-contained enough to read without flipping back to COURSE.md. The duplication is a small cost; the alternative (require cross-file lookup at every read) is worse.
- Citations are numbered-footnote style (`[^N]`), full bibliographic refs at the end. Pandoc-CSL is overkill for v0.1.

### 3.3 research.md

Per-lesson research artifact. Output of `DeepResearchAgent`. Consumed by `LessonPlannerAgent`. Lives at `lessons/L<N>-<slug>/research.md`.

```markdown
---
lesson_id: L1
generated_at: 2026-05-15T14:32:01Z
---

# Research notes: Introduction to hashing

## Topic 1: Defining cryptographic hash functions

The standard textbook definition characterises a cryptographic hash function as a deterministic function $H: \{0,1\}^* \to \{0,1\}^n$ that is efficiently computable [^1]. The distinction from non-cryptographic hash functions (such as those used in hash tables) lies not in the input-output shape but in the assumed adversary model: cryptographic hashes are designed to resist computationally bounded adversaries attempting to find structure in the mapping [^2]...

[Synthesized notes with inline citations, organized by sub-topic]

## Topic 2: ...

# References

[^1]: Menezes, A., van Oorschot, P., & Vanstone, S. (1996). *Handbook of Applied Cryptography*. CRC Press.
[^2]: Rogaway, P., & Shrimpton, T. (2004). Cryptographic Hash-Function Basics. *FSE 2004*. https://eprint.iacr.org/2004/035
```

The schema is intentionally less prescriptive than COURSE.md or PLAN.md — `DeepResearchAgent` decides its own topic structure based on what it finds. Required only: frontmatter (`lesson_id`, `generated_at`), one H1 with the lesson title, and a `# References` H1 at the end with the source list.

**Source preference** (encoded in the agent's prompt): primary academic sources first, textbooks second, reputable secondary sources (course notes from research universities, peer-reviewed surveys) third, general web sources only as last resort.

### 3.4 PEDAGOGY.md

The course-level pedagogical voice and style guide. Lives at course root. Scaffolded by `init` with the default below; the educator is expected to edit before generating lessons.

```markdown
# Pedagogical guidance

## Voice

Authoritative but accessible. Clarity over comprehensiveness. Concrete examples over abstract definitions where the topic permits.

## Pedagogical approach

Traditional lecture format. The lecturer presents material in person; the goal is to distill and structure information clearly. No active-learning components, exercises, problem sets, or flipped-classroom expectations in the current version.

## Constraints

Slides should be readable from the back of a lecture hall. Favour short bullets, generous whitespace, clear typography. One main idea per slide. Speaker notes carry the prose; slides carry the scaffolding.
```

Consumed verbatim by `LessonPlannerAgent` and `SlidesAgent` as part of their prompt context. Free-form prose is fine — the agents are instructed to honour its substance, not parse a schema.

### 3.5 style.css

Visual style spec for generated slide decks. One CSS file at course root, inlined by `SlidesAgent` into each generated HTML deck before LibreOffice export. The educator owns and edits this file.

```css
/* Educator Agency: course visual style.
 * Edit this file to customize the look of all generated slide decks.
 * Slides are NOT auto-regenerated when this file changes —
 * run `educator-agency regenerate-slides L<N>` to apply.
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
  font-size: 28px;       /* readable from the back of a lecture hall */
  line-height: 1.45;
  padding: 60px;
}

.slide h1 {
  font-family: var(--font-heading);
  font-size: 48px;
  color: var(--accent);
  margin: 0 0 0.5em 0;
}

.slide h2 {
  font-family: var(--font-heading);
  font-size: 36px;
  margin: 0 0 0.5em 0;
}

.slide ul,
.slide ol {
  margin: 0.5em 0;
  padding-left: 1.2em;
}

.slide li {
  margin-bottom: 0.4em;
}

.slide code {
  font-family: var(--font-mono);
  background: #f4f4f4;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 0.85em;
}

.slide .footnote {
  font-size: 0.6em;
  color: var(--muted);
  position: absolute;
  bottom: 30px;
  right: 60px;
}
```

**CSS integration strategy (Phase 1 decision).** The current OpenSwarm `slides_agent` emits HTML using a richer themed system (`.slide`, `.slide-wrapper`, `.content-safe-area`, `.canvas`, `.glass-panel`, `.bg-grid` — and notably **no `.footnote`**), driven by [slides_agent/tools/slide_html_utils.py](../../../../slides_agent/tools/slide_html_utils.py) and [ManageTheme.py](../../../../slides_agent/tools/ManageTheme.py). For educator-agency, we **replace** that themed system with the flat `style.css` above as the only style source. The HTML template is adjusted to emit `.slide`, `.slide h1/h2`, `.footnote`, and `.speaker-notes` blocks; the gradient-to-SVG/theme machinery is bypassed. Re-adding theme variants is a Phase 2+ concern.

## 4. Learning-objective numbering

The course's pedagogical spine. The numbering scheme is enforced by all agents and surfaced explicitly in prompts so the LLM produces consistent IDs across runs.

**Course-level LOs** are numbered `LO-1`, `LO-2`, `LO-3`, … in COURSE.md's `# Learning objectives` section. They are the outcomes the *course* delivers. Phrased as student-facing capability statements ("Define …", "Compare …", "Apply …").

**Lesson-level micro-LOs** are numbered `LO-<N>.<M>` where `<N>` matches a course LO and `<M>` is sequential within the supporting lesson. So `LO-1.1`, `LO-1.2`, `LO-1.3` are three micro-LOs supporting `LO-1`. They appear:

1. In COURSE.md's `## L<N>: <Title>` bullets (the canonical declaration).
2. In `lessons/L<N>-<slug>/PLAN.md`'s `# Learning objectives` section (duplicated for self-contained reading).

**Multi-mapping rule.** A micro-LO that genuinely supports more than one course LO is listed twice with two IDs (e.g., the same sentence appears under both `LO-1.3` and `LO-4.2`). The POC does not attempt graph-aware dedup. Honest duplication beats clever indirection at this stage.

**Renumbering rule.** Once assigned, IDs are stable for the life of the course. Inserting a new course LO between `LO-2` and `LO-3` produces `LO-5`, not a renumber cascade. Gaps are allowed and expected.

**Prompt enforcement.** All four content agents have explicit prompt instructions: "Course LOs use the format `LO-N`. Lesson micro-LOs use the format `LO-N.M`. Never renumber existing IDs. Multi-supporting micro-LOs are listed under each ID they support."

## 5. Agent topology

Five agents. Three are forked/adapted from OpenSwarm; two are built fresh.

### OrchestratorAgent (adapted from OpenSwarm)

**Role.** Routes user requests to specialists. Never produces content directly.

**Inputs.** User messages; high-level course state (which lessons exist, which are in what state).

**Outputs.** Handoffs to specialists; pass-through narration to the user.

**Handoffs.** To `CourseDesignerAgent` for course-level work; to a per-lesson sub-flow (Research → Plan → Slides) for lesson-level work.

**Adaptation from OpenSwarm.** Prompt rewritten for the educator-course domain. Single-shot framing replaced with multi-turn coordination: after each artifact is accepted, the orchestrator asks the user whether to continue, regenerate, or stop.

### CourseDesignerAgent (new)

**Role.** Interactively negotiates with the user to produce COURSE.md from scratch (Q's "outline is co-created from scratch" path).

**Inputs.** User's initial intent ("I want to teach X to Y over Z lectures"); follow-up Q&A in conversation.

**Outputs.** Proposed COURSE.md content, via the diff-approval write flow.

**Tools.** `read_file` (to inspect existing COURSE.md if any), `write_file` (to propose).

**Prompt commitments.** Asks follow-up questions when the user's brief is under-specified (audience details, scope, depth, prior-knowledge assumptions, total course duration). Drafts iteratively: first the frontmatter and course-level LOs, then the lesson list with micro-LOs. Pushes back if the user proposes a `lesson_count` and `lesson_duration_minutes` that mathematically can't fit the proposed scope.

### DeepResearchAgent (carried from OpenSwarm, lightly adapted)

**Role.** Produces `lessons/L<N>-<slug>/research.md` for a given lesson, grounded in primary academic sources where possible.

**Inputs.** COURSE.md (for context), the specific lesson's micro-LOs, PEDAGOGY.md (to gauge depth/voice).

**Outputs.** Proposed `research.md` via the write flow.

**Adaptation from OpenSwarm.** Source-preference instructions adjusted to favour academic / primary sources over commercial web content. Citation format aligned with the footnote convention defined in §3.3 — note this is an **inversion** of the current OpenSwarm prompt, which explicitly forbids the trailing source list and mandates inline `[Source: URL]` markers; expect to verify the rewrite holds under load.

### LessonPlannerAgent (new)

**Role.** Produces `lessons/L<N>-<slug>/PLAN.md` for a given lesson, synthesizing research and pedagogy into a lecturer-facing brief.

**Inputs.** COURSE.md, PEDAGOGY.md, the lesson's `research.md`, the lesson's micro-LOs.

**Outputs.** Proposed `PLAN.md` via the write flow.

**Prompt commitments.** Lecture outline as prose (not slide-by-slide). Pedagogy voice honoured. Citations carried forward from `research.md` into the PLAN's `# References`. Worked examples emerge from the research where present; if none surface, the agent must say so rather than invent.

### SlidesAgent (carried from OpenSwarm, adapted)

**Role.** Produces `lessons/L<N>-<slug>/slides.pptx` from PLAN.md, PEDAGOGY.md, and style.css.

**Inputs.** PLAN.md (primary), PEDAGOGY.md (voice and constraints), style.css (visual style, inlined into generated HTML).

**Outputs.** Proposed `slides.pptx` via the write flow.

**Adaptation from OpenSwarm.** Prompt rewritten to consume the structured PLAN.md schema rather than freeform input. HTML template adjusted to emit the flat-`style.css` class names (per the §3.5 strategy) instead of the existing themed system. **Speaker notes** generated at ~3-5 sentences per slide and emitted as `<div class="speaker-notes">` blocks in the HTML; a `python-pptx` post-processing pass after `dom-to-pptx` lifts those blocks into PPTX `notesSlide` content (the current OpenSwarm pipeline has no notion of speaker notes — this is net-new work). Slide-density tuned for `lesson_duration_minutes`.

## 6. End-to-end flows

### 6.1 Cold-start course creation

The canonical Phase 1 flow. User has nothing on disk; produces a complete course.

```
User                Orchestrator          Specialist agents
──── "I want to     ─────────────►        
teach a course
on cryptographic
hashing to
2nd-year CS
students, 6 lessons
of 50 min each"

                    ──── handoff ────►   CourseDesigner

                                          asks 2-4 follow-ups
                                          (depth, prior knowledge,
                                          textbook to align with)

User                ◄──── Q&A in chat ───
[answers]           ─────────────►

                                          drafts COURSE.md
                    ◄────── proposes write ──────

User reviews diff,
approves with
minor edits
                    ─────── decision ──────►

Orchestrator: "Course outline accepted. Generate lesson 1?"
User: yes

                    ──── handoff ────►   DeepResearcher (L1)
                                          produces research.md
                    ◄────── proposes write ──────
User approves.
                    ──── handoff ────►   LessonPlanner (L1)
                                          produces PLAN.md
                    ◄────── proposes write ──────
User approves.
                    ──── handoff ────►   SlidesAgent (L1)
                                          produces slides.pptx
                    ◄────── proposes write ──────
User approves.

Orchestrator: "Lesson 1 complete. Generate lesson 2?"
[loop]
```

**Properties this flow upholds:**

- One artifact per approval gate. The user is never asked to approve a `proposal_set` of more than one file in the POC. (Batching is a Phase 2 feature.)
- Lessons are generated sequentially with approval between each. Per Q2.
- Each lesson's research happens before its PLAN. Per Q3.
- The user can stop at any boundary. If they only ever generate L1's slides and walk away, the course-root is a valid intermediate state (COURSE.md present, lessons/L1/ populated, lessons/L2/ through L6/ absent).

### 6.2 Per-lesson regeneration

The user has manually edited COURSE.md — say, refined `LO-2.3` for L4 — and wants L4's downstream artifacts updated.

```
User edits COURSE.md in their editor.
User: "Regenerate lesson 4"

Orchestrator:                       
  ──── handoff ────►  DeepResearcher (L4)
                       overwrites research.md
  ◄── proposes write ──
User approves.

  ──── handoff ────►  LessonPlanner (L4)
                       overwrites PLAN.md
  ◄── proposes write ──
User approves.

  ──── handoff ────►  SlidesAgent (L4)
                       overwrites slides.pptx
  ◄── proposes write ──
User approves.

Orchestrator: "Lesson 4 regenerated."
```

**Properties:**

- The user explicitly triggers regeneration (per "do not automatically update"). The orchestrator does not detect drift on its own in v0.1.
- Each regeneration step is a separate diff/approval. The user can accept the new research but reject the new PLAN, for example, and the system leaves a coherent intermediate state.
- Previous versions are recoverable from git (Phase 3 concern in full architecture, but recommended even for POC — `git init` the course root manually).

## 7. Deferred decisions

Explicitly out of scope for Phase 1, with the reason:

- **Per-lesson prerequisites and dependency graphs.** Not needed for the POC; lessons are read in document order.
- **Slide-level granularity in PLAN.md.** Pinned at prose-level (Q7) to avoid doubling the editing surface; reconsider if SlidesAgent's slide breakdowns prove unsatisfactory.
- **Active-learning components.** PEDAGOGY.md template hardcodes "traditional lecture". Variants come later.
- **Multi-language support.** `language` frontmatter field exists but only `en` is exercised.
- **Cost / token-usage surfacing.** Out for the POC; a Phase 2 or Phase 3 concern.
- **Auto-detection of stale lessons after COURSE.md edits.** User explicitly triggers regeneration.
- **Parallel lesson generation.** Sequential with per-lesson approval (Q2); parallel is a Phase 3 `--batch` mode.
- **Themed slide system (gradients, glass panels, `ManageTheme.py`).** Phase 1 replaces the existing themed system with the flat `style.css` (per §3.5); re-adding theme variants is a Phase 2+ concern.

## 8. Implementation notes for the OpenSwarm fork

Things that are not design decisions but matter for the first commits:

- **License.** OpenSwarm is MIT. Fork is clean; preserve the original copyright header in any files retained, add a NOTICE if substantially modified.
- **In-place adaptation, not a fresh fork.** This repo *is* the OpenSwarm fork — there is no separate upstream import step. Educator-agency is implemented as a sibling agency definition (`educator_agency/agency_def/agency.py`) that lives alongside the existing [swarm.py](../../../../swarm.py) and imports a subset of the existing agent modules. The other agents (Image, Video, Data Analyst, Virtual Assistant, Docs) stay on disk and stay wired into the original `swarm.py` so the full OpenSwarm agency continues to work — only educator-agency excludes them. Re-evaluate Phase 2 whether to maintain both agencies long-term.
- **The OpenSwarm prompts assume single-shot.** All four reused agents' instructions will be rewritten. Treat OpenSwarm's prompts as scaffolding, not foundations.
- **`FileOpsBackend` for Phase 1.** A minimal Python `Protocol` with `read_file`, `write_file`, `list_files`. Two stacked implementations: `LocalFsBackend` (real disk writes; returns `Accepted` or `Failed`) and `ApprovalGatingBackend` (wraps `LocalFsBackend`, stores proposed writes in an `ApprovalBuffer` keyed by `proposal_id`, returns `Pending(proposal_id)` until the user resolves via `/approve` or `/reject`). Agent tools call the backend, not `open()` directly. This is the architectural seam that keeps the dual-mode property reachable. The `WriteOutcome` union is extended from the triple in ARCHITECTURE.md §4.2 to a 4-tuple: `Accepted | Rejected | Failed | Pending(proposal_id)` — the `Pending` variant is needed because the agent's turn completes before the user replies, and the proposal must be addressable across turns. **Architectural impact:** flag this back to ARCHITECTURE.md §4.2; the rejection-handling commitment from §4.3 still holds (agents must reason about `Rejected` even though it only fires when `/reject` is used).
- **Integration test asserts structure, not exact content.** LLM determinism is not the goal. The Phase 1 integration test: given a fixed prompt and style.css, the system produces a COURSE.md with valid frontmatter, the expected H1 sections, at least 3 lessons with valid LO numbering, at least one `lessons/L<N>-*/` directory populated with all three artifacts, and a `slides.pptx` that opens without error. Content quality is for the user to assess.
- **Heavy native deps from OpenSwarm.** Playwright/Chromium and a Node.js `dom-to-pptx` runner ship with the OpenSwarm fork — *not* LibreOffice, despite an earlier draft of this document claiming otherwise (the slide path is [BuildPptxFromHtmlSlides.py](../../../../slides_agent/tools/BuildPptxFromHtmlSlides.py) → [html2pptx_runner.js](../../../../slides_agent/tools/html2pptx_runner.js)). Acceptable for Phase 1 (single developer, local machine). Will reappear as a distribution problem in Phase 3 (Obsidian plugin bundling) — flag, but don't solve yet.

## 9. What to build first

Per PLAN.md's "write the integration test first" rule, applied to this design:

1. **Write the test scaffolding.** Define the integration test that asserts the §6.1 cold-start flow produces a course directory matching this document's schemas. The test will fail until everything below exists.
2. **Define the file-format parsers.** Small Python modules that read and validate COURSE.md, PLAN.md, research.md against §3's schemas. Tested in isolation. These are the trust boundary between agent output and the rest of the system.
3. **Define `FileOpsBackend` and `LocalFsBackend`.** Per the §8 implementation notes.
4. **Define the new agency.** Create `educator_agency/agency_def/agency.py` importing the three reused agents (`OrchestratorAgent`, `DeepResearchAgent`, `SlidesAgent`) and the two new ones. **Keep** the other agents (Image, Video, Data Analyst, Virtual Assistant, Docs) on disk and in [swarm.py](../../../../swarm.py) — they may have future use cases; educator-agency simply doesn't import them.
5. **Rewrite the agent prompts.** Per §5 commitments, against the schemas in §3 and §4.
6. **Build `CourseDesignerAgent` and `LessonPlannerAgent`.** New code.
7. **Wire orchestrator handoffs.** Per §6.1.
8. **Run the integration test.** Iterate until green.

Cross-cutting throughout: structured JSON logging with a turn ID on every record (PLAN.md cross-cutting commitment). Without it, debugging step 8 is unreasonably painful.
