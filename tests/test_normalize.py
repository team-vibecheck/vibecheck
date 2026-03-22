from pathlib import Path

import pytest

from core.errors import HookPayloadError, UnsupportedMutationError
from core.normalize import is_code_mutation_tool, normalize_mutation_payload


def test_normalize_write_payload_reads_existing_file_from_disk(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    target = repo / "core" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('old')\n", encoding="utf-8")

    payload = {
        "tool_name": "Write",
        "session_id": "session-1",
        "tool_use_id": "tool-1",
        "cwd": str(repo),
        "tool_input": {
            "file_path": "core/example.py",
            "content": "print('new')\n",
        },
    }

    proposal = normalize_mutation_payload(payload, cwd=repo)

    assert proposal.tool_name == "Write"
    assert proposal.targets[0].old_content == "print('old')\n"
    assert proposal.targets[0].new_content == "print('new')\n"
    assert proposal.targets[0].language == "python"
    assert proposal.diff_stats.files_changed == 1
    assert proposal.diff_stats.additions == 1
    assert proposal.diff_stats.deletions == 1
    assert "+print('new')" in proposal.unified_diff


def test_normalize_edit_payload_applies_single_replacement(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    target = repo / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("answer = 41\nprint(answer)\n", encoding="utf-8")

    proposal = normalize_mutation_payload(
        {
            "tool_name": "Edit",
            "cwd": str(repo),
            "tool_input": {
                "file_path": "app.py",
                "old_string": "41",
                "new_string": "42",
            },
        },
        cwd=repo,
    )

    assert proposal.targets[0].old_content == "answer = 41\nprint(answer)\n"
    assert proposal.targets[0].new_content == "answer = 42\nprint(answer)\n"
    assert proposal.diff_stats.additions == 1
    assert proposal.diff_stats.deletions == 1


def test_normalize_multiedit_payload_applies_edits_in_order(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    target = repo / "service.py"
    target.parent.mkdir(parents=True)
    target.write_text("value = 1\nlabel = 'old'\n", encoding="utf-8")

    proposal = normalize_mutation_payload(
        {
            "tool_name": "MultiEdit",
            "cwd": str(repo),
            "tool_input": {
                "file_path": "service.py",
                "edits": [
                    {"old_string": "1", "new_string": "2"},
                    {"old_string": "old", "new_string": "new"},
                ],
            },
        },
        cwd=repo,
    )

    assert proposal.targets[0].new_content == "value = 2\nlabel = 'new'\n"
    assert proposal.diff_stats.additions == 2
    assert proposal.diff_stats.deletions == 2


def test_normalize_notebook_edit_raises_unsupported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(UnsupportedMutationError):
        normalize_mutation_payload(
            {
                "tool_name": "NotebookEdit",
                "cwd": str(repo),
                "tool_input": {"file_path": "notes.ipynb", "content": "{}"},
            },
            cwd=repo,
        )


def test_normalize_edit_raises_when_old_string_is_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    target = repo / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("answer = 41\n", encoding="utf-8")

    with pytest.raises(HookPayloadError):
        normalize_mutation_payload(
            {
                "tool_name": "Edit",
                "cwd": str(repo),
                "tool_input": {
                    "file_path": "app.py",
                    "old_string": "999",
                    "new_string": "42",
                },
            },
            cwd=repo,
        )


def test_is_code_mutation_tool_recognizes_supported_tools() -> None:
    assert is_code_mutation_tool("Write") is True
    assert is_code_mutation_tool("Edit") is True
    assert is_code_mutation_tool("MultiEdit") is True
    assert is_code_mutation_tool("Bash") is False
