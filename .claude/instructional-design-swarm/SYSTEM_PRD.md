# PRD — Educator Agency

> A multi-agent co-creation tool for educators, built on Obsidian and the Agency Swarm framework.

## 1. Vision

Educators producing self-directed learning content spend disproportionate effort on **conversion and packaging** — turning a lesson concept into slides, handouts, narrated videos, quizzes. The intellectual work is in the design; the production is mechanical but slow.

Educator Agency is a co-creation tool: educators work in their existing markdown-based notes (Obsidian), and a multi-agent AI system collaborates with them inside that environment to design, draft, refine, and **automatically produce** the multimedia artifacts that flow from their content. Every change the agents propose passes through a diff-style approval gate. Every accepted change is committed to git. Nothing happens behind the educator's back.

The conceptual lineage is Cursor IDE applied to educational content design rather than code.

## 2. Target users

Two distinct audiences, served by two distinct deliverables from the same codebase.

**Primary: educators (Obsidian plugin users).** Working educators — university lecturers, instructional designers, online course authors — who already use Obsidian or markdown-based notes. They are domain experts, comfortable with markdown, not necessarily comfortable with terminals, Python, or git internals. The Obsidian plugin is built for them.

**Secondary: developers and researchers (CLI users).** AI researchers, instructional-design technologists, and developers who want to extend the agency, build custom multimedia tools, or run the agents headlessly. They get a Python CLI and the same agency runtime. The CLI exists to keep the architecture honest and to enable researcher workflows; it is not the path through which non-technical educators are expected to engage.

Importantly, the *agency definition itself* is shared by both audiences and lives inside the user's vault (see §3.5), which means a developer's customisations can be packaged and shared with educator colleagues.

## 3. Requirements

### 3.1 Co-creation, not autocompletion

The agents must be designed for **multi-turn collaborative authoring**, not single-shot generation. The educator gives intent; the agency proposes structure, content, and artifacts; the educator reviews and refines; agents iterate. The expected unit of interaction is "design a unit of teaching material", not "generate a paragraph".

### 3.2 Diff-gated approval

Every change to a vault file proposed by an agent must be reviewable as a diff before it is written. The educator can accept, reject with feedback, or edit the proposal before accepting. The agents must be designed to handle rejection productively — rejection is a normal signal, not a failure.

### 3.3 Multi-agent decomposition

The system must support **specialised agents** (e.g. a content designer, a multimedia producer, an editor) coordinated by an orchestrator. The agency topology must be configurable: educators or developers can add, remove, or reconfigure agents.

### 3.4 Multimedia tool integration

Agents must be able to invoke production tools that convert markdown content into other formats: at minimum PowerPoint (`md → pptx`), Word (`md → docx`), and PDF. The architecture must accommodate additional tools as a first-class extension point: avatar narration video (HeyGen-style), screencast generation, quiz extraction, podcast-style audio. Tools must be invocable both as agent tool calls and standalone from the CLI.

### 3.5 User-customisable agency, in the vault

The agency definition — agent instructions, tool wiring, topology — must live inside the user's Obsidian vault, not inside the installed Python package. This enables:

- Per-vault customisation (different agency configurations for different courses or institutions)
- The agency itself being editable through the same diff/approval loop that edits content
- Sharing of agency configurations alongside content

The installed package provides a canonical default agency that users can scaffold into their vault and modify from there.

### 3.6 Git as audit trail

Every accepted agent-authored change must be committed to git with structured provenance:
- Commit author identifies which agent made the change
- Commit committer is the user's git identity
- Commit message includes which conversation turn produced the change

The educator must be able to revert any agent-authored change with a single command. Manual edits are auto-committed alongside agent changes so history stays linear.

The system must work in vaults that are not git repositories, but should clearly surface that audit history will be unavailable.

### 3.7 Dual-mode delivery

A single Python package must expose two entry points:
- A **WebSocket bridge** (`educator-agency-bridge`) spawned by the Obsidian plugin
- A **CLI** (`educator-agency`) for direct terminal use

Both must use the same agency definitions, the same agent instructions, the same tools. Mode is a runtime choice, not a forking of the codebase.

### 3.8 Local-first

All vault data stays on the user's machine. The only network egress is to the LLM provider (Anthropic, OpenAI, or another configured via LiteLLM) and any explicitly opted-in third-party multimedia services (e.g. HeyGen). The educator's content is never sent to any server operated by the project.

### 3.9 Transparency and observability

The educator must always be able to see:
- Which agent is currently active
- What tool is being called and with what arguments
- What changes are about to be proposed
- What was committed to git and by which agent

Tool calls and agent reasoning are narrated in the chat interface as they happen.

## 4. Design principles

These are the principles the architecture is required to enforce; they are not user-facing features but constrain how features are built.

**The agents are mode-agnostic.** Agent instructions must not contain references to "the chat sidebar" or "the terminal" or anything that ties them to a UI. Whether running in CLI or in Obsidian, an agent should behave identically. This is what makes the dual-mode promise meaningful rather than nominal.

**Rejection is a first-class outcome.** Agents must be written to assume any proposed write may be rejected with feedback, in both modes — even though CLI mode never actually rejects. This prevents prompt drift between modes.

**The vault is the source of truth.** No staging directories, no shadow folders. The vault holds the canonical content; pending proposals exist only as in-memory state in the plugin until accepted.

**The plugin owns vault writes.** When running in bridge mode, the bridge does not write to disk directly. All writes go through the plugin's Obsidian vault API so the editor's in-memory state and the disk stay consistent.

**Statelessness across turns.** The bridge process holds no state that needs to survive a restart. The plugin is the source of truth for conversation history; the vault is the source of truth for content. A bridge crash should be recoverable by restarting the process.

**Trust through transparency, not sandboxing.** The agency is Python code that runs on the user's machine. We do not attempt to sandbox it. Instead, we make it explicit that installing an agency is equivalent to installing a Python package, and we surface what the agency contains before first run.

## 5. Non-goals

To be explicit about what is *not* in scope:

- **Cloud-hosted version.** No SaaS, no hosted instance, no team collaboration server.
- **Real-time multi-user editing.** One educator per vault. Conflicts between multiple educators editing the same vault simultaneously are out of scope.
- **Replacing the educator's judgement.** The system is a co-author, not an autonomous publisher. Nothing ships without explicit acceptance.
- **General-purpose Obsidian AI assistant.** Tools like Smart Composer already do this well. This project is specifically for the multimedia co-creation workflow.
- **Code editing.** The agency is for prose, slide content, and multimedia artifacts. Code agents (Claude Code, Cursor) are out of scope and complementary.
- **Sandboxing the agency.** The agency runs as a normal Python process with full disk and network access. Users opt in by installing.
- **Windows as Tier 1.** Windows is Tier 2 — best effort, tested when possible, but macOS and Linux are the primary platforms.

## 6. Constraints and assumptions

- The educator has an LLM API key (Anthropic or OpenAI) and is comfortable configuring it.
- The educator has Python 3.11+ on their system, or is willing to install it via `uv`.
- The educator's vault is on a local filesystem (not synced through a real-time collaboration backend).
- The Obsidian community plugin submission process is the primary distribution path for the plugin.
- The `uv` tooling is the primary distribution path for the Python package.

## 7. Success criteria

A v1.0 release is successful if:

1. A working educator can install the Obsidian plugin from the community plugins directory, configure an API key, and produce their first multimedia artifact (slides or document) from a markdown lesson within 15 minutes.
2. A developer can clone the repository, install via `uv`, and run the CLI against a sample vault within 5 minutes.
3. A developer can add a new multimedia tool (e.g. a custom export format) by writing a Python module, register it in their vault's agency configuration, and have agents use it — without modifying the core package.
4. A user can review the git log and clearly identify which content was authored by which agent versus by themselves.
5. The trust model is documented prominently enough that no user is surprised by what the agency can do on their machine.

## 8. Open product questions

These are deferred decisions worth flagging:

- **Onboarding flow for non-developer educators.** The current plan assumes users can configure API keys via plugin settings, but the experience of getting a first conversation working may need more scaffolding.
- **Agency sharing mechanism.** §3.5 enables sharing, but the actual UX (export agency, import agency, browse community agencies) is undefined.
- **Multi-turn cost visibility.** Educators using premium LLM models will care about cost. Whether the plugin surfaces a running cost estimate is undecided.
- **Failure recovery UX.** When an agent fails mid-multimedia-export (e.g. HeyGen API rate-limited), the recovery experience for non-technical users is undefined.
