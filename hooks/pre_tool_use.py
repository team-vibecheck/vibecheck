# ruff: noqa: E402

from __future__ import annotations

import os
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
from qa.sidecar.leases import heartbeat_lease, prune_stale_leases
from qa.sidecar.presence import set_session_state

STATE_DIR = Path(os.environ.get("VIBECHECK_STATE_DIR", "state"))


def _status_reason(
    gate_decision: str,
    *,
    confidence: float | None = None,
    qa_passed: bool | None = None,
    qa_error: bool = False,
    error_type: str | None = None,
    attempt_count: int = 0,
    max_attempts: int = 3,
    bypass_tool: str | None = None,
) -> str:
    if bypass_tool:
        return f"VibeCheck: bypass (non-mutation: {bypass_tool})"

    if gate_decision == "allow":
        conf_str = f" (confidence={confidence:.2f})" if confidence is not None else ""
        return f"VibeCheck: gate=allow{conf_str}"

    if qa_error and error_type:
        return f"VibeCheck: gate=block, qa=error({error_type}), fail-open"

    if qa_passed is not None:
        ok = "passed" if qa_passed else "failed"
        if not qa_passed:
            return f"VibeCheck: gate=block, qa={ok} (attempts={attempt_count}/{max_attempts}), policy=allow"
        return f"VibeCheck: gate=block, qa={ok} (attempt={attempt_count}/{max_attempts})"

    return "VibeCheck: gate=block"


def handle_pre_tool_use(
    payload: Mapping[str, Any],
    *,
    state_dir: Path = STATE_DIR,
    event_logger: EventLogger | None = None,
) -> dict[str, Any]:
    logger = event_logger or EventLogger(state_dir / "logs" / "events.jsonl")

    tool_name = get_tool_name(dict(payload))
    logged_tool_name = tool_name or "unknown"
    logger.log("hook_payload_received", tool_name=logged_tool_name)

    if not is_code_mutation_tool(tool_name):
        logger.log("non_mutation_bypass", tool_name=logged_tool_name, status="allow")
        reason = _status_reason("", bypass_tool=tool_name or "unknown")
        return allow_response(
            reason,
            {"tool_name": tool_name},
        )

    session_id = _optional_text(payload, "session_id")
    set_session_state(
        session_id,
        "gate_thinking",
        state_dir=state_dir,
        detail="Evaluating mutation in knowledge gate",
    )
    heartbeat = heartbeat_lease(session_id, source="PreToolUse", state_dir=state_dir)
    pruned_sessions = prune_stale_leases(state_dir=state_dir)
    logger.log(
        "sidecar_lease_heartbeat",
        session_id=session_id,
        details={
            "active_leases": heartbeat.get("active_count", 0),
            "stale_pruned": len(pruned_sessions),
        },
    )

    cwd = get_cwd(payload)
    transcript_excerpt = _optional_text(
        payload, "transcript_excerpt"
    ) or extract_transcript_excerpt(payload)
    user_prompt_excerpt = _optional_text(
        payload, "user_prompt_excerpt"
    ) or extract_user_prompt_excerpt(
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
        surrounding_code=_optional_text(payload, "surrounding_code")
        or _derive_surrounding_code(proposal),
        repo_notes=repo_notes,
    )
    logger.log("context_aggregated", proposal_id=proposal.proposal_id)

    competence_path = state_dir / "competence_model.yaml"
    competence_model = load_competence_model(competence_path)

    try:
        gate_decision = evaluate_change(proposal, aggregated_context, competence_model)
    except Exception as exc:  # noqa: BLE001
        error_type = type(exc).__name__
        logger.log(
            "gate_evaluation_failed",
            proposal_id=proposal.proposal_id,
            status="error",
            details={"error_type": error_type, "error": str(exc)},
        )
        reason = _status_reason("allow", confidence=0.0, qa_error=True, error_type=error_type)
        logger.log("decision_returned", proposal_id=proposal.proposal_id, status="allow")
        return allow_response(
            reason,
            {
                "proposal_id": proposal.proposal_id,
                "gate_decision": "error",
                "gate_error": True,
                "gate_error_type": error_type,
                "qa_passed": None,
            },
        )

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
        set_session_state(
            proposal.session_id,
            "gate_allow",
            state_dir=state_dir,
            detail="Knowledge gate allowed mutation",
            proposal_id=proposal.proposal_id,
            tool_use_id=proposal.tool_use_id,
            auto_reset_after_seconds=4,
            auto_reset_to="sleeping",
        )
        logger.log("decision_returned", proposal_id=proposal.proposal_id, status="allow")
        reason = _status_reason("allow", confidence=gate_decision.confidence)
        return allow_response(
            reason,
            {
                "proposal_id": proposal.proposal_id,
                "gate_decision": gate_decision.decision,
            },
        )

    try:
        set_session_state(
            proposal.session_id,
            "gate_block",
            state_dir=state_dir,
            detail="Knowledge gate blocked mutation; QA required",
            proposal_id=proposal.proposal_id,
            tool_use_id=proposal.tool_use_id,
        )
        qa_result = QALoop(event_logger=logger).run(
            proposal=proposal,
            gate_decision=gate_decision,
            competence_model=competence_model,
            competence_path=competence_path,
            state_dir=state_dir,
        )
    except Exception as exc:  # noqa: BLE001
        error_type = type(exc).__name__
        set_session_state(
            proposal.session_id,
            "error",
            state_dir=state_dir,
            detail=f"QA loop failed: {error_type}",
            proposal_id=proposal.proposal_id,
            tool_use_id=proposal.tool_use_id,
        )
        logger.log(
            "qa_loop_failed",
            proposal_id=proposal.proposal_id,
            status="error",
            details={"error_type": error_type, "error": str(exc)},
        )
        logger.log(
            "decision_returned",
            proposal_id=proposal.proposal_id,
            status="allow",
            details={"qa_error": True, "qa_error_type": error_type},
        )
        reason = _status_reason(
            "block",
            qa_error=True,
            error_type=error_type,
        )
        return allow_response(
            reason,
            {
                "attempt_count": 0,
                "gate_decision": gate_decision.decision,
                "proposal_id": proposal.proposal_id,
                "qa_error": True,
                "qa_error_type": error_type,
                "qa_passed": None,
            },
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
    set_session_state(
        proposal.session_id,
        "qa_pass" if qa_result.passed else "qa_fail_terminal",
        state_dir=state_dir,
        detail="QA passed" if qa_result.passed else "QA failed after max attempts",
        proposal_id=proposal.proposal_id,
        tool_use_id=proposal.tool_use_id,
        attempt_number=qa_result.attempt_count,
        auto_reset_after_seconds=4 if qa_result.passed else None,
        auto_reset_to="sleeping" if qa_result.passed else None,
    )
    reason = _status_reason(
        gate_decision.decision,
        qa_passed=qa_result.passed,
        attempt_count=qa_result.attempt_count,
    )
    return allow_response(
        reason,
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
