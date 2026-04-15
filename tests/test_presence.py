from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from qa.sidecar.presence import get_presence_snapshot, set_session_state


def test_presence_tracks_active_and_retains_recent_sessions(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    set_session_state("session-a", "watching", state_dir=state_dir, detail="Prompt submit")
    set_session_state("session-b", "gate_thinking", state_dir=state_dir, detail="Gate")

    snap = get_presence_snapshot(state_dir=state_dir)
    assert snap["session_count"] == 2
    assert snap["active_session_id"] == "session-b"
    active = snap["active_session"]
    assert isinstance(active, dict)
    assert active["state"] == "gate_thinking"
    assert active["emoji"] == "🤔"


def test_presence_updates_metadata(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    set_session_state(
        "session-a",
        "qa_waiting_submission",
        state_dir=state_dir,
        proposal_id="proposal-1",
        tool_use_id="tool-1",
        attempt_number=2,
        question_id="q-123",
        queue_depth=4,
    )

    snap = get_presence_snapshot(state_dir=state_dir)
    active = snap["active_session"]
    assert isinstance(active, dict)
    assert active["proposal_id"] == "proposal-1"
    assert active["tool_use_id"] == "tool-1"
    assert active["attempt_number"] == 2
    assert active["question_id"] == "q-123"
    assert active["queue_depth"] == 4


def test_presence_overwrites_transient_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    set_session_state("session-a", "qa_evaluating", state_dir=state_dir, detail="old")
    set_session_state("session-a", "watching", state_dir=state_dir, detail="new")

    snap = get_presence_snapshot(state_dir=state_dir)
    active = snap["active_session"]
    assert isinstance(active, dict)
    assert active["state"] == "watching"
    assert active["detail"] == "new"


def test_presence_auto_resets_transient_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    set_session_state(
        "session-a",
        "gate_allow",
        state_dir=state_dir,
        detail="Allowed",
        auto_reset_after_seconds=4,
        auto_reset_to="sleeping",
    )

    presence_path = state_dir / "qa" / "sidecar.presence.json"
    payload = json.loads(presence_path.read_text(encoding="utf-8"))
    payload["sessions"]["session-a"]["auto_reset_at"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    presence_path.write_text(json.dumps(payload), encoding="utf-8")

    snap = get_presence_snapshot(state_dir=state_dir)
    active = snap["active_session"]
    assert isinstance(active, dict)
    assert active["state"] == "sleeping"
    assert active["detail"] == "Waiting for agent loop"
