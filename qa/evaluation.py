from __future__ import annotations

from dataclasses import dataclass

from core.models import QuestionType
from qa.llm_wrapper import get_llm_client


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
    client = get_llm_client()
    result = client.evaluate_answer(
        question=question,
        answer=answer,
        question_type=question_type,
        context_excerpt=context_excerpt,
        attempt_number=attempt_number,
    )
    return AnswerEvaluation(passed=result.passed, feedback=result.feedback)
