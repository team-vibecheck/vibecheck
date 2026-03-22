from __future__ import annotations

from core.models import GateDecision, QuestionType


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
    """Build a question prompt using LLM if available, otherwise fall back to local generation."""
    if gate_decision.qa_packet is None:
        raise ValueError("Cannot build a question without a QA packet.")

    try:
        from qa.llm_wrapper import get_llm_client

        client = get_llm_client()
        generated = client.generate_question(
            gate_decision=gate_decision,
            attempt_number=attempt_number,
            competence_entries=competence_entries,
        )
        return generated.question
    except Exception:
        return _local_build_prompt(gate_decision, attempt_number)


def build_follow_up_question(question: str, feedback: str) -> str:
    return f"{question}\n\nPrevious feedback: {feedback}"


def _local_build_prompt(gate_decision: GateDecision, attempt_number: int) -> str:
    """Deterministic fallback when LLM is unavailable."""
    qa_packet = gate_decision.qa_packet
    if qa_packet is None:
        raise ValueError("Cannot build a question without a QA packet.")

    sections = [
        _build_mechanism_intro(attempt_number, qa_packet.question_type),
        "\n\nChange being evaluated:",
        f"Seed: {qa_packet.prompt_seed}",
        "\nCode context:",
        qa_packet.context_excerpt or "<missing>",
        _format_competence_hints(gate_decision.relevant_competence_entries),
    ]
    return "\n".join(sections)


def _build_mechanism_intro(attempt_number: int, question_type: QuestionType) -> str:
    intros = {
        1: {
            "faded_example": "Fill in the key mechanism to complete this change safely.",
            "plain_english": "Explain the mechanism behind this change. Focus on why it works, not just what syntax appears.",
            "true_false": "Evaluate this statement about the change. Explain your reasoning briefly.",
        },
        2: {
            "faded_example": "Try again with more scaffolding. Focus on the key control flow and data transformation.",
            "plain_english": "Try again with more concrete reasoning about execution flow. Name the mechanism, affected control flow, and one potential failure mode.",
            "true_false": "Try again. Consider edge cases and side effects before answering.",
        },
        3: {
            "faded_example": "Final attempt: show the minimal complete implementation that captures the essence of this change.",
            "plain_english": "Final attempt: answer with the clearest mechanism-focused explanation you can. Focus on the 'why' not the 'what'.",
            "true_false": "Final attempt: answer true or false and give the single most important reason.",
        },
    }
    return intros.get(attempt_number, {}).get(
        question_type,
        f"Attempt {attempt_number}: Answer the question about this change.",
    )


def _format_competence_hints(relevant_entries: list) -> str:
    if not relevant_entries:
        return ""
    hints = []
    for entry in relevant_entries[:3]:
        concept = entry.concept if hasattr(entry, "concept") else str(entry)
        notes = entry.notes if hasattr(entry, "notes") else []
        if notes:
            hints.append(f"- {concept}: {notes[0]}")
    if hints:
        return "\n\nRelevant context:\n" + "\n".join(hints)
    return ""
