from core.competence_store import default_competence_model
from core.models import (
    ChangeProposal,
    ChangeTarget,
    CompetenceGap,
    DiffStats,
    GateDecision,
    QAPacket,
)
from qa.loop import QALoop
from qa.question_generation import select_question_type


class FakeRenderer:
    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.index = 0

    def ask(self, question: str, attempt_number: int, packet: QAPacket) -> str:
        del question, attempt_number, packet
        answer = self.answers[self.index]
        self.index += 1
        return answer


def _make_proposal(proposal_id: str = "proposal-1") -> ChangeProposal:
    return ChangeProposal(
        proposal_id=proposal_id,
        session_id="session-1",
        tool_use_id="tool-1",
        tool_name="Write",
        cwd="/repo",
        targets=[
            ChangeTarget(
                path="core/example.py",
                language="python",
                old_content="",
                new_content="value = 1\n",
            )
        ],
        unified_diff="+value = 1",
        diff_stats=DiffStats(files_changed=1, additions=1, deletions=0),
        created_at="2026-03-21T00:00:00Z",
    )


def _make_gate_decision(qa_packet: QAPacket | None = None) -> GateDecision:
    return GateDecision(
        decision="block",
        reasoning="Need a quick explanation.",
        confidence=0.5,
        relevant_concepts=["python_basics"],
        competence_gap=CompetenceGap(size="medium", rationale="Scaffold test."),
        qa_packet=qa_packet
        or QAPacket(
            question_type="plain_english",
            prompt_seed="Explain why the assignment is safe.",
            context_excerpt="+value = 1",
        ),
    )


def test_qa_loop_passes_first_attempt(tmp_path) -> None:
    loop = QALoop(renderer=FakeRenderer(["This assigns a constant value safely and does not alter control flow."]))
    result = loop.run(
        proposal=_make_proposal(),
        gate_decision=_make_gate_decision(),
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )

    assert result.final_decision == "allow"
    assert result.passed is True
    assert result.attempt_count == 1
    assert len(result.attempts) == 1
    assert result.attempts[0].passed is True


def test_qa_loop_passes_after_retry(tmp_path) -> None:
    proposal = ChangeProposal(
        proposal_id="proposal-1",
        session_id="session-1",
        tool_use_id="tool-1",
        tool_name="Write",
        cwd="/repo",
        targets=[
            ChangeTarget(
                path="core/example.py",
                language="python",
                old_content="",
                new_content="value = 1\n",
            )
        ],
        unified_diff="+value = 1",
        diff_stats=DiffStats(files_changed=1, additions=1, deletions=0),
        created_at="2026-03-21T00:00:00Z",
    )
    gate_decision = GateDecision(
        decision="block",
        reasoning="Need a quick explanation.",
        confidence=0.5,
        relevant_concepts=["python_basics"],
        competence_gap=CompetenceGap(size="medium", rationale="Scaffold test."),
        qa_packet=QAPacket(
            question_type="plain_english",
            prompt_seed="Explain why the assignment is safe.",
            context_excerpt="+value = 1",
        ),
    )
    competence_model = default_competence_model()
    competence_path = tmp_path / "state" / "competence_model.yaml"
    loop = QALoop(
        renderer=FakeRenderer(
            ["too short", "This change assigns a constant value and does not alter control flow."]
        )
    )

    result = loop.run(
        proposal=proposal,
        gate_decision=gate_decision,
        competence_model=competence_model,
        competence_path=competence_path,
        state_dir=tmp_path / "state",
    )

    assert result.final_decision == "allow"
    assert result.passed is True
    assert result.attempt_count == 2
    assert (tmp_path / "state" / "qa" / "results" / "proposal-1.yaml").exists()
    saved_competence = competence_path.read_text(encoding="utf-8")
    assert "pass_after_2" in saved_competence


def test_qa_loop_fails_all_attempts(tmp_path) -> None:
    loop = QALoop(
        renderer=FakeRenderer(["bad", "still bad", "nope"])
    )
    result = loop.run(
        proposal=_make_proposal("proposal-fail"),
        gate_decision=_make_gate_decision(),
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )

    assert result.final_decision == "allow"
    assert result.passed is False
    assert result.attempt_count == 3
    saved_competence = (tmp_path / "cm.yaml").read_text(encoding="utf-8")
    assert "fail_limit_reached" in saved_competence
    assert "epistemic debt" in saved_competence.lower()


def test_qa_loop_writes_pending_and_result_artifacts(tmp_path) -> None:
    loop = QALoop(renderer=FakeRenderer(["short answer that is long enough to pass"]))
    loop.run(
        proposal=_make_proposal("proposal-art"),
        gate_decision=_make_gate_decision(),
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )

    pending_path = tmp_path / "state" / "qa" / "pending" / "proposal-art.yaml"
    result_path = tmp_path / "state" / "qa" / "results" / "proposal-art.yaml"
    assert pending_path.exists()
    assert result_path.exists()


def test_qa_loop_respects_max_attempts(tmp_path) -> None:
    loop = QALoop(renderer=FakeRenderer(["a", "b", "c", "d"]), max_attempts=2)
    result = loop.run(
        proposal=_make_proposal("proposal-2att"),
        gate_decision=_make_gate_decision(),
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )

    assert result.attempt_count == 2


def test_select_question_type_high_gap() -> None:
    assert select_question_type("high") == "faded_example"


def test_select_question_type_medium_gap() -> None:
    assert select_question_type("medium") == "plain_english"


def test_select_question_type_low_gap() -> None:
    assert select_question_type("low") == "true_false"


def test_qa_loop_true_false_question(tmp_path) -> None:
    loop = QALoop(renderer=FakeRenderer(["True"]))
    gate = _make_gate_decision(
        QAPacket(
            question_type="true_false",
            prompt_seed="Is this change safe?",
            context_excerpt="+value = 1",
        )
    )
    result = loop.run(
        proposal=_make_proposal("proposal-tf"),
        gate_decision=gate,
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )
    assert result.passed is True
    assert result.attempt_count == 1


def test_qa_loop_faded_example_question(tmp_path) -> None:
    loop = QALoop(
        renderer=FakeRenderer(["value = 1\nreturn value"])
    )
    gate = _make_gate_decision(
        QAPacket(
            question_type="faded_example",
            prompt_seed="Complete the implementation.",
            context_excerpt="def get_value():\n    pass",
        )
    )
    result = loop.run(
        proposal=_make_proposal("proposal-fe"),
        gate_decision=gate,
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )
    assert result.passed is True


def test_question_prompt_includes_mechanism_focus(tmp_path) -> None:
    from core.models import RelevantCompetenceEntry
    gate = GateDecision(
        decision="block",
        reasoning="Need explanation.",
        confidence=0.5,
        relevant_concepts=["async_programming"],
        relevant_competence_entries=[
            RelevantCompetenceEntry(
                concept="async_programming",
                score=0.42,
                notes=["Understands basic await usage"],
            )
        ],
        competence_gap=CompetenceGap(size="medium", rationale="Scaffold test."),
        qa_packet=QAPacket(
            question_type="plain_english",
            prompt_seed="Why is await needed here?",
            context_excerpt="async def fetch():\n    data = await get_data()",
        ),
    )
    from qa.question_generation import build_question_prompt
    prompt = build_question_prompt(gate, attempt_number=1)
    assert "mechanism" in prompt.lower()
    assert "await" in prompt


def test_follow_up_includes_previous_feedback() -> None:
    from qa.question_generation import build_follow_up_question
    result = build_follow_up_question(
        "Original question?",
        "Your answer was too vague."
    )
    assert "Your answer was too vague" in result
