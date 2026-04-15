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
from qa.question_generation import build_question_prompt
from qa.renderer_selection import select_renderer
from qa.sidecar.presence import set_session_state

if TYPE_CHECKING:
    from core.event_logger import EventLogger


class QARenderer(Protocol):
    def ask(
        self,
        question: str,
        attempt_number: int,
        packet: QAPacket,
        *,
        session_id: str = "",
        proposal_id: str = "",
        tool_use_id: str = "",
    ) -> str: ...


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
            renderer = select_renderer(
                gate_decision.qa_packet.question_type,
                max_attempts=self.max_attempts,
            )
            configure = getattr(renderer, "configure", None)
            if callable(configure):
                configure(event_logger=self._logger, state_dir=state_dir)
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
        context_excerpt = gate_decision.qa_packet.context_excerpt or ""
        for attempt_number in range(1, self.max_attempts + 1):
            question = build_question_prompt(
                gate_decision,
                attempt_number=attempt_number,
                competence_entries=gate_decision.relevant_competence_entries,
            )
            self._log(
                "qa_attempt_started",
                proposal_id=proposal.proposal_id,
                details={
                    "attempt_number": attempt_number,
                    "question_type": gate_decision.qa_packet.question_type,
                },
            )
            set_session_state(
                proposal.session_id,
                "qa_waiting_submission",
                state_dir=state_dir,
                detail="Question ready for user submission",
                proposal_id=proposal.proposal_id,
                tool_use_id=proposal.tool_use_id,
                attempt_number=attempt_number,
            )
            answer = renderer.ask(
                question,
                attempt_number,
                gate_decision.qa_packet,
                session_id=proposal.session_id,
                proposal_id=proposal.proposal_id,
                tool_use_id=proposal.tool_use_id,
            )
            set_session_state(
                proposal.session_id,
                "qa_evaluating",
                state_dir=state_dir,
                detail="Evaluating submitted answer",
                proposal_id=proposal.proposal_id,
                tool_use_id=proposal.tool_use_id,
                attempt_number=attempt_number,
            )
            evaluation = evaluate_answer(
                question=question,
                answer=answer,
                question_type=gate_decision.qa_packet.question_type,
                context_excerpt=context_excerpt,
                attempt_number=attempt_number,
            )
            _append_qa_history(
                state_dir=state_dir,
                proposal=proposal,
                packet=gate_decision.qa_packet,
                question=question,
                answer=answer,
                attempt_number=attempt_number,
                passed=evaluation.passed,
                feedback=evaluation.feedback,
                relevant_concepts=gate_decision.relevant_concepts,
            )
            self._log(
                "qa_answer_evaluated",
                proposal_id=proposal.proposal_id,
                status="passed" if evaluation.passed else "failed",
                details={"attempt_number": attempt_number, "feedback": evaluation.feedback},
            )
            set_session_state(
                proposal.session_id,
                "qa_pass" if evaluation.passed else "qa_fail_attempt",
                state_dir=state_dir,
                detail=evaluation.feedback,
                proposal_id=proposal.proposal_id,
                tool_use_id=proposal.tool_use_id,
                attempt_number=attempt_number,
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
            _try_show_feedback(renderer, evaluation.feedback, passed=evaluation.passed)

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
                _try_show_outcome(renderer, passed=True, attempt_count=attempt_number)
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
        _try_show_outcome(renderer, passed=False, attempt_count=self.max_attempts)
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


def _try_show_feedback(renderer: object, feedback: str, *, passed: bool) -> None:
    if hasattr(renderer, "show_feedback"):
        renderer.show_feedback(feedback, passed=passed)  # type: ignore[union-attr]


def _try_show_outcome(renderer: object, *, passed: bool, attempt_count: int) -> None:
    if hasattr(renderer, "show_outcome"):
        renderer.show_outcome(passed=passed, attempt_count=attempt_count)  # type: ignore[union-attr]


def _write_yaml(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _append_qa_history(
    *,
    state_dir: Path,
    proposal: ChangeProposal,
    packet: QAPacket,
    question: str,
    answer: str,
    attempt_number: int,
    passed: bool,
    feedback: str,
    relevant_concepts: list[str],
) -> None:
    import json
    from datetime import UTC, datetime

    history_path = state_dir / "qa" / "history" / "qa_attempts.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    question_id = f"{proposal.proposal_id}:attempt-{attempt_number}"
    record = {
        "record_version": 1,
        "question_id": question_id,
        "proposal_id": proposal.proposal_id,
        "session_id": proposal.session_id,
        "tool_use_id": proposal.tool_use_id,
        "attempt_number": attempt_number,
        "question_type": packet.question_type,
        "question": question,
        "answer": answer,
        "feedback": feedback,
        "passed": passed,
        "relevant_concepts": relevant_concepts,
        "context_ref": "agg/current_attempt.md",
        "created_at": now,
        "answered_at": now,
    }

    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
