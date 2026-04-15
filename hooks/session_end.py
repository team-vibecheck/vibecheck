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
from qa.sidecar.leases import detach_lease, prune_stale_leases
from qa.sidecar.presence import set_session_state

STATE_DIR = Path(os.environ.get("VIBECHECK_STATE_DIR", "state"))


def handle_session_end(payload: dict[str, Any], *, state_dir: Path = STATE_DIR) -> None:
    logger = EventLogger(state_dir / "logs" / "events.jsonl")
    session_id = str(payload.get("session_id") or "").strip()
    reason = str(payload.get("reason") or payload.get("source") or "session_end").strip()
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
        },
    )


def main() -> None:
    payload = read_hook_payload()
    handle_session_end(payload)


if __name__ == "__main__":
    main()
