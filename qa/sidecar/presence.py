from __future__ import annotations

import contextlib
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

from qa.sidecar.leases import list_active_leases

ENV_SESSION_RETENTION = "VIBECHECK_SIDECAR_SESSION_RETENTION"
ENV_INACTIVE_STATE_GRACE = "VIBECHECK_SIDECAR_INACTIVE_STATE_GRACE"
DEFAULT_SESSION_RETENTION_SECONDS = 3600
DEFAULT_INACTIVE_STATE_GRACE_SECONDS = 90

_INACTIVE_TO_DETACHED_STATES = {
    "starting",
    "watching",
    "gate_thinking",
    "gate_allow",
    "gate_block",
    "qa_waiting_submission",
    "qa_evaluating",
    "qa_pass",
    "qa_fail_attempt",
    "qa_fail_terminal",
    "recovering",
    "error",
}

_STATE_META: dict[str, dict[str, str]] = {
    "starting": {"emoji": "🚀", "label": "Starting"},
    "sleeping": {"emoji": "😴", "label": "Sleeping"},
    "watching": {"emoji": "👀", "label": "Watching"},
    "gate_thinking": {"emoji": "🤔", "label": "Gate Thinking"},
    "gate_allow": {"emoji": "🙂", "label": "Gate Allow"},
    "gate_block": {"emoji": "🤨🙅", "label": "Gate Block"},
    "qa_waiting_submission": {"emoji": "✍️", "label": "Waiting Submission"},
    "qa_evaluating": {"emoji": "🤔", "label": "Evaluating"},
    "qa_pass": {"emoji": "✅", "label": "Passed"},
    "qa_fail_attempt": {"emoji": "❌", "label": "Failed Attempt"},
    "qa_fail_terminal": {"emoji": "😞", "label": "Failed"},
    "recovering": {"emoji": "🔄", "label": "Recovering"},
    "error": {"emoji": "⚠️", "label": "Error"},
    "detached": {"emoji": "👋", "label": "Detached"},
}


def set_session_state(
    session_id: str,
    state_key: str,
    *,
    state_dir: Path | None = None,
    detail: str = "",
    proposal_id: str = "",
    tool_use_id: str = "",
    attempt_number: int | None = None,
    question_id: str = "",
    queue_depth: int | None = None,
    make_active: bool = True,
    auto_reset_after_seconds: int | None = None,
    auto_reset_to: str | None = None,
) -> dict[str, Any]:
    session = session_id.strip()
    if not session:
        return {"updated": False}

    with _presence_lock(state_dir):
        payload = _read_presence_file(state_dir)
        active_sessions = _active_session_ids(state_dir)
        _prune_inactive(payload, active_sessions)

        now_iso = _utc_now_iso()
        state = _state_payload(state_key)
        existing = payload["sessions"].get(session)
        if not isinstance(existing, dict):
            existing = {"session_id": session}
        existing_any = cast(dict[str, Any], existing)
        mutable_existing: dict[str, Any] = existing_any

        mutable_existing.update(
            {
                "session_id": session,
                "state": state_key,
                "emoji": state["emoji"],
                "label": state["label"],
                "detail": detail or str(mutable_existing.get("detail") or ""),
                "proposal_id": proposal_id or str(mutable_existing.get("proposal_id") or ""),
                "tool_use_id": tool_use_id or str(mutable_existing.get("tool_use_id") or ""),
                "question_id": question_id or str(mutable_existing.get("question_id") or ""),
                "updated_at": now_iso,
            }
        )

        if auto_reset_after_seconds is not None and auto_reset_to:
            reset_seconds = max(auto_reset_after_seconds, 0)
            mutable_existing["auto_reset_from"] = state_key
            mutable_existing["auto_reset_to"] = auto_reset_to
            mutable_existing["auto_reset_at"] = _iso_after_seconds(reset_seconds)
        else:
            mutable_existing.pop("auto_reset_from", None)
            mutable_existing.pop("auto_reset_to", None)
            mutable_existing.pop("auto_reset_at", None)

        if attempt_number is not None:
            mutable_existing["attempt_number"] = attempt_number
        if queue_depth is not None:
            mutable_existing["queue_depth"] = queue_depth

        payload["sessions"][session] = mutable_existing
        if make_active or not str(payload.get("active_session_id") or "").strip():
            payload["active_session_id"] = session

        payload["updated_at"] = now_iso
        _write_presence_file(payload, state_dir)

        return {
            "updated": True,
            "active_session_id": payload.get("active_session_id") or "",
            "session_count": len(payload["sessions"]),
        }


def get_presence_snapshot(*, state_dir: Path | None = None) -> dict[str, Any]:
    with _presence_lock(state_dir):
        payload = _read_presence_file(state_dir)
        active_sessions = _active_session_ids(state_dir)
        changed = _prune_inactive(payload, active_sessions)
        changed = _apply_auto_resets(payload) or changed

        active_session_id = str(payload.get("active_session_id") or "").strip()
        if active_session_id and active_session_id not in payload["sessions"]:
            active_session_id = ""

        if not active_session_id and payload["sessions"]:
            active_session_id = _pick_recent_session_id(payload["sessions"])
            payload["active_session_id"] = active_session_id
            changed = True

        now = datetime.now(UTC)
        sessions: list[dict[str, Any]] = []
        for session_id, raw in payload["sessions"].items():
            if not isinstance(raw, dict):
                continue
            updated = _parse_iso(raw.get("updated_at")) or now
            age_seconds = int(max((now - updated).total_seconds(), 0.0))
            sessions.append(
                {
                    "session_id": session_id,
                    "state": str(raw.get("state") or "sleeping"),
                    "emoji": str(raw.get("emoji") or "😴"),
                    "label": str(raw.get("label") or "Sleeping"),
                    "detail": str(raw.get("detail") or ""),
                    "proposal_id": str(raw.get("proposal_id") or ""),
                    "tool_use_id": str(raw.get("tool_use_id") or ""),
                    "question_id": str(raw.get("question_id") or ""),
                    "attempt_number": _as_int(raw.get("attempt_number")),
                    "queue_depth": _as_int(raw.get("queue_depth")),
                    "updated_at": str(raw.get("updated_at") or ""),
                    "age_seconds": age_seconds,
                    "is_active": session_id in active_sessions,
                    "is_selected": session_id == active_session_id,
                }
            )

        sessions.sort(key=lambda item: (not item["is_selected"], item["age_seconds"]))

        if changed:
            payload["updated_at"] = _utc_now_iso()
            payload["active_session_id"] = active_session_id
            _write_presence_file(payload, state_dir)

        selected = next((s for s in sessions if s["is_selected"]), None)
        return {
            "active_session_id": active_session_id,
            "active_session": selected,
            "session_count": len(sessions),
            "sessions": sessions,
        }


def session_retention_seconds() -> int:
    raw = os.environ.get(ENV_SESSION_RETENTION)
    if raw is None:
        return DEFAULT_SESSION_RETENTION_SECONDS
    with contextlib.suppress(ValueError):
        parsed = int(raw)
        if parsed > 0:
            return parsed
    return DEFAULT_SESSION_RETENTION_SECONDS


def inactive_state_grace_seconds() -> int:
    raw = os.environ.get(ENV_INACTIVE_STATE_GRACE)
    if raw is None:
        return DEFAULT_INACTIVE_STATE_GRACE_SECONDS
    with contextlib.suppress(ValueError):
        parsed = int(raw)
        if parsed >= 0:
            return parsed
    return DEFAULT_INACTIVE_STATE_GRACE_SECONDS


def _active_session_ids(state_dir: Path | None) -> set[str]:
    records = list_active_leases(prune=True, state_dir=state_dir)
    return {
        str(record.get("session_id") or "").strip()
        for record in records
        if str(record.get("session_id") or "").strip()
    }


def _prune_inactive(payload: dict[str, Any], active_sessions: set[str]) -> bool:
    retention = session_retention_seconds()
    inactive_grace = inactive_state_grace_seconds()
    now = datetime.now(UTC)
    changed = False

    sessions = payload["sessions"]
    for session_id, raw in list(sessions.items()):
        if session_id in active_sessions:
            continue
        if not isinstance(raw, dict):
            sessions.pop(session_id, None)
            changed = True
            continue

        updated = _parse_iso(raw.get("updated_at"))
        if updated is None:
            sessions.pop(session_id, None)
            changed = True
            continue

        age_seconds = max((now - updated).total_seconds(), 0.0)
        state_key = str(raw.get("state") or "")

        if state_key in _INACTIVE_TO_DETACHED_STATES and age_seconds >= inactive_grace:
            detached_state = _state_payload("detached")
            raw["state"] = "detached"
            raw["emoji"] = detached_state["emoji"]
            raw["label"] = detached_state["label"]
            raw["detail"] = "Session inactive"
            raw["updated_at"] = _utc_now_iso()
            raw.pop("auto_reset_from", None)
            raw.pop("auto_reset_to", None)
            raw.pop("auto_reset_at", None)
            changed = True
            continue

        if age_seconds >= retention:
            sessions.pop(session_id, None)
            changed = True

    active_id = str(payload.get("active_session_id") or "").strip()
    if active_id and active_id not in sessions:
        payload["active_session_id"] = ""
        changed = True

    return changed


def _pick_recent_session_id(sessions: dict[str, Any]) -> str:
    best_session = ""
    best_time = datetime.fromtimestamp(0, tz=UTC)
    for session_id, raw in sessions.items():
        if not isinstance(raw, dict):
            continue
        updated = _parse_iso(raw.get("updated_at"))
        if updated is None:
            continue
        if updated >= best_time:
            best_session = session_id
            best_time = updated
    return best_session


def _apply_auto_resets(payload: dict[str, Any]) -> bool:
    sessions = payload["sessions"]
    changed = False
    now = datetime.now(UTC)

    for _session_id, raw in sessions.items():
        if not isinstance(raw, dict):
            continue

        reset_at = _parse_iso(raw.get("auto_reset_at"))
        if reset_at is None:
            continue

        if reset_at > now:
            continue

        from_state = str(raw.get("auto_reset_from") or "")
        to_state = str(raw.get("auto_reset_to") or "")
        current_state = str(raw.get("state") or "")
        if from_state and to_state and current_state == from_state:
            state = _state_payload(to_state)
            raw["state"] = to_state
            raw["emoji"] = state["emoji"]
            raw["label"] = state["label"]
            if to_state == "sleeping":
                raw["detail"] = "Waiting for agent loop"
            raw["updated_at"] = _utc_now_iso()

        raw.pop("auto_reset_from", None)
        raw.pop("auto_reset_to", None)
        raw.pop("auto_reset_at", None)
        changed = True

    return changed


def _state_payload(state_key: str) -> dict[str, str]:
    return _STATE_META.get(state_key, {"emoji": "⚪", "label": state_key.replace("_", " ").title()})


@contextmanager
def _presence_lock(state_dir: Path | None) -> Any:
    qa_dir = _qa_state_dir(state_dir)
    qa_dir.mkdir(parents=True, exist_ok=True)
    lock_path = qa_dir / "sidecar.presence.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_presence_file(state_dir: Path | None) -> dict[str, Any]:
    path = _presence_path(state_dir)
    if not path.exists():
        return _default_payload()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _default_payload()

    if not isinstance(payload, dict):
        return _default_payload()

    sessions = payload.get("sessions")
    if not isinstance(sessions, dict):
        sessions = {}

    return {
        "version": 1,
        "updated_at": str(payload.get("updated_at") or _utc_now_iso()),
        "active_session_id": str(payload.get("active_session_id") or ""),
        "sessions": {str(k): v for k, v in sessions.items() if isinstance(k, str)},
    }


def _write_presence_file(payload: dict[str, Any], state_dir: Path | None) -> None:
    path = _presence_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp_path.replace(path)


def _default_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": _utc_now_iso(),
        "active_session_id": "",
        "sessions": {},
    }


def _qa_state_dir(state_dir: Path | None = None) -> Path:
    base = state_dir or Path(os.environ.get("VIBECHECK_STATE_DIR", "state"))
    return base / "qa"


def _presence_path(state_dir: Path | None = None) -> Path:
    return _qa_state_dir(state_dir) / "sidecar.presence.json"


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        with contextlib.suppress(ValueError):
            return int(value)
    return None


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_after_seconds(seconds: int) -> str:
    target = datetime.now(UTC).timestamp() + seconds
    return datetime.fromtimestamp(target, tz=UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
