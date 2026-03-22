from __future__ import annotations

from core.models import GateDecision, QuestionType
from qa.llm_wrapper import GeneratedQuestion, get_llm_client


def select_question_type(gap_size: str) -> QuestionType:
    if gap_size == "high":
        return "faded_example"
    if gap_size == "low":
        return "true_false"
    return "plain_english"


def build_question_prompt(
    gate_decision: GateDecision,
    attempt_number: int,
    competence_entries: list | None = None,
) -> str:
    if gate_decision.qa_packet is None:
        raise ValueError("Cannot build a question without a QA packet.")

    client = get_llm_client()
    generated = client.generate_question(
        gate_decision=gate_decision,
        attempt_number=attempt_number,
        competence_entries=competence_entries,
    )
    return generated.question


def build_follow_up_question(question: str, feedback: str) -> str:
    return f"{question}\n\nPrevious feedback: {feedback}"


def generate_question_with_options(
    gate_decision: GateDecision,
    attempt_number: int,
    competence_entries: list | None = None,
) -> GeneratedQuestion:
    if gate_decision.qa_packet is None:
        raise ValueError("Cannot build a question without a QA packet.")

    client = get_llm_client()
    return client.generate_question(
        gate_decision=gate_decision,
        attempt_number=attempt_number,
        competence_entries=competence_entries,
    )
