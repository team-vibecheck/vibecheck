from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeGuard

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

_STATE_DIR = Path(os.environ.get("VIBECHECK_STATE_DIR", "state"))
_SIDECAR_DIR = _STATE_DIR / "qa"

ENV_PORT = "VIBECHECK_SIDECAR_PORT"
ENV_TIMEOUT = "VIBECHECK_SIDECAR_TIMEOUT"
ENV_IDLE_TIMEOUT = "VIBECHECK_SIDECAR_IDLE_TIMEOUT"
ENV_POLL_INTERVAL = "VIBECHECK_SIDECAR_POLL_INTERVAL"
ENV_STARTUP_TIMEOUT = "VIBECHECK_SIDECAR_STARTUP_TIMEOUT"

DEFAULT_PORT = 7865
DEFAULT_TIMEOUT = 540
DEFAULT_IDLE_TIMEOUT = 1800
DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_STARTUP_TIMEOUT = 20.0
_HEALTH_COMPAT_KEY = "current_question_id"
_HEALTH_MIN_COMPAT_VERSION = 2


def get_config() -> dict[str, int | float]:
    return {
        "port": int(os.environ.get(ENV_PORT, DEFAULT_PORT)),
        "timeout": int(os.environ.get(ENV_TIMEOUT, DEFAULT_TIMEOUT)),
        "idle_timeout": int(os.environ.get(ENV_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT)),
        "poll_interval": float(os.environ.get(ENV_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)),
        "startup_timeout": float(os.environ.get(ENV_STARTUP_TIMEOUT, DEFAULT_STARTUP_TIMEOUT)),
    }


def find_available_port(start: int = 7865, max_attempts: int = 100) -> int:
    for port in range(start, start + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    msg = f"Could not find available port in range {start}-{start + max_attempts - 1}"
    raise RuntimeError(msg)


def write_port_file(port: int) -> None:
    _SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    (_SIDECAR_DIR / "sidecar.port").write_text(str(port), encoding="utf-8")


def read_port_file() -> int | None:
    return _read_int_file(_SIDECAR_DIR / "sidecar.port")


def resolve_port(preferred: int) -> tuple[int, bool]:
    saved_port = read_port_file()
    if saved_port is not None:
        saved_health = _query_health(saved_port)
        if _is_compatible_health(saved_health):
            return saved_port, False
        if saved_health is None and _is_port_available(saved_port):
            return saved_port, False

    preferred_health = _query_health(preferred)
    if _is_compatible_health(preferred_health):
        write_port_file(preferred)
        return preferred, False

    if preferred_health is None and _is_port_available(preferred):
        write_port_file(preferred)
        return preferred, True

    port = find_available_port(preferred)
    write_port_file(port)
    return port, True


def _is_port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
            return True
    except OSError:
        return False


def write_pid_file(pid: int) -> None:
    _SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    (_SIDECAR_DIR / "sidecar.pid").write_text(str(pid), encoding="utf-8")


def read_pid_file() -> int | None:
    return _read_int_file(_SIDECAR_DIR / "sidecar.pid")


def is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cleanup_pid_file() -> None:
    pid_path = _SIDECAR_DIR / "sidecar.pid"
    if pid_path.exists():
        pid_path.unlink()


def cleanup_port_file() -> None:
    port_path = _SIDECAR_DIR / "sidecar.port"
    if port_path.exists():
        port_path.unlink()


def check_health(port: int) -> bool:
    return _query_health(port) is not None


def spawn_sidecar(port: int) -> subprocess.Popen[bytes]:
    cleanup_pid_file()
    _SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    python_exe = sys.executable
    script_path = Path(__file__).parent / "server.py"
    env = os.environ.copy()
    env["VIBECHECK_SIDECAR_PORT"] = str(port)
    env["VIBECHECK_STATE_DIR"] = str(_STATE_DIR)
    stderr_log = (_SIDECAR_DIR / "sidecar.stderr.log").open("a", encoding="utf-8")
    proc = subprocess.Popen(
        [python_exe, str(script_path)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=stderr_log,
        start_new_session=True,
    )
    stderr_log.close()
    write_pid_file(proc.pid)
    return proc


def shutdown_sidecar(port: int, *, force: bool = False) -> bool:
    try:
        query = "?force=1" if force else ""
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/shutdown{query}", timeout=5)
        return req.status in (200, 204)
    except Exception:
        return False


def ensure_sidecar_running() -> tuple[int, int]:
    preferred = int(get_config()["port"])
    startup_timeout = float(get_config()["startup_timeout"])

    with _lifecycle_lock():
        port, _ = resolve_port(preferred)
        health = _query_health(port)
        if _is_compatible_health(health):
            compatible_health = health
            running_pid = _as_int(compatible_health.get("pid")) or read_pid_file() or -1
            if running_pid > 0:
                write_pid_file(running_pid)
            return port, running_pid

        if health is not None:
            _recycle_incompatible_sidecar(port, health)
            cleanup_pid_file()
            cleanup_port_file()
            port, _ = resolve_port(preferred)
            health = _query_health(port)
            if _is_compatible_health(health):
                compatible_health = health
                running_pid = _as_int(compatible_health.get("pid")) or read_pid_file() or -1
                if running_pid > 0:
                    write_pid_file(running_pid)
                return port, running_pid

        pid = read_pid_file()
        if pid is not None and is_process_alive(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, 15)
            _wait_for_pid_exit(pid, timeout_seconds=1.5)

        cleanup_pid_file()
        cleanup_port_file()
        port, _ = resolve_port(preferred)

        proc = spawn_sidecar(port)
        deadline = time.monotonic() + max(startup_timeout, 0.5)
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                cleanup_pid_file()
                cleanup_port_file()
                msg = f"Sidecar exited early with code {proc.returncode} on port {port}"
                raise RuntimeError(msg)

            health = _query_health(port)
            if health is not None:
                running_pid = _as_int(health.get("pid")) or proc.pid
                write_pid_file(running_pid)
                return port, running_pid
            time.sleep(0.25)

        _terminate_process(proc)
        cleanup_pid_file()
        msg = f"Sidecar failed to start on port {port}"
        raise RuntimeError(msg)


def _read_int_file(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        with contextlib.suppress(OSError):
            path.unlink()
        return None


def _query_health(port: int) -> dict[str, Any] | None:
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
        if req.status != 200:
            return None
        payload = req.read().decode("utf-8", errors="replace").strip()
        if not payload:
            return {}
        decoded = json.loads(payload)
        if isinstance(decoded, dict):
            return decoded
        return {}
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None
    except Exception:
        return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        with contextlib.suppress(ValueError):
            return int(value)
    return None


def _is_compatible_health(health: dict[str, Any] | None) -> TypeGuard[dict[str, Any]]:
    if not (isinstance(health, dict) and _HEALTH_COMPAT_KEY in health):
        return False

    compat_version = _as_int(health.get("compat_version"))
    if compat_version is None:
        return False

    return compat_version >= _HEALTH_MIN_COMPAT_VERSION


def _recycle_incompatible_sidecar(port: int, health: dict[str, Any] | None) -> None:
    if shutdown_sidecar(port, force=True) and _wait_for_port_unhealthy(port, timeout_seconds=2.0):
        return

    pid = _as_int((health or {}).get("pid")) or read_pid_file()
    if pid is not None and is_process_alive(pid):
        with contextlib.suppress(OSError):
            os.kill(pid, 15)
        if not _wait_for_pid_exit(pid, timeout_seconds=2.0):
            with contextlib.suppress(OSError):
                os.kill(pid, 9)
            _wait_for_pid_exit(pid, timeout_seconds=1.0)

    _wait_for_port_unhealthy(port, timeout_seconds=1.0)


@contextmanager
def _lifecycle_lock() -> Any:
    _SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _SIDECAR_DIR / "sidecar.lifecycle.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return

    with contextlib.suppress(OSError):
        proc.terminate()

    try:
        proc.wait(timeout=1.5)
        return
    except subprocess.TimeoutExpired:
        pass

    with contextlib.suppress(OSError):
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=1.0)


def _wait_for_pid_exit(pid: int, *, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not is_process_alive(pid):
            return True
        time.sleep(0.05)
    return not is_process_alive(pid)


def _wait_for_port_unhealthy(port: int, *, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _query_health(port) is None:
            return True
        time.sleep(0.05)
    return _query_health(port) is None
