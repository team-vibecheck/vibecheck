from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from qa.sidecar.server import SidecarState, _build_context_preview, _limit_detached_sessions


def test_limit_detached_sessions_keeps_only_two_detached() -> None:
    sessions = [
        {"session_id": "a", "state": "watching"},
        {"session_id": "b", "state": "detached"},
        {"session_id": "c", "state": "detached"},
        {"session_id": "d", "state": "detached"},
        {"session_id": "e", "state": "sleeping"},
    ]

    limited = _limit_detached_sessions(sessions, max_detached=2)

    states = [str(item.get("state") or "") for item in limited]
    assert states.count("detached") == 2
    assert len(limited) == 4


def test_detach_session_questions_removes_current_and_pending(tmp_path: Path) -> None:
    state = _build_state(tmp_path)

    state.enqueue_question(_question_payload(question="Q1", session_id="session-a", request_id="req-a1"))
    state.enqueue_question(_question_payload(question="Q2", session_id="session-b", request_id="req-b1"))
    state.enqueue_question(_question_payload(question="Q3", session_id="session-a", request_id="req-a2"))

    result = state.detach_session_questions("session-a", reason="unit_test")
    snap = state.snapshot()

    assert result["removed_total"] == 2
    assert result["removed_current"] == 1
    assert result["removed_pending"] == 1
    assert snap["has_current_question"] is True
    assert snap["current_session_id"] == "session-b"
    assert snap["queue_depth"] == 0


def test_prune_orphaned_questions_removes_stale_unleased_question(tmp_path: Path, monkeypatch) -> None:
    state = _build_state(tmp_path)

    state.enqueue_question(_question_payload(question="Q1", session_id="session-a", request_id="req-a1"))
    assert state._current_question is not None
    state._current_question["created_at"] = _iso_ago(seconds=1200)

    monkeypatch.setattr("qa.sidecar.server._active_leased_sessions", lambda state_dir: set())

    result = state.prune_orphaned_questions(reason="unit_test")
    snap = state.snapshot()

    assert result["removed_total"] == 1
    assert snap["has_current_question"] is False
    assert snap["queue_depth"] == 0


def test_reconcile_runtime_drops_orphan_and_promotes_leased_question(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / "state"
    runtime_path = state_dir / "qa" / "sidecar.runtime.json"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": _iso_ago(seconds=0),
                "last_question_activity_at": _iso_ago(seconds=1200),
                "no_leases_since": _iso_ago(seconds=1200),
                "current_question": {
                    "question_id": "q-orphan",
                    "session_id": "session-orphan",
                    "proposal_id": "p-orphan",
                    "tool_use_id": "t-orphan",
                    "attempt": 1,
                    "question_type": "plain_english",
                    "question": "orphan",
                    "context_excerpt": "",
                    "created_at": _iso_ago(seconds=1200),
                },
                "pending": [
                    {
                        "question_id": "q-keep",
                        "session_id": "session-keep",
                        "proposal_id": "p-keep",
                        "tool_use_id": "t-keep",
                        "attempt": 1,
                        "question_type": "plain_english",
                        "question": "keep",
                        "context_excerpt": "",
                        "created_at": _iso_ago(seconds=1200),
                    }
                ],
                "answered": {},
                "request_index": {
                    "req-orphan": "q-orphan",
                    "req-keep": "q-keep",
                },
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "qa.sidecar.server._active_leased_sessions",
        lambda state_dir: {"session-keep"},
    )

    state = _build_state(tmp_path)
    snap = state.snapshot()
    persisted = json.loads(runtime_path.read_text(encoding="utf-8"))

    assert snap["has_current_question"] is True
    assert snap["current_question_id"] == "q-keep"
    assert snap["current_session_id"] == "session-keep"
    assert snap["queue_depth"] == 0
    assert persisted["request_index"] == {"req-keep": "q-keep"}


def test_snapshot_context_preview_prefers_new_code_section(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    state.enqueue_question(
        {
            "question": "Explain",
            "session_id": "session-a",
            "proposal_id": "proposal-a",
            "tool_use_id": "tool-a",
            "attempt": 1,
            "question_type": "plain_english",
            "context_excerpt": "\n".join(
                [
                    "# VibeCheck Aggregated Context",
                    "## Metadata",
                    "- proposal_id: proposal-a",
                    "## New Code",
                    "```text",
                    "def x():",
                    "    return 1",
                    "```",
                    "## Unified Diff",
                    "```diff",
                    "+def x():",
                    "+    return 1",
                    "```",
                    "## Relevant Transcript Slice",
                    "long transcript",
                ]
            ),
            "request_id": "req-preview",
        }
    )

    snap = state.snapshot()
    preview = str(snap.get("context_preview") or "")
    assert preview.startswith("## New Code")
    assert "## Relevant Transcript Slice" not in preview


def test_snapshot_context_preview_and_language_are_targeted(tmp_path: Path) -> None:
    state = _build_state(tmp_path)
    state.enqueue_question(
        {
            "question": "Why does city_lower = city.lower() matter for get_mock_current_weather?",
            "session_id": "session-a",
            "proposal_id": "proposal-a",
            "tool_use_id": "tool-a",
            "attempt": 1,
            "question_type": "faded_example",
            "context_excerpt": "\n".join(
                [
                    "# QA Context",
                    "## Metadata",
                    "- primary_path: weather_api.py",
                    "- primary_language: python",
                    "## New Code",
                    "```text",
                    "def get_mock_current_weather(city):",
                    "    city_lower = city.lower()",
                    "    return MOCK_DATA.get(city_lower)",
                    "```",
                    "## Unified Diff",
                    "```diff",
                    "+    city_lower = city.lower()",
                    "```",
                ]
            ),
            "request_id": "req-targeted",
        }
    )

    snap = state.snapshot()
    preview = str(snap.get("context_preview") or "")
    assert "city_lower = city.lower()" in preview
    assert snap.get("context_preview_language") == "python"
    assert snap.get("context_primary_path") == "weather_api.py"


def test_build_context_preview_targets_focus_window() -> None:
    excerpt = "\n".join(
        [
            "# QA Context",
            "## New Code",
            "```text",
            "def before_one():",
            "    return 1",
            "",
            "def get_mock_current_weather(city):",
            "    city_lower = city.lower()",
            "    return MOCK_DATA.get(city_lower)",
            "",
            "def after_one():",
            "    return 2",
            "```",
        ]
    )

    preview = _build_context_preview(
        excerpt,
        focus_text="Why does city_lower = city.lower() matter for get_mock_current_weather?",
        max_lines=6,
    )

    assert "city_lower = city.lower()" in preview
    assert "def get_mock_current_weather(city):" in preview


def _build_state(tmp_path: Path) -> SidecarState:
    return SidecarState(
        state_dir=tmp_path / "state",
        idle_timeout_seconds=600,
        detach_grace_seconds=180,
        answer_ttl_seconds=1800,
    )


def _question_payload(*, question: str, session_id: str, request_id: str) -> dict[str, object]:
    return {
        "question": question,
        "session_id": session_id,
        "proposal_id": f"proposal-{request_id}",
        "tool_use_id": f"tool-{request_id}",
        "attempt": 1,
        "question_type": "plain_english",
        "context_excerpt": "",
        "request_id": request_id,
    }


def _iso_ago(*, seconds: int) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
