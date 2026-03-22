from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from core.config import resolve_provider_config


class OpenRouterClientError(RuntimeError):
    """Raised when a call to OpenRouter fails or returns malformed content."""


@dataclass(slots=True)
class InputMessage:
    role: str
    content: str

    def as_payload(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class OpenRouterClient:
    """OpenRouter client using the stateless Responses API."""

    def __init__(
        self,
        model: str = "openai/gpt-4o-mini",
        site_url: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        try:
            config = resolve_provider_config()
        except FileNotFoundError as exc:
            raise OpenRouterClientError(
                "OpenRouter credentials are required. Set OPENROUTER_API_KEY or run 'vibecheck auth'."
            ) from exc

        if not config.api_key:
            raise OpenRouterClientError(
                "OpenRouter credentials are required. Set OPENROUTER_API_KEY or run 'vibecheck auth'."
            )

        self._api_key = config.api_key
        self._model = model
        self._endpoint = f"{config.base_url.rstrip('/')}/responses"
        self._app_name = "VibeCheck"
        self._site_url = site_url
        self._timeout_seconds = timeout_seconds

    def create_response(
        self,
        input_data: str | list[InputMessage] | list[dict[str, Any]],
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "input": _normalize_input(input_data),
        }
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        if extra_body:
            payload.update(extra_body)

        req = request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise OpenRouterClientError(
                f"OpenRouter request failed with HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except error.URLError as exc:
            raise OpenRouterClientError(f"OpenRouter network error: {exc.reason}") from exc

        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise OpenRouterClientError("OpenRouter returned invalid JSON.") from exc

        if not isinstance(parsed, dict):
            raise OpenRouterClientError("OpenRouter returned an unexpected response shape.")

        text = _extract_output_text(parsed)
        if text is None:
            raise OpenRouterClientError("OpenRouter response did not include output text.")
        return text

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._site_url:
            headers["HTTP-Referer"] = self._site_url
        if self._app_name:
            headers["X-Title"] = self._app_name
        return headers


def _normalize_input(input_data: str | list[InputMessage] | list[dict[str, Any]]) -> Any:
    if isinstance(input_data, str):
        return input_data
    return [
        item.as_payload() if isinstance(item, InputMessage) else dict(item) for item in input_data
    ]


def _extract_output_text(response: dict[str, Any]) -> str | None:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    output = response.get("output")
    if not isinstance(output, list):
        return None

    text_parts: list[str] = []
    for output_item in output:
        if not isinstance(output_item, dict):
            continue
        content = output_item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type not in {"output_text", "text"}:
                continue
            value = block.get("text")
            if isinstance(value, str) and value.strip():
                text_parts.append(value.strip())

    if text_parts:
        return "\n".join(text_parts)
    return None
