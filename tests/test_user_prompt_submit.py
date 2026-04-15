from __future__ import annotations

import json
from pathlib import Path

from hooks.user_prompt_submit import handle_user_prompt_submit
from qa.sidecar.presence import get_presence_snapshot


def test_user_prompt_submit_updates_presence_and_logs(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    handle_user_prompt_submit({"session_id": "session-xyz"}, state_dir=state_dir)

    snap = get_presence_snapshot(state_dir=state_dir)
    active = snap["active_session"]
    assert isinstance(active, dict)
    assert active["session_id"] == "session-xyz"
    assert active["state"] == "watching"

    events = _read_events(state_dir)
    assert any(event["event"] == "sidecar_user_prompt_submit" for event in events)


def _read_events(state_dir: Path) -> list[dict]:
    log_path = state_dir / "logs" / "events.jsonl"
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
