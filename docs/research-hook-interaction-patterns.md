# Research: Hook User Interaction Patterns in Claude Code

## Context

During MVP demos, the QA loop UI (Gradio) caused significant overhead and failures because a **fresh Gradio app is launched for every single question**. This research examines how user interactivity actually works during Claude Code hook execution and what alternatives exist.

## Critical Constraints on Hook User Interaction

### 1. stdin/stdout are occupied by hook protocol

Claude Code hooks communicate with the host via **redirected stdin/stdout**:
- **stdin** receives JSON describing the hook event (tool name, input, etc.)
- **stdout** is parsed for JSON hook responses (allow/block decisions)
- **stderr** is shown to the user as feedback

A hook script **cannot** use `input()` or read from stdin — that channel is occupied.

### 2. `/dev/tty` is NOT viable — empirically tested and confirmed broken

Claude Code is a **TUI application** (Ink/React-based) that owns the controlling terminal. Empirical testing with a simple hook script:

```bash
#!/bin/bash
echo "Hello from /dev/tty test" > /dev/tty
read -p "Enter something: " input < /dev/tty
```

**Results:**
- `echo > /dev/tty` — output does not appear in the TUI (or corrupts its rendering)
- `read < /dev/tty` — **I/O error**, then **soft-locks the TUI** by blocking input
- The Claude Code session becomes unresponsive and the UI layout breaks

**Why:** Unlike traditional Unix programs (ssh, sudo, gpg) that use a plain terminal, Claude Code renders a full-screen interactive UI via `/dev/tty`. The hook subprocess shares the same controlling terminal, so:
- Writes to `/dev/tty` collide with the TUI's rendering
- Reads from `/dev/tty` compete with the TUI's input loop
- This is fundamentally incompatible — not a bug, just a constraint of TUI-based hosts

**Conclusion:** The `TerminalQARenderer` in `qa/terminal_renderer.py` is **broken for real hook usage**. It can only work in test environments or standalone CLI invocations where no TUI owns the terminal.

### 3. Summary of blocked channels

| Channel | Status | Why |
|---------|--------|-----|
| stdin | Blocked | Hook protocol JSON input |
| stdout | Blocked | Hook protocol JSON output |
| /dev/tty write | Broken | Corrupts Claude Code TUI |
| /dev/tty read | Broken | I/O error, soft-locks TUI |
| stderr | Partial | Can display text but cannot collect input |

**The hook has NO direct path to interactive user input.** All user interaction must go through an out-of-band mechanism.

## The Statefulness Problem

### Why Gradio launches every time — it's not just a code bug

The deeper issue is the **process lifecycle model** of the hook:

```
Claude Code triggers PreToolUse
  → spawns fresh `vibecheck-hook` process
    → reads stdin JSON, loads files from disk, runs gate logic
    → if blocked: launches Gradio, waits for answer, tears down Gradio
    → writes files to disk, emits JSON to stdout
  → process exits
```

Each hook invocation is a **completely ephemeral process**. There is:
- No long-running daemon or background service
- No in-memory state carried between invocations
- No process management (PID files, sockets, health checks)
- No IPC mechanism between hook invocations

All persistence is **file-backed**:
- `state/competence_model.yaml` — loaded fresh, saved back each time
- `state/logs/events.jsonl` — append-only log
- `state/agg/current_attempt.md` — overwritten each invocation
- `state/qa/pending/` and `state/qa/results/` — per-proposal YAML artifacts

This stateless-process + file-persistence design is **correct for the gate logic and competence tracking**, but it creates a fundamental mismatch with Gradio: you can't keep a server alive across invocations when the process dies after each one.

### The architectural gap

The current architecture has two kinds of work that need different lifecycle models:

| Concern | Needs | Current lifecycle |
|---------|-------|-------------------|
| Gate evaluation | Stateless, fast, per-invocation | Correct (ephemeral process + file state) |
| QA user interaction | Persistent UI, low-latency, session-scoped | Wrong (new Gradio per question) |

The fix requires **separating the UI lifecycle from the hook process lifecycle** — the UI needs to outlive individual hook invocations.

## Current Implementation Problems

### Gradio Renderer (`qa/gradio_renderer.py`)
- Launches a **new Gradio `gr.Blocks()` app per question**
- Starts an HTTP server, opens a browser, waits for form submission via `queue.Queue`
- 9-minute timeout, then tears down the app
- **This is the source of the demo overhead and fragility** — each question pays: Gradio import, server bind, port allocation, browser launch

### Terminal Renderer (`qa/terminal_renderer.py`)
- Uses `/dev/tty` — **does not work** inside Claude Code hooks (see above)
- Only usable in tests or standalone CLI mode

### Renderer Selection (`qa/renderer_selection.py`)
- Hardcoded to Gradio only — raises `RuntimeError` if Gradio isn't installed
- Terminal renderer exists but is never selected; broken anyway for real use

## Viable Approaches for QA Interaction

Since all terminal channels are blocked, the hook **must use an out-of-band mechanism**. The options are:

### Option A: Persistent Gradio sidecar (Recommended short-term)

- Start Gradio **once** (at `SessionStart` hook or on first gate trigger) as a **detached background process**
- Each hook invocation communicates with the running sidecar via HTTP or file-based IPC
- Sidecar updates the existing UI with each new question — no restart
- Eliminates per-question startup cost (the main pain point)

**Sidecar lifecycle management:**
- First hook invocation that needs QA checks for a running sidecar (PID file + health endpoint)
- If not running, spawns it with `subprocess.Popen` + `setsid`/`nohup` (detached from hook process)
- Sidecar writes PID file and listens on a known port (e.g., `127.0.0.1:7865`)
- Hook pushes question JSON via HTTP POST, polls for answer via HTTP GET
- Sidecar auto-exits after idle timeout (e.g., 30 minutes) or on explicit shutdown signal
- `SessionStart` hook can optionally pre-warm the sidecar

**Communication protocol (simple HTTP):**
```
Hook → POST /question  {question, attempt, packet}
       → 200 OK (question queued)

Hook → GET  /answer
       → 200 {answer: "..."} when user submits
       → 204 No Content while waiting (hook polls)
```

**Complexity:** PID file management, port reservation, health checks, graceful shutdown. Well-bounded and standard patterns.

### Option B: Lightweight SPA + HTTP server (Future direction)

- A minimal Python HTTP server serving a single-page app (vanilla JS or lightweight framework)
- Same persistent sidecar pattern as Option A, but without Gradio's weight
- Faster startup, fewer dependencies, more control over UX
- The QA form is simple enough that a custom SPA is practical
- **Keep this door open** — the sidecar communication protocol (HTTP POST/GET) is the same regardless of whether the server is Gradio or custom

### Option C: File-based polling

- Hook writes question to a known file (e.g., `state/qa/interactive/pending.json`)
- User answers via a watcher script, editor, or companion CLI
- Hook polls for a response file to appear
- Extremely simple, no server needed
- Poor UX — requires user to know where to write answers
- Could be paired with a desktop notification

### Option D: Claude Code's own permission flow

- Hook returns exit code 2 (block) with a descriptive stderr message
- Claude Code shows the block reason in its UI — user sees the question
- User responds by typing in Claude Code's own input, which triggers the next hook cycle
- Leverages Claude Code's built-in permission/feedback mechanism
- **Limitation**: doesn't support structured QA format (attempts, question types, scoring)
- May be viable for simple approve/deny gates but not for the full QA loop

## Recommendation

**Phase 1 — Persistent Gradio sidecar (Option A):**
- Gradio is already a dependency and the UI is already built
- The only real problem was per-question startup overhead — persistence solves that
- Keeps the rich code editor for `faded_example` questions
- Clear architectural boundary: sidecar manages UI lifecycle, hook manages gate/QA logic
- The HTTP communication protocol is transport-agnostic — swapping Gradio for a custom SPA later requires no hook-side changes

**Phase 2 — Lightweight SPA (Option B):**
- Once the sidecar pattern is proven, replace Gradio with a snappy custom SPA
- Minimize barrier to entry: fast load, minimal UI, gets out of your way
- The sidecar lifecycle and HTTP protocol carry over unchanged

**Option D** is worth exploring as a lightweight complement for simple gate decisions that don't need the full QA loop.

## Files to Modify (if implementing Option A)

- `qa/gradio_renderer.py` — Refactor from per-question launch to persistent server with question push/answer pull
- `qa/renderer_selection.py` — Add sidecar lifecycle management (start-if-not-running, health check)
- `hooks/pre_tool_use.py` — Potentially add `SessionStart` hook for sidecar warmup
- `qa/terminal_renderer.py` — Mark as test-only; document that it does not work in Claude Code hooks
- New: `qa/sidecar.py` — Sidecar process management (PID file, spawn, health check, shutdown)
