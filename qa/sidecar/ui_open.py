from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Any

ENV_BROWSER_CMD = "VIBECHECK_SIDECAR_BROWSER_CMD"


def open_ui_once_for_pid(
    port: int,
    pid: int,
    *,
    state_dir: Path,
    allow_reopen_after_seconds: int | None = None,
) -> bool:
    if pid <= 0:
        return False

    tracker = _tracker_path(state_dir)
    tracker_payload = _read_tracker(tracker)
    seen_pid = _as_int(tracker_payload.get("last_opened_pid"))
    last_opened_at = _as_int(tracker_payload.get("last_opened_at")) or 0

    if allow_reopen_after_seconds is None and seen_pid == pid:
        return False

    if allow_reopen_after_seconds is not None:
        now = int(time.time())
        elapsed = now - last_opened_at
        if elapsed < max(allow_reopen_after_seconds, 0):
            return False

    url = f"http://127.0.0.1:{port}/"
    opened = _open_url(url)
    _write_tracker(tracker, pid)
    return opened


def _open_url(url: str) -> bool:
    explicit_cmd = os.environ.get(ENV_BROWSER_CMD, "").strip()
    if explicit_cmd:
        cmd = explicit_cmd.replace("{url}", url)
        with contextlib.suppress(Exception):
            subprocess.Popen(shlex.split(cmd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True

    with contextlib.suppress(Exception):
        if webbrowser.open(url, new=1):
            return True

    with contextlib.suppress(Exception):
        subprocess.run(
            ["xdg-open", url],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True

    return False


def _read_tracker(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _write_tracker(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "last_opened_pid": pid,
        "last_opened_at": int(time.time()),
    }
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp_path.replace(path)


def _tracker_path(state_dir: Path) -> Path:
    return state_dir / "qa" / "sidecar.ui.json"


def _as_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        with contextlib.suppress(ValueError):
            return int(value)
    return None
