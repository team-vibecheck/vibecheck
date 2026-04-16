from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest


def test_answer_api_uses_question_and_session_query(tmp_path: Path) -> None:
    gradio = pytest.importorskip("gradio")
    del gradio

    state_dir = tmp_path / "state"
    env = {
        **dict(**{}),
    }
    import os

    env = {**os.environ, "VIBECHECK_STATE_DIR": str(state_dir), "VIBECHECK_SIDECAR_PORT": "7891"}

    proc = subprocess.Popen(
        [
            os.environ.get("PYTHON", "python"),
            str(Path(__file__).resolve().parents[1] / "qa" / "sidecar" / "server.py"),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_health(7891)
        with urlopen("http://127.0.0.1:7891/openapi.json", timeout=5) as resp:
            spec = json.loads(resp.read().decode("utf-8"))
        params = spec["paths"]["/api/answer"]["get"].get("parameters", [])
        names = {param.get("name") for param in params if isinstance(param, dict)}
        assert "question_id" in names
        assert "request" not in names

        with urlopen("http://127.0.0.1:7891/api/health", timeout=5) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        assert health["compat_version"] >= 2
        assert health["supports_session_detach"] is True

        try:
            urlopen("http://127.0.0.1:7891/api/answer", timeout=5)
        except HTTPError as exc:
            assert exc.code == 400
        else:  # pragma: no cover
            raise AssertionError("Expected 400 when question_id is missing")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def test_status_includes_context_excerpt_and_identity_metadata(tmp_path: Path) -> None:
    gradio = pytest.importorskip("gradio")
    del gradio

    state_dir = tmp_path / "state"
    import os

    env = {**os.environ, "VIBECHECK_STATE_DIR": str(state_dir), "VIBECHECK_SIDECAR_PORT": "7892"}

    proc = subprocess.Popen(
        [
            os.environ.get("PYTHON", "python"),
            str(Path(__file__).resolve().parents[1] / "qa" / "sidecar" / "server.py"),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_health(7892)

        payload = json.dumps(
            {
                "question": "Explain this change",
                "session_id": "session-abc",
                "proposal_id": "proposal-123",
                "tool_use_id": "tool-xyz",
                "attempt": 2,
                "question_type": "plain_english",
                "context_excerpt": "diff --git a/x b/x\\n+value = 2",
                "request_id": "req-1",
            }
        ).encode("utf-8")
        question_req = urlopen(
            _request("http://127.0.0.1:7892/api/question", data=payload),
            timeout=5,
        )
        assert question_req.status == 202

        with urlopen("http://127.0.0.1:7892/api/status", timeout=5) as resp:
            status = json.loads(resp.read().decode("utf-8"))

        assert status["has_current_question"] is True
        assert status["current_session_id"] == "session-abc"
        assert status["current_proposal_id"] == "proposal-123"
        assert status["current_tool_use_id"] == "tool-xyz"
        assert status["context_excerpt"] == "diff --git a/x b/x\\n+value = 2"
        assert status["context_preview"] == "diff --git a/x b/x\\n+value = 2"
        assert status["context_preview_language"] == "diff"
        assert status["context_primary_path"] == ""
        assert status["attempt"] == 2
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def test_session_detach_endpoint_clears_owned_questions(tmp_path: Path) -> None:
    gradio = pytest.importorskip("gradio")
    del gradio

    state_dir = tmp_path / "state"
    import os

    env = {**os.environ, "VIBECHECK_STATE_DIR": str(state_dir), "VIBECHECK_SIDECAR_PORT": "7893"}

    proc = subprocess.Popen(
        [
            os.environ.get("PYTHON", "python"),
            str(Path(__file__).resolve().parents[1] / "qa" / "sidecar" / "server.py"),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_health(7893)

        q1 = json.dumps(
            {
                "question": "Q1",
                "session_id": "session-a",
                "proposal_id": "p-a",
                "tool_use_id": "t-a",
                "attempt": 1,
                "question_type": "plain_english",
                "request_id": "req-a",
            }
        ).encode("utf-8")
        q2 = json.dumps(
            {
                "question": "Q2",
                "session_id": "session-b",
                "proposal_id": "p-b",
                "tool_use_id": "t-b",
                "attempt": 1,
                "question_type": "plain_english",
                "request_id": "req-b",
            }
        ).encode("utf-8")
        q3 = json.dumps(
            {
                "question": "Q3",
                "session_id": "session-a",
                "proposal_id": "p-a2",
                "tool_use_id": "t-a2",
                "attempt": 1,
                "question_type": "plain_english",
                "request_id": "req-a2",
            }
        ).encode("utf-8")

        assert urlopen(_request("http://127.0.0.1:7893/api/question", data=q1), timeout=5).status == 202
        assert urlopen(_request("http://127.0.0.1:7893/api/question", data=q2), timeout=5).status == 202
        assert urlopen(_request("http://127.0.0.1:7893/api/question", data=q3), timeout=5).status == 202

        detach_body = json.dumps({"session_id": "session-a", "reason": "test"}).encode("utf-8")
        with urlopen(
            _request("http://127.0.0.1:7893/api/session/detach", data=detach_body), timeout=5
        ) as resp:
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))

        assert payload["removed_total"] == 2
        assert payload["removed_current"] == 1
        assert payload["removed_pending"] == 1

        with urlopen("http://127.0.0.1:7893/api/status", timeout=5) as resp:
            status = json.loads(resp.read().decode("utf-8"))

        assert status["has_current_question"] is True
        assert status["current_session_id"] == "session-b"
        assert status["queue_depth"] == 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def _request(url: str, *, data: bytes):
    from urllib.request import Request

    return Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )


def _wait_health(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1):
                return
        except Exception:
            time.sleep(0.1)
    raise AssertionError("sidecar health did not come up")
