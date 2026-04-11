# Newcomer Guide

## What VibeCheck Is

VibeCheck is a Python-first guardrail in Claude Code's mutation path.

Flow:
1. Claude Code triggers `PreToolUse`.
2. VibeCheck normalizes the proposed code change into `ChangeProposal`.
3. VibeCheck builds aggregated context.
4. Knowledge gate decides `allow` or `block`.
5. If blocked, QA loop runs before the original tool call continues.
6. State and artifacts are written to disk for audit and debugging.

Source of truth for MVP shape: `finalized_MVP_spec.md`.

## Current Project Shape

- `hooks/`: Claude-facing entrypoints and decision output.
- `core/`: normalization, context aggregation, gate logic, competence state, event logging.
- `qa/`: question generation, answer evaluation, renderer selection, QA loop.
- `state/`: YAML, Markdown, JSONL artifacts.
- `tests/`: unit and integration coverage.

## What Exists Today

Core path exists end to end:
- `hooks/pre_tool_use.py` reads hook payload, filters non-mutation tools, normalizes mutations, builds context, runs gate, then runs QA loop when blocked.
- `core/gate.py` calls OpenRouter through `client/openrouter_client.py` and parses structured JSON into `GateDecision`.
- `qa/loop.py` writes pending/result YAML artifacts, evaluates answers, updates competence state, and logs lifecycle events.
- `core/competence_store.py` persists `state/competence_model.yaml`.
- `core/event_logger.py` writes append-only events to `state/logs/events.jsonl`.

Project already has real persisted artifacts and test coverage. Repo is not empty scaffold anymore.

## What Is Still Weak

### 1. QA UX is not settled

This is current biggest product risk.

- `qa/terminal_renderer.py` uses `/dev/tty`.
- `docs/research-hook-interaction-patterns.md` shows `/dev/tty` is not viable inside Claude Code hooks.
- `qa/renderer_selection.py` currently always selects Gradio when available and raises if not installed.
- `qa/gradio_renderer.py` launches a fresh Gradio app per question, which is slow and fragile.

Short version: current renderer story does not match real Claude Code hook constraints yet.

### 2. Competence model is still simple

- Current model is flat concept -> score + notes + evidence.
- Updates are additive deltas in `core/competence_store.py`.
- This is easy to inspect, but weak for retrieval, generalization, and long-term tutoring.

### 3. Some docs are historical

Some older notes assume terminal QA is workable or treat Gradio as default answer. Current direction is more specific:
- terminal direct input in hooks is not reliable
- sidecar architecture is likely needed
- renderer choice should stay open behind that seam

## Current Priorities

### Priority 1: Fix QA UX for real hook use

Near-term target is not "pick Gradio" or "pick custom UI" first.
Near-term target is: prove a sidecar architecture that survives across hook invocations.

Why:
- hook processes are ephemeral
- UI interaction needs session-scoped state and low latency
- per-question app launch is too expensive

Practical direction:
- define hook <-> sidecar protocol first
- handle spawn, health check, answer polling, idle shutdown, and replayability
- keep renderer behind that boundary
- use persistent Gradio sidecar only if it is fastest way to validate lifecycle
- move to lighter custom sidecar UI if Gradio remains too heavy

### Priority 2: Upgrade competence model

Near-term direction is retrieval-first, not graph-first.

Recommended sequence:
1. Retrieve recent interactions and QA outcomes.
2. Weight retrieval by recency.
3. Weight retrieval by locality to changed files or code regions.
4. Retrieve docs and examples for language features, syntax, objects, and libraries touched by current change.
5. Add embedding similarity over prior interactions and learning materials.
6. Revisit graph or ontology approaches only if simpler retrieval is not enough.

Stretch direction later:
- graph-based competence relationships
- knowledge graph linking code, skills, mistakes, docs, conversations, and teaching material
- ontology-backed tutoring system

## How To Read Repo Quickly

Read in this order:
1. `finalized_MVP_spec.md`
2. `docs/newcomer_guide.md`
3. `docs/next_steps.md`
4. `docs/research-hook-interaction-patterns.md`
5. `hooks/pre_tool_use.py`
6. `core/gate.py`
7. `qa/loop.py`
8. `core/competence_store.py`

## What Good Next Work Looks Like

Good next work is small, explicit, and tied to one of two priorities.

Best near-term tasks:
- sidecar lifecycle design doc
- hook/sidecar transport contract
- renderer selection refactor around sidecar boundary
- retrieval design for competence context
- artifact schema updates for retrieval inputs and outputs
- tests for sidecar failure modes and retrieval behavior

Avoid for now:
- deep framework refactors
- hidden orchestration state
- graph-heavy competence rewrite before retrieval baseline exists
- treating current Gradio path as final architecture

## Development Commands

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv run pyright
```

## Bottom Line

System has real hook, gate, QA, state, and tests.
Main gap is not "build everything". Main gap is making QA interaction work reliably inside Claude Code's real execution model, then making competence context smarter with retrieval before heavier graph ideas.
