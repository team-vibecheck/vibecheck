from __future__ import annotations

import json
from pathlib import Path

from hooks.session_end import handle_session_end
from hooks.session_start import handle_session_start
from qa.sidecar.leases import count_active_leases
from qa.sidecar.presence import get_presence_snapshot


def test_session_start_attaches_lease_and_logs(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"

    monkeypatch.setattr("hooks.session_start.ensure_sidecar_running", lambda: (7865, 12345))
    monkeypatch.setattr("hooks.session_start.open_ui_once_for_pid", lambda port, pid, state_dir: True)
    handle_session_start(
        {
            "session_id": "session-1",
            "source": "startup",
        },
        state_dir=state_dir,
    )

    assert count_active_leases(state_dir=state_dir) == 1
    events = _read_events(state_dir)
    event_names = [event["event"] for event in events]
    assert "sidecar_session_attached" in event_names
    assert "sidecar_prewarm_ok" in event_names
    presence = get_presence_snapshot(state_dir=state_dir)
    active = presence["active_session"]
    assert isinstance(active, dict)
    assert active["state"] == "sleeping"


def test_session_end_detaches_lease_and_logs(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"

    monkeypatch.setattr("hooks.session_start.ensure_sidecar_running", lambda: (7865, 12345))
    monkeypatch.setattr("hooks.session_start.open_ui_once_for_pid", lambda port, pid, state_dir: False)
    handle_session_start(
        {
            "session_id": "session-1",
            "source": "startup",
        },
        state_dir=state_dir,
    )
    assert count_active_leases(state_dir=state_dir) == 1

    handle_session_end(
        {
            "session_id": "session-1",
            "reason": "prompt_input_exit",
        },
        state_dir=state_dir,
    )

    assert count_active_leases(state_dir=state_dir) == 0
    events = _read_events(state_dir)
    event_names = [event["event"] for event in events]
    assert "sidecar_session_detached" in event_names


def _read_events(state_dir: Path) -> list[dict]:
    log_path = state_dir / "logs" / "events.jsonl"
    if not log_path.exists():
        return []
    events = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events
