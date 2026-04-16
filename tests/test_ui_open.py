from __future__ import annotations

from pathlib import Path

from qa.sidecar.ui_open import open_ui_once_for_pid


def test_open_ui_once_for_pid_runs_once_per_pid(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    opened: list[str] = []

    monkeypatch.setattr("qa.sidecar.ui_open._open_url", lambda url: opened.append(url) or True)

    first = open_ui_once_for_pid(7865, 111, state_dir=state_dir)
    second = open_ui_once_for_pid(7865, 111, state_dir=state_dir)
    third = open_ui_once_for_pid(7865, 222, state_dir=state_dir)

    assert first is True
    assert second is False
    assert third is True
    assert len(opened) == 2
    assert opened[0] == "http://127.0.0.1:7865/"


def test_open_ui_once_for_pid_opens_for_new_session_same_pid(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    opened: list[str] = []

    monkeypatch.setattr("qa.sidecar.ui_open._open_url", lambda url: opened.append(url) or True)

    first = open_ui_once_for_pid(7865, 111, state_dir=state_dir, session_id="session-a")
    second = open_ui_once_for_pid(7865, 111, state_dir=state_dir, session_id="session-b")

    assert first is True
    assert second is True
    assert len(opened) == 2


def test_open_ui_once_for_pid_ignores_invalid_pid(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    assert open_ui_once_for_pid(7865, -1, state_dir=state_dir) is False


def test_open_ui_can_reopen_after_cooldown(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    opened: list[str] = []
    current_time = {"value": 1000}

    monkeypatch.setattr("qa.sidecar.ui_open._open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr("qa.sidecar.ui_open.time.time", lambda: current_time["value"])

    first = open_ui_once_for_pid(7865, 111, state_dir=state_dir)

    current_time["value"] = 1005
    second = open_ui_once_for_pid(
        7865,
        111,
        state_dir=state_dir,
        allow_reopen_after_seconds=10,
    )

    current_time["value"] = 1012
    third = open_ui_once_for_pid(
        7865,
        111,
        state_dir=state_dir,
        allow_reopen_after_seconds=10,
    )

    assert first is True
    assert second is False
    assert third is True
    assert len(opened) == 2
