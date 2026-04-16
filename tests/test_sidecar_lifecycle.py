from __future__ import annotations

from pathlib import Path

import qa.sidecar.lifecycle as lifecycle


class DummyResponse:
    def __init__(self, *, status: int = 200, body: str = "{}") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body.encode("utf-8")


def test_resolve_port_reuses_healthy_saved_port(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    qa_dir = state_dir / "qa"
    qa_dir.mkdir(parents=True)
    (qa_dir / "sidecar.port").write_text("7867", encoding="utf-8")
    monkeypatch.setenv("VIBECHECK_STATE_DIR", str(state_dir))

    monkeypatch.setattr(lifecycle, "_STATE_DIR", state_dir)
    monkeypatch.setattr(lifecycle, "_SIDECAR_DIR", qa_dir)
    monkeypatch.setattr(
        lifecycle,
        "_query_health",
        lambda port: {"status": "ok", "current_question_id": None, "compat_version": 2}
        if port == 7867
        else None,
    )
    monkeypatch.setattr(lifecycle, "_is_port_available", lambda port: False)

    port, was_new = lifecycle.resolve_port(7865)
    assert port == 7867
    assert was_new is False


def test_ensure_sidecar_running_kills_timed_out_process(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    qa_dir = state_dir / "qa"
    qa_dir.mkdir(parents=True)
    monkeypatch.setenv("VIBECHECK_STATE_DIR", str(state_dir))

    monkeypatch.setattr(lifecycle, "_STATE_DIR", state_dir)
    monkeypatch.setattr(lifecycle, "_SIDECAR_DIR", qa_dir)

    class Proc:
        def __init__(self) -> None:
            self.pid = 4242
            self.returncode = None
            self.terminated = False
            self.killed = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            del timeout
            return None

        def kill(self):
            self.killed = True

    proc = Proc()

    monkeypatch.setattr(lifecycle, "resolve_port", lambda preferred: (7865, True))
    monkeypatch.setattr(lifecycle, "read_pid_file", lambda: None)
    monkeypatch.setattr(lifecycle, "cleanup_pid_file", lambda: None)
    monkeypatch.setattr(lifecycle, "cleanup_port_file", lambda: None)
    monkeypatch.setattr(lifecycle, "spawn_sidecar", lambda port: proc)
    monkeypatch.setattr(lifecycle, "_query_health", lambda port: None)
    monkeypatch.setattr(lifecycle, "_terminate_process", lambda p: setattr(p, "terminated", True))
    monkeypatch.setattr(
        lifecycle,
        "get_config",
        lambda: {
            "port": 7865,
            "timeout": 540,
            "idle_timeout": 1800,
            "poll_interval": 0.5,
            "startup_timeout": 0.6,
        },
    )

    import time

    monkeypatch.setattr(time, "sleep", lambda _: None)

    try:
        lifecycle.ensure_sidecar_running()
    except RuntimeError as exc:
        assert "failed to start" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("Expected startup failure")

    assert proc.terminated is True


def test_ensure_sidecar_running_adopts_health_pid(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    qa_dir = state_dir / "qa"
    qa_dir.mkdir(parents=True)
    monkeypatch.setenv("VIBECHECK_STATE_DIR", str(state_dir))

    monkeypatch.setattr(lifecycle, "_STATE_DIR", state_dir)
    monkeypatch.setattr(lifecycle, "_SIDECAR_DIR", qa_dir)
    monkeypatch.setattr(lifecycle, "resolve_port", lambda preferred: (7865, False))
    monkeypatch.setattr(
        lifecycle,
        "_query_health",
        lambda port: {"status": "ok", "pid": 9999, "current_question_id": None, "compat_version": 2},
    )
    monkeypatch.setattr(
        lifecycle,
        "get_config",
        lambda: {
            "port": 7865,
            "timeout": 540,
            "idle_timeout": 1800,
            "poll_interval": 0.5,
            "startup_timeout": 20.0,
        },
    )

    port, pid = lifecycle.ensure_sidecar_running()
    assert port == 7865
    assert pid == 9999
    assert (qa_dir / "sidecar.pid").read_text(encoding="utf-8").strip() == "9999"


def test_spawn_sidecar_passes_idle_and_grace_env(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    qa_dir = state_dir / "qa"
    qa_dir.mkdir(parents=True)
    monkeypatch.setenv("VIBECHECK_STATE_DIR", str(state_dir))
    monkeypatch.setenv("VIBECHECK_SIDECAR_IDLE_TIMEOUT", "600")
    monkeypatch.setenv("VIBECHECK_SIDECAR_DETACH_GRACE", "180")

    monkeypatch.setattr(lifecycle, "_STATE_DIR", state_dir)
    monkeypatch.setattr(lifecycle, "_SIDECAR_DIR", qa_dir)

    captured: dict[str, object] = {}

    class Proc:
        pid = 4321

    def fake_popen(cmd, env=None, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["env"] = dict(env or {})
        return Proc()

    monkeypatch.setattr(lifecycle.subprocess, "Popen", fake_popen)

    proc = lifecycle.spawn_sidecar(7865)
    assert proc.pid == 4321
    env = captured.get("env")
    assert isinstance(env, dict)
    assert env["VIBECHECK_SIDECAR_PORT"] == "7865"
    assert env["VIBECHECK_STATE_DIR"] == str(state_dir)
    assert env["VIBECHECK_SIDECAR_IDLE_TIMEOUT"] == "600"
    assert env["VIBECHECK_SIDECAR_DETACH_GRACE"] == "180"


def test_incompatible_health_recycled_then_spawned(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "state"
    qa_dir = state_dir / "qa"
    qa_dir.mkdir(parents=True)
    monkeypatch.setenv("VIBECHECK_STATE_DIR", str(state_dir))

    monkeypatch.setattr(lifecycle, "_STATE_DIR", state_dir)
    monkeypatch.setattr(lifecycle, "_SIDECAR_DIR", qa_dir)
    monkeypatch.setattr(
        lifecycle,
        "get_config",
        lambda: {
            "port": 7865,
            "timeout": 540,
            "idle_timeout": 1800,
            "poll_interval": 0.5,
            "startup_timeout": 1.0,
        },
    )

    calls = {"query": 0}

    def fake_query(port: int):
        del port
        calls["query"] += 1
        if calls["query"] <= 2:
            return {"status": "ok", "pid": 7777}
        if calls["query"] == 3:
            return None
        return {"status": "ok", "pid": 8888, "current_question_id": None, "compat_version": 2}

    class Proc:
        pid = 8888

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            del timeout
            return None

        def kill(self):
            return None

    monkeypatch.setattr(lifecycle, "resolve_port", lambda preferred: (7865, False))
    monkeypatch.setattr(lifecycle, "_query_health", fake_query)
    monkeypatch.setattr(lifecycle, "shutdown_sidecar", lambda port, force=False: False)
    monkeypatch.setattr(lifecycle, "read_pid_file", lambda: None)
    monkeypatch.setattr(lifecycle, "cleanup_pid_file", lambda: None)
    monkeypatch.setattr(lifecycle, "cleanup_port_file", lambda: None)
    monkeypatch.setattr(lifecycle, "spawn_sidecar", lambda port: Proc())
    monkeypatch.setattr(lifecycle, "is_process_alive", lambda pid: False)

    import time

    monkeypatch.setattr(time, "sleep", lambda _: None)

    port, pid = lifecycle.ensure_sidecar_running()
    assert port == 7865
    assert pid == 8888


def test_health_without_compat_version_is_incompatible() -> None:
    assert lifecycle._is_compatible_health({"status": "ok", "current_question_id": None}) is False


def test_health_with_old_compat_version_is_incompatible() -> None:
    assert (
        lifecycle._is_compatible_health(
            {"status": "ok", "current_question_id": None, "compat_version": 1}
        )
        is False
    )


def test_health_with_required_compat_version_is_compatible() -> None:
    assert (
        lifecycle._is_compatible_health(
            {"status": "ok", "current_question_id": None, "compat_version": 2}
        )
        is True
    )
