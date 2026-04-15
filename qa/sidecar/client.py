from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from core.models import QAPacket
from qa.sidecar.lifecycle import (
    cleanup_pid_file,
    ensure_sidecar_running,
    get_config,
    shutdown_sidecar,
)
from qa.sidecar.presence import set_session_state
from qa.sidecar.ui_open import open_ui_once_for_pid

if TYPE_CHECKING:
    from core.event_logger import EventLogger


@dataclass
class QuestionPayload:
    question: str
    attempt: int
    question_type: str
    context_excerpt: str
    session_id: str
    proposal_id: str
    tool_use_id: str
    request_id: str


class SidecarClient:
    def __init__(
        self,
        event_logger: EventLogger | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self._logger = event_logger
        self._state_dir = state_dir or Path(os.environ.get("VIBECHECK_STATE_DIR", "state"))
        self._config = get_config()
        self._port: int | None = None
        self._last_pid: int | None = None
        self._last_question_time: float = 0.0
        self._idle_shutdown_enabled = True
        self._auto_open_browser = _should_auto_open_browser()
        self._qa_reopen_cooldown_seconds = int(
            os.environ.get("VIBECHECK_SIDECAR_QA_REOPEN_COOLDOWN", "90")
        )

    def ask(
        self,
        question: str,
        attempt: int,
        packet: QAPacket,
        *,
        session_id: str = "",
        proposal_id: str = "",
        tool_use_id: str = "",
    ) -> str:
        _import_gradio_or_raise()
        effective_session = session_id or "unknown_session"
        effective_proposal = proposal_id or "unknown_proposal"
        set_session_state(
            effective_session,
            "qa_waiting_submission",
            state_dir=self._state_dir,
            detail="Ensuring sidecar and queueing question",
            proposal_id=effective_proposal,
            tool_use_id=tool_use_id,
            attempt_number=attempt,
        )
        port, pid = ensure_sidecar_running()
        self._port = port
        if self._auto_open_browser and self._should_reopen_for_qa():
            open_ui_once_for_pid(
                port,
                pid,
                state_dir=self._state_dir,
                allow_reopen_after_seconds=self._qa_reopen_cooldown_seconds,
            )
        self._last_pid = pid
        self._log(
            "sidecar_spawned" if self._is_first_spawn() else "sidecar_health_check_ok",
            port=port,
            pid=pid,
        )
        question_id = self._push_question(
            question,
            attempt,
            packet,
            port,
            session_id=effective_session,
            proposal_id=effective_proposal,
            tool_use_id=tool_use_id,
        )
        set_session_state(
            effective_session,
            "qa_waiting_submission",
            state_dir=self._state_dir,
            detail="Question queued, waiting for answer",
            proposal_id=effective_proposal,
            tool_use_id=tool_use_id,
            attempt_number=attempt,
            question_id=question_id,
        )
        answer = self._poll_answer(port, question_id=question_id, session_id=effective_session)
        self._last_question_time = time.time()
        return answer

    def configure(
        self,
        *,
        event_logger: EventLogger | None = None,
        state_dir: Path | None = None,
        auto_open_browser: bool | None = None,
    ) -> None:
        if event_logger is not None:
            self._logger = event_logger
        if state_dir is not None:
            self._state_dir = state_dir
        if auto_open_browser is not None:
            self._auto_open_browser = auto_open_browser

    def _is_first_spawn(self) -> bool:
        return not (self._state_dir / "qa" / "sidecar.pid").exists()

    def _push_question(
        self,
        question: str,
        attempt: int,
        packet: QAPacket,
        port: int,
        *,
        session_id: str,
        proposal_id: str,
        tool_use_id: str,
    ) -> str:
        request_id = f"req-{uuid4().hex}"
        payload = QuestionPayload(
            question=question,
            attempt=attempt,
            question_type=packet.question_type,
            context_excerpt=packet.context_excerpt or "",
            session_id=session_id or "unknown_session",
            proposal_id=proposal_id or "unknown_proposal",
            tool_use_id=tool_use_id,
            request_id=request_id,
        )
        body = json.dumps(
            {
                "question": payload.question,
                "attempt": payload.attempt,
                "question_type": payload.question_type,
                "context_excerpt": payload.context_excerpt,
                "session_id": payload.session_id,
                "proposal_id": payload.proposal_id,
                "tool_use_id": payload.tool_use_id,
                "request_id": payload.request_id,
            }
        ).encode("utf-8")

        last_error: str | None = None
        last_question_id: str | None = None
        for _ in range(5):
            try:
                request_obj = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/question",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                req = urllib.request.urlopen(request_obj, timeout=10)
                if req.status == 202:
                    response_body = req.read().decode("utf-8", errors="replace")
                    decoded = json.loads(response_body) if response_body else {}
                    if isinstance(decoded, dict):
                        question_id = decoded.get("question_id")
                        if isinstance(question_id, str) and question_id.strip():
                            last_question_id = question_id.strip()
                            self._log("sidecar_question_queued", question_id=last_question_id)
                            return last_question_id
                    last_error = "missing question_id in enqueue response"
                    time.sleep(0.2)
                    continue
                last_error = f"unexpected status {req.status}"
            except urllib.error.HTTPError as exc:
                payload = exc.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code}: {payload or exc.reason}"
            except urllib.error.URLError:
                last_error = "network error"
            except ValueError:
                last_error = "invalid JSON response"
            time.sleep(0.2)

        if last_question_id:
            return last_question_id
        detail = f" ({last_error})" if last_error else ""
        msg = f"Failed to push question to sidecar on port {port}{detail}"
        raise RuntimeError(msg)

    def _poll_answer(self, port: int, *, question_id: str, session_id: str) -> str:
        timeout = self._config["timeout"]
        poll_interval = self._config["poll_interval"]
        start = time.time()

        query = urllib.parse.urlencode(
            {
                "question_id": question_id,
                "session_id": session_id or "unknown_session",
            }
        )
        endpoint = f"http://127.0.0.1:{port}/api/answer?{query}"

        while time.time() - start < timeout:
            try:
                req = urllib.request.urlopen(endpoint, timeout=5)
                if req.status == 200:
                    data = json.loads(req.read().decode("utf-8"))
                    answer = str(data.get("answer", ""))
                    self._log(
                        "sidecar_answer_received",
                        question_id=question_id,
                        answer_length=len(answer),
                    )
                    set_session_state(
                        session_id,
                        "qa_evaluating",
                        state_dir=self._state_dir,
                        detail="Answer received; evaluating",
                        question_id=question_id,
                    )
                    return answer
                if req.status == 202:
                    time.sleep(poll_interval)
                    continue
            except urllib.error.HTTPError as exc:
                if exc.code in (202, 204, 404):
                    pass
                elif exc.code == 422:
                    payload = exc.read().decode("utf-8", errors="replace")
                    self._log(
                        "sidecar_poll_protocol_error",
                        question_id=question_id,
                        status_code=422,
                        payload=payload,
                    )
                    msg = f"Sidecar answer protocol mismatch on port {port}: {payload or '422'}"
                    raise RuntimeError(msg) from exc
            except Exception:
                pass
            time.sleep(poll_interval)

        self._log("sidecar_poll_timeout", question_id=question_id)
        return ""

    def check_idle_and_shutdown(self) -> bool:
        if not self._idle_shutdown_enabled:
            return False
        if self._port is None:
            return False

        idle_time = time.time() - self._last_question_time
        if idle_time > self._config["idle_timeout"]:
            try:
                shutdown_sidecar(self._port)
                self._log(
                    "sidecar_idle_shutdown",
                    idle_seconds=int(idle_time),
                )
                cleanup_pid_file()
                return True
            except Exception:
                pass
        return False

    def _log(self, event: str, **kwargs: object) -> None:
        if self._logger is not None:
            details = {k: v for k, v in kwargs.items()}
            self._logger.log(event, details=details if details else None)

    def _best_effort_open_browser(self, port: int) -> None:
        url = f"http://127.0.0.1:{port}/"
        with contextlib.suppress(Exception):
            if webbrowser.open(url, new=1):
                return

        with contextlib.suppress(Exception):
            subprocess.run(
                ["xdg-open", url],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def _should_reopen_for_qa(self) -> bool:
        if self._qa_reopen_cooldown_seconds <= 0:
            return True
        if self._last_question_time <= 0:
            return False
        elapsed = time.time() - self._last_question_time
        return elapsed >= self._qa_reopen_cooldown_seconds


def _import_gradio_or_raise() -> None:
    import importlib.util

    if importlib.util.find_spec("gradio") is None:
        raise RuntimeError("Gradio is not installed. Install with: uv pip install 'vibecheck[ui]'")


def _should_auto_open_browser() -> bool:
    # Keep browser opening friendly in real interactive runs, but never do this in
    # automated environments unless explicitly enabled.
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("CI"):
        return _env_flag("VIBECHECK_SIDECAR_AUTO_OPEN_BROWSER", default=False)
    return _env_flag("VIBECHECK_SIDECAR_AUTO_OPEN_BROWSER", default=True)


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
