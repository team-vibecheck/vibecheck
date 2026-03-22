# Validation Report: `origin/gate` Branch (commit 8e33b2d)

**Date**: 2026-03-22
**Reviewer**: Claude (automated validation)
**Branch**: `origin/gate` (author: vishv843)

## Summary

The gate branch replaces the scaffold-based `KnowledgeGate` with a live
OpenRouter-backed implementation using LangChain's `JsonOutputParser`. The
direction is correct and aligned with the MVP spec's guidance that the gate
should be "model-driven." However, several issues must be resolved before
merging.

## Issues

### CRITICAL: API Key Leaked in Discord

The OpenRouter API key was posted in full in a Discord message. **Rotate this
key immediately** via the OpenRouter dashboard.

### P0: Constructor Signature Mismatch (gate.py vs test_gate.py)

- `KnowledgeGate.__init__` takes no parameters.
- `tests/test_gate.py` calls `KnowledgeGate(client=FakeClient())`.
- This test would fail with `TypeError` at runtime.

**Proposed fix**: Accept an optional `client` parameter in `__init__` so tests
can inject a fake, and production code defaults to a real `OpenRouterClient`.

### P0: test_openai_client.py References Non-Existent Names

- Uses `OpenAIClient` (should be `OpenRouterClient`).
- Patches `client.openai_client.request.urlopen` (should be
  `client.openrouter_client.request.urlopen`).
- `create_response` is typed to return `str` but test asserts `response["id"]`.
- Constructor called with kwargs (`api_key`, `default_model`, `app_name`) that
  don't exist in the implementation.

**Proposed fix**: Rename all references to match the actual `OpenRouterClient`
class and its real constructor signature. Decide whether `create_response`
returns raw `str` or parsed `dict`; update either the implementation or tests to
match.

### P1: ScaffoldGateModelAdapter Deleted Without Migration Path

`core/llm_adapter.py` is deleted entirely. The scaffold adapter was used by all
existing integration and replay tests. Merging this branch would break 59
passing tests.

**Proposed fix**: Keep `llm_adapter.py` (or inline the scaffold) so tests that
don't need a live model can still run. The `KnowledgeGate` can accept either an
adapter or client, defaulting to the OpenRouter client in production.

### P1: Error Handling Contradicts Itself

```python
except Exception:
    raise RuntimeError("...Defaulting to allow.")
```

The message says "defaulting to allow" but it raises, which means neither allow
nor block is returned. The original exception is also swallowed (no `from exc`
or logging).

**Proposed fix**: Either actually default to allow (return a GateDecision with
`decision="allow"`) or propagate the error with context (`raise ... from exc`).
Log the original error either way.

### P2: Redundant requirements.txt

The project uses `uv` + `pyproject.toml`. A separate `requirements.txt` with
only `langchain-core` duplicates what's already in `pyproject.toml` and will
drift.

**Proposed fix**: Remove `requirements.txt`. The dependency is already declared
in `pyproject.toml`.

### P2: test_script.py at Repo Root

Manual smoke tests at root don't follow the `tests/` convention and require a
live API key.

**Proposed fix**: Move to `tests/` or `scripts/`, add a `@pytest.mark.live`
marker or `if __name__` guard, and document that it needs
`OPENROUTER_API_KEY`.

### P3: Mixed Indentation in openrouter_client.py

The class body uses tabs while helper functions use spaces. This will fail
`ruff format` and may cause issues with some editors.

**Proposed fix**: Run `ruff format client/openrouter_client.py`.

### P3: Missing Trailing Newlines

`.gitignore`, `requirements.txt`, and `core/gate.py` are missing POSIX trailing
newlines.

## Spec Alignment Notes

### Aligned with Spec
- Uses LangChain for structured output parsing (spec §Implementation Constraints)
- Gate returns `allow`/`block` with the correct `GateDecision` shape (spec §Knowledge Gate Output)
- Calls `select_question_type` based on gap size (spec §Adaptive Question Types)
- Uses OpenRouter for model inference (local-first, no cloud dependency beyond LLM)

### Needs Attention
- Spec says "wrap model calls behind a Python adapter" — the gate branch removes
  the adapter pattern entirely instead of evolving it
- Spec says the gate evaluator should be configurable — hardcoding
  `OpenRouterClient()` in `__init__` without parameters reduces testability

## Recommendation

Do NOT merge as-is. Fix the P0 issues (broken tests), then address P1 (adapter
deletion, error handling). The P2/P3 items can be fixed in a follow-up.
