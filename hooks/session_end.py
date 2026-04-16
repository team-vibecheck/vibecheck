# ruff: noqa: E402

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.event_logger import EventLogger
from hooks.stdin_payload import read_hook_payload
from qa.sidecar.leases import detach_lease, prune_stale_leases
from qa.sidecar.presence import set_session_state

STATE_DIR = Path(os.environ.get("VIBECHECK_STATE_DIR", "state"))


def handle_session_end(payload: dict[str, Any], *, state_dir: Path = STATE_DIR) -> None:
    logger = EventLogger(state_dir / "logs" / "events.jsonl")
    session_id = str(payload.get("session_id") or "").strip()
    reason = str(payload.get("reason") or payload.get("source") or "session_end").strip()
    sidecar_cleanup = _notify_sidecar_detach(session_id, reason=reason, state_dir=state_dir)
    set_session_state(
        session_id,
        "detached",
        state_dir=state_dir,
        detail=f"Session ended: {reason}",
        make_active=False,
    )
    detach = detach_lease(session_id, reason=reason, state_dir=state_dir)
    pruned = prune_stale_leases(state_dir=state_dir)
    logger.log(
        "sidecar_session_detached",
        session_id=session_id,
        status="ok",
        details={
            "reason": reason,
            "removed": detach.get("removed", False),
            "active_leases": detach.get("active_count", 0),
            "stale_pruned": len(pruned),
            "sidecar_cleanup_attempted": sidecar_cleanup.get("attempted", False),
            "sidecar_cleanup_ok": sidecar_cleanup.get("ok", False),
            "sidecar_cleanup_removed_total": sidecar_cleanup.get("removed_total", 0),
            "sidecar_cleanup_removed_current": sidecar_cleanup.get("removed_current", 0),
            "sidecar_cleanup_removed_pending": sidecar_cleanup.get("removed_pending", 0),
            "sidecar_cleanup_error": sidecar_cleanup.get("error", ""),
        },
    )


def _notify_sidecar_detach(session_id: str, *, reason: str, state_dir: Path) -> dict[str, Any]:
    if not session_id.strip():
        return {"attempted": False, "ok": False, "error": "missing_session_id"}

    port_path = state_dir / "qa" / "sidecar.port"
    if not port_path.exists():
        return {"attempted": False, "ok": False, "error": "port_file_missing"}

    try:
        port = int(port_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return {"attempted": False, "ok": False, "error": "invalid_port_file"}

    body = json.dumps({"session_id": session_id, "reason": reason}, separators=(",", ":")).encode(
        "utf-8"
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/session/detach",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        response = urllib.request.urlopen(request, timeout=2)
        payload = response.read().decode("utf-8", errors="replace")
        decoded = json.loads(payload) if payload else {}
        if not isinstance(decoded, dict):
            decoded = {}
        return {
            "attempted": True,
            "ok": response.status == 200,
            "port": port,
            "removed_total": int(decoded.get("removed_total") or 0),
            "removed_current": int(decoded.get("removed_current") or 0),
            "removed_pending": int(decoded.get("removed_pending") or 0),
        }
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        return {
            "attempted": True,
            "ok": False,
            "port": port,
            "error": payload or exc.reason,
            "status_code": exc.code,
        }
    except (urllib.error.URLError, TimeoutError) as exc:
        return {
            "attempted": True,
            "ok": False,
            "port": port,
            "error": str(getattr(exc, "reason", exc)),
        }
    except ValueError as exc:
        return {
            "attempted": True,
            "ok": False,
            "port": port,
            "error": f"invalid_json: {exc}",
        }


def main() -> None:
    payload = read_hook_payload()
    handle_session_end(payload)


if __name__ == "__main__":
    main()
