from __future__ import annotations

from dataclasses import dataclass

from core.models import QuestionType


@dataclass(slots=True)
class AnswerEvaluation:
    passed: bool
    feedback: str


def evaluate_answer(
    question: str,
    answer: str,
    question_type: QuestionType,
    context_excerpt: str,
    attempt_number: int = 1,
) -> AnswerEvaluation:
    """Evaluate an answer using LLM if available, otherwise fall back to heuristics."""
    try:
        from qa.llm_wrapper import get_llm_client

        client = get_llm_client()
        result = client.evaluate_answer(
            question=question,
            answer=answer,
            question_type=question_type,
            context_excerpt=context_excerpt,
            attempt_number=attempt_number,
        )
        return AnswerEvaluation(passed=result.passed, feedback=result.feedback)
    except Exception:
        return _heuristic_evaluate(question_type, answer)


def _heuristic_evaluate(question_type: QuestionType, answer: str) -> AnswerEvaluation:
    """Deterministic fallback when LLM is unavailable."""
    normalized = answer.strip()
    if question_type == "true_false":
        passed = normalized.lower() in {"true", "false"}
        feedback = (
            "Answer with a clear true/false decision and a brief reason." if not passed else ""
        )
        return AnswerEvaluation(passed=passed, feedback=feedback)

    if question_type == "plain_english":
        passed = len(normalized.split()) >= 8
        feedback = (
            "Name the mechanism, affected control flow, and one failure mode." if not passed else ""
        )
        return AnswerEvaluation(passed=passed, feedback=feedback)

    passed = len(normalized.splitlines()) >= 2 and any(
        token in normalized for token in ("=", "if", "return")
    )
    feedback = (
        "Fill in a few lines of code or pseudocode that show the key mechanism."
        if not passed
        else ""
    )
    return AnswerEvaluation(passed=passed, feedback=feedback)
