# ruff: noqa: E402

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.competence_store import load_competence_model
from core.context_aggregation import build_aggregated_context
from core.errors import VibeCheckError
from core.event_logger import EventLogger
from core.gate import evaluate_change
from core.models import ChangeProposal
from core.normalize import is_code_mutation_tool, normalize_mutation_payload
from hooks.decision_output import allow_response, deny_response, emit_decision
from hooks.stdin_payload import (
    discover_repo_notes,
    extract_transcript_excerpt,
    extract_user_prompt_excerpt,
    get_cwd,
    get_tool_name,
    read_hook_payload,
)
from qa.loop import QALoop

STATE_DIR = Path("state")


def handle_pre_tool_use(
    payload: Mapping[str, Any],
    *,
    state_dir: Path = STATE_DIR,
    event_logger: EventLogger | None = None,
) -> dict[str, Any]:
    logger = event_logger or EventLogger(state_dir / "logs" / "events.jsonl")

    tool_name = get_tool_name(dict(payload))
    logger.log("hook_payload_received", tool_name=tool_name)

    if not is_code_mutation_tool(tool_name):
        logger.log("non_mutation_bypass", tool_name=tool_name, status="allow")
        return allow_response(
            "Non-mutation tool call bypassed by VibeCheck scaffold.",
            {"tool_name": tool_name},
        )

    cwd = get_cwd(payload)
    transcript_excerpt = _optional_text(payload, "transcript_excerpt") or extract_transcript_excerpt(payload)
    user_prompt_excerpt = _optional_text(payload, "user_prompt_excerpt") or extract_user_prompt_excerpt(
        payload,
        transcript_excerpt,
    )
    repo_notes = _optional_text(payload, "repo_notes") or discover_repo_notes(cwd)

    proposal = normalize_mutation_payload(payload, cwd=cwd)
    logger.log(
        "mutation_normalized",
        proposal_id=proposal.proposal_id,
        session_id=proposal.session_id,
        tool_name=proposal.tool_name,
        details={"files_changed": proposal.diff_stats.files_changed},
    )

    aggregated_context = build_aggregated_context(
        proposal,
        state_dir,
        user_prompt_excerpt=user_prompt_excerpt,
        transcript_excerpt=transcript_excerpt,
        surrounding_code=_optional_text(payload, "surrounding_code") or _derive_surrounding_code(proposal),
        repo_notes=repo_notes,
    )
    logger.log("context_aggregated", proposal_id=proposal.proposal_id)

    competence_path = state_dir / "competence_model.yaml"
    competence_model = load_competence_model(competence_path)
    gate_decision = evaluate_change(proposal, aggregated_context, competence_model)
    logger.log(
        "gate_decision_made",
        proposal_id=proposal.proposal_id,
        status=gate_decision.decision,
        details={
            "confidence": gate_decision.confidence,
            "reasoning": gate_decision.reasoning,
        },
    )

    if gate_decision.decision == "allow":
        logger.log("decision_returned", proposal_id=proposal.proposal_id, status="allow")
        return allow_response(
            gate_decision.reasoning,
            {
                "proposal_id": proposal.proposal_id,
                "gate_decision": gate_decision.decision,
            },
        )

    qa_result = QALoop(event_logger=logger).run(
        proposal=proposal,
        gate_decision=gate_decision,
        competence_model=competence_model,
        competence_path=competence_path,
        state_dir=state_dir,
    )
    logger.log(
        "decision_returned",
        proposal_id=proposal.proposal_id,
        status="allow",
        details={
            "qa_passed": qa_result.passed,
            "attempt_count": qa_result.attempt_count,
        },
    )
    return allow_response(
        qa_result.summary,
        {
            "attempt_count": qa_result.attempt_count,
            "gate_decision": gate_decision.decision,
            "proposal_id": proposal.proposal_id,
            "qa_passed": qa_result.passed,
        },
    )


def main() -> None:
    try:
        payload = read_hook_payload()
        response = handle_pre_tool_use(payload)
    except VibeCheckError as exc:
        response = deny_response(str(exc), {"error_type": type(exc).__name__})
    emit_decision(response)


def _optional_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _derive_surrounding_code(payload: ChangeProposal) -> str:
    blocks: list[str] = []
    for target in payload.targets:
        blocks.append(f"# {target.path}\n{target.old_content or target.new_content}")
    return "\n\n".join(blocks)


if __name__ == "__main__":
    main()
