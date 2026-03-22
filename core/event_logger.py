"""Append-only JSONL event logger for VibeCheck lifecycle events."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EventLogger:
    """Appends structured events to a JSONL file."""

    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._log_path

    def log(
        self,
        event: str,
        *,
        proposal_id: str = "",
        session_id: str = "",
        tool_name: str = "",
        status: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "timestamp": _utc_now_iso(),
            "event": event,
        }
        if proposal_id:
            record["proposal_id"] = proposal_id
        if session_id:
            record["session_id"] = session_id
        if tool_name:
            record["tool_name"] = tool_name
        if status:
            record["status"] = status
        if details:
            record["details"] = details

        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")

    def read_events(self) -> list[dict[str, Any]]:
        """Read all events from the log file. Useful for testing."""
        if not self._log_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self._log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
        return events


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
