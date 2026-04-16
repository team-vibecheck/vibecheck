# ruff: noqa: E402

from __future__ import annotations

import html
import json
import os
import re
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from qa.sidecar.presence import get_presence_snapshot, set_session_state

# FastAPI Request type is rebound at runtime in run_server().
Request = Any


_STATE_BORDER_COLORS = {
    "sleeping": "#8FBF97",
    "watching": "#4A8DB8",
    "gate_thinking": "#D4A27F",
    "gate_allow": "#4FA56A",
    "gate_block": "#C96B6B",
    "qa_waiting_submission": "#D4A27F",
    "qa_evaluating": "#4A8DB8",
    "qa_pass": "#4FA56A",
    "qa_fail_attempt": "#C96B6B",
    "qa_fail_terminal": "#AF4F4F",
    "recovering": "#8B6FCE",
    "error": "#D08A43",
    "detached": "#8E8E8E",
}

ENV_IDLE_TIMEOUT = "VIBECHECK_SIDECAR_IDLE_TIMEOUT"
ENV_DETACH_GRACE = "VIBECHECK_SIDECAR_DETACH_GRACE"
ENV_ANSWER_TTL = "VIBECHECK_SIDECAR_ANSWER_TTL"
ENV_TRANSIENT_STATE_MAX_AGE = "VIBECHECK_SIDECAR_TRANSIENT_STATE_MAX_AGE"
ENV_ORPHAN_TTL = "VIBECHECK_SIDECAR_ORPHAN_TTL"

DEFAULT_IDLE_TIMEOUT = 600
DEFAULT_DETACH_GRACE = 180
DEFAULT_ANSWER_TTL = 1800
DEFAULT_TRANSIENT_STATE_MAX_AGE = 90
DEFAULT_ORPHAN_TTL = 600
SIDECAR_COMPAT_VERSION = 2
RUNTIME_FILE_NAME = "sidecar.runtime.json"


class SidecarState:
    def __init__(
        self,
        *,
        state_dir: Path,
        idle_timeout_seconds: int,
        detach_grace_seconds: int,
        answer_ttl_seconds: int,
    ) -> None:
        self._lock = threading.Lock()
        self._pending: deque[dict[str, Any]] = deque()
        self._current_question: dict[str, Any] | None = None
        self._answered: dict[str, dict[str, Any]] = {}
        self._request_index: dict[str, str] = {}
        self._last_feedback = "Waiting for questions from VibeCheck..."
        self._started_at = time.time()
        self._last_activity_at = self._started_at
        self._last_question_activity_at = self._started_at
        self._no_leases_since: float | None = None
        self._runtime_path = state_dir / "qa" / RUNTIME_FILE_NAME
        self._state_dir = state_dir
        self._idle_timeout_seconds = max(idle_timeout_seconds, 1)
        self._detach_grace_seconds = max(detach_grace_seconds, 1)
        self._answer_ttl_seconds = max(answer_ttl_seconds, 30)
        self._transient_state_max_age = max(
            int(os.environ.get(ENV_TRANSIENT_STATE_MAX_AGE, str(DEFAULT_TRANSIENT_STATE_MAX_AGE))),
            5,
        )
        self._orphan_ttl_seconds = max(
            _as_int(os.environ.get(ENV_ORPHAN_TTL), default=DEFAULT_ORPHAN_TTL),
            30,
        )
        self._load_runtime_state()
        self._reconcile_runtime_state()

    def _touch(self) -> None:
        self._last_activity_at = time.time()

    def _touch_question_activity(self) -> None:
        now = time.time()
        self._last_question_activity_at = now
        self._no_leases_since = None

    def enqueue_question(self, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question", "")).strip()
        if not question:
            raise ValueError("Missing question")

        session_id = str(payload.get("session_id") or "unknown_session").strip()
        proposal_id = str(payload.get("proposal_id") or "unknown_proposal").strip()
        tool_use_id = str(payload.get("tool_use_id") or "").strip()
        request_id = str(payload.get("request_id") or "").strip()
        attempt = _as_int(payload.get("attempt"), default=1)
        question_type = str(payload.get("question_type") or "plain_english").strip() or "plain_english"
        context_excerpt = str(payload.get("context_excerpt") or "")

        with self._lock:
            self._prune_answered_locked()

            if request_id:
                existing_id = self._request_index.get(request_id)
                if existing_id:
                    status = self._question_status_locked(existing_id, session_id=session_id)
                    if status["state"] != "unknown":
                        self._touch()
                        return {
                            "status": "queued",
                            "question_id": existing_id,
                            "state": status["state"],
                            "queue_position": status.get("queue_position"),
                            "queue_depth": len(self._pending),
                            "active_question_id": self._current_question_id_locked(),
                            "deduped": True,
                        }

            question_id = f"q-{uuid4().hex}"
            record = {
                "question_id": question_id,
                "session_id": session_id,
                "proposal_id": proposal_id,
                "tool_use_id": tool_use_id,
                "attempt": attempt,
                "question_type": question_type,
                "question": question,
                "context_excerpt": context_excerpt,
                "created_at": _utc_now_iso(),
            }

            self._pending.append(record)
            if self._current_question is None:
                self._current_question = self._pending.popleft()
            if request_id:
                self._request_index[request_id] = question_id
            self._touch()
            self._touch_question_activity()
            self._persist_runtime_locked()

            queue_position = self._queue_position_locked(question_id)
            queue_depth = len(self._pending)
            has_current = self._current_question is not None
            active_question_id = self._current_question_id_locked()
            active_session_id = self._current_session_id_locked()

            set_session_state(
                active_session_id or session_id,
                "qa_waiting_submission",
                state_dir=self._state_dir,
                detail="Question ready for submission",
                proposal_id=proposal_id,
                tool_use_id=tool_use_id,
                attempt_number=attempt,
                question_id=active_question_id or question_id,
                queue_depth=queue_depth,
            )

        return {
            "question_id": question_id,
            "queue_depth": queue_depth,
            "has_current_question": has_current,
            "state": "current_or_queued",
            "queue_position": queue_position,
            "active_question_id": active_question_id,
            "deduped": False,
        }

    def submit_answer(
        self,
        answer: str,
        *,
        question_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        cleaned = answer.strip()
        if not cleaned:
            return {
                "accepted": False,
                "code": "empty_answer",
                "message": "Please provide an answer before submitting.",
            }

        with self._lock:
            if self._current_question is None:
                return {
                    "accepted": False,
                    "code": "no_active_question",
                    "message": "No active question yet.",
                }

            current_question_id = str(self._current_question.get("question_id") or "")
            current_session_id = str(self._current_question.get("session_id") or "")
            if question_id and question_id != current_question_id:
                return {
                    "accepted": False,
                    "code": "question_not_active",
                    "message": "The requested question is not currently active.",
                }

            if session_id and current_session_id and session_id != current_session_id:
                return {
                    "accepted": False,
                    "code": "session_mismatch",
                    "message": "Question is owned by another session.",
                }

            answered_at = _utc_now_iso()
            self._answered[current_question_id] = {
                "question_id": current_question_id,
                "session_id": current_session_id,
                "proposal_id": str(self._current_question.get("proposal_id") or ""),
                "tool_use_id": str(self._current_question.get("tool_use_id") or ""),
                "attempt": _as_int(self._current_question.get("attempt"), default=1),
                "question_type": str(self._current_question.get("question_type") or "plain_english"),
                "question": str(self._current_question.get("question") or ""),
                "context_excerpt": str(self._current_question.get("context_excerpt") or ""),
                "created_at": str(self._current_question.get("created_at") or answered_at),
                "answer": cleaned,
                "answered_at": answered_at,
            }

            self._last_feedback = "Answer submitted. Waiting for next question..."
            if self._pending:
                self._current_question = self._pending.popleft()
            else:
                self._current_question = None
            self._prune_answered_locked()
            self._touch()
            self._touch_question_activity()
            self._persist_runtime_locked()
            queue_depth = len(self._pending)
            active_question_id = self._current_question_id_locked()
            active_session_id = self._current_session_id_locked()

            set_session_state(
                current_session_id,
                "watching",
                state_dir=self._state_dir,
                detail="Answer submitted",
                proposal_id=str(self._answered[current_question_id].get("proposal_id") or ""),
                tool_use_id=str(self._answered[current_question_id].get("tool_use_id") or ""),
                attempt_number=_as_int(self._answered[current_question_id].get("attempt"), default=1),
                question_id=current_question_id,
                queue_depth=queue_depth,
            )

            if active_session_id:
                set_session_state(
                    active_session_id,
                    "qa_waiting_submission",
                    state_dir=self._state_dir,
                    detail="Question ready for submission",
                    proposal_id=str(self._current_question.get("proposal_id") or "")
                    if self._current_question
                    else "",
                    tool_use_id=str(self._current_question.get("tool_use_id") or "")
                    if self._current_question
                    else "",
                    attempt_number=_as_int(self._current_question.get("attempt"), default=1)
                    if self._current_question
                    else None,
                    question_id=active_question_id or "",
                    queue_depth=queue_depth,
                )

        return {
            "accepted": True,
            "code": "accepted",
            "message": self._last_feedback,
            "queue_depth": queue_depth,
            "active_question_id": active_question_id,
        }

    def detach_session_questions(self, session_id: str, *, reason: str = "session_detach") -> dict[str, Any]:
        session = session_id.strip()
        if not session:
            with self._lock:
                return {
                    "status": "noop",
                    "removed_total": 0,
                    "removed_current": 0,
                    "removed_pending": 0,
                    "queue_depth": len(self._pending),
                    "active_question_id": self._current_question_id_locked(),
                }

        with self._lock:
            removed = self._drop_questions_locked(
                lambda item: str(item.get("session_id") or "").strip() == session,
                reason=reason,
            )
            if removed["removed_total"] > 0:
                self._touch()
                self._touch_question_activity()
                queue_depth = len(self._pending)
                active_session_id = self._current_session_id_locked()
                if active_session_id:
                    self._set_active_waiting_presence_locked(active_session_id, queue_depth=queue_depth)
                self._persist_runtime_locked()

            return {
                "status": "ok",
                "session_id": session,
                **removed,
                "queue_depth": len(self._pending),
                "active_question_id": self._current_question_id_locked(),
            }

    def prune_orphaned_questions(self, *, reason: str = "orphan_reaper") -> dict[str, Any]:
        active_sessions = _active_leased_sessions(state_dir=self._state_dir)
        now = datetime.now(UTC)

        with self._lock:
            removed = self._drop_questions_locked(
                lambda item: self._is_orphaned_question_locked(
                    item,
                    active_sessions=active_sessions,
                    now=now,
                ),
                reason=reason,
            )
            if removed["removed_total"] > 0:
                self._touch()
                self._touch_question_activity()
                queue_depth = len(self._pending)
                active_session_id = self._current_session_id_locked()
                if active_session_id:
                    self._set_active_waiting_presence_locked(active_session_id, queue_depth=queue_depth)
                self._persist_runtime_locked()

            return {
                "status": "ok",
                **removed,
                "active_leases": len(active_sessions),
                "orphan_ttl_seconds": self._orphan_ttl_seconds,
            }

    def answer_status(self, question_id: str, *, session_id: str = "") -> dict[str, Any]:
        question_id = question_id.strip()
        if not question_id:
            return {"state": "unknown"}

        with self._lock:
            self._prune_answered_locked()
            status = self._question_status_locked(question_id, session_id=session_id)
            self._touch()
            return status

    def snapshot(self) -> dict[str, Any]:
        active_leases = _count_active_leases(prune=True, state_dir=self._runtime_path.parent.parent)
        presence = get_presence_snapshot(state_dir=self._state_dir)

        with self._lock:
            self._prune_answered_locked()
            current = self._current_question
            queue_depth = len(self._pending)
            feedback = self._last_feedback
            current_question_id = self._current_question_id_locked()
            answered_count = len(self._answered)
            current_context_excerpt = str(current.get("context_excerpt", "")) if current else ""
            current_path = _extract_metadata_value(current_context_excerpt, key="primary_path")
            current_language = _extract_metadata_value(current_context_excerpt, key="primary_language")

        context_preview = _build_context_preview(
            current_context_excerpt,
            focus_text=str(current.get("question", "")) if current else "",
        )
        context_language = _resolve_preview_language(
            explicit_language=current_language,
            path=current_path,
            preview_text=context_preview,
        )

        presence = self._normalize_presence_for_idle(
            presence,
            has_current_question=current is not None,
            queue_depth=queue_depth,
        )

        uptime_seconds = int(time.time() - self._started_at)
        idle_seconds = int(time.time() - self._last_activity_at)
        question_idle_seconds = int(time.time() - self._last_question_activity_at)
        return {
            "queue_depth": queue_depth,
            "has_current_question": current is not None,
            "current_question_id": current_question_id,
            "current_session_id": str(current.get("session_id", "")) if current else "",
            "current_proposal_id": str(current.get("proposal_id", "")) if current else "",
            "current_tool_use_id": str(current.get("tool_use_id", "")) if current else "",
            "current_created_at": str(current.get("created_at", "")) if current else "",
            "question": str(current.get("question", "")) if current else "",
            "context_excerpt": current_context_excerpt,
            "context_preview": context_preview,
            "context_preview_language": context_language,
            "context_primary_path": current_path,
            "attempt": int(current.get("attempt", 1)) if current else None,
            "question_type": str(current.get("question_type", "plain_english"))
            if current
            else None,
            "feedback": feedback,
            "uptime_seconds": uptime_seconds,
            "idle_seconds": idle_seconds,
            "question_idle_seconds": question_idle_seconds,
            "active_leases": active_leases,
            "answered_count": answered_count,
            "presence": presence,
        }

    def _normalize_presence_for_idle(
        self,
        presence: dict[str, Any],
        *,
        has_current_question: bool,
        queue_depth: int,
    ) -> dict[str, Any]:
        if has_current_question or queue_depth > 0:
            return presence

        active = presence.get("active_session") if isinstance(presence, dict) else None
        if not isinstance(active, dict):
            return presence

        state = str(active.get("state") or "")
        if state not in {"qa_evaluating", "qa_waiting_submission", "gate_thinking"}:
            return presence

        age_seconds = _as_int(active.get("age_seconds"), default=0)
        if age_seconds < self._transient_state_max_age:
            return presence

        session_id = str(active.get("session_id") or "").strip()
        if not session_id:
            return presence

        fallback = "watching" if bool(active.get("is_active")) else "sleeping"
        set_session_state(
            session_id,
            fallback,
            state_dir=self._state_dir,
            detail="Idle between requests",
            make_active=False,
        )
        return get_presence_snapshot(state_dir=self._state_dir)

    def can_shutdown(self, *, force: bool = False) -> bool:
        if force:
            return True

        with self._lock:
            is_idle = self._current_question is None and not self._pending
            return is_idle

    def should_idle_shutdown(self) -> bool:
        active_leases = _count_active_leases(prune=True, state_dir=self._runtime_path.parent.parent)

        with self._lock:
            if self._current_question is not None or self._pending:
                self._no_leases_since = None
                return False

            now = time.time()
            if active_leases > 0:
                self._no_leases_since = None
                self._persist_runtime_locked()
                return False

            question_idle_for = now - self._last_question_activity_at
            if self._no_leases_since is None:
                self._no_leases_since = now

            no_leases_for = 0.0
            if self._no_leases_since is not None:
                no_leases_for = now - self._no_leases_since

            should_shutdown = (
                question_idle_for >= self._idle_timeout_seconds
                or no_leases_for >= self._detach_grace_seconds
            )
            if should_shutdown:
                return True

            self._persist_runtime_locked()
            return False

    def _question_status_locked(self, question_id: str, *, session_id: str = "") -> dict[str, Any]:
        if self._current_question is not None:
            current_id = str(self._current_question.get("question_id") or "")
            current_session = str(self._current_question.get("session_id") or "")
            if question_id == current_id:
                if session_id and current_session and session_id != current_session:
                    return {"state": "unknown", "question_id": question_id}
                return {
                    "state": "pending",
                    "question_id": question_id,
                    "queue_position": 0,
                    "active_question_id": current_id,
                }

        for idx, item in enumerate(self._pending, start=1):
            pending_id = str(item.get("question_id") or "")
            pending_session = str(item.get("session_id") or "")
            if question_id != pending_id:
                continue
            if session_id and pending_session and session_id != pending_session:
                return {"state": "unknown", "question_id": question_id}
            return {
                "state": "pending",
                "question_id": question_id,
                "queue_position": idx,
                "active_question_id": self._current_question_id_locked(),
            }

        answered = self._answered.get(question_id)
        if answered is not None:
            answered_session = str(answered.get("session_id") or "")
            if session_id and answered_session and session_id != answered_session:
                return {"state": "unknown", "question_id": question_id}
            return {
                "state": "answered",
                "question_id": question_id,
                "answer": str(answered.get("answer") or ""),
                "answered_at": str(answered.get("answered_at") or ""),
            }

        return {"state": "unknown", "question_id": question_id}

    def _queue_position_locked(self, question_id: str) -> int | None:
        if self._current_question is not None and question_id == str(
            self._current_question.get("question_id") or ""
        ):
            return 0
        for idx, item in enumerate(self._pending, start=1):
            if question_id == str(item.get("question_id") or ""):
                return idx
        return None

    def _current_question_id_locked(self) -> str | None:
        if self._current_question is None:
            return None
        question_id = str(self._current_question.get("question_id") or "").strip()
        return question_id or None

    def _current_session_id_locked(self) -> str | None:
        if self._current_question is None:
            return None
        session_id = str(self._current_question.get("session_id") or "").strip()
        return session_id or None

    def _prune_answered_locked(self) -> None:
        now = datetime.now(UTC)
        removed: list[str] = []
        for question_id, record in list(self._answered.items()):
            answered_at = _parse_iso(record.get("answered_at"))
            if answered_at is None:
                removed.append(question_id)
                self._answered.pop(question_id, None)
                continue
            age = max((now - answered_at).total_seconds(), 0.0)
            if age > self._answer_ttl_seconds:
                removed.append(question_id)
                self._answered.pop(question_id, None)

        if removed:
            for request_id, question_id in list(self._request_index.items()):
                if question_id in removed:
                    self._request_index.pop(request_id, None)

    def _load_runtime_state(self) -> None:
        if not self._runtime_path.exists():
            return

        try:
            payload = json.loads(self._runtime_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return

        if not isinstance(payload, dict):
            return

        current = payload.get("current_question")
        if isinstance(current, dict):
            self._current_question = current

        pending = payload.get("pending")
        if isinstance(pending, list):
            for item in pending:
                if isinstance(item, dict):
                    self._pending.append(item)

        answered = payload.get("answered")
        if isinstance(answered, dict):
            for question_id, record in answered.items():
                if isinstance(question_id, str) and isinstance(record, dict):
                    self._answered[question_id] = record

        request_index = payload.get("request_index")
        if isinstance(request_index, dict):
            for request_id, question_id in request_index.items():
                if isinstance(request_id, str) and isinstance(question_id, str):
                    self._request_index[request_id] = question_id

        last_question_activity = _parse_iso(payload.get("last_question_activity_at"))
        if last_question_activity is not None:
            self._last_question_activity_at = last_question_activity.timestamp()

        no_leases_since = _parse_iso(payload.get("no_leases_since"))
        if no_leases_since is not None:
            self._no_leases_since = no_leases_since.timestamp()

        self._prune_answered_locked()

    def _persist_runtime_locked(self) -> None:
        self._runtime_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": _utc_now_iso(),
            "last_question_activity_at": _iso_from_ts(self._last_question_activity_at),
            "no_leases_since": _iso_from_ts(self._no_leases_since),
            "current_question": self._current_question,
            "pending": list(self._pending),
            "answered": self._answered,
            "request_index": self._request_index,
        }
        tmp_path = self._runtime_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp_path.replace(self._runtime_path)

    def _reconcile_runtime_state(self) -> None:
        with self._lock:
            changed = False

            if self._current_question is None and self._pending:
                self._current_question = self._pending.popleft()
                changed = True

            self._prune_answered_locked()

            active_sessions = _active_leased_sessions(state_dir=self._state_dir)
            now = datetime.now(UTC)
            removed = self._drop_questions_locked(
                lambda item: self._is_orphaned_question_locked(
                    item,
                    active_sessions=active_sessions,
                    now=now,
                ),
                reason="startup_reconcile",
            )
            changed = changed or removed["removed_total"] > 0

            repaired = self._repair_request_index_locked()
            changed = changed or repaired

            if self._current_question is not None:
                self._touch_question_activity()
                active_session_id = self._current_session_id_locked()
                if active_session_id:
                    self._set_active_waiting_presence_locked(
                        active_session_id,
                        queue_depth=len(self._pending),
                    )

            if changed:
                self._touch()
                self._persist_runtime_locked()

    def _drop_questions_locked(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        reason: str,
    ) -> dict[str, Any]:
        removed_current = 0
        removed_pending = 0
        removed_ids: list[str] = []

        if self._current_question is not None and predicate(self._current_question):
            current_id = str(self._current_question.get("question_id") or "").strip()
            if current_id:
                removed_ids.append(current_id)
            self._current_question = None
            removed_current = 1

        kept_pending: deque[dict[str, Any]] = deque()
        for item in self._pending:
            if predicate(item):
                removed_pending += 1
                pending_id = str(item.get("question_id") or "").strip()
                if pending_id:
                    removed_ids.append(pending_id)
                continue
            kept_pending.append(item)

        self._pending = kept_pending
        if self._current_question is None and self._pending:
            self._current_question = self._pending.popleft()

        if removed_ids:
            removed_id_set = set(removed_ids)
            for request_id, question_id in list(self._request_index.items()):
                if question_id in removed_id_set:
                    self._request_index.pop(request_id, None)

        removed_total = removed_current + removed_pending
        if removed_total > 0:
            self._last_feedback = (
                f"Dropped {removed_total} orphaned question(s) ({reason.replace('_', ' ')})."
            )

        return {
            "removed_total": removed_total,
            "removed_current": removed_current,
            "removed_pending": removed_pending,
            "removed_question_ids": removed_ids,
        }

    def _set_active_waiting_presence_locked(self, session_id: str, *, queue_depth: int) -> None:
        if self._current_question is None:
            return

        set_session_state(
            session_id,
            "qa_waiting_submission",
            state_dir=self._state_dir,
            detail="Question ready for submission",
            proposal_id=str(self._current_question.get("proposal_id") or ""),
            tool_use_id=str(self._current_question.get("tool_use_id") or ""),
            attempt_number=_as_int(self._current_question.get("attempt"), default=1),
            question_id=str(self._current_question.get("question_id") or ""),
            queue_depth=queue_depth,
        )

    def _is_orphaned_question_locked(
        self,
        item: dict[str, Any],
        *,
        active_sessions: set[str],
        now: datetime,
    ) -> bool:
        session_id = str(item.get("session_id") or "").strip()
        if session_id and session_id in active_sessions:
            return False

        age_seconds = self._question_age_seconds_locked(item, now=now)
        return not age_seconds < self._orphan_ttl_seconds

    def _question_age_seconds_locked(self, item: dict[str, Any], *, now: datetime) -> float:
        created = _parse_iso(item.get("created_at"))
        if created is None:
            return float(self._orphan_ttl_seconds)
        return max((now - created).total_seconds(), 0.0)

    def _repair_request_index_locked(self) -> bool:
        known_ids: set[str] = set()
        if self._current_question is not None:
            current_id = str(self._current_question.get("question_id") or "").strip()
            if current_id:
                known_ids.add(current_id)

        for item in self._pending:
            pending_id = str(item.get("question_id") or "").strip()
            if pending_id:
                known_ids.add(pending_id)

        for answered_id in self._answered:
            if answered_id.strip():
                known_ids.add(answered_id.strip())

        changed = False
        for request_id, question_id in list(self._request_index.items()):
            if question_id not in known_ids:
                self._request_index.pop(request_id, None)
                changed = True
        return changed


def _import_gradio() -> Any:
    import importlib

    return importlib.import_module("gradio")


def run_server(port: int = 7865) -> None:
    gr = _import_gradio()
    fastapi_module = __import__("fastapi", fromlist=["FastAPI"])
    responses_module = __import__("fastapi.responses", fromlist=["JSONResponse"])
    uvicorn_module = __import__("uvicorn", fromlist=["Config", "Server"])

    FastAPI = fastapi_module.FastAPI
    globals()["Request"] = fastapi_module.Request
    JSONResponse = responses_module.JSONResponse
    UvicornConfig = uvicorn_module.Config
    UvicornServer = uvicorn_module.Server

    state_dir = Path(os.environ.get("VIBECHECK_STATE_DIR", "state"))
    idle_timeout_seconds = int(os.environ.get(ENV_IDLE_TIMEOUT, str(DEFAULT_IDLE_TIMEOUT)))
    detach_grace_seconds = int(os.environ.get(ENV_DETACH_GRACE, str(DEFAULT_DETACH_GRACE)))
    answer_ttl_seconds = int(os.environ.get(ENV_ANSWER_TTL, str(DEFAULT_ANSWER_TTL)))
    state = SidecarState(
        state_dir=state_dir,
        idle_timeout_seconds=idle_timeout_seconds,
        detach_grace_seconds=detach_grace_seconds,
        answer_ttl_seconds=answer_ttl_seconds,
    )
    server_ref: dict[str, Any] = {}

    with gr.Blocks(title="VibeCheck QA") as blocks:
        gr.Markdown("## VibeCheck QA")
        sessions_html = gr.HTML("No session activity yet.")
        sessions_md = gr.Markdown("No session activity yet.", visible=False)
        status_md = gr.Markdown("Status: waiting for questions")
        question_md = gr.Markdown("No active question.")
        context_md = gr.Markdown("", visible=False)
        answer_box = gr.Textbox(
            label="Your answer",
            lines=6,
            placeholder="Type your answer here...",
        )
        submit_btn = gr.Button("Submit", variant="primary")
        feedback_md = gr.Markdown("Waiting for questions from VibeCheck...")

        def poll_ui() -> tuple[Any, ...]:
            snap = state.snapshot()
            presence = snap.get("presence") if isinstance(snap.get("presence"), dict) else {}

            sessions = presence.get("sessions") if isinstance(presence, dict) else []
            if isinstance(sessions, list) and sessions:
                visible_sessions = _limit_detached_sessions(sessions, max_detached=2)
                lines = []
                for item in visible_sessions[:8]:
                    if not isinstance(item, dict):
                        continue
                    selected = "**" if item.get("is_selected") else ""
                    sid = str(item.get("session_id") or "")
                    short_sid = sid[:8] if sid else "unknown"
                    lines.append(
                        f"- {selected}{item.get('emoji', '⚪')} `{short_sid}` {item.get('label', '')}{selected}"
                    )
                sessions_line = "\n".join(lines) if lines else "No session activity yet."
                sessions_grid = _render_session_cards(visible_sessions)
            else:
                sessions_line = "No session activity yet."
                sessions_grid = "<div>No session activity yet.</div>"

            if not snap["has_current_question"]:
                status = (
                    "Status: idle"
                    f" | queued: {snap['queue_depth']}"
                    f" | idle: {snap['idle_seconds']}s"
                    f" | uptime: {snap['uptime_seconds']}s"
                )
                return (
                    sessions_grid,
                    sessions_line,
                    status,
                    "No active question.",
                    "",
                    snap["feedback"],
                    gr.update(visible=False),
                    gr.update(visible=False),
                    gr.update(visible=True),
                    gr.update(visible=False),
                    gr.update(visible=False),
                )

            qtype = str(snap["question_type"]).replace("_", " ").title()
            status = (
                "Status: question ready"
                f" | attempt: {snap['attempt']}/3"
                f" | type: {qtype}"
                f" | queued: {snap['queue_depth']}"
                f" | idle: {snap['idle_seconds']}s"
            )
            context_excerpt = str(snap.get("context_excerpt") or "").strip()
            context_preview = str(snap.get("context_preview") or "").strip()
            context_language = str(snap.get("context_preview_language") or "text").strip() or "text"
            context_display = ""
            if context_preview:
                context_display = (
                    "### Context Preview\n```"
                    + context_language
                    + "\n"
                    + context_preview
                    + "\n```"
                )
            return (
                sessions_grid,
                sessions_line,
                status,
                str(snap["question"]),
                context_display,
                snap["feedback"],
                gr.update(visible=True),
                gr.update(visible=True),
                gr.update(visible=False),
                gr.update(visible=True),
                gr.update(visible=bool(context_preview or context_excerpt)),
            )

        def submit_ui(answer_text: str) -> tuple[str, str]:
            result = state.submit_answer(answer_text or "")
            if not result["accepted"]:
                return str(result["message"]), answer_text
            return str(result["message"]), ""

        submit_btn.click(fn=submit_ui, inputs=[answer_box], outputs=[feedback_md, answer_box])
        timer = gr.Timer(value=0.5, active=True)
        timer.tick(
            fn=poll_ui,
            outputs=[
                sessions_html,
                sessions_md,
                status_md,
                question_md,
                context_md,
                feedback_md,
                answer_box,
                submit_btn,
                sessions_html,
                sessions_md,
                context_md,
            ],
        )

    app = FastAPI()

    @app.get("/api/health")
    async def api_health() -> Any:
        snap = state.snapshot()
        return JSONResponse(
            {
                "status": "ok",
                "pid": os.getpid(),
                "compat_version": SIDECAR_COMPAT_VERSION,
                "supports_session_detach": True,
                "queue_depth": snap["queue_depth"],
                "has_current_question": snap["has_current_question"],
                "current_question_id": snap["current_question_id"],
            }
        )

    @app.get("/api/status")
    async def api_status() -> Any:
        snap = state.snapshot()
        return JSONResponse({**snap, "pid": os.getpid()})

    @app.post("/api/question")
    async def api_question(body: dict[str, Any]) -> Any:
        try:
            if not isinstance(body, dict):
                return JSONResponse({"error": "Invalid payload"}, status_code=400)

            status = state.enqueue_question(body)
            return JSONResponse({"status": "queued", **status}, status_code=202)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/answer")
    async def api_answer(request: Request) -> Any:
        question_id = str(request.query_params.get("question_id", "")).strip()
        session_id = str(request.query_params.get("session_id", "")).strip()
        if not question_id:
            return JSONResponse(
                {"error": "Missing question_id query parameter"},
                status_code=400,
            )

        status = state.answer_status(question_id, session_id=session_id)
        if status["state"] == "answered":
            return JSONResponse(status, status_code=200)
        if status["state"] == "pending":
            return JSONResponse(status, status_code=202)
        return JSONResponse(status, status_code=404)

    @app.post("/api/submit")
    async def api_submit(body: dict[str, Any]) -> Any:
        if not isinstance(body, dict):
            return JSONResponse({"error": "Invalid payload"}, status_code=400)

        result = state.submit_answer(
            str(body.get("answer", "")),
            question_id=str(body.get("question_id", "")),
            session_id=str(body.get("session_id", "")),
        )
        if result["accepted"]:
            return JSONResponse(result, status_code=202)

        code = result.get("code")
        status_code = 409
        if code == "empty_answer":
            status_code = 400
        elif code == "session_mismatch":
            status_code = 403
        return JSONResponse(result, status_code=status_code)

    @app.post("/api/session/detach")
    async def api_session_detach(body: dict[str, Any]) -> Any:
        if not isinstance(body, dict):
            return JSONResponse({"error": "Invalid payload"}, status_code=400)

        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            return JSONResponse({"error": "Missing session_id"}, status_code=400)

        reason = str(body.get("reason") or "session_detach").strip() or "session_detach"
        result = state.detach_session_questions(session_id, reason=reason)
        return JSONResponse(result, status_code=200)

    @app.post("/api/shutdown")
    async def api_shutdown(request: Request) -> Any:
        force = str(request.query_params.get("force", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not state.can_shutdown(force=force):
            return JSONResponse({"status": "busy", "reason": "queue_not_empty"}, status_code=409)
        server = server_ref.get("server")
        if server is not None:
            server.should_exit = True
        return JSONResponse({"status": "shutting_down", "force": force})

    app = gr.mount_gradio_app(app, blocks, path="/")

    def _idle_shutdown_monitor() -> None:
        while True:
            time.sleep(1.0)
            server = server_ref.get("server")
            if server is None or server.should_exit:
                return
            state.prune_orphaned_questions(reason="idle_monitor")
            if state.should_idle_shutdown():
                server.should_exit = True
                return

    config = UvicornConfig(app=app, host="127.0.0.1", port=port, log_level="error")
    server = UvicornServer(config)
    server_ref["server"] = server
    threading.Thread(target=_idle_shutdown_monitor, daemon=True).start()
    server.run()
def _as_int(value: Any, *, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _iso_from_ts(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _count_active_leases(*, prune: bool, state_dir: Path) -> int:
    from qa.sidecar.leases import count_active_leases

    return count_active_leases(prune=prune, state_dir=state_dir)


def _active_leased_sessions(*, state_dir: Path) -> set[str]:
    from qa.sidecar.leases import list_active_leases

    records = list_active_leases(prune=True, state_dir=state_dir)
    return {
        str(record.get("session_id") or "").strip()
        for record in records
        if str(record.get("session_id") or "").strip()
    }


def _limit_detached_sessions(
    sessions: list[dict[str, Any]],
    *,
    max_detached: int,
) -> list[dict[str, Any]]:
    if max_detached < 0:
        max_detached = 0

    detached_count = 0
    limited: list[dict[str, Any]] = []
    for item in sessions:
        if not isinstance(item, dict):
            continue
        is_detached = str(item.get("state") or "") == "detached"
        if is_detached:
            if detached_count >= max_detached:
                continue
            detached_count += 1
        limited.append(item)

    return limited


def _build_context_preview(
    context_excerpt: str,
    *,
    focus_text: str = "",
    max_lines: int = 16,
    max_chars: int = 2200,
) -> str:
    text = (context_excerpt or "").strip()
    if not text:
        return ""

    lines = text.splitlines()
    section = _extract_most_relevant_section(lines, focus_text=focus_text)
    selected = _trim_relevant_window(section if section else lines, focus_text=focus_text, max_lines=max_lines)
    preview = "\n".join(selected).strip()
    if len(preview) > max_chars:
        preview = preview[: max_chars - 3].rstrip() + "..."

    has_more = len(section) > max_lines if section else len(lines) > max_lines
    if has_more and not preview.endswith("..."):
        preview += "\n..."
    return preview


def _extract_most_relevant_section(lines: list[str], *, focus_text: str) -> list[str]:
    sections: list[tuple[str, list[str]]] = []
    for heading in ("## New Code", "## Unified Diff", "## Surrounding Code"):
        section = _extract_markdown_section(lines, heading=heading)
        if section:
            sections.append((heading, section))

    if not sections:
        return []

    if not focus_text.strip():
        return sections[0][1]

    best_section = sections[0][1]
    best_score = _score_relevance("\n".join(best_section), focus_text)
    for _heading, section in sections[1:]:
        score = _score_relevance("\n".join(section), focus_text)
        if score > best_score:
            best_score = score
            best_section = section
    return best_section


def _trim_relevant_window(lines: list[str], *, focus_text: str, max_lines: int) -> list[str]:
    if len(lines) <= max_lines:
        return lines

    if not focus_text.strip():
        return lines[:max_lines]

    best_start = 0
    best_score = float("-inf")
    upper = max(len(lines) - max_lines + 1, 1)
    for start in range(upper):
        window = lines[start : start + max_lines]
        score = _score_relevance("\n".join(window), focus_text)
        if score > best_score:
            best_score = score
            best_start = start
    return lines[best_start : best_start + max_lines]


def _score_relevance(text: str, focus_text: str) -> float:
    snippet = text.lower()
    tokens = _focus_tokens(focus_text)
    if not tokens:
        return 0.0

    score = 0.0
    for token in tokens:
        if token in snippet:
            score += 2.0
    for token in tokens:
        score += min(snippet.count(token), 3) * 0.25
    return score


def _focus_tokens(text: str) -> list[str]:
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
    stop_words = {
        "the",
        "and",
        "for",
        "from",
        "with",
        "that",
        "this",
        "when",
        "into",
        "your",
        "code",
        "function",
        "line",
        "fill",
        "blank",
        "blanks",
        "context",
        "excerpt",
        "weather",
    }
    tokens: list[str] = []
    for token in raw:
        lowered = token.lower()
        if lowered in stop_words:
            continue
        if lowered not in tokens:
            tokens.append(lowered)
        if len(tokens) >= 24:
            break
    return tokens


def _extract_metadata_value(context_excerpt: str, *, key: str) -> str:
    for line in context_excerpt.splitlines():
        stripped = line.strip()
        prefix = f"- {key}:"
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return ""


def _resolve_preview_language(*, explicit_language: str, path: str, preview_text: str) -> str:
    language = explicit_language.strip().lower()
    if language and language != "text":
        return language

    if path:
        detected = _detect_language(path)
        if detected:
            return detected

    if preview_text.lstrip().startswith(("diff --git", "@@ ", "--- ", "+++ ", "+", "-")):
        return "diff"

    return "text"


def _detect_language(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".json": "json",
        ".md": "markdown",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".rs": "rust",
        ".java": "java",
        ".html": "html",
        ".css": "css",
    }.get(suffix)


def _extract_markdown_section(lines: list[str], *, heading: str) -> list[str]:
    start_index: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start_index = idx
            break

    if start_index is None:
        return []

    end_index = len(lines)
    for idx in range(start_index + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("## "):
            end_index = idx
            break

    return lines[start_index:end_index]


def _render_session_cards(sessions: list[dict[str, Any]]) -> str:
    cards: list[str] = []
    for item in sessions[:9]:
        state = str(item.get("state") or "sleeping")
        emoji = html.escape(str(item.get("emoji") or "⚪"))
        label = html.escape(str(item.get("label") or state.replace("_", " ").title()))
        sid = str(item.get("session_id") or "")
        short_sid = html.escape(sid[:8] if sid else "unknown")
        detail = html.escape(str(item.get("detail") or ""))
        border = _STATE_BORDER_COLORS.get(state, "#8E8E8E")
        selected_style = " box-shadow: 0 0 0 2px rgba(255,255,255,0.22) inset;" if item.get(
            "is_selected"
        ) else ""
        cards.append(
            f"""
<div style="border:2px solid {border}; border-radius:12px; padding:12px; min-height:126px;
            background: rgba(255,255,255,0.03);{selected_style}">
  <div style="font-size:36px; line-height:1.1; margin-bottom:8px;">{emoji}</div>
  <div style="font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; margin-bottom:6px;">{short_sid}</div>
  <div style="font-weight:600; margin-bottom:4px;">{label}</div>
  <div style="font-size:12px; opacity:0.8;">{detail}</div>
</div>
"""
        )

    if not cards:
        return "<div>No session activity yet.</div>"

    return (
        "<div style='display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));"
        "gap:10px;margin-top:8px;margin-bottom:8px;'>"
        + "".join(cards)
        + "</div>"
    )


if __name__ == "__main__":
    port = int(os.environ.get("VIBECHECK_SIDECAR_PORT", "7865"))
    print(f"Starting VibeCheck QA sidecar on port {port}", file=sys.stderr)
    run_server(port=port)
