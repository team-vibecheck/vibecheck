from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from client.openrouter_client import InputMessage, OpenRouterClient, OpenRouterClientError


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

    client = OpenRouterClient(
        api_key="test-key",
        default_model="openai/gpt-4o-mini",
        app_name="vibecheck",
        site_url="https://example.com",
    )

    response = client.create_response(
        [
            InputMessage(role="system", content="You are concise."),
            InputMessage(role="user", content="Say hi."),
        ],
        instructions="Answer in one sentence.",
        temperature=0.3,
        max_output_tokens=64,
    )

    sent_body = json.loads(str(captured["body"]))
    assert sent_body["model"] == "openai/gpt-4o-mini"
    assert sent_body["input"][1]["content"] == "Say hi."
    assert sent_body["instructions"] == "Answer in one sentence."
    assert sent_body["temperature"] == 0.3
    assert sent_body["max_output_tokens"] == 64

    sent_headers = dict(captured["headers"])
    assert sent_headers["Authorization"] == "Bearer test-key"
    assert sent_headers["HTTP-Referer"] == "https://example.com"
    assert sent_headers["X-Title"] == "vibecheck"
    assert response["id"] == "resp_1"


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

    monkeypatch.setattr("client.openai_client.request.urlopen", fake_urlopen)
    client = OpenAIClient(api_key="test-key")

    text = client.complete_text("test")

    assert text == "Line one.\nLine two."


def test_client_raises_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(OpenRouterClientError, match="OPENROUTER_API_KEY"):
        OpenAIClient()


def test_client_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout: float):  # type: ignore[no-untyped-def]
        del req, timeout
        raise HTTPError(
            url="https://openrouter.ai/api/v1/responses",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=BytesIO(b'{"error":"bad key"}'),
        )

    monkeypatch.setattr("client.openai_client.request.urlopen", fake_urlopen)
    client = OpenAIClient(api_key="bad-key")

    with pytest.raises(OpenRouterClientError, match="HTTP 401"):
        client.create_response("hello")
