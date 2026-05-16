# ARCHITECTURE — Educator Agency

> Detailed technical architecture for the dual-mode multi-agent system described in [PRD.md](./PRD.md). This document is intended for contributors and integrators. For the work plan, see [PLAN.md](./PLAN.md).

## 1. Overview

Educator Agency is a Python package that hosts a multi-agent system (built on [Agency Swarm](https://github.com/VRSEN/agency-swarm)) for co-creating educational content. The same package ships in two runtime modes:

- **Bridge mode** — runs as a WebSocket server (`educator-agency-bridge`) spawned by an Obsidian plugin. The plugin is the user interface; the bridge mediates between the plugin and the agency runtime. All file writes are gated by user approval through a diff view.
- **CLI mode** — runs as a terminal application (`educator-agency`) with direct disk access. Used by developers and researchers. No approval gate; the user trusts the agency directly.

The two modes share the same agency definitions, agent instructions, tools, and runtime. The only difference is which `FileOpsBackend` implementation is instantiated and which user-facing layer is on top.

## 2. System topology

```
                ┌──────────────────────┐    ┌──────────────────────┐
                │   Obsidian plugin    │    │   Terminal / TUI     │
                │   (TypeScript)       │    │                      │
                └──────────┬───────────┘    └──────────┬───────────┘
                           │                           │
                  WebSocket│                           │stdio
                           │                           │
                ┌──────────▼───────────┐    ┌──────────▼───────────┐
                │   bridge server      │    │   CLI runner         │
                │   (Python)           │    │   (Python)           │
                │                      │    │                      │
                │   ObsidianBridge-    │    │   LocalFsBackend     │
                │   Backend            │    │                      │
                └──────────┬───────────┘    └──────────┬───────────┘
                           │                           │
                           └─────────────┬─────────────┘
                                         │
                              ┌──────────▼───────────┐
                              │   AgencyRunner       │
                              │   (shared)           │
                              └──────────┬───────────┘
                                         │
                              ┌──────────▼───────────┐
                              │   FileOpsBackend     │
                              │   (protocol)         │
                              └──────────┬───────────┘
                                         │
                              ┌──────────▼───────────┐
                              │   Agency Swarm       │
                              │   (agents, tools)    │
                              └──────────┬───────────┘
                                         │
                              ┌──────────▼───────────┐
                              │   Vault (filesystem) │
                              └──────────────────────┘
```

In bridge mode, writes do not flow directly from the backend to the filesystem — they route back up through the plugin (see §6.5, "write inversion").

## 3. Components

### 3.1 Obsidian plugin

The plugin is the user interface for non-developer educators. It is a TypeScript Obsidian community plugin that bundles three concerns:

- **Sidecar lifecycle.** Spawns the `educator-agency-bridge` Python process at plugin load, parses the assigned port from stdout, monitors health via WebSocket ping/pong, restarts with exponential backoff on crash, and kills the process on plugin unload. Surfaces stderr to a plugin log file in `vault/.obsidian/plugins/agency/server.log` and provides a "Show server logs" command.
- **Chat interface.** A right-panel `ItemView` rendering the conversation: user messages, streamed assistant tokens, tool-call narration cards, and proposal cards. Proposal cards link to the diff view.
- **Diff view.** A main-area `ItemView` hosting a CodeMirror 6 `MergeView` (side-by-side, with `@codemirror/lang-markdown` and GFM) for reviewing proposed changes. Per-chunk accept/reject controls; the right side is editable so the educator can amend a proposal before accepting.
- **Vault writes.** Executes accepted writes via Obsidian's `vault.modify` / `vault.create` API. The plugin — not the bridge — is the only thing that mutates the vault in bridge mode.
- **Git integration.** Auto-commits accepted proposals with structured metadata (see §9).

The plugin owns the conversation history as authoritative state. The bridge is treated as stateless; conversation history is replayed on reconnect.

### 3.2 Bridge server (`educator-agency-bridge`)

A Python WebSocket server spawned by the plugin. Its responsibilities:

- **Process lifecycle.** Binds to `--port 0` (OS-assigned), prints the port to stdout, handles SIGTERM gracefully (drain in-flight turn, close WS, exit). Fail-fast on internal errors; restart is the plugin's responsibility.
- **WebSocket transport.** Single-client connection. JSON-over-WS with versioned handshake. Detailed in §5.
- **Translation between WS and runtime.** Receives `user_message`, invokes `AgencyRunner.run_turn`, streams runtime events back as WS messages, awaits `decision` messages for proposal sets and resolves Futures.
- **ObsidianBridgeBackend instance.** The backend implementation that buffers proposals during a turn and routes them through the WS layer for approval.

The bridge holds two kinds of in-flight state: connection state (handshake, version, client present) and per-turn state (proposal buffer, pending Futures). It holds *no* state that survives a turn — conversation history is sent by the plugin on each invocation.

### 3.3 CLI runner (`educator-agency`)

A Python terminal application for headless or developer use. Its responsibilities:

- Reads user input from stdin (or via a TUI library — Textual or a fork of AgentSwarm CLI).
- Invokes the same `AgencyRunner` with a `LocalFsBackend`.
- Renders streaming tokens, tool-call narration, and file operation summaries to the terminal.
- Persists conversation history to `.agency/sessions/{id}.jsonl` for resume.

The CLI runner is materially simpler than the bridge: no protocol, no second process, no approval flow. It is essentially `AgencyRunner` driven by a terminal.

### 3.4 AgencyRunner (shared orchestration)

The layer below both modes that owns turn-level orchestration. Constructed with a `FileOpsBackend`, an agency definition, and conversation history.

- Exposes `run_turn(text) -> AsyncIterator[Event]` (or callback equivalent).
- Events emitted: `Token`, `ToolCallStarted`, `ToolCallCompleted`, `ProposalRequested`, `TurnComplete`, `Error`.
- The bridge translates these events to WS messages; the CLI translates them to terminal output.

This layer is what keeps the agency itself I/O-naïve. Without it, mode-specific concerns would leak into agent code.

### 3.5 FileOpsBackend (shared abstraction)

A Python `Protocol` defining the universe of vault operations available to agents. Two implementations:

- `LocalFsBackend` — direct disk I/O, used in CLI mode. Writes always succeed (or fail with an OS error).
- `ObsidianBridgeBackend` — buffers proposals, routes through WS to plugin for approval, returns the decision.

Detailed in §4.

### 3.6 Agency Swarm runtime

The third-party multi-agent framework that wires up agents, tools, and LLM calls. The project's agents and tools are defined as user code consumed by this framework; we do not modify Agency Swarm itself.

Tools that touch files do so via the `FileOpsBackend` passed in at agency construction. Tools that produce multimedia artifacts call external Python libraries (`python-pptx`, `python-docx`, `weasyprint`, `pandoc`) or external APIs (HeyGen, ElevenLabs, etc.).

### 3.7 Agency definition (in-vault)

The agency lives in `vault/agency/` and contains:

- Python files defining `Agent` instances, their instructions, and their tool wiring
- An `agency.py` entry that constructs the `Agency` topology
- A `tools/` directory for any user-added tools
- An `instructions/` directory for shared instruction snippets

The default agency is scaffolded by `educator-agency init`; user customisations live in the vault and persist alongside content. The agency directory is excluded from Obsidian's search index. Hot-reload happens only between agent turns, never mid-turn.

## 4. The FileOpsBackend contract

The backend is the single point through which agent tools touch the vault. The interface is small, async, and intentionally ignorant of "approval flow" as a concept.

### 4.1 Operations

Reads — never blocked, return data directly:

```python
async def read_file(path: VaultPath) -> str
async def list_files(directory: VaultPath, glob: str | None = None) -> list[VaultPath]
async def search_files(query: str, scope: VaultPath | None = None) -> list[Match]
async def stat(path: VaultPath) -> FileMeta
```

Writes — return a `WriteOutcome`:

```python
async def write_file(path: VaultPath, content: str, mode: Literal["create", "overwrite"], meta: ProposalMeta) -> WriteOutcome
async def delete_file(path: VaultPath, meta: ProposalMeta) -> WriteOutcome
async def rename_file(from_path: VaultPath, to_path: VaultPath, meta: ProposalMeta) -> WriteOutcome
```

A `patch_file(path, edits)` operation sits one layer up at the agent-tool level as sugar on `write_file`. The tool computes the new content from search/replace edits, then calls the backend. The patch primitive is preserved at the tool layer because LLMs express edits better than full rewrites, but the backend stays primitive.

### 4.2 WriteOutcome

Both backends return the same shape:

```python
WriteOutcome = Accepted | Rejected | Failed

class Accepted:
    path: VaultPath

class Rejected:
    reason: str  # human-written feedback the agent can reason about

class Failed:
    error: str   # OS error, write error, or other technical failure
```

`LocalFsBackend` always returns `Accepted` (or `Failed` on disk error). `ObsidianBridgeBackend` can return any of the three.

### 4.3 Naming decision: `write_file`, not `propose_write`

`propose_*` would make the rejection model visible at every call site, but it pays a conceptual tax in CLI mode where there's no proposal — it's just a write. The rejection model is encoded in the return type, not the method name. Agent instructions are written assuming any write may be rejected with feedback, regardless of mode. This prevents prompt drift: a prompt written for CLI mode that assumes "writes always succeed" would silently break when swapped to bridge mode.

### 4.4 What the backend deliberately does not define

- **Buffering and batching.** Proposal batching at turn boundary is internal to `ObsidianBridgeBackend`, not part of the interface.
- **Git commits.** Writing a file is not committing it. Commits happen above the backend.
- **Path validation.** The backend assumes valid vault-relative paths; validation is at tool-input time.
- **Conflict resolution.** Dirty-buffer detection lives in the bridge, not the backend.
- **Diff computation.** The backend submits full content; the plugin computes the diff from old + new.

The interface staying this small is what makes the dual-mode property hold.

### 4.5 Why async

`write_file` in bridge mode can wait many seconds for a user decision. Sync blocking would freeze the agency runtime. Agency Swarm is async-native, so the cost is paid once and never again.

## 5. The WebSocket protocol

The wire contract between the Obsidian plugin and the bridge server. This is the most carefully versioned part of the system because it crosses a process and language boundary.

### 5.1 Connection lifecycle

```
plugin                                  bridge
  │   spawn subprocess                    │
  │ ─────────────────────────────────────►│
  │   "port=NNNNN" on stdout              │
  │ ◄─────────────────────────────────────│
  │   WS upgrade                          │
  │ ─────────────────────────────────────►│
  │   handshake { protocol_version }      │
  │ ─────────────────────────────────────►│
  │   handshake_ack { agreed_version }    │
  │ ◄─────────────────────────────────────│
  │   ... session ...                     │
  │   shutdown (or SIGTERM)               │
  │ ─────────────────────────────────────►│
  │   close                               │
  │ ◄─────────────────────────────────────│
```

Version negotiation: incompatible major versions close the connection with a structured error. The plugin surfaces a "bridge version mismatch — please upgrade" message.

Health: ping/pong every 30s. Three missed pongs trigger restart by the plugin.

### 5.2 Message categories

**Conversation messages.** Carry the substance of a turn.

- `user_message` (plugin → bridge): user input plus the conversation history. Bridge is stateless across turns.
- `assistant_token` (bridge → plugin): incremental token from the active agent.
- `tool_call_started` (bridge → plugin): an agent is about to invoke a tool. Includes tool name, arguments, and the agent that issued it. Used for narration in the chat UI.
- `tool_call_completed` (bridge → plugin): tool finished. Includes a short result summary (not the full content).
- `assistant_done` (bridge → plugin): the current agent's turn portion is complete.
- `agent_handoff` (bridge → plugin): control passed to a different agent. Used for narration.

**Proposal messages.** Carry the diff-gated approval flow.

- `proposal_set` (bridge → plugin): one or more proposed file operations, batched at turn boundary. Each proposal carries the file path, the operation kind (create/overwrite/delete/rename), the proposed content (for create/overwrite), the original content (for diff display), and metadata (issuing agent, turn ID, optional human-readable summary).
- `decision` (plugin → bridge): the user's response to a `proposal_set`. Per-proposal: accepted, rejected with optional feedback, or accepted-with-edits (the user modified the content before accepting). For accepted proposals, includes a `write_status` field indicating whether the plugin's actual disk write succeeded.

**Control messages.** Lifecycle and errors.

- `handshake` / `handshake_ack`: version negotiation.
- `ping` / `pong`: liveness check.
- `error`: structured error from the bridge (uncaught exception, agency failure, etc.) — the plugin renders this distinctly from agent output.
- `shutdown` (plugin → bridge): graceful shutdown request.

### 5.3 Schema versioning

The protocol document (`protocol.md` in the repo) is the canonical reference. Both the Python bridge and the TypeScript plugin implement against it. Schemas are versioned with semantic versioning at the protocol level — backward-compatible additions bump the minor version; breaking changes bump the major.

The bridge advertises supported major versions on handshake; the plugin selects the highest mutually supported version. Mismatch is a hard error with a user-facing upgrade prompt.

### 5.4 Per-turn flow

The non-trivial sequence — what happens during a single user turn:

```
1. plugin → bridge:  user_message(text, history)
2. bridge:           runner.run_turn(text, history) — starts iterating events
3. bridge → plugin:  agent_handoff(CoordinatorAgent)
4. bridge → plugin:  assistant_token, assistant_token, ...
5. bridge → plugin:  tool_call_started(read_file, args)
6. bridge → plugin:  tool_call_completed(read_file, summary)
7.                   [agent calls write_file via backend]
                     [backend buffers the proposal, awaits Future]
8. bridge → plugin:  assistant_done
9. bridge → plugin:  proposal_set([proposal_1, proposal_2])
10. plugin:          user reviews diff, clicks accept on both
11. plugin → bridge: decision({proposal_1: accepted, proposal_2: accepted_with_edits(...)})
12. plugin:          executes vault.modify for each accepted
13. plugin → bridge: writes complete, included in decision payload
14. bridge:          resolves Futures, backend returns Accepted to agent
15. bridge:          [if rejection occurred] agent reasons about feedback, may try again
16. bridge → plugin: turn_complete
```

Steps 9–14 are the architectural pivot. The agent is suspended on `await backend.write_file(...)` from step 7 until step 14 — and the suspend is across a network boundary, a UI interaction, and a disk write that the bridge itself does not perform.

### 5.5 Write inversion (key property)

`ObsidianBridgeBackend.write_file` does not write to the vault. The actual write is performed by the plugin via `vault.modify` / `vault.create`. This is because Obsidian's editor maintains its own in-memory state for open files; direct disk writes from the bridge would leave the editor stale until the next file scan.

The cost of this inversion: disk-write failures are reported back over WS as part of the `decision` message rather than thrown locally. The error path is longer. Mitigation: the `decision` message carries a required `write_status` field, and the bridge surfaces "we tried, OS said no" (`Failed`) distinctly from "you said no" (`Rejected`) — the agent reasons differently about each.

## 6. The stdio path (CLI mode)

In CLI mode there is no protocol and no second process. The CLI runner is a single process that:

1. Reads input from stdin (or a TUI library managing input/output).
2. Calls `AgencyRunner.run_turn(text, history)`.
3. Consumes the `AsyncIterator[Event]` and renders to the terminal:
   - `Token` → write to stdout incrementally
   - `ToolCallStarted` → print a narration line ("Reading lesson_3.md...")
   - `ProposalRequested` → in default CLI mode, auto-accept and proceed; in `--require-approval` mode, prompt the user
   - `TurnComplete` → flush, persist turn to session file, return to prompt
4. Writes the turn to `.agency/sessions/{id}.jsonl` for resume.

The `LocalFsBackend` writes directly to disk on every `write_file` call. No buffering, no Futures awaiting external decisions.

The CLI's approval-by-default-off is intentional: developers using the CLI are trusting the agency to do what it claims, the same way they would trust any Python package they've installed.

## 7. State ownership

A property-by-property map of who owns what:

| State | Owner | Notes |
|---|---|---|
| Conversation history (bridge mode) | Plugin | Replayed to bridge on each turn |
| Conversation history (CLI mode) | CLI runner | Persisted to `.agency/sessions/{id}.jsonl` |
| Vault content | Filesystem | The single source of truth |
| Pending proposals | Bridge (in-flight) → Plugin (post-emit) | Bridge buffers during a turn, emits at boundary, then plugin holds them until decision |
| Agent state mid-turn | Agency Swarm runtime | Ephemeral, not persisted |
| Agency definition | Vault (`agency/`) | Editable, version-controlled with vault |
| API keys, settings | Plugin settings + `vault/.agent/.env` | Precedence documented in distribution docs |
| WS connection state | Bridge (ephemeral) | Re-established on reconnect |
| Git history | Vault (`.git/`) | Managed by plugin in bridge mode, by user in CLI mode |

The statelessness of the bridge across turns is what allows the plugin-restarts-bridge supervisor pattern to be safe: nothing valuable is lost.

## 8. Distribution

Three artifacts ship separately:

**The Python package.** Published to PyPI as `educator-agency`. Two entry points in `pyproject.toml`:

```toml
[project.scripts]
educator-agency = "educator_agency.cli:main"
educator-agency-bridge = "educator_agency.bridge:main"
```

Multimedia tools are optional extras: `educator-agency[multimedia-local]`, `[multimedia-heygen]`, etc. The base install has no native dependencies; users opt in to heavier deps per their needs.

Primary install path: `uv tool install educator-agency`. Documented secondary paths: `pipx`, raw `pip`.

**The Obsidian plugin.** Submitted to the Obsidian community plugin registry. Installed through Obsidian's plugin browser. On first run, the plugin checks for the Python package and prompts the user to install if missing.

**Example agencies.** Published as a separate `educator-agencies-examples` repository. Users can `degit` or clone these into their vault as starting points.

API keys can be configured via plugin settings, environment variables, or `vault/.agent/.env`. Precedence: plugin settings > env > .env file. The macOS GUI environment-variable inheritance limitation is addressed by spawning the bridge through a login shell so PATH and env vars are populated correctly.

## 9. Git integration

In bridge mode, the plugin auto-commits accepted proposals with structured provenance.

**Commit format.**
```
[agent:CoordinatorAgent] Drafted introduction to lesson 3

Proposal context: turn-2025-05-15T14:32:01Z
Files: content/lessons/lesson_3.md
```

**Identity.** Commit author is set to the agent name (e.g. `CoordinatorAgent <agent@educator-agency.local>`). Committer is the user's configured git identity. This lets `git log --author=` and `git blame` distinguish agent-authored from human-authored content.

**Pre-commit safety.** If the working tree has uncommitted manual edits, the plugin commits them first with a `[manual]` prefix before committing the agent's change. This keeps history linear and ensures the agent's commit is cleanly attributable to a single proposal.

**Revert.** A "Revert last agent change" command in the Obsidian command palette runs `git revert HEAD --no-edit` when HEAD is an agent commit.

**Non-git vaults.** If the vault is not a git repository, git integration is silently disabled and a warning is surfaced in the plugin settings. The user opts in by initialising git themselves.

**CLI mode.** Git is the user's responsibility. The CLI does not auto-commit. This is intentional: developers using the CLI typically integrate with their own workflows.

## 10. Trust model

We do not sandbox the agency. It is Python code that runs with the user's full privileges. The trust model is:

- Installing an agency is equivalent to installing any Python package — users opt in.
- On first load of a new or modified agency, the plugin computes a hash and prompts the user to confirm: "This agency has changed. Continue?"
- The trust model is documented prominently in `INSTALL.md` and surfaced in plugin onboarding.
- We do not run user agencies from untrusted sources without warning.

The decision against sandboxing is deliberate: sandboxing Python that needs to call arbitrary multimedia conversion libraries and external APIs is hard, brittle, and would prevent the very thing the project is for. Transparency beats security theatre.

## 11. Accepted tradeoffs

The architecture explicitly accepts these tradeoffs:

**Async everywhere vs sync simplicity.** Async wins because bridge-mode writes need to wait on user decisions across a network boundary. Cost: every tool and runtime path is async, tests are harder, async stack traces are noisier.

**Plugin-writes vs bridge-writes.** Plugin-writes wins because Obsidian's editor state must be authoritative. Cost: longer error path for disk-write failures (routed back over WS rather than thrown locally).

**Stateless bridge vs persistent bridge.** Stateless wins because crash recovery is simpler when the supervisor can just restart the process. Cost: plugin must re-send conversation history on every turn, and the protocol is verbose.

**Side-by-side diff (CM6 `MergeView`) vs inline diff (`unifiedMergeView`).** Side-by-side wins for v1 because the lifecycle is simpler and behaviour at prose scale is more predictable. Cost: takes a dedicated main-area pane rather than overlaying the original document.

**No staging directory vs shadow folder.** No staging wins because the vault stays the single source of truth. Cost: proposals exist only in plugin memory until accepted; a plugin crash mid-proposal loses pending work (mitigated by treating proposals as cheap to regenerate).

**Mode-agnostic agents always assuming rejection vs mode-aware agents.** Mode-agnostic wins to prevent prompt drift. Cost: CLI-mode agents include rejection-handling logic that never fires — slight prompt-length overhead, conceptual mismatch.

**Trust-by-transparency vs sandboxing.** Transparency wins because real sandboxing of a multimedia-producing Python agency is infeasible. Cost: the trust model has to be documented and surfaced clearly, and is fundamentally weaker than a sandbox would be.

## 12. Open architectural questions

These decisions are deferred:

- **AgencyRunner event model exact shape.** The events listed in §3.4 are a sketch; the precise typing, ordering guarantees, and cancellation semantics are not yet specified.
- **Concurrent writes to the same file within a turn.** The current design implies a write lock at plugin level, but the exact mechanism (per-file mutex? rejection of the second write? merge?) is undecided.
- **Conflict detection at write time.** If the on-disk content has changed since the agent read it, the proposal is potentially based on stale information. The plan is to include the read-content hash in `ProposalMeta` and let the plugin reject if stale, but this is not yet specified in detail.
- **Multi-window Obsidian.** What happens if the user has the same vault open in two Obsidian windows simultaneously? Current assumption: single-client connection, second window's plugin gets a connection error.
- **Cost / token usage surfacing.** Whether and how to surface LLM token usage to the user. Probably a v2 concern.
- **Plugin and bridge version compatibility matrix.** The version handshake is specified but the actual compatibility policy (how far back does the bridge support old plugin versions?) is undecided.
