from __future__ import annotations

import json
from pathlib import Path

from qa.sidecar import leases


def test_attach_heartbeat_and_detach_cycle(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"

    attached = leases.attach_lease("session-a", source="test", state_dir=state_dir)
    assert attached["updated"] is True
    assert attached["active_count"] == 1

    beat = leases.heartbeat_lease("session-a", source="test", state_dir=state_dir)
    assert beat["updated"] is True
    assert beat["active_count"] == 1

    listed = leases.list_active_leases(state_dir=state_dir)
    assert len(listed) == 1
    assert listed[0]["session_id"] == "session-a"

    detached = leases.detach_lease("session-a", reason="done", state_dir=state_dir)
    assert detached["removed"] is True
    assert detached["active_count"] == 0
    assert leases.count_active_leases(state_dir=state_dir) == 0


def test_prune_stale_leases(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"

    leases.attach_lease("session-a", source="test", state_dir=state_dir)
    path = state_dir / "qa" / "sidecar.leases.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["leases"]["session-a"]["last_heartbeat_at"] = "2000-01-01T00:00:00Z"
    path.write_text(json.dumps(payload), encoding="utf-8")

    removed = leases.prune_stale_leases(ttl_seconds=1, state_dir=state_dir)
    assert removed == ["session-a"]
    assert leases.count_active_leases(state_dir=state_dir) == 0
