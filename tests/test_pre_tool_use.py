from pathlib import Path

import pytest

from core.errors import UnsupportedMutationError
from hooks.pre_tool_use import handle_pre_tool_use


def test_pre_tool_use_allows_small_write_with_realistic_claude_payload(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state_dir = tmp_path / "state"
    target = repo / "core" / "example.py"
    transcript = repo / "transcript.jsonl"
    repo.mkdir()
    target.parent.mkdir(parents=True)
    target.write_text("value = 1\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("Repository note for hook tests.\n", encoding="utf-8")
    transcript.write_text(
        '{"role":"user","content":"Please rename the variable."}\n'
        '{"role":"assistant","content":"I will update the file."}\n',
        encoding="utf-8",
    )

    response = handle_pre_tool_use(
        {
            "tool_name": "Write",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "cwd": str(repo),
            "transcript_path": str(transcript),
            "tool_input": {
                "file_path": "core/example.py",
                "content": "value = 2\n",
            },
        },
        state_dir=state_dir,
    )

    aggregated_context = (state_dir / "agg" / "current_attempt.md").read_text(encoding="utf-8")

    assert response["decision"] == "allow"
    assert "Please rename the variable." in aggregated_context
    assert "assistant: I will update the file." in aggregated_context
    assert "Repository note for hook tests." in aggregated_context


def test_pre_tool_use_raises_for_unsupported_mutation_shape(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(UnsupportedMutationError):
        handle_pre_tool_use(
            {
                "tool_name": "NotebookEdit",
                "session_id": "session-1",
                "tool_use_id": "tool-1",
                "cwd": str(repo),
                "tool_input": {"file_path": "notes.ipynb", "content": "{}"},
            },
            state_dir=tmp_path / "state",
        )
