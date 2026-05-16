# PLAN — Work breakdown for Educator Agency

> Implementation plan for the system described in [PRD.md](./PRD.md) and [ARCHITECTURE.md](./ARCHITECTURE.md). Four phases, each ending in a demonstrable artifact.

## Approach

The plan is shareable-milestone-driven rather than feature-driven. At the end of every phase, there is a working artifact someone can run. The phases compound — Phase 2 builds on Phase 1's runtime; Phase 3 wraps Phase 2's runtime in a bridge; Phase 4 polishes the whole stack for outside contributors.

Two non-negotiable cross-cutting commitments are established in Phase 1 and maintained throughout: a scenario test harness, and structured logging with turn IDs. Without these, multi-agent behaviour is impossible to debug.

Estimated total effort: ~10–12 weeks at a focused pace, longer part-time.

---

## Phase 1 — Vertical slice

**Duration estimate:** 1–2 weeks

**Goal:** prove the architecture with the smallest possible end-to-end slice. One agent, one markdown file, one multimedia tool, routed through the `FileOpsBackend` abstraction.

**Demonstrable artifact:** a command-line invocation that takes a markdown file, has a single agent modify it based on a natural-language prompt, and produces a `.pptx` from the result. Runnable from a clean checkout in under 5 minutes.

### Subtasks

- **Repository scaffolding.** `uv` project, `pyproject.toml` declaring both `educator-agency` and `educator-agency-bridge` entry points (the bridge is stubbed for now). Package layout: `educator_agency/{agents,tools,backend,runtime}/`.
- **`FileOpsBackend` protocol.** Define the full interface — read operations, write operations, `WriteOutcome` type — even though only `LocalFsBackend` is implemented. Designing against an imagined second implementation prevents leaky abstractions.
- **`LocalFsBackend` implementation.** Direct disk I/O, all writes return `Accepted` (or `Failed` on OS error).
- **Single `EditorAgent`.** Minimal agent with three tools wired through the backend: `read_file`, `update_file`, `list_files`. Agent instructions written assuming rejection is possible (even though it never is in CLI mode).
- **`md_to_pptx` tool.** End-to-end multimedia tool using `python-pptx`. Chosen because pure-Python, no native deps, fast feedback loop.
- **Bare CLI runner.** stdin → agency → stdout. No TUI, no streaming polish, just a working loop.
- **`.env` loading.** Read `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` from a `.env` file in the working directory.
- **One integration test.** Given a sample markdown file and a hardcoded prompt, the agent edits the file and produces a pptx. Asserts file state and tool calls.

### Cross-cutting setup that begins in this phase

- **Scenario test harness.** A small framework for "given starting vault state and a user message, run the agency, assert resulting file state and tool calls." Used by the integration test above; expanded in later phases.
- **Structured JSON logging.** Every tool call, backend operation, and agent handoff logged with a turn ID. Future-you needs this.
- **Strict separation of agency definition from agency runtime.** Agency code in `educator_agency/agency_def/`, runtime in `educator_agency/runtime/`. The runtime loads agency definitions from a path; in v1 that path is internal to the package, in Phase 3 it becomes a vault path.

### First thing to write

The integration test, before it passes. This forces the architecture to be testable from the first commit and gives a concrete definition of "done" for the phase.

### Decision point at end of phase

Do the agent's instructions feel genuinely mode-agnostic? Re-read them imagining bridge mode where every write might be rejected. If anything in the instructions assumes "the write happened", fix it now before the prompt drifts further.

---

## Phase 2 — Educator agency CLI

**Duration estimate:** 2–3 weeks

**Goal:** something a colleague could install, configure, and use for real work after reading a short README. The CLI is the deliverable; colleagues are the audience.

**Demonstrable artifact:** `uv tool install educator-agency` from a published-but-private PyPI package (or `uv tool install git+https://...`) installs the tool. The user runs `educator-agency init` to scaffold an agency into a directory, configures an API key, runs `educator-agency` to enter a chat loop, and can produce three multimedia artifacts (pptx, docx, pdf) from collaborative editing. Tag a v0.1 release; share with 2–3 colleagues.

### Subtasks

- **Multi-agent topology.** At minimum a `CoordinatorAgent` plus two specialists — `ContentDesignerAgent` and `MultimediaProducerAgent`. Real handoffs, not contrived ones. Configured through Agency Swarm's standard topology constructs.
- **Full tool surface.** Implement `read_file`, `create_file`, `update_file`, `patch_file`, `delete_file`, `rename_file`, `list_files`, `search_files`. All routed through `LocalFsBackend`.
- **Additional multimedia tools.** `md_to_docx` via `python-docx`, `md_to_pdf` via `weasyprint` or `pandoc`. One external-API tool (recommend HeyGen for avatar narration, or ElevenLabs for audio) to prove the pattern for third-party services.
- **Optional extras packaging.** Multimedia tools declared as `[project.optional-dependencies]` extras: `educator-agency[multimedia-local]`, `[multimedia-heygen]`, etc. Base install pulls only LLM dependencies.
- **CLI UX.** Either adopt AgentSwarm CLI (the VRSEN OpenCode fork) for the TUI, or build a minimal Textual app. Lean toward AgentSwarm CLI if it's a clean fit. Streaming tokens, tool-call narration, basic input/output editing.
- **Conversation persistence.** Sessions written to `.agency/sessions/{id}.jsonl`. `educator-agency --resume {id}` reloads.
- **Configuration.** `.env` for secrets, `agency.toml` (or similar) for non-secret config: model choice, agent enable/disable, default working directory.
- **Logging.** Structured logs to file with sensible defaults; `--verbose` flag for stdout streaming.
- **README and quickstart.** Install, configure, two example workflows, the trust model section, links to the example agencies repo (which will exist by Phase 4).
- **Tag v0.1, share.** Distribute to a small group of colleagues. Ask them what they would build with it for their own work — not whether it's useful.

### First thing to ship in this phase

The v0.1 README, written before the bulk of the code. Forces you to explain what the thing is and what the install experience looks like before building either. Edit ruthlessly.

### Watch out for

Scope creep into "every multimedia tool I can think of". Two well-implemented tools beat eight half-implemented ones. The tool architecture is provable with two; the rest is mechanical.

Also: do not let colleague feedback in this phase push you toward UI features. Feedback at this stage is about agent behaviour, tool selection, and the agency's reasoning quality. UI feedback waits until Phase 3.

---

## Phase 3 — Obsidian bridge and plugin MVP

**Duration estimate:** 3–4 weeks

**Goal:** end-to-end demo of the actual co-creation experience — edit a markdown file in Obsidian, agent proposes changes, CM6 diff review, accept, git auto-commit. Not feature-complete; just enough to feel the experience.

**Demonstrable artifact:** a screen recording of the full loop — user types into a chat sidebar, agent proposes a markdown change, user opens the diff view, accepts a chunk, the file updates, a git commit appears in the history with the agent as author. Plugin is installable from a local build folder (not yet community-submitted).

### Subtasks

Three workstreams running roughly in parallel — Python bridge, TypeScript plugin, and cross-cutting concerns.

**Python: the bridge.**

- `educator-agency-bridge` entry point. Spawns a WebSocket server on `--port 0`, prints the assigned port to stdout.
- `ObsidianBridgeBackend` implementation. Internal proposal buffer; writes are routed over WS to the plugin and await `decision` messages.
- Wire protocol v1. Message schemas for `user_message`, `assistant_token`, `tool_call_started`, `tool_call_completed`, `assistant_done`, `proposal_set`, `decision`, `handshake`, `ping`/`pong`, `error`, `shutdown`. Versioning negotiated in handshake. Documented in `protocol.md` in the repo.
- Turn-boundary buffering. Proposals accumulated during a turn, flushed as one `proposal_set` at turn end. Futures resolved when `decision` arrives.
- Stateless across turns. Plugin sends conversation history on every `user_message`; bridge stores nothing persistent.
- Clean shutdown on SIGTERM. Error reporting on disconnect.

**TypeScript: the plugin.**

- Plugin scaffold from Obsidian's sample plugin template.
- Sidecar lifecycle. Spawn `educator-agency-bridge`, parse port from stdout, restart with exponential backoff on crash, kill on plugin unload, surface stderr to a log file with a "Show server logs" command.
- WebSocket client implementing the protocol. Schema validation on incoming messages.
- Right-panel chat `ItemView`. Message rendering, token streaming, tool-call narration cards, proposal cards with summary and "Open diff" button.
- Diff view in a separate `ItemView` hosted in the main editor area. Uses `@codemirror/merge`'s `MergeView` with `@codemirror/lang-markdown`, `revertControls: "a-to-b"`, `collapseUnchanged`. Side B editable for "accept with edits".
- Accept/reject controls. On accept, plugin executes the write via `vault.modify` or `vault.create` — bridge does not write to disk.
- Settings tab. Bridge binary path discovery, API keys (or pointer to `.env` file), model selection, default agency directory.

**Cross-cutting.**

- Git integration. Detect repo at vault root, auto-commit on accepted proposal with structured message (see `ARCHITECTURE.md` §9), author set to agent name, committer set to user. Pre-commit safety: commit any manual edits with `[manual]` prefix before the agent commit.
- "Revert last agent change" command in the command palette.
- "Review proposals" command that re-opens the diff view if dismissed.
- One end-to-end test using Playwright against a test vault. Hard to write but pays back during all subsequent changes.
- Agency-in-vault. Migrate the default agency from internal package location to `vault/agency/`, scaffolded by `educator-agency init` and `educator-agency-bridge --init-vault`.

### First thing to build in this phase

`protocol.md`. Write the message schemas before writing the code that emits them. Both sides implement against the document; conflicts surface as documentation disagreements rather than runtime bugs.

### Watch out for

The temptation to build inline `unifiedMergeView` instead of side-by-side `MergeView`. The inline version looks more "Cursor-like" and is harder, with fragile lifecycle issues. Side-by-side first; inline as a Phase-5 enhancement.

Also: do not let the plugin accumulate logic that should be in the bridge or vice versa. The plugin renders, the bridge orchestrates. When in doubt, the plugin is the simpler half — Python is more debuggable.

### Decision point at end of phase

Does the dual-mode architecture hold up? Run the same conversation in CLI mode and bridge mode (with auto-accept). Do the agents behave identically? If not, mode-specific assumptions have leaked somewhere — fix before Phase 4.

---

## Phase 4 — OSS release

**Duration estimate:** 2 weeks

**Goal:** people who aren't you can install and use this without you in the loop. The plugin is in the Obsidian community registry; the Python package is on PyPI; the documentation is sufficient.

**Demonstrable artifact:** a third party — someone who hasn't seen the code — installs the plugin from the Obsidian community plugins directory, configures it, and produces their first multimedia artifact, following only the published documentation. You watch over their shoulder once and they never need to ask you a question again.

### Subtasks

- **Cross-platform testing.** macOS (both architectures), Linux (Ubuntu LTS), Windows (best-effort, Tier 2). Document known issues per platform.
- **Install documentation.** Separate paths for CLI-only users vs plugin users. Troubleshooting section for the common issues: PATH not picked up by Obsidian on macOS (login shell hack), Windows Python via uv, antivirus flagging the subprocess spawn, OAuth-style API key flows for users with managed Anthropic accounts.
- **Trust model documentation.** A prominent `TRUST.md` or section in the README explaining what the agency can do on the user's machine and why we don't sandbox.
- **Example agencies repository.** Split out as its own repo (`educator-agencies-examples`) with a `degit`-friendly structure. At least three example agencies covering different use cases.
- **PyPI publishing CI.** GitHub Actions workflow for tagged releases. Version pinning strategy documented.
- **Obsidian community plugin submission.** PR to `obsidianmd/obsidian-releases` following their checklist. Plugin manifest, README, screenshots.
- **Contribution guidelines.** `CONTRIBUTING.md` with the test harness explanation, how to add a multimedia tool, how to add an agent.
- **Issue and PR templates.** Standard ones plus a "tell me about your agency" issue type for community feedback.
- **Version compatibility check.** Plugin checks bridge version on connect; if mismatched, prompts user with upgrade instructions before attempting to use the connection.
- **Tag v1.0, write a launch post.** Explain the project's premise, the dual-mode architecture, and the multimedia ecosystem. Crosspost to relevant Obsidian and AI research communities.

### First thing to do in this phase — and the highest-leverage activity in the whole plan

Ask 2–3 colleagues to install the system from scratch on different OSes, following only your published documentation. Watch over their shoulder or screen-share. Every confusion is a doc fix. Every assumed dependency is a `prerequisites` section. Every "wait, what did you click?" is a screenshot.

This single activity will find more issues than a month of internal QA.

### Watch out for

Submitting to the Obsidian community plugins registry too early. The submission PR is reviewed — wait until at least one external person has used the plugin successfully end-to-end. Otherwise the maintainers' feedback becomes your install testing.

Also: resist the urge to add features in this phase. Phase 4 is polish, documentation, and packaging. New features cause new bugs, which delay release indefinitely.

---

## Cross-cutting work, present in every phase

These are not phase-specific; they evolve continuously.

- **Scenario test harness.** Established in Phase 1, expanded with each new agent and tool. Every new agent gets at least one scenario test. The test harness is the safety net for refactoring as the architecture matures.
- **Structured logging.** Established in Phase 1. Logs are how you debug multi-agent behaviour. JSON to file by default, with a `--verbose` flag for terminal streaming. Turn ID in every record.
- **The `protocol.md` document.** Created in Phase 3 but maintained from then on. Every WS message change is a protocol-document change first.
- **Trust-model surfacing.** Drafted in Phase 2's README, formalised in Phase 4, but surfaced in user-facing copy throughout — first-run prompts, agency-load prompts, install docs.
- **Architecture decision records (ADRs).** Optional but recommended. For each non-trivial decision (dual-mode, plugin-writes, async-everywhere, no-staging-directory, no-sandboxing), a short ADR documents what was decided, what alternatives were considered, and why. Future contributors will need this.

## Decision points and gates

Each phase has a gate condition before moving to the next.

- **End of Phase 1:** the agent's instructions feel mode-agnostic. The integration test runs from a clean checkout.
- **End of Phase 2:** at least one colleague has used the tool for real work (not toy work) and given feedback. Their feedback is acknowledged in a tracking issue, not necessarily acted on.
- **End of Phase 3:** the same conversation works identically in CLI mode and bridge mode (with auto-accept). The dual-mode property is verified by behaviour, not just by code structure.
- **End of Phase 4:** a third party installs and produces an artifact from documentation alone. Until this happens, the system is not actually shipped.

## Out of scope for v1.0

To prevent scope creep, the following are explicitly deferred to post-1.0:

- Inline `unifiedMergeView` diff (side-by-side only for v1)
- Cost / token-usage surfacing in the UI
- Multi-window Obsidian support
- Three-way merge when files are dirty in the editor (v1 blocks proposals on dirty files)
- Community marketplace for sharing agencies (manual sharing only)
- Hosted/cloud version (out of scope permanently — see PRD §5)
- Agency hot-reload mid-turn (only between turns)
- Plugin support for non-markdown content (e.g. canvas, kanban)

## Estimated total effort

| Phase | Duration | Cumulative |
|---|---|---|
| 1: Vertical slice | 1–2 weeks | 1–2 weeks |
| 2: Educator agency CLI | 2–3 weeks | 3–5 weeks |
| 3: Obsidian bridge + plugin MVP | 3–4 weeks | 6–9 weeks |
| 4: OSS release | 2 weeks | 8–11 weeks |

Full-time focused execution: ~10–12 weeks. Part-time or interrupted execution: meaningfully longer. Calendar duration should include buffer for the things you don't yet know are hard.


# Work tracking

16/05/2026: Implemented a very basic version of the POC described in 001_presentation_poc that can go from a high level course design to a series of slides, implementing basic control gating and orchestration. However, the newly created slides agent, that aligns with the POC design, loses out from the stylistic functionality of the agency swarm slides_agent. A preliminary analysis as to why is described in EXPLANATION.md in Task 002