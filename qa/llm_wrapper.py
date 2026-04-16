from __future__ import annotations

import os

from langchain_openrouter import ChatOpenRouter
from pydantic import BaseModel, Field

from core.config import resolve_provider_config
from core.models import GateDecision, QuestionType, RelevantCompetenceEntry


class GeneratedQuestion(BaseModel):
    question: str = Field(description="The generated question for the user")
    distractors: list[str] = Field(
        description="Plausible wrong answers that reflect common misunderstandings"
    )
    hint: str = Field(description="A hint to provide if the user struggles")


class AnswerEvaluationResult(BaseModel):
    passed: bool = Field(description="Whether the answer demonstrates understanding")
    feedback: str = Field(
        description="Constructive feedback explaining why the answer passed or failed"
    )
    concept_mentioned: bool = Field(
        description="Whether the answer mentioned the key mechanism/concept"
    )
    reasoning_quality: str = Field(
        description="Assessment of the reasoning quality: strong, adequate, weak"
    )


class LLMQAClient:
    _DEFAULT_MODEL = "google/gemma-4-26b-a4b-it:free"
    _DEFAULT_FALLBACK_MODEL = "google/gemma-4-26b-a4b-it"
    _MODEL_ENV = "VIBECHECK_QA_MODEL"
    _FALLBACK_MODEL_ENV = "VIBECHECK_QA_FALLBACK_MODEL"

    def __init__(self, model: str | None = None, fallback_model: str | None = None) -> None:
        try:
            config = resolve_provider_config()
        except FileNotFoundError as exc:
            raise RuntimeError(
                "OpenRouter credentials are required. Set OPENROUTER_API_KEY or run 'vibecheck auth'."
            ) from exc
        if not config.api_key:
            raise RuntimeError(
                "OpenRouter credentials are required. Set OPENROUTER_API_KEY or run 'vibecheck auth'."
            )

        resolved_model = (
            model
            or os.environ.get(self._MODEL_ENV, "").strip()
            or self._DEFAULT_MODEL
        )
        resolved_fallback = (
            fallback_model
            or os.environ.get(self._FALLBACK_MODEL_ENV, "").strip()
            or (self._DEFAULT_FALLBACK_MODEL if resolved_model == self._DEFAULT_MODEL else "")
        )
        if resolved_fallback == resolved_model:
            resolved_fallback = ""

        os.environ.setdefault("OPENROUTER_API_KEY", config.api_key)
        self._model = ChatOpenRouter(model=resolved_model, temperature=0.3)
        self._fallback_model = (
            ChatOpenRouter(model=resolved_fallback, temperature=0.3) if resolved_fallback else None
        )

    def generate_question(
        self,
        gate_decision: GateDecision,
        attempt_number: int,
        competence_entries: list[RelevantCompetenceEntry] | None = None,
    ) -> GeneratedQuestion:
        if gate_decision.qa_packet is None:
            raise ValueError("Cannot generate question without a QA packet.")

        qa_packet = gate_decision.qa_packet
        scaffolding_prompt = _get_scaffolding_prompt(attempt_number, qa_packet.question_type)

        system_prompt = _build_question_system_prompt(
            attempt_number, qa_packet.question_type, competence_entries
        )

        user_prompt = f"""{scaffolding_prompt}

## Code Change Context
{qa_packet.context_excerpt}

## Question Seed
{qa_packet.prompt_seed}

## Competence Gap Rationale
{gate_decision.competence_gap.rationale if gate_decision.competence_gap else "Not specified"}"""

        return _invoke_with_optional_fallback(
            primary=self._model,
            fallback=self._fallback_model,
            output_model=GeneratedQuestion,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    def evaluate_answer(
        self,
        question: str,
        answer: str,
        question_type: QuestionType,
        context_excerpt: str,
        attempt_number: int,
    ) -> AnswerEvaluationResult:
        system_prompt = _build_evaluation_system_prompt(question_type)
        user_prompt = f"""## Question
    {question}

    ## User's Answer
    {answer}

    ## Context (the code change being evaluated)
    {context_excerpt}

    ## Attempt Number
    {attempt_number}"""

        return _invoke_with_optional_fallback(
            primary=self._model,
            fallback=self._fallback_model,
            output_model=AnswerEvaluationResult,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )


def _get_scaffolding_prompt(attempt_number: int, question_type: QuestionType) -> str:
    base_level = {
        1: "conceptual + mechanism",
        2: "more guided / narrower",
        3: "almost direct hinting",
    }.get(attempt_number, "conceptual + mechanism")

    type_specific = {
        "faded_example": "Generate a fill-in-the-blank or partial implementation question.",
        "plain_english": "Generate a plain-English question about program logic.",
        "true_false": "Generate a true/false question with a clear rationale.",
    }.get(question_type, "Generate a question about the mechanism.")

    return f"This is attempt {attempt_number}. Questions should be at the {base_level} level. {type_specific}"


def _build_question_system_prompt(
    attempt_number: int,
    question_type: QuestionType,
    competence_entries: list[RelevantCompetenceEntry] | None,
) -> str:
    base_prompt = """You are a pedagogical assistant for a code learning system called VibeCheck.
    Your task is to generate targeted questions that test whether a user understands WHY a code change works, not just what it does.

    Key principles:
    1. Focus on the MECHANISM (how the code works internally)
    2. Test REASONING, not memorization
    3. Include PLAUSIBLE DISTRACTORS (wrong answers that reflect common misunderstandings)
    4. Scaffolding should match the attempt level (earlier attempts are more open-ended)"""

    competence_context = ""
    if competence_entries:
        entries_str = "\n".join(
            f"- {entry.concept}: score={entry.score}, notes={[n for n in entry.notes]}"
            for entry in competence_entries
        )
        competence_context = f"\n\nRelevant competence entries:\n{entries_str}"

    attempt_guidance = {
        1: "Attempt 1: Ask conceptual questions about WHY this change is needed and HOW it works.",
        2: "Attempt 2: Provide more guided questions with narrower focus. Include hints about the mechanism.",
        3: "Attempt 3: Ask almost direct hinting questions that guide the user toward the key insight.",
    }.get(attempt_number, "Attempt 1 guidance applies.")

    type_guidance = {
        "faded_example": "Question type: faded_example - Generate a partial code example with blanks for the user to fill.",
        "plain_english": "Question type: plain_english - Ask questions that can be answered in prose.",
        "true_false": "Question type: true_false - Generate a statement with true/false answer.",
    }.get(question_type, "")

    return f"""{base_prompt}{competence_context}

    {attempt_guidance}
    {type_guidance}
    """


def _build_evaluation_system_prompt(question_type: QuestionType) -> str:
    type_specific = {
        "faded_example": "The user just needs to provide all correct code completions. Pass if the answer contains the key required elements.",
        "plain_english": "The user should explain the mechanism in their own words with reasonable detail. Pass if they identify the key concept and provide sound reasoning.",
        "true_false": "The user just needs to pick the correct option (True or False). Pass if they select the correct answer.",
    }.get(question_type, "The user should demonstrate understanding of the mechanism.")

    return f"""You are evaluating whether a user demonstrates understanding of a code change in VibeCheck.
        {type_specific}

        Provide a fair evaluation. Pass answers that show genuine understanding, even if imperfect. Fail answers that are superficial, missing key concepts, or show misunderstanding."""


_client: LLMQAClient | None = None


def get_llm_client() -> LLMQAClient:
    global _client
    if _client is None:
        _client = LLMQAClient()
    return _client


def _invoke_with_optional_fallback(
    *,
    primary: ChatOpenRouter,
    fallback: ChatOpenRouter | None,
    output_model: type[BaseModel],
    system_prompt: str,
    user_prompt: str,
) -> BaseModel:
    messages = [
        ("system", system_prompt),
        ("human", user_prompt),
    ]

    try:
        chain = primary.with_structured_output(output_model)
        result = chain.invoke(messages)
        if isinstance(result, dict):
            return output_model.model_validate(result)
        return result
    except Exception:  # noqa: BLE001
        if fallback is None:
            raise

    chain = fallback.with_structured_output(output_model)
    result = chain.invoke(messages)
    if isinstance(result, dict):
        return output_model.model_validate(result)
    return result
