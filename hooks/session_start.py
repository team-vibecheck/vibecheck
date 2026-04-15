# ruff: noqa: E402

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.event_logger import EventLogger
from hooks.stdin_payload import read_hook_payload
from qa.sidecar.leases import attach_lease, prune_stale_leases
from qa.sidecar.lifecycle import ensure_sidecar_running
from qa.sidecar.presence import set_session_state
from qa.sidecar.ui_open import open_ui_once_for_pid

STATE_DIR = Path(os.environ.get("VIBECHECK_STATE_DIR", "state"))


def handle_session_start(payload: dict[str, Any], *, state_dir: Path = STATE_DIR) -> None:
    logger = EventLogger(state_dir / "logs" / "events.jsonl")
    session_id = str(payload.get("session_id") or "").strip()
    source = str(payload.get("source") or "startup").strip() or "startup"

    set_session_state(
        session_id,
        "starting",
        state_dir=state_dir,
        detail=f"Session start ({source})",
    )

    lease = attach_lease(session_id, source=f"SessionStart:{source}", state_dir=state_dir)
    pruned = prune_stale_leases(state_dir=state_dir)
    logger.log(
        "sidecar_session_attached",
        session_id=session_id,
        status="ok",
        details={
            "source": source,
            "active_leases": lease.get("active_count", 0),
            "stale_pruned": len(pruned),
        },
    )

    try:
        port, pid = ensure_sidecar_running()
        set_session_state(
            session_id,
            "sleeping",
            state_dir=state_dir,
            detail="Waiting for agent loop",
        )
        opened = open_ui_once_for_pid(port, pid, state_dir=state_dir)
        logger.log(
            "sidecar_prewarm_ok",
            session_id=session_id,
            status="ok",
            details={"port": port, "pid": pid, "browser_opened": opened},
        )
    except Exception as exc:  # noqa: BLE001
        set_session_state(
            session_id,
            "error",
            state_dir=state_dir,
            detail=f"Prewarm failed: {type(exc).__name__}",
        )
        logger.log(
            "sidecar_prewarm_failed",
            session_id=session_id,
            status="error",
            details={"error_type": type(exc).__name__, "error": str(exc)},
        )


def main() -> None:
    payload = read_hook_payload()
    handle_session_start(payload)


if __name__ == "__main__":
    main()
