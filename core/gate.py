from __future__ import annotations

from typing import Any

from langchain_core.output_parsers import JsonOutputParser

from client.openrouter_client import InputMessage, OpenRouterClient, OpenRouterClientError
from core.models import (
    AggregatedContext,
    ChangeProposal,
    CompetenceGap,
    CompetenceGapSize,
    CompetenceModel,
    GateDecision,
    QAPacket,
    RelevantCompetenceEntry,
)
from qa.question_generation import select_question_type


class KnowledgeGate:
    def __init__(self, client: OpenRouterClient | None = None) -> None:
        self._client = client or OpenRouterClient()

    def evaluate(
        self,
        proposal: ChangeProposal,
        aggregated_context: AggregatedContext,
        competence_model: CompetenceModel,
    ) -> GateDecision:

        parser = JsonOutputParser()
        input_data = self._create_input_data(proposal, aggregated_context, competence_model, parser)
        try:
            response_text = self._client.create_response(
                input_data=input_data,
                temperature=0.1,
                max_output_tokens=500,
            )
            parsed = parser.parse(response_text)
            if not isinstance(parsed, dict):
                raise ValueError("Parsed response is not a JSON object.")
            return self._decision_from_parsed(
                parsed,
                aggregated_context=aggregated_context,
                competence_model=competence_model,
            )
        except Exception:  # noqa: B904
            raise RuntimeError(
                "Knowledge gate evaluation failed due to an error. Defaulting to allow."
            ) from None

    def _create_input_data(
        self,
        proposal: ChangeProposal,
        aggregated_context: AggregatedContext,
        competence_model: CompetenceModel,
        parser: JsonOutputParser,
    ) -> list[InputMessage]:
        format_instructions = parser.get_format_instructions()
        concepts_blob = "\n".join(
            f"- {name}: score={entry.score:.2f}, notes={'; '.join(entry.notes) or 'none'}"
            for name, entry in competence_model.concepts.items()
        )
        system_prompt = (
            "You are the Knowledge gate. "
            "Analyze proposed code changes versus user competence and return strict JSON only. "
            "Never include markdown, prose before JSON, or code fences."
        )
        user_prompt = "\n\n".join(
            [
                "Assess whether to allow or block this mutation.",
                "Inputs:",
                "0) Proposal metadata",
                (
                    f"proposal_id={proposal.proposal_id}; tool_name={proposal.tool_name}; "
                    f"files_changed={proposal.diff_stats.files_changed}; "
                    f"additions={proposal.diff_stats.additions}; deletions={proposal.diff_stats.deletions}"
                ),
                "Changed paths:",
                *(f"- {target.path}" for target in proposal.targets),
                "1) Aggregated context markdown",
                aggregated_context.markdown,
                "2) Competence model concepts",
                concepts_blob or "<none>",
                "Required output schema (JSON):",
                "{",
                '  "decision": "allow" | "block",',
                '  "reasoning": "string",',
                '  "confidence": 0.0 to 1.0,',
                '  "relevant_concepts": ["string"],',
                '  "competence_gap": {"size": "high"|"medium"|"low", "rationale": "string"}',
                '  "prompt_seed": "string"',
                "}",
                "Notes:",
                "- If decision is allow, still provide low/medium/high gap and rationale.",
                "- Choose relevant concepts from the provided competence concepts or infer plausible ones.",
                "- prompt_seed should be a concise mechanism-focused QA seed.",
                "Formatting requirements:",
                format_instructions,
            ]
        )
        return [
            InputMessage(role="system", content=system_prompt),
            InputMessage(role="user", content=user_prompt),
        ]

    def _decision_from_parsed(
        self,
        parsed: dict[str, Any],
        *,
        aggregated_context: AggregatedContext,
        competence_model: CompetenceModel,
    ) -> GateDecision:
        decision_raw = str(parsed.get("decision", "allow")).lower()
        decision = "block" if decision_raw == "block" else "allow"

        confidence = self._to_float(parsed.get("confidence"), default=0.5)
        confidence = min(1.0, max(0.0, confidence))
        reasoning = str(parsed.get("reasoning", "Model-based gate evaluation.")).strip()

        raw_concepts = parsed.get("relevant_concepts", [])
        relevant_concepts = [str(item) for item in raw_concepts if isinstance(item, str)]

        competence_gap_payload = parsed.get("competence_gap")
        gap_size = self._extract_gap_size(competence_gap_payload)
        gap_rationale = self._extract_gap_rationale(competence_gap_payload)
        competence_gap = CompetenceGap(size=gap_size, rationale=gap_rationale)

        relevant_entries = self._relevant_entries(competence_model, relevant_concepts)
        if decision == "allow":
            return GateDecision(
                decision="allow",
                reasoning=reasoning,
                confidence=confidence,
                relevant_concepts=relevant_concepts,
                relevant_competence_entries=relevant_entries,
                competence_gap=competence_gap,
                qa_packet=None,
            )

        question_type = select_question_type(gap_size)
        prompt_seed = str(parsed.get("prompt_seed", "Explain why this change works.")).strip()
        qa_packet = QAPacket(
            question_type=question_type,
            prompt_seed=prompt_seed,
            context_excerpt=aggregated_context.markdown,
        )
        return GateDecision(
            decision="block",
            reasoning=reasoning,
            confidence=confidence,
            relevant_concepts=relevant_concepts,
            relevant_competence_entries=relevant_entries,
            competence_gap=competence_gap,
            qa_packet=qa_packet,
        )

    @staticmethod
    def _relevant_entries(
        competence_model: CompetenceModel,
        concepts: list[str],
    ) -> list[RelevantCompetenceEntry]:
        entries: list[RelevantCompetenceEntry] = []
        for concept in concepts:
            entry = competence_model.concepts.get(concept)
            if entry is None:
                continue
            entries.append(
                RelevantCompetenceEntry(
                    concept=concept,
                    score=entry.score,
                    notes=list(entry.notes),
                )
            )
        return entries

    @staticmethod
    def _to_float(value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _extract_gap_size(raw_gap: Any) -> CompetenceGapSize:
        if isinstance(raw_gap, dict):
            size = str(raw_gap.get("size", "medium")).lower()
            if size == "high":
                return "high"
            if size == "medium":
                return "medium"
            if size == "low":
                return "low"
        return "medium"

    @staticmethod
    def _extract_gap_rationale(raw_gap: Any) -> str:
        if isinstance(raw_gap, dict):
            rationale = str(raw_gap.get("rationale", "Potential knowledge gap identified.")).strip()
            if rationale:
                return rationale
        return "Potential knowledge gap identified."


def evaluate_change(
    proposal: ChangeProposal,
    aggregated_context: AggregatedContext,
    competence_model: CompetenceModel,
) -> GateDecision:
    """Evaluate a change proposal. Uses LLM gate if API key is available, falls back to scaffold."""
    try:
        return KnowledgeGate().evaluate(proposal, aggregated_context, competence_model)
    except (OpenRouterClientError, RuntimeError):
        from core.llm_adapter import ScaffoldGateModelAdapter

        return ScaffoldGateModelAdapter().evaluate(proposal, aggregated_context, competence_model)
