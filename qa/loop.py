from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import yaml

from core.competence_store import save_competence_model
from core.errors import StateValidationError
from core.models import ChangeProposal, CompetenceModel, GateDecision, QAAttempt, QAPacket, QAResult
from qa.competence_updates import apply_qa_outcome
from qa.evaluation import evaluate_answer
from qa.question_generation import build_follow_up_question, build_question_prompt
from qa.renderer_selection import select_renderer

if TYPE_CHECKING:
    from core.event_logger import EventLogger


class QARenderer(Protocol):
    def ask(self, question: str, attempt_number: int, packet: QAPacket) -> str: ...


class QALoop:
    def __init__(
        self,
        renderer: QARenderer | None = None,
        max_attempts: int = 3,
        auto_select_renderer: bool = True,
        event_logger: EventLogger | None = None,
    ) -> None:
        self._explicit_renderer = renderer
        self.renderer = renderer
        self.max_attempts = max_attempts
        self.auto_select_renderer = auto_select_renderer
        self._logger = event_logger

    def run(
        self,
        *,
        proposal: ChangeProposal,
        gate_decision: GateDecision,
        competence_model: CompetenceModel,
        competence_path: Path,
        state_dir: Path,
    ) -> QAResult:
        if gate_decision.qa_packet is None:
            raise StateValidationError("Blocked gate decisions must include a QA packet.")

        if self._explicit_renderer is None and self.auto_select_renderer:
            renderer = select_renderer(gate_decision.qa_packet.question_type)
        else:
            renderer = self.renderer
        assert renderer is not None

        pending_path = state_dir / "qa" / "pending" / f"{proposal.proposal_id}.yaml"
        result_path = state_dir / "qa" / "results" / f"{proposal.proposal_id}.yaml"
        _write_yaml(
            pending_path,
            {
                "proposal_id": proposal.proposal_id,
                "question_type": gate_decision.qa_packet.question_type,
                "prompt_seed": gate_decision.qa_packet.prompt_seed,
                "relevant_concepts": gate_decision.relevant_concepts,
            },
        )

        attempts: list[QAAttempt] = []
        question = build_question_prompt(gate_decision, attempt_number=1)
        for attempt_number in range(1, self.max_attempts + 1):
            self._log(
                "qa_attempt_started",
                proposal_id=proposal.proposal_id,
                details={"attempt_number": attempt_number, "question_type": gate_decision.qa_packet.question_type},
            )
            answer = renderer.ask(question, attempt_number, gate_decision.qa_packet)
            evaluation = evaluate_answer(gate_decision.qa_packet.question_type, answer)
            self._log(
                "qa_answer_evaluated",
                proposal_id=proposal.proposal_id,
                status="passed" if evaluation.passed else "failed",
                details={"attempt_number": attempt_number, "feedback": evaluation.feedback},
            )
            attempts.append(
                QAAttempt(
                    attempt_number=attempt_number,
                    question=question,
                    answer=answer,
                    passed=evaluation.passed,
                    feedback=evaluation.feedback,
                )
            )
            if evaluation.passed:
                apply_qa_outcome(
                    competence_model,
                    concepts=gate_decision.relevant_concepts,
                    passed=True,
                    attempt_count=attempt_number,
                )
                save_competence_model(competence_model, competence_path)
                self._log(
                    "competence_updated",
                    proposal_id=proposal.proposal_id,
                    status="pass",
                    details={"attempt_count": attempt_number},
                )
                result = QAResult(
                    proposal_id=proposal.proposal_id,
                    final_decision="allow",
                    passed=True,
                    attempt_count=attempt_number,
                    attempts=attempts,
                    summary="QA loop passed; allowing the suspended mutation to continue.",
                )
                _write_yaml(result_path, _result_payload(result))
                return result
            if attempt_number < self.max_attempts:
                question = build_follow_up_question(question, evaluation.feedback)

        apply_qa_outcome(
            competence_model,
            concepts=gate_decision.relevant_concepts,
            passed=False,
            attempt_count=self.max_attempts,
        )
        save_competence_model(competence_model, competence_path)
        self._log(
            "competence_updated",
            proposal_id=proposal.proposal_id,
            status="fail",
            details={"attempt_count": self.max_attempts},
        )
        result = QAResult(
            proposal_id=proposal.proposal_id,
            final_decision="allow",
            passed=False,
            attempt_count=self.max_attempts,
            attempts=attempts,
            summary="QA loop reached the fail limit; allowing the mutation with a competence penalty.",
        )
        _write_yaml(result_path, _result_payload(result))
        return result

    def _log(self, event: str, **kwargs: object) -> None:
        if self._logger is not None:
            self._logger.log(event, **kwargs)  # type: ignore[arg-type]


def _result_payload(result: QAResult) -> dict[str, object]:
    return {
        "proposal_id": result.proposal_id,
        "final_decision": result.final_decision,
        "passed": result.passed,
        "attempt_count": result.attempt_count,
        "summary": result.summary,
        "attempts": [asdict(attempt) for attempt in result.attempts],
    }


def _write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
