# UX Refactor Plan: Persistent Gradio Sidecar

**Created:** 2026-04-12T00:00:00Z

## Context

The current VibeCheck UX spawns a fresh Gradio app per question inside an ephemeral hook process. This is broken because:

1. Hook processes are stateless/ephemeral — they exit after each invocation
2. `/dev/tty` is confirmed broken for Claude Code's TUI (soft-locks the session)
3. Per-question Gradio launch is slow/fragile (import, server bind, port, browser open — every time)
4. Each question opens a new browser tab, leaving old pages sitting with no feedback

**Goal:** Replace per-question Gradio spawn with a persistent sidecar that outlives hook invocations, handles multiple questions in sequence, and communicates via HTTP.

---

## Architecture

```
Claude Code (PreToolUse hook, ephemeral process)
    │
    ▼
hooks/pre_tool_use.py
    │
    ▼
qa/loop.py ──► SidecarClient.ask()
                    │
                    │  HTTP POST /question {question, attempt, packet}
                    ▼
              ┌─────────────────────────────────┐
              │   Sidecar Server (persistent)   │
              │   - Gradio UI                    │
              │   - Question queue              │
              │   - Answer queue                │
              │   - Idle timeout shutdown       │
              └─────────────────────────────────┘
                    │
                    │  HTTP GET /answer → answer text
                    ▼
              SidecarClient.ask() returns answer
                    │
                    ▼
              qa/loop.py continues...
```

---

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| UI Approach | Persistent Gradio sidecar | Keep existing Gradio UI, make it persistent. Fastest path. |
| Lifecycle Management | PID file + health endpoint | Standard daemon pattern, reliable |
| Transport Protocol | Local-only HTTP | Simple, sufficient for MVP; remote support noted for later |
| Port Discovery | Fixed env var with fallback | VIBECHECK_SIDECAR_PORT default 7865; auto-select if busy; write to `state/qa/sidecar.port` |
| Old renderers | gradio_renderer becomes UI builder | Refactor to extract UI building functions called by sidecar server |
| Terminal renderer | Mark test-only | Confirmed non-viable for real hook use |

---

## Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `VIBECHECK_SIDECAR_PORT` | `7865` | Preferred port; auto-select if busy |
| `VIBECHECK_SIDECAR_TIMEOUT` | `540` | Poll timeout in seconds (9 min) |
| `VIBECHECK_SIDECAR_IDLE_TIMEOUT` | `1800` | Idle shutdown in seconds (30 min) |
| `VIBECHECK_SIDECAR_POLL_INTERVAL` | `0.5` | Poll interval in seconds |

---

## Module Design

### Directory Structure

```
qa/sidecar/
    __init__.py       # Exports: SidecarClient, SidecarServer, LifecycleManager
    server.py         # Persistent Gradio server with queue management
    client.py         # HTTP client: spawn, health, push question, poll answer
    lifecycle.py      # PID file ops, health check, idle timeout, port selection

qa/
    gradio_renderer.py   # REFACTOR: UI builder functions called by sidecar server
    terminal_renderer.py # DEPRECATE: test-only, non-viable for hooks
    renderer_selection.py # MODIFY: returns SidecarClient
    loop.py              # UNCHANGED: QARenderer protocol preserved via adapter
```

### Sidecar Server (`qa/sidecar/server.py`)

- Starts once, runs until idle timeout or explicit shutdown
- Manages question queue (FIFO) and answer queue
- Gradio UI: shows current question, attempt counter, submit button
- After submit: stores answer in answer queue, immediately shows next question if queued
- If queue empty: shows "Waiting for next question..." state
- Endpoints:
  - `GET /health` → 200 OK (for lifecycle health check)
  - `POST /shutdown` → shuts down gracefully
  - `POST /question` → queues question, returns 202 Accepted
  - `GET /answer` → returns answer if ready (200), else 204 No Content
  - `GET /status` → returns queue depth, current question info (for debugging)

### Sidecar Client (`qa/sidecar/client.py`)

```
class SidecarClient:
    ask(question: str, attempt: int, packet: QAPacket) -> str
    _ensure_running()     # Check PID + health, spawn if needed
    _push_question()      # POST /question
    _poll_answer()        # GET /answer with timeout
```

- `_ensure_running()`:
  1. Read PID from `state/qa/sidecar.pid`
  2. If process alive and `GET /health` → 200: done
  3. If not: unlink PID file, spawn new sidecar via `subprocess.Popen` + `setsid`
  4. Wait briefly, verify health, then proceed
- `_push_question()`: POST JSON `{question, attempt, question_type, context_excerpt}`
- `_poll_answer()`: poll `GET /answer` every 0.5s, timeout 9 minutes
- On timeout: return empty string, log event
- Idle tracking: track time since last question; send `POST /shutdown` if idle > configured timeout

### Lifecycle Manager (`qa/sidecar/lifecycle.py`)

```
class LifecycleManager:
    get_port()              # Read env var VIBECHECK_SIDECAR_PORT, fallback to file or default 7865
    find_available_port()   # Bind socket to port=0 to get OS-assigned port
    write_port_file(port)   # Write to state/qa/sidecar.port
    read_port_file()        # Read from state/qa/sidecar.port
    write_pid_file(pid)     # Write state/qa/sidecar.pid
    read_pid_file()         # Read state/qa/sidecar.pid
    is_process_alive(pid)   # os.kill(pid, 0) check
    check_health(port)      # GET /health, return bool
    spawn_sidecar(port)     # Popen sidecar server process, detached
    shutdown(port)          # POST /shutdown
```

### Port Discovery Logic

```
1. Check env var VIBECHECK_SIDECAR_PORT
   - If set and available: use it
   - If set and busy: log warning, fall back to auto-select
   - If not set: use default 7865
2. Try to bind to port
   - If available: write to state/qa/sidecar.port, proceed
   - If busy: call find_available_port(), write to file, proceed
3. Hook reads from env var first, then falls back to state/qa/sidecar.port
```

### Refactored `qa/gradio_renderer.py`

- No longer launches per-question Gradio app
- Exports functions that build Gradio UI components:
  - `build_qa_block(question, attempt, question_type)` → Gradio block
  - `create_submission_handlers()` → submit button handlers
- Called by sidecar server when initializing the persistent app
- State managed by server: current question, attempt count, passed/failed feedback

### Modified `qa/renderer_selection.py`

- `select_renderer()` returns `SidecarClient` instead of `GradioQARenderer`
- No more RuntimeError if Gradio unavailable (client handles graceful degradation)

---

## Event Log Additions

New events in `state/logs/events.jsonl`:

| Event | Fields | Description |
|-------|--------|-------------|
| `sidecar_spawned` | `port`, `pid` | Sidecar process started |
| `sidecar_health_check_ok` | `port` | Health check passed |
| `sidecar_question_pushed` | `proposal_id`, `attempt` | Question sent to sidecar |
| `sidecar_answer_received` | `proposal_id`, `answer_length` | Answer received from sidecar |
| `sidecar_idle_shutdown` | `pid`, `idle_seconds` | Shutdown due to inactivity |
| `sidecar_explicit_shutdown` | `pid` | Graceful shutdown requested |
| `sidecar_spawn_failed` | `error` | Failed to start sidecar |
| `sidecar_poll_timeout` | `proposal_id` | Timed out waiting for answer |

---

## Files Summary

| File | Action |
|------|--------|
| `qa/sidecar/__init__.py` | New — public exports |
| `qa/sidecar/server.py` | New — persistent Gradio server |
| `qa/sidecar/client.py` | New — HTTP client for hook side |
| `qa/sidecar/lifecycle.py` | New — PID, port, health, spawn |
| `qa/gradio_renderer.py` | Refactor — extract UI building into functions called by server |
| `qa/renderer_selection.py` | Modify — return SidecarClient; no RuntimeError if Gradio missing |
| `qa/terminal_renderer.py` | Deprecate — mark test-only, update docstring |
| `hooks/pre_tool_use.py` | Minor — SidecarClient handles lifecycle; add sidecar lifecycle events |
| `state/qa/sidecar.pid` | Add to .gitignore |
| `state/qa/sidecar.port` | Add to .gitignore |

---

## Testing Plan

1. **Unit: `lifecycle.py`** — mock PID file, port binding, health check
2. **Unit: `client.py`** — mock HTTP server, test spawn logic, poll timeout
3. **Integration: full sidecar flow** — spawn real sidecar, push question, get answer
4. **Existing tests** — `FakeRenderer` in test_qa_loop.py unchanged; loop tests still pass

---

## Out of Scope (LLM/Prompting Layer)

These are separate concerns to address after this refactor:

- Question simplification after failure (partial answer → hint → next attempt)
- Inline feedback explaining why answer was wrong before asking to retry
- Multi-turn refinement within a single question
- LLM prompt tuning for question generation and answer evaluation

---

## Open Questions for Later

1. Should `SessionStart` hook pre-warm the sidecar on Claude Code session start?
2. Should there be a CLI command to manually start/stop the sidecar?
3. Should we add `share=True` support for Gradio (remote browser access)?
4. When to swap Gradio for a lighter custom SPA?

---

## Reflection

**Completed:** 2026-04-12T00:30:00Z

### Implementation Summary

Successfully implemented the persistent Gradio sidecar architecture as specified. The core infrastructure is in place:

**Created:**
- `qa/sidecar/lifecycle.py` — PID file management, port selection, health checks, sidecar spawning
- `qa/sidecar/client.py` — HTTP client that pushes questions to sidecar and polls for answers
- `qa/sidecar/server.py` — Persistent Gradio server with question queue, answer queue, and HTTP endpoints
- `qa/sidecar/__init__.py` — Public exports

**Modified:**
- `qa/renderer_selection.py` — Now returns `SidecarClient` instead of `GradioQARenderer`
- `qa/terminal_renderer.py` — Deprecated with clear docstring explaining non-viability for hooks
- `tests/test_renderer_selection.py` — Updated tests to expect `SidecarClient`
- `.gitignore` — Added `state/qa/sidecar.pid` and `state/qa/sidecar.port`

**Note on gradio_renderer.py:**
The plan mentioned refactoring `gradio_renderer.py` to extract UI building functions. However, the server.py has its own inline Gradio UI building that works with the queue model. The old `gradio_renderer.py` is retained (imported in tests via `qa/__init__.py`) but the new sidecar architecture doesn't use it directly. This is a cleaner separation — the sidecar owns its own UI.

### Test Results
- 102 tests pass
- All lint checks pass
- All type checks pass

### Architecture Notes

The sidecar server runs as a detached background process:
1. First hook invocation that needs QA calls `ensure_sidecar_running()`
2. This spawns the server if not already running (PID file + health check)
3. Questions are pushed via `POST /question` and answers polled via `GET /answer`
4. Server auto-shuts down after idle timeout (configurable via `VIBECHECK_SIDECAR_IDLE_TIMEOUT`)

### Key Decisions Made During Implementation

1. **Port file is always written** — After finding an available port (either preferred or auto-selected), the port is written to `state/qa/sidecar.port` so subsequent hook invocations can find it even if they don't know the PID

2. **Health check on spawn** — After spawning, we poll `/health` up to 5 times with 0.25s delay to ensure the server is ready before returning

3. **No logger passed through select_renderer** — The QALoop creates the renderer via `select_renderer()` which doesn't receive the event_logger. SidecarClient accepts an event_logger but currently doesn't log through the hook's logger path. This could be improved later.

4. **Server uses global queues** — The `QUESTION_QUEUE` and `ANSWER_QUEUE` are module-level globals in `server.py`. This is simple but means the server can't serve multiple simultaneous hook invocations (though the architecture doesn't require this since hooks are sequential).

### Out of Scope (LLM/Prompting)

These remain unimplemented as planned:
- Question simplification after failure
- Inline feedback between attempts in the browser UI
- Multi-turn refinement within a question
- LLM prompt tuning

### Still Open / Deferred
- SessionStart hook pre-warming
- CLI commands for manual sidecar start/stop
- share=True Gradio support for remote access
- Custom lightweight SPA as Gradio alternative

### Additional Debrief: Gradio Stability and SPA Pivot

During runtime validation against Gradio 6.x and Claude Code hooks, we hit several concrete pain points:

1. **Route registration mismatch in Gradio 6**
   - Adding routes directly on `Blocks` did not survive launch in the expected way.
   - Health/status endpoints became unreliable when tied too tightly to `Blocks.launch()` internals.

2. **Lifecycle observability gap**
   - Sidecar startup failures were opaque until we added explicit stderr logging.
   - We now write sidecar stderr to `state/qa/sidecar.stderr.log` to diagnose failures quickly.

3. **Port collision sensitivity**
   - Existing listeners on default ports (e.g., 7865) caused startup failures.
   - Dynamic port resolution helps, but we still need robust endpoint health checks and clear diagnostics.

4. **Auto-open/browser UX variability**
   - Browser auto-open is best effort and environment-dependent.
   - Users may still need to manually open the sidecar URL in some shells/terminal setups.

Given these, we should keep Gradio for now (to ship quickly), but define explicit SPA pivot criteria:

- **Pivot trigger A:** Recurrent route/lifecycle regressions across Gradio minor versions.
- **Pivot trigger B:** Need tighter control over server lifecycle and request routing than Gradio provides.
- **Pivot trigger C:** Need richer realtime QA UX (streaming state, deterministic status bar behavior, advanced retry hints) that is awkward in Blocks.

If any trigger persists, move to a minimal FastAPI + static SPA frontend while preserving the same sidecar transport contract (`/api/health`, `/api/question`, `/api/answer`, `/api/status`, `/api/shutdown`).
