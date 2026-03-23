# Research: Hook User Interaction Patterns in Claude Code

## Context

During MVP demos, the QA loop UI (Gradio) caused significant overhead and failures because a **fresh Gradio app is launched for every single question**. This research examines how user interactivity is *supposed* to work during Claude Code hook execution and what alternatives exist.

## Key Finding: Hooks Cannot Use stdin/stdout for User Interaction

Claude Code hooks communicate with the host via **redirected stdin/stdout**:
- **stdin** receives JSON describing the hook event (tool name, input, etc.)
- **stdout** is parsed for JSON hook responses (allow/block decisions)
- **stderr** is shown to the user as feedback

This means a hook script **cannot** use `input()` or read from stdin to prompt the user — that channel is occupied by Claude Code's own protocol.

## How the Current Codebase Handles This

### Gradio Renderer (`qa/gradio_renderer.py`)
- Launches a **new Gradio `gr.Blocks()` app per question** (line 33: "launches a local Gradio app for each question")
- Starts an HTTP server, opens a browser, waits for form submission via `queue.Queue`
- 9-minute timeout, then tears down the app
- **This is the source of the overhead and fragility** — each question pays the full Gradio startup cost (import, server bind, browser launch)

### Terminal Renderer (`qa/terminal_renderer.py`)
- Uses **`/dev/tty` directly** to bypass the stdin/stdout redirection (line 67-76)
- Opens `/dev/tty` in read+write mode, writes the prompt, reads a line
- Falls back to `input()` if `/dev/tty` doesn't exist
- **This is the correct low-overhead pattern for terminal-based hook interaction**

### Renderer Selection (`qa/renderer_selection.py`)
- Currently **hardcoded to Gradio only** — raises `RuntimeError` if Gradio isn't installed
- The terminal renderer exists but is never selected in production; only used in tests

## The `/dev/tty` Pattern — Why It Works

When Claude Code runs a hook as a subprocess:
- stdin/stdout are pipes connected to Claude Code
- But `/dev/tty` is the **controlling terminal** of the process — it's the actual user's terminal
- Writing to `/dev/tty` shows text to the user regardless of stdout redirection
- Reading from `/dev/tty` gets keystrokes from the user regardless of stdin redirection

This is a well-established Unix pattern used by programs like `ssh` (password prompts), `sudo`, and `gpg` — all of which need user input even when their stdin is piped.

## Viable Approaches for the QA Interaction

### Option A: Terminal-first with `/dev/tty` (Recommended for MVP)
- **Already implemented** in `terminal_renderer.py`
- Zero startup overhead, no external dependencies
- Works reliably in any terminal environment
- Limitation: single-line input only (no code editor), no rich formatting
- Could be enhanced with multi-line input mode for `faded_example` questions

### Option B: Persistent Gradio server (long-running sidecar)
- Start Gradio **once** (e.g., at `SessionStart` hook or first gate trigger) and keep it running
- Each question updates the UI via Gradio's reactive state, no restart needed
- Eliminates per-question startup cost
- Adds complexity: lifecycle management, port management, server health checks

### Option C: Hybrid — terminal for simple questions, Gradio for code questions
- Use terminal renderer for `true_false` and `plain_english` (where a textbox suffices)
- Only launch Gradio for `faded_example` (where a code editor adds real value)
- Reduces Gradio launches to ~1/3 of questions
- Could combine with Option B for persistent Gradio when code questions arise

## Recommendation

**For immediate stability**: Update `renderer_selection.py` to default to `TerminalQARenderer` and use Gradio only as an opt-in enhancement. The terminal renderer already exists, works, and has zero overhead. This is a ~5-line change.

**For a better UX later**: Implement a persistent Gradio sidecar that starts once and stays running for the session, with the terminal as the automatic fallback.

## Files to Modify (if implementing)

- `qa/renderer_selection.py` — Change default renderer to terminal, add fallback logic
- `qa/terminal_renderer.py` — Potentially enhance multi-line input for faded_example
- `qa/gradio_renderer.py` — Optionally refactor to persistent server model later
