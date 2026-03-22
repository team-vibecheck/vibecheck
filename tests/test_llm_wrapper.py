from __future__ import annotations

import os
from pathlib import Path

from core.config import ProviderConfig, save_config


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


def test_get_llm_client_uses_saved_config_when_env_missing(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeChatOpenRouter:
        def __init__(self, *, model: str, temperature: float) -> None:
            captured["model"] = model
            captured["temperature"] = temperature
            captured["api_key"] = os.environ.get("OPENROUTER_API_KEY")

    from qa import llm_wrapper as llm_wrapper_module

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("core.config._CONFIG_FILE", _write_config(tmp_path))
    monkeypatch.setattr(llm_wrapper_module, "ChatOpenRouter", FakeChatOpenRouter)
    monkeypatch.setattr(llm_wrapper_module, "_client", None)

    client = llm_wrapper_module.get_llm_client()

    assert isinstance(client, llm_wrapper_module.LLMQAClient)
    assert captured["api_key"] == "stored-key"
    assert captured["model"] == "openai/gpt-4o-mini"
    assert captured["temperature"] == 0.3
