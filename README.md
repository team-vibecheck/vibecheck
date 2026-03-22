# VibeCheck

VibeCheck is a Python-first competence-aware guardrail in the Claude Code mutation path.

## Architecture

The initial project shape follows the MVP spec directly:

- `hooks/` holds Claude-facing hook entrypoints and decision output helpers.
- `core/` holds mutation normalization, context aggregation, gate orchestration, and competence state logic.
- `qa/` holds the blocking QA loop plus terminal and optional Gradio renderer boundaries.
- `state/` holds inspectable persisted artifacts like YAML, Markdown, and JSONL files.
- `tests/` covers the gate, hook, QA loop, and CLI flows.

## Tooling

This repo uses `uv` for Python environment and dependency management.

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv run pyright
```

## CI

GitHub Actions runs the same core checks on pushes and pull requests to `main`:

- `uv run ruff check .`
- `uv run pyright`
- `uv run pytest`

The workflow lives at `.github/workflows/ci.yml`.

## Notes

- The gate and QA loop are model-backed through OpenRouter.
- Runtime auth resolves `OPENROUTER_API_KEY` first, then falls back to `~/.vibecheck/config.toml`.
- Terminal QA is the day-one path.
- `qa/gradio_renderer.py` is the optional Python web UI seam if richer browser-based QA becomes necessary.
