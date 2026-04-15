from __future__ import annotations

import contextlib
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

ENV_LEASE_TTL = "VIBECHECK_SIDECAR_LEASE_TTL"
DEFAULT_LEASE_TTL_SECONDS = 1800


def attach_lease(
    session_id: str,
    *,
    source: str = "",
    state_dir: Path | None = None,
) -> dict[str, Any]:
    return _upsert_lease(session_id, source=source, state_dir=state_dir)


def heartbeat_lease(
    session_id: str,
    *,
    source: str = "",
    state_dir: Path | None = None,
) -> dict[str, Any]:
    return _upsert_lease(session_id, source=source, state_dir=state_dir)


def detach_lease(
    session_id: str,
    *,
    reason: str = "",
    state_dir: Path | None = None,
) -> dict[str, Any]:
    if not session_id.strip():
        return {"removed": False, "active_count": count_active_leases(state_dir=state_dir)}

    with _leases_lock(state_dir):
        data = _read_leases_file(state_dir)
        removed = data["leases"].pop(session_id, None) is not None
        if reason:
            data["last_detach_reason"] = reason
        data["updated_at"] = _utc_now_iso()
        _write_leases_file(data, state_dir)
        return {"removed": removed, "active_count": len(data["leases"])}


def prune_stale_leases(
    *,
    ttl_seconds: int | None = None,
    state_dir: Path | None = None,
) -> list[str]:
    ttl = ttl_seconds if ttl_seconds is not None else lease_ttl_seconds()
    now = datetime.now(UTC)
    removed: list[str] = []

    with _leases_lock(state_dir):
        data = _read_leases_file(state_dir)
        leases = data["leases"]
        for session_id, raw in list(leases.items()):
            last_heartbeat_at = raw.get("last_heartbeat_at") if isinstance(raw, dict) else None
            heartbeat_dt = _parse_iso(last_heartbeat_at)
            if heartbeat_dt is None:
                removed.append(session_id)
                leases.pop(session_id, None)
                continue
            age_seconds = max((now - heartbeat_dt).total_seconds(), 0.0)
            if age_seconds >= ttl:
                removed.append(session_id)
                leases.pop(session_id, None)

        if removed:
            data["updated_at"] = _utc_now_iso()
            _write_leases_file(data, state_dir)

    return removed


def count_active_leases(
    *,
    prune: bool = False,
    ttl_seconds: int | None = None,
    state_dir: Path | None = None,
) -> int:
    if prune:
        prune_stale_leases(ttl_seconds=ttl_seconds, state_dir=state_dir)

    with _leases_lock(state_dir):
        data = _read_leases_file(state_dir)
        return len(data["leases"])


def list_active_leases(
    *,
    prune: bool = False,
    ttl_seconds: int | None = None,
    state_dir: Path | None = None,
) -> list[dict[str, Any]]:
    if prune:
        prune_stale_leases(ttl_seconds=ttl_seconds, state_dir=state_dir)

    with _leases_lock(state_dir):
        data = _read_leases_file(state_dir)
        leases = data["leases"]
        records = []
        for session_id in sorted(leases.keys()):
            raw = leases[session_id]
            if isinstance(raw, dict):
                records.append({"session_id": session_id, **raw})
        return records


def lease_ttl_seconds() -> int:
    raw = os.environ.get(ENV_LEASE_TTL)
    if raw is None:
        return DEFAULT_LEASE_TTL_SECONDS
    with contextlib.suppress(ValueError):
        parsed = int(raw)
        if parsed > 0:
            return parsed
    return DEFAULT_LEASE_TTL_SECONDS


def _upsert_lease(session_id: str, *, source: str, state_dir: Path | None) -> dict[str, Any]:
    session = session_id.strip()
    if not session:
        return {"updated": False, "active_count": count_active_leases(state_dir=state_dir)}

    now_iso = _utc_now_iso()
    with _leases_lock(state_dir):
        data = _read_leases_file(state_dir)
        leases = data["leases"]
        existing = leases.get(session)
        attached_at = now_iso
        if isinstance(existing, dict):
            attached_at = str(existing.get("attached_at") or now_iso)

        leases[session] = {
            "attached_at": attached_at,
            "last_heartbeat_at": now_iso,
            "source": source or "unknown",
        }
        data["updated_at"] = now_iso
        _write_leases_file(data, state_dir)
        return {"updated": True, "active_count": len(leases)}


@contextmanager
def _leases_lock(state_dir: Path | None) -> Any:
    qa_dir = _qa_state_dir(state_dir)
    qa_dir.mkdir(parents=True, exist_ok=True)
    lock_path = qa_dir / "sidecar.leases.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_leases_file(state_dir: Path | None) -> dict[str, Any]:
    path = _leases_path(state_dir)
    if not path.exists():
        return _default_payload()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _default_payload()

    if not isinstance(payload, dict):
        return _default_payload()

    leases = payload.get("leases")
    if not isinstance(leases, dict):
        leases = {}

    return {
        "version": 1,
        "updated_at": str(payload.get("updated_at") or _utc_now_iso()),
        "leases": {str(k): v for k, v in leases.items() if isinstance(k, str)},
    }


def _write_leases_file(payload: dict[str, Any], state_dir: Path | None) -> None:
    path = _leases_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp_path.replace(path)


def _default_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": _utc_now_iso(),
        "leases": {},
    }


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


def _qa_state_dir(state_dir: Path | None = None) -> Path:
    base = state_dir or Path(os.environ.get("VIBECHECK_STATE_DIR", "state"))
    return base / "qa"


def _leases_path(state_dir: Path | None = None) -> Path:
    return _qa_state_dir(state_dir) / "sidecar.leases.json"


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
