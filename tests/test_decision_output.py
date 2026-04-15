from hooks.decision_output import allow_response, deny_response


def test_allow_response_matches_claude_pre_tool_use_shape() -> None:
    response = allow_response(
        "VibeCheck: gate=allow (confidence=0.91)", {"proposal_id": "proposal-1"}
    )

    assert response["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert response["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "VibeCheck" in response["hookSpecificOutput"]["permissionDecisionReason"]
    assert response["metadata"]["proposal_id"] == "proposal-1"


def test_deny_response_matches_claude_pre_tool_use_shape() -> None:
    response = deny_response("blocked")

    assert response["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert response["hookSpecificOutput"]["permissionDecisionReason"] == "blocked"
