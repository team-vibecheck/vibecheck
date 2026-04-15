"""Replay fixture-driven integration tests.

Each fixture in tests/fixtures/*.json represents a complete hook payload
with documented expected artifacts. These tests replay each fixture through
handle_pre_tool_use and verify the expected outcomes.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from core.errors import HookPayloadError
from hooks.pre_tool_use import handle_pre_tool_use
from qa.llm_wrapper import GeneratedQuestion
from qa.terminal_renderer import TerminalQARenderer

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _read_events(state_dir: Path) -> list[dict]:
    log_path = state_dir / "logs" / "events.jsonl"
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _strip_meta(fixture: dict) -> dict:
    """Remove _description and other test metadata keys from the payload."""
    return {k: v for k, v in fixture.items() if not k.startswith("_")}


def _install_gate_client(monkeypatch: pytest.MonkeyPatch, *, decision: str) -> None:
    if decision == "allow":
        response = '{"decision": "allow", "reasoning": "Small change.", "confidence": 0.9, "relevant_concepts": []}'
    else:
        response = """{
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

    class FakeGateClient:
        def create_response(self, *args, **kwargs):
            del args, kwargs
            return response

    monkeypatch.setattr("core.gate.OpenRouterClient", lambda: FakeGateClient())


def _install_question_client(
    monkeypatch: pytest.MonkeyPatch, *, passed_sequence: list[bool]
) -> None:
    outcomes = iter(passed_sequence)

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
            passed = next(outcomes)
            feedback = "Good explanation!" if passed else "Not enough detail."
            return SimpleNamespace(passed=passed, feedback=feedback)

    from qa import llm_wrapper as llm_wrapper_module

    monkeypatch.setattr(llm_wrapper_module, "_client", FakeQuestionClient())


def _install_terminal_renderer(monkeypatch: pytest.MonkeyPatch, answer_fn) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "qa.loop.select_renderer",
        lambda question_type, max_attempts=3: TerminalQARenderer(max_attempts=max_attempts),
    )
    monkeypatch.setattr(TerminalQARenderer, "ask", answer_fn)


class TestFixture01SmallWriteAllow:
    def test_gate_allows_small_write(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fixture = _load_fixture("01_small_write_allow.json")
        state_dir = tmp_path / "state"
        _install_gate_client(monkeypatch, decision="allow")

        response = handle_pre_tool_use(_strip_meta(fixture), state_dir=state_dir)

        assert response["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert response["metadata"]["gate_decision"] == "allow"

    def test_no_qa_artifacts_on_allow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixture = _load_fixture("01_small_write_allow.json")
        state_dir = tmp_path / "state"
        _install_gate_client(monkeypatch, decision="allow")

        handle_pre_tool_use(_strip_meta(fixture), state_dir=state_dir)

        assert not (state_dir / "qa" / "pending").exists() or not list(
            (state_dir / "qa" / "pending").iterdir()
        )
        assert not (state_dir / "qa" / "results").exists() or not list(
            (state_dir / "qa" / "results").iterdir()
        )

    def test_event_log_allow_flow(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fixture = _load_fixture("01_small_write_allow.json")
        state_dir = tmp_path / "state"
        _install_gate_client(monkeypatch, decision="allow")

        handle_pre_tool_use(_strip_meta(fixture), state_dir=state_dir)

        events = _read_events(state_dir)
        event_names = [e["event"] for e in events]
        assert "hook_payload_received" in event_names
        assert "gate_decision_made" in event_names
        assert "decision_returned" in event_names
        assert "qa_attempt_started" not in event_names


class TestFixture02LargeWriteBlockPass:
    def test_blocks_then_passes_qa(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fixture = _load_fixture("02_large_write_block_pass.json")
        state_dir = tmp_path / "state"
        fake_answer = fixture["_fake_answer"]
        _install_gate_client(monkeypatch, decision="block")
        _install_question_client(monkeypatch, passed_sequence=[True])

        _install_terminal_renderer(monkeypatch, lambda self, q, n, p, **kwargs: fake_answer)

        response = handle_pre_tool_use(_strip_meta(fixture), state_dir=state_dir)

        assert response["metadata"]["gate_decision"] == "block"
        assert response["metadata"]["qa_passed"] is True
        assert response["metadata"]["attempt_count"] == 1

    def test_result_artifact_structure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixture = _load_fixture("02_large_write_block_pass.json")
        state_dir = tmp_path / "state"
        _install_gate_client(monkeypatch, decision="block")
        _install_question_client(monkeypatch, passed_sequence=[True])

        _install_terminal_renderer(
            monkeypatch,
            lambda self, q, n, p, **kwargs: fixture["_fake_answer"],
        )

        response = handle_pre_tool_use(_strip_meta(fixture), state_dir=state_dir)
        proposal_id = response["metadata"]["proposal_id"]

        result_data = yaml.safe_load(
            (state_dir / "qa" / "results" / f"{proposal_id}.yaml").read_text(encoding="utf-8")
        )
        assert result_data["passed"] is True
        assert result_data["final_decision"] == "allow"
        assert len(result_data["attempts"]) == 1

    def test_competence_updated_on_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixture = _load_fixture("02_large_write_block_pass.json")
        state_dir = tmp_path / "state"
        _install_gate_client(monkeypatch, decision="block")
        _install_question_client(monkeypatch, passed_sequence=[True])

        _install_terminal_renderer(
            monkeypatch,
            lambda self, q, n, p, **kwargs: fixture["_fake_answer"],
        )

        handle_pre_tool_use(_strip_meta(fixture), state_dir=state_dir)

        competence = (state_dir / "competence_model.yaml").read_text(encoding="utf-8")
        assert "pass_first_try" in competence


class TestFixture03LargeWriteBlockFailLimit:
    def test_fails_all_attempts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fixture = _load_fixture("03_large_write_block_fail_limit.json")
        state_dir = tmp_path / "state"
        answers = iter(fixture["_fake_answers"])
        _install_gate_client(monkeypatch, decision="block")
        _install_question_client(monkeypatch, passed_sequence=[False, False, False])

        _install_terminal_renderer(monkeypatch, lambda self, q, n, p, **kwargs: next(answers))

        response = handle_pre_tool_use(_strip_meta(fixture), state_dir=state_dir)

        assert response["metadata"]["gate_decision"] == "block"
        assert response["metadata"]["qa_passed"] is False
        assert response["metadata"]["attempt_count"] == 3

    def test_competence_docked_on_fail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixture = _load_fixture("03_large_write_block_fail_limit.json")
        state_dir = tmp_path / "state"
        answers = iter(fixture["_fake_answers"])
        _install_gate_client(monkeypatch, decision="block")
        _install_question_client(monkeypatch, passed_sequence=[False, False, False])

        _install_terminal_renderer(monkeypatch, lambda self, q, n, p, **kwargs: next(answers))

        handle_pre_tool_use(_strip_meta(fixture), state_dir=state_dir)

        competence = (state_dir / "competence_model.yaml").read_text(encoding="utf-8")
        assert "fail_limit_reached" in competence
        assert "epistemic debt" in competence.lower()

    def test_event_log_records_all_attempts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixture = _load_fixture("03_large_write_block_fail_limit.json")
        state_dir = tmp_path / "state"
        answers = iter(fixture["_fake_answers"])
        _install_gate_client(monkeypatch, decision="block")
        _install_question_client(monkeypatch, passed_sequence=[False, False, False])

        _install_terminal_renderer(monkeypatch, lambda self, q, n, p, **kwargs: next(answers))

        handle_pre_tool_use(_strip_meta(fixture), state_dir=state_dir)

        events = _read_events(state_dir)
        attempt_events = [e for e in events if e["event"] == "qa_attempt_started"]
        eval_events = [e for e in events if e["event"] == "qa_answer_evaluated"]
        assert len(attempt_events) == 3
        assert len(eval_events) == 3
        assert all(e["status"] == "failed" for e in eval_events)


class TestFixture04MalformedPayload:
    def test_raises_hook_payload_error(self, tmp_path: Path) -> None:
        fixture = _load_fixture("04_malformed_payload.json")

        with pytest.raises(HookPayloadError):
            handle_pre_tool_use(_strip_meta(fixture), state_dir=tmp_path / "state")


class TestFixture05NonMutationBypass:
    def test_bypasses_gate(self, tmp_path: Path) -> None:
        fixture = _load_fixture("05_non_mutation_bypass.json")
        state_dir = tmp_path / "state"

        response = handle_pre_tool_use(_strip_meta(fixture), state_dir=state_dir)

        assert response["hookSpecificOutput"]["permissionDecision"] == "allow"
        reason_lower = response["hookSpecificOutput"]["permissionDecisionReason"].lower()
        assert "bypass" in reason_lower

    def test_no_gate_artifacts(self, tmp_path: Path) -> None:
        fixture = _load_fixture("05_non_mutation_bypass.json")
        state_dir = tmp_path / "state"

        handle_pre_tool_use(_strip_meta(fixture), state_dir=state_dir)

        assert not (state_dir / "agg").exists()
        assert not (state_dir / "qa").exists()

    def test_event_log_bypass(self, tmp_path: Path) -> None:
        fixture = _load_fixture("05_non_mutation_bypass.json")
        state_dir = tmp_path / "state"

        handle_pre_tool_use(_strip_meta(fixture), state_dir=state_dir)

        events = _read_events(state_dir)
        event_names = [e["event"] for e in events]
        assert "non_mutation_bypass" in event_names
        assert "gate_decision_made" not in event_names
