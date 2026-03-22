# Hooks

`hooks/` contains Claude-facing entrypoints and payload/response helpers.

- `pre_tool_use.py` is the blocking orchestration entrypoint for the `PreToolUse` hook.
- `stdin_payload.py` parses the hook payload from stdin and extracts tool metadata.
- `decision_output.py` emits the final `allow` or `deny` payload.

The scaffold keeps hook logic thin so normalization, gating, and QA remain testable in `core/` and `qa/`.

## Day-One Mutation Tool Matrix

| Tool | Status | Input shape handled | `ChangeProposal` mapping |
| --- | --- | --- | --- |
| `Write` | supported | `tool_input.file_path`, `tool_input.content` | Reads current file from disk as `old_content`, uses proposed `content` as `new_content` |
| `Edit` | supported | `tool_input.file_path`, `tool_input.old_string`, `tool_input.new_string`, optional `replace_all` | Reads current file from disk, applies the edit in memory, emits one normalized target |
| `MultiEdit` | supported | `tool_input.file_path`, `tool_input.edits[]` with `old_string`, `new_string`, optional `replace_all` | Reads current file once, applies edits in order, emits one normalized target |
| `NotebookEdit` | recognized but denied | payload shape not normalized yet | Treated as a mutation tool, but currently raises an explicit unsupported error instead of bypassing the gate |

## Context Inputs

`stdin_payload.py` now enriches the hook request with:

- transcript excerpts from `transcript_path` when Claude provides one
- best-effort user prompt extraction from explicit payload fields or transcript messages
- repo-local note discovery from nearby `AGENTS.md`, `CLAUDE.md`, and `README.md` files

This keeps the Claude-facing layer explicit while giving `core/context_aggregation.py` better inputs for the persisted audit packet.
