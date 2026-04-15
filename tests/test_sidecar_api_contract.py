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


def _wait_health(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1):
                return
        except Exception:
            time.sleep(0.1)
    raise AssertionError("sidecar health did not come up")
