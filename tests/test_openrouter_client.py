from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from client.openrouter_client import InputMessage, OpenRouterClient, OpenRouterClientError
from core.config import ProviderConfig, save_config


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        del exc_type, exc_val, exc_tb
        return False


def _write_config(tmp_path: Path) -> Path:
    config_file = tmp_path / "config.toml"
    save_config(
        ProviderConfig(
            api_key="stored-key",
            base_url="https://openrouter.ai/api/v1",
            default_model="anthropic/claude-sonnet-4",
        ),
        config_file,
    )
    return config_file


def test_create_response_sends_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout: float):  # type: ignore[no-untyped-def]
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["body"] = req.data.decode("utf-8")
        captured["timeout"] = timeout
        return FakeHTTPResponse(
            {
                "id": "resp_1",
                "output_text": "Hello from OpenRouter.",
            }
        )

    monkeypatch.setattr("client.openrouter_client.request.urlopen", fake_urlopen)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    client = OpenRouterClient(
        model="openai/gpt-4o-mini",
        site_url="https://example.com",
    )

    response = client.create_response(
        [
            InputMessage(role="system", content="You are concise."),
            InputMessage(role="user", content="Say hi."),
        ],
        temperature=0.3,
        max_output_tokens=64,
    )

    sent_body = json.loads(str(captured["body"]))
    assert sent_body["model"] == "openai/gpt-4o-mini"
    assert sent_body["input"][1]["content"] == "Say hi."
    assert sent_body["temperature"] == 0.3
    assert sent_body["max_output_tokens"] == 64

    headers_dict = captured["headers"]
    assert isinstance(headers_dict, dict)
    auth_value = headers_dict.get("Authorization", "")
    assert "test-key" in auth_value
    referer_value = headers_dict.get("Http-referer", "")
    assert "example.com" in referer_value
    title_value = headers_dict.get("X-title", "")
    assert "VibeCheck" in title_value
    assert response == "Hello from OpenRouter."


def test_complete_text_reads_nested_output_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout: float):  # type: ignore[no-untyped-def]
        del req, timeout
        return FakeHTTPResponse(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "Line one."},
                            {"type": "output_text", "text": "Line two."},
                        ],
                    }
                ]
            }
        )

    monkeypatch.setattr("client.openrouter_client.request.urlopen", fake_urlopen)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = OpenRouterClient()

    text = client.create_response("test")

    assert text == "Line one.\nLine two."


def test_client_raises_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("core.config._CONFIG_FILE", Path("/definitely/missing/config.toml"))

    with pytest.raises(OpenRouterClientError, match="OpenRouter credentials are required"):
        OpenRouterClient()


def test_client_uses_saved_config_when_env_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout: float):  # type: ignore[no-untyped-def]
        captured["headers"] = dict(req.headers)
        captured["timeout"] = timeout
        return FakeHTTPResponse({"output_text": "Hello from config."})

    monkeypatch.setattr("client.openrouter_client.request.urlopen", fake_urlopen)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("core.config._CONFIG_FILE", _write_config(tmp_path))

    client = OpenRouterClient()

    response = client.create_response("hello")

    headers_dict = captured["headers"]
    assert isinstance(headers_dict, dict)
    assert "stored-key" in headers_dict.get("Authorization", "")
    assert response == "Hello from config."


def test_env_wins_over_saved_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout: float):  # type: ignore[no-untyped-def]
        del timeout
        captured["headers"] = dict(req.headers)
        return FakeHTTPResponse({"output_text": "Hello from env."})

    monkeypatch.setattr("client.openrouter_client.request.urlopen", fake_urlopen)
    monkeypatch.setattr("core.config._CONFIG_FILE", _write_config(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")

    client = OpenRouterClient()

    response = client.create_response("hello")

    headers_dict = captured["headers"]
    assert isinstance(headers_dict, dict)
    assert "env-key" in headers_dict.get("Authorization", "")
    assert "stored-key" not in headers_dict.get("Authorization", "")
    assert response == "Hello from env."


def test_client_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout: float):  # type: ignore[no-untyped-def]
        del req, timeout
        raise HTTPError(
            url="https://openrouter.ai/api/v1/responses",
            code=401,
            msg="Unauthorized",
            hdrs={},  # type: ignore[arg-type]
            fp=BytesIO(b'{"error":"bad key"}'),
        )

    monkeypatch.setattr("client.openrouter_client.request.urlopen", fake_urlopen)
    monkeypatch.setenv("OPENROUTER_API_KEY", "bad-key")
    client = OpenRouterClient()

    with pytest.raises(OpenRouterClientError, match="HTTP 401"):
        client.create_response("hello")


def test_openrouter_defaults_to_gemma_26b_a4b_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    client = OpenRouterClient()

    assert client._model == "google/gemma-4-26b-a4b-it:free"  # noqa: SLF001
    assert client._fallback_model == "google/gemma-4-26b-a4b-it"  # noqa: SLF001


def test_openrouter_honors_gate_model_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("VIBECHECK_GATE_MODEL", "provider/gate-fast")
    monkeypatch.setenv("VIBECHECK_GATE_FALLBACK_MODEL", "provider/gate-safe")

    client = OpenRouterClient()

    assert client._model == "provider/gate-fast"  # noqa: SLF001
    assert client._fallback_model == "provider/gate-safe"  # noqa: SLF001
