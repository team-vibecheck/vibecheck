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
from qa.sidecar.presence import set_session_state

STATE_DIR = Path(os.environ.get("VIBECHECK_STATE_DIR", "state"))


def handle_user_prompt_submit(payload: dict[str, Any], *, state_dir: Path = STATE_DIR) -> None:
    logger = EventLogger(state_dir / "logs" / "events.jsonl")
    session_id = str(payload.get("session_id") or "").strip()
    set_session_state(
        session_id,
        "watching",
        state_dir=state_dir,
        detail="User prompt submitted",
    )
    logger.log(
        "sidecar_user_prompt_submit",
        session_id=session_id,
        status="ok",
    )


def main() -> None:
    payload = read_hook_payload()
    handle_user_prompt_submit(payload)


if __name__ == "__main__":
    main()
