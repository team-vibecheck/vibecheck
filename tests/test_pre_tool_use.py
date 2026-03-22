from pathlib import Path

import pytest

from core.errors import HookPayloadError, UnsupportedMutationError
from hooks.pre_tool_use import handle_pre_tool_use
from qa.terminal_renderer import TerminalQARenderer


def test_pre_tool_use_bypasses_non_mutation_tools(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"

    response = handle_pre_tool_use(
        {
            "tool_name": "Bash",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "cwd": "/repo",
        },
        state_dir=state_dir,
    )

    assert response["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "bypassed" in response["hookSpecificOutput"]["permissionDecisionReason"].lower()
    # Event log is created even for bypassed tools, but no QA or agg artifacts
    assert not (state_dir / "agg").exists()
    assert not (state_dir / "qa").exists()


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

    assert response["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert response["metadata"]["gate_decision"] == "allow"
    assert "Please rename the variable." in aggregated_context
    assert "assistant: I will update the file." in aggregated_context
    assert "Repository note for hook tests." in aggregated_context


def test_pre_tool_use_runs_blocked_flow_and_persists_qa_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"

    def fake_ask(
        self: TerminalQARenderer,
        question: str,
        attempt_number: int,
        packet: object,
    ) -> str:
        del self, question, attempt_number, packet
        return (
            "This change assigns several constants safely without altering control flow semantics."
        )

    monkeypatch.setattr(TerminalQARenderer, "ask", fake_ask)

    response = handle_pre_tool_use(
        {
            "tool_name": "Write",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "cwd": "/repo",
            "user_prompt_excerpt": "Expand the constants in this file.",
            "input": {
                "path": "core/example.py",
                "old_content": "value = 1\n",
                "new_content": (
                    "value = 1\nalpha = 1\nbeta = 2\ngamma = 3\ndelta = 4\nepsilon = 5\nzeta = 6\n"
                ),
            },
        },
        state_dir=state_dir,
    )

    proposal_id = response["metadata"]["proposal_id"]
    result_artifact = state_dir / "qa" / "results" / f"{proposal_id}.yaml"
    competence_model = (state_dir / "competence_model.yaml").read_text(encoding="utf-8")

    assert response["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert response["metadata"]["gate_decision"] == "block"
    assert response["metadata"]["qa_passed"] is True
    assert response["metadata"]["attempt_count"] == 1
    assert result_artifact.exists()
    assert "pass_first_try" in competence_model


def test_pre_tool_use_raises_for_invalid_mutation_payload(tmp_path: Path) -> None:
    with pytest.raises(HookPayloadError):
        handle_pre_tool_use(
            {
                "tool_name": "Write",
                "session_id": "session-1",
                "tool_use_id": "tool-1",
                "cwd": "/repo",
                "input": {
                    "path": "core/example.py",
                    "old_content": "value = 1\n",
                },
            },
            state_dir=tmp_path / "state",
        )


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
