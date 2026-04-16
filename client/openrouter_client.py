from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class OpenRouterClientError(RuntimeError):
    """Raised when a call to OpenRouter fails or returns malformed content."""


class _OpenRouterRateLimitError(RuntimeError):
    """Raised when OpenRouter returns an HTTP 429 response."""


@dataclass(slots=True)
class InputMessage:
    role: str
    content: str

    def as_payload(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class OpenRouterClient:
    """OpenRouter client using the stateless Responses API."""

    _FREE_GEMMA_MODEL = "google/gemma-4-26b-a4b-it:free"
    _PAID_GEMMA_MODEL = "google/gemma-4-26b-a4b-it"
    _MODEL_ENV = "VIBECHECK_GATE_MODEL"
    _FALLBACK_MODEL_ENV = "VIBECHECK_GATE_FALLBACK_MODEL"

    def __init__(
        self,
        model: str | None = None,
        site_url: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        from core.config import resolve_provider_config

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

        resolved_model = (model or os.environ.get(self._MODEL_ENV, "").strip() or self._FREE_GEMMA_MODEL)
        fallback_override = os.environ.get(self._FALLBACK_MODEL_ENV, "").strip()

        fallback_model: str | None = None
        if fallback_override:
            fallback_model = fallback_override
        elif resolved_model == self._FREE_GEMMA_MODEL:
            fallback_model = self._PAID_GEMMA_MODEL

        if fallback_model == resolved_model:
            fallback_model = None

        self._api_key = config.api_key
        self._model = resolved_model
        self._fallback_model = fallback_model
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
        max_retries: int = 3,
    ) -> str:
        payload: dict[str, Any] = {
            "input": _normalize_input(input_data),
        }
        if max_output_tokens is not None:
            payload["max_output_tokens"] = max_output_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        if extra_body:
            payload.update(extra_body)

        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                parsed = self._request_response({**payload, "model": self._model})
                if not isinstance(parsed, dict):
                    raise OpenRouterClientError("OpenRouter returned an unexpected response shape.")
                text = _extract_output_text(parsed)
                if text is None:
                    raise OpenRouterClientError("OpenRouter response did not include output text.")
                return text
            except _OpenRouterRateLimitError as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    wait_time = (2**attempt) + 0.5
                    time.sleep(wait_time)
                elif self._fallback_model is not None:
                    try:
                        parsed = self._request_response({**payload, "model": self._fallback_model})
                        text = _extract_output_text(parsed)
                        if text is not None:
                            return text
                    except Exception as fallback_exc:
                        last_exc = fallback_exc

        if last_exc is not None:
            raise OpenRouterClientError(
                f"OpenRouter request failed after {max_retries} retries: {last_exc}"
            ) from last_exc
        raise OpenRouterClientError("OpenRouter request failed with unknown error")

    def _request_response(self, payload: dict[str, Any]) -> dict[str, Any]:
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
            if exc.code == 429:
                raise _OpenRouterRateLimitError(detail or exc.reason) from exc
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
        return parsed

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
