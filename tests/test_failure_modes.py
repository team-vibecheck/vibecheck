"""Failure-mode and edge-case tests.

Covers: malformed payloads, empty payloads, missing competence model,
blocked gate without QA packet, fail-limit path verification,
and non-mutation bypass producing no artifacts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.competence_store import default_competence_model
from core.errors import HookPayloadError, StateValidationError, UnsupportedMutationError
from core.models import GateDecision
from hooks.pre_tool_use import handle_pre_tool_use
from hooks.stdin_payload import read_hook_payload
from qa.evaluation import AnswerEvaluation
from qa.llm_wrapper import GeneratedQuestion
from qa.loop import QALoop
from qa.terminal_renderer import TerminalQARenderer
from tests.test_qa_loop import FakeRenderer, _install_fake_llm, _make_gate_decision, _make_proposal

# --- Malformed JSON payload ---


def test_read_hook_payload_rejects_malformed_json() -> None:
    with pytest.raises(HookPayloadError, match="not valid JSON"):
        read_hook_payload("{not json at all")


def test_read_hook_payload_rejects_non_object_json() -> None:
    with pytest.raises(HookPayloadError, match="must decode to an object"):
        read_hook_payload("[1, 2, 3]")


# --- Empty / missing payload ---


def test_read_hook_payload_rejects_empty_string() -> None:
    with pytest.raises(HookPayloadError, match="empty"):
        read_hook_payload("")


def test_read_hook_payload_rejects_whitespace_only() -> None:
    with pytest.raises(HookPayloadError, match="empty"):
        read_hook_payload("   \n  ")


# --- Missing competence model file ---


def test_missing_competence_model_creates_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When competence_model.yaml doesn't exist, handle_pre_tool_use creates a default."""
    state_dir = tmp_path / "state"
    competence_path = state_dir / "competence_model.yaml"
    assert not competence_path.exists()

    class FakeGateClient:
        def create_response(self, *args, **kwargs):
            del args, kwargs
            return """{
  "decision": "block",
  "reasoning": "Large change requires validation.",
  "confidence": 0.7,
  "relevant_concepts": ["python_basics"],
  "competence_gap": {
    "size": "medium",
    "rationale": "Control flow changed."
  },
  "prompt_seed": "Explain the assignment mechanism."
}"""

    class FakeQuestionClient:
        def generate_question(self, gate_decision, attempt_number, competence_entries=None):
            del competence_entries
            return GeneratedQuestion(
                question=f"Attempt {attempt_number}: explain the mechanism. {gate_decision.qa_packet.prompt_seed}",
                distractors=[],
                hint="Think about the mechanism.",
            )

        def evaluate_answer(self, question, answer, question_type, context_excerpt, attempt_number):
            del question, answer, question_type, context_excerpt, attempt_number
            return type("Eval", (), {"passed": True, "feedback": "Good explanation!"})()

    monkeypatch.setattr("core.gate.OpenRouterClient", lambda: FakeGateClient())
    from qa import llm_wrapper as llm_wrapper_module

    monkeypatch.setattr(llm_wrapper_module, "_client", FakeQuestionClient())
    monkeypatch.setattr(
        "qa.loop.select_renderer",
        lambda question_type, max_attempts=3: TerminalQARenderer(max_attempts=max_attempts),
    )
    monkeypatch.setattr(
        TerminalQARenderer,
        "ask",
        lambda self, q, n, p, **kwargs: (
            "This assigns constants safely without altering control flow semantics."
        ),
    )

    handle_pre_tool_use(
        {
            "tool_name": "Write",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "cwd": "/repo",
            "input": {
                "path": "example.py",
                "old_content": "",
                "new_content": "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\nf = 6\n",
            },
        },
        state_dir=state_dir,
    )

    assert competence_path.exists()
    data = yaml.safe_load(competence_path.read_text(encoding="utf-8"))
    assert "python_basics" in data["concepts"]


# --- Blocked gate with no QA packet ---


def test_qa_loop_raises_without_qa_packet(tmp_path: Path) -> None:
    gate_decision = GateDecision(
        decision="block",
        reasoning="Blocked but no QA packet.",
        confidence=0.3,
        competence_gap=None,
        qa_packet=None,
    )
    loop = QALoop(renderer=FakeRenderer(["anything"]))

    with pytest.raises(StateValidationError, match="QA packet"):
        loop.run(
            proposal=_make_proposal("no-packet"),
            gate_decision=gate_decision,
            competence_model=default_competence_model(),
            competence_path=tmp_path / "cm.yaml",
            state_dir=tmp_path / "state",
        )


# --- Fail-limit path: verify artifacts and competence decrement ---


def test_fail_limit_path_artifacts_and_competence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    competence_path = tmp_path / "cm.yaml"
    model = default_competence_model()
    initial_score = model.concepts["python_basics"].score

    _install_fake_llm(
        monkeypatch,
        [
            AnswerEvaluation(passed=False, feedback="Nope."),
            AnswerEvaluation(passed=False, feedback="Still wrong."),
            AnswerEvaluation(passed=False, feedback="Incorrect."),
        ],
    )
    loop = QALoop(renderer=FakeRenderer(["bad", "still bad", "nope"]))
    result = loop.run(
        proposal=_make_proposal("fail-verify"),
        gate_decision=_make_gate_decision(),
        competence_model=model,
        competence_path=competence_path,
        state_dir=state_dir,
    )

    # Result correctness
    assert result.passed is False
    assert result.attempt_count == 3
    assert result.final_decision == "allow"
    assert len(result.attempts) == 3
    assert all(not a.passed for a in result.attempts)

    # Competence decremented
    saved = yaml.safe_load(competence_path.read_text(encoding="utf-8"))
    final_score = saved["concepts"]["python_basics"]["score"]
    assert final_score < initial_score
    assert abs(final_score - (initial_score - 0.06)) < 0.001

    # Result artifact exists and is valid
    result_yaml = state_dir / "qa" / "results" / "fail-verify.yaml"
    assert result_yaml.exists()
    result_data = yaml.safe_load(result_yaml.read_text(encoding="utf-8"))
    assert result_data["passed"] is False
    assert result_data["attempt_count"] == 3


# --- Non-mutation bypass: no QA artifacts ---


def test_non_mutation_bypass_creates_no_qa_or_agg_artifacts(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"

    response = handle_pre_tool_use(
        {
            "tool_name": "Read",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "cwd": "/repo",
        },
        state_dir=state_dir,
    )

    assert response["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert not (state_dir / "agg").exists()
    assert not (state_dir / "qa").exists()
    assert not (state_dir / "competence_model.yaml").exists()


# --- Write with completely empty tool_input ---


def test_write_with_empty_tool_input_raises(tmp_path: Path) -> None:
    with pytest.raises((HookPayloadError, UnsupportedMutationError)):
        handle_pre_tool_use(
            {
                "tool_name": "Write",
                "session_id": "session-1",
                "tool_use_id": "tool-1",
                "cwd": "/repo",
                "tool_input": {},
            },
            state_dir=tmp_path / "state",
        )


# --- Edit with missing old_string ---


def test_edit_missing_old_string_raises(tmp_path: Path) -> None:
    with pytest.raises(HookPayloadError):
        handle_pre_tool_use(
            {
                "tool_name": "Edit",
                "session_id": "session-1",
                "tool_use_id": "tool-1",
                "cwd": "/repo",
                "tool_input": {
                    "file_path": "example.py",
                    "new_string": "updated = True\n",
                },
            },
            state_dir=tmp_path / "state",
        )
