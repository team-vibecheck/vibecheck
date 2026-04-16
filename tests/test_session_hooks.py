from __future__ import annotations

import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

from hooks.session_end import handle_session_end
from hooks.session_start import handle_session_start
from qa.sidecar.leases import count_active_leases
from qa.sidecar.presence import get_presence_snapshot


def test_session_start_attaches_lease_and_logs(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"

    monkeypatch.setattr("hooks.session_start.ensure_sidecar_running", lambda: (7865, 12345))
    monkeypatch.setattr(
        "hooks.session_start.open_ui_once_for_pid",
        lambda port, pid, state_dir, session_id="": True,
    )
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
    monkeypatch.setattr(
        "hooks.session_start.open_ui_once_for_pid",
        lambda port, pid, state_dir, session_id="": False,
    )
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


def test_session_end_requests_sidecar_queue_cleanup_when_available(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / "state"
    qa_dir = state_dir / "qa"
    qa_dir.mkdir(parents=True)
    (qa_dir / "sidecar.port").write_text("7865", encoding="utf-8")

    monkeypatch.setattr("hooks.session_start.ensure_sidecar_running", lambda: (7865, 12345))
    monkeypatch.setattr(
        "hooks.session_start.open_ui_once_for_pid",
        lambda port, pid, state_dir, session_id="": False,
    )
    handle_session_start(
        {
            "session_id": "session-1",
            "source": "startup",
        },
        state_dir=state_dir,
    )

    captured: dict[str, str | bytes | int | None] = {}

    class FakeResponse:
        status = 200

        def read(self) -> bytes:
            return b'{"removed_total":2,"removed_current":1,"removed_pending":1}'

    def fake_urlopen(request, timeout=2):  # type: ignore[no-untyped-def]
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = request.data
        return FakeResponse()

    monkeypatch.setattr("hooks.session_end.urllib.request.urlopen", fake_urlopen)

    handle_session_end(
        {
            "session_id": "session-1",
            "reason": "prompt_input_exit",
        },
        state_dir=state_dir,
    )

    assert captured["url"] == "http://127.0.0.1:7865/api/session/detach"
    body = captured.get("body")
    body_bytes = body if isinstance(body, bytes) else b"{}"
    payload = json.loads(body_bytes.decode("utf-8"))
    assert payload["session_id"] == "session-1"
    assert payload["reason"] == "prompt_input_exit"

    events = _read_events(state_dir)
    detach_events = [event for event in events if event["event"] == "sidecar_session_detached"]
    assert detach_events
    details = detach_events[-1].get("details") or {}
    assert details.get("sidecar_cleanup_attempted") is True
    assert details.get("sidecar_cleanup_ok") is True
    assert details.get("sidecar_cleanup_removed_total") == 2


def test_session_end_cleanup_is_best_effort_when_sidecar_unreachable(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / "state"
    qa_dir = state_dir / "qa"
    qa_dir.mkdir(parents=True)
    (qa_dir / "sidecar.port").write_text("7865", encoding="utf-8")

    monkeypatch.setattr("hooks.session_end.urllib.request.urlopen", _raise_http_503)

    handle_session_end(
        {
            "session_id": "session-404",
            "reason": "prompt_input_exit",
        },
        state_dir=state_dir,
    )

    events = _read_events(state_dir)
    detach_events = [event for event in events if event["event"] == "sidecar_session_detached"]
    assert detach_events
    details = detach_events[-1].get("details") or {}
    assert details.get("sidecar_cleanup_attempted") is True
    assert details.get("sidecar_cleanup_ok") is False


def test_session_start_passes_session_id_to_ui_open(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    captured: dict[str, str] = {}

    monkeypatch.setattr("hooks.session_start.ensure_sidecar_running", lambda: (7865, 12345))

    def fake_open(port: int, pid: int, *, state_dir: Path, session_id: str) -> bool:
        del port, pid, state_dir
        captured["session_id"] = session_id
        return True

    monkeypatch.setattr("hooks.session_start.open_ui_once_for_pid", fake_open)

    handle_session_start(
        {
            "session_id": "session-open-test",
            "source": "startup",
        },
        state_dir=state_dir,
    )

    assert captured.get("session_id") == "session-open-test"


def _raise_http_503(*args, **kwargs):  # type: ignore[no-untyped-def]
    del kwargs
    request = args[0]
    raise HTTPError(request.full_url, 503, "service unavailable", hdrs=Message(), fp=None)


def _read_events(state_dir: Path) -> list[dict]:
    log_path = state_dir / "logs" / "events.jsonl"
    if not log_path.exists():
        return []
    events = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events
