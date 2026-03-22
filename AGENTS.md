# AGENTS.md
This file is for human contributors and coding agents working in `vibecheck`.

## Repository Status
- This repository is currently spec-first and early-stage.
- The current product and architecture source of truth is `prompts/unified_mvp_spec.md`.
- The intended implementation direction is Python-first.
- TypeScript or Bun is acceptable only for optional richer QA UI surfaces.
- As of this file, there is no committed `pyproject.toml`, `package.json`, `Makefile`, `pytest.ini`, `.cursorrules`, `.cursor/rules/`, or `.github/copilot-instructions.md`.
- No prior `AGENTS.md` existed in this repository.

## Source of Truth
- Treat `prompts/unified_mvp_spec.md` as authoritative for MVP shape and terminology.
- Treat the other files in `prompts/` as design context, not equal-weight implementation specs.
- If prompt files conflict, prefer the unified spec and the latest direct user instruction.
- Keep the MVP narrow: code agent interface, knowledge gate, QA loop.

## MCP-First Research Rule
- Before adding or changing any external dependency, framework integration, or provider API usage, consult the configured MCP documentation servers first.
- Repo MCP config files: `opencode.json` and `mcp-servers.json`.
- `context7` / `context7-mcp`: use for third-party package docs and examples.
- `langchain-docs` / `langchain-docs-mcp`: use for LangChain Python APIs, structured outputs, wrappers, and related patterns.

Expected workflow for external libraries:
1. Resolve the package/library first with Context7 if needed.
2. Read the relevant docs/examples through MCP before writing code.
3. Prefer current documented APIs over memory.
4. When using LangChain, verify the exact Python API shape in the LangChain MCP docs.
5. Record significant dependency choices in commit/PR context, or in code comments only when the choice is non-obvious.

Do not guess library APIs when MCP docs are available.

## Build / Lint / Test Commands
Current repo state:
- There is no committed runtime/tooling configuration yet.
- If you are only editing docs/specs, no build step exists.
- If you introduce Python code, also introduce the minimal tooling needed to run and verify it.

Preferred Python command conventions once code lands:
- Run all tests: `python -m pytest`
- Run one test file: `python -m pytest tests/path/test_file.py`
- Run one test case: `python -m pytest tests/path/test_file.py::test_name`
- Run tests by keyword: `python -m pytest -k "keyword"`
- Run with verbose output: `python -m pytest -vv`
- Lint: `python -m ruff check .`
- Format: `python -m ruff format .`
- Type check: `python -m pyright`

If the repository later adopts a different toolchain, update this section immediately.

## Pre-Change Checklist
- Read `prompts/unified_mvp_spec.md`.
- Inspect the worktree with `git status --short`.
- Do not overwrite or revert unrelated local changes.
- Check whether the task touches Python-only code, optional web UI code, or both.
- If a dependency or API is unfamiliar, use the MCP servers first.

## Git and Collaboration Practices
Be a considerate GitHub neighbor.
- Keep changes scoped to the task.
- Avoid opportunistic refactors unless they are necessary.
- Do not reformat unrelated files.
- Do not rename files or move modules without a task-driven reason.
- Never commit secrets, tokens, `.env` files, or local machine paths.
- Do not delete untracked user files unless explicitly asked.
- Do not revert someone else’s work to make your task easier.
- If the worktree is dirty, isolate your edits carefully and mention unrelated changes in your summary if relevant.
- Prefer small, reviewable commits when asked to commit.

## Architecture Guidelines
- Keep control flow explicit and mostly synchronous.
- Prefer plain Python modules and functions over orchestration frameworks.
- Do not introduce LangGraph for MVP work.
- Avoid hidden global state.
- Use request-local objects plus file-backed persisted state.
- Keep the Claude hook path understandable and debuggable.
- Remember the current MVP assumption: the original tool call is suspended while the gate and QA loop resolve.

## Preferred Project Shape
If implementation files are added, favor a layout close to:
- `hooks/` for Claude-facing hook entrypoints
- `core/` for normalization, aggregation, gating, model wrappers, and competence logic
- `qa/` for QA orchestration and renderers
- `state/` for persisted YAML, Markdown, JSON, or JSONL artifacts
- `tests/` for pytest coverage

Do not create deeply nested abstractions unless the codebase has earned them.

## Python Style Guidelines
- Target modern Python and use type hints everywhere practical.
- Prefer explicit imports over wildcard imports.
- Group imports as: standard library, third-party, local.
- Keep one import per line unless using a compact from-import with only a few names.
- Prefer `pathlib.Path` over raw string path manipulation.
- Prefer small pure functions where possible.
- Use `dataclass` or Pydantic models for structured boundaries.
- Use `TypedDict` only for lightweight interoperable payloads.
- Keep modules focused; avoid giant utility files.

## Naming and Formatting
- Modules and files: `snake_case`
- Functions and variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Test functions: `test_<behavior>`
- Use domain names from the spec consistently: `ChangeProposal`, `GateDecision`, `QAPacket`, `competence_model`
- Use `ruff format` once configured.
- Keep lines readable; favor clarity over squeezing logic into one line.
- Avoid unnecessary comments.
- Add comments only when a block is non-obvious or encodes a product rule.
- Use Markdown and YAML examples that are valid and minimal.

## Error Handling
- Fail loudly on invalid structured model output.
- Validate hook inputs at boundaries.
- Surface actionable errors with context.
- Do not silently swallow exceptions.
- Convert provider/library failures into explicit domain errors where useful.
- When a fallback path exists, record why the fallback was used.
- For file-backed state, handle missing files gracefully when that is expected; otherwise error clearly.

## State and Persistence
- Prefer YAML for the competence model.
- Prefer Markdown for human-readable aggregated context packets.
- Prefer JSON or JSONL for machine-friendly event logs and result records.
- Keep persisted artifacts inspectable.
- Do not invent a database for the MVP unless the user explicitly changes scope.

## Testing Expectations
- Add tests with code changes whenever practical.
- Test the smallest unit that proves the behavior.
- For parser and normalizer code, use focused fixture-driven unit tests.
- For hook logic, test both Claude-facing outputs and internal decision behavior.
- For model wrappers, mock provider responses and validate schema handling.
- For QA logic, test attempt progression and competence updates.

## LangChain Usage Guidance
- LangChain is allowed for structured outputs, retries, wrappers, and model integration utilities.
- Verify exact LangChain Python APIs in the LangChain MCP docs before coding.
- Prefer thin wrappers around LangChain models.
- Do not couple core business logic to LangChain internals more than necessary.
- Keep gate and QA domain models independent from provider-specific objects.

## Web / GUI Guidance
- Default to Python-first implementation.
- Only add Bun or TypeScript when the QA interaction clearly benefits from a browser UI.
- Keep any web surface minimal and local-first.
- Do not let optional UI code dominate the repository structure.

## Documentation and Change Hygiene
- Update docs when changing architecture, commands, or file layout assumptions.
- If you introduce a canonical toolchain, update the Build / Lint / Test section here.
- If Cursor or Copilot rules are added later, mirror the important constraints into this file.
- If a task reveals a conflict with this file, fix this file in the same change when appropriate.

## Decision Heuristic for Agents
When in doubt:
- prefer the smallest coherent change
- prefer explicit over magical
- prefer documented MCP-backed APIs over assumptions
- prefer repo consistency over cleverness
- prefer preserving neighbor changes over local cleanliness
