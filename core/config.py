"""VibeCheck configuration management.

Handles loading and saving provider config from ``~/.vibecheck/config.toml``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_CONFIG_DIR = Path.home() / ".vibecheck"
_CONFIG_FILE = _CONFIG_DIR / "config.toml"

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "anthropic/claude-sonnet-4"


@dataclass(slots=True)
class ProviderConfig:
    api_key: str
    base_url: str = _DEFAULT_BASE_URL
    default_model: str = _DEFAULT_MODEL


def config_path() -> Path:
    return _CONFIG_FILE


def load_config(path: Path | None = None) -> ProviderConfig:
    """Load provider configuration from TOML file.

    Raises ``FileNotFoundError`` if the config file does not exist.
    """
    p = path or _CONFIG_FILE
    if not p.exists():
        raise FileNotFoundError(f"VibeCheck config not found at {p}. Run 'vibecheck auth' first.")
    text = p.read_text(encoding="utf-8")
    return _parse_toml(text)


def resolve_provider_config(path: Path | None = None) -> ProviderConfig:
    """Resolve provider config from env first, then fall back to saved config."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if api_key:
        return ProviderConfig(api_key=api_key)
    return load_config(path)


def save_config(cfg: ProviderConfig, path: Path | None = None) -> Path:
    """Write provider config to TOML file with restrictive permissions."""
    p = path or _CONFIG_FILE
    p.parent.mkdir(parents=True, exist_ok=True)

    content = (
        "[provider]\n"
        f'api_key = "{cfg.api_key}"\n'
        f'base_url = "{cfg.base_url}"\n'
        f'default_model = "{cfg.default_model}"\n'
    )
    p.write_text(content, encoding="utf-8")
    os.chmod(p, 0o600)
    return p


def _parse_toml(text: str) -> ProviderConfig:
    """Minimal TOML parser — only handles the flat [provider] table we write."""
    values: dict[str, str] = {}
    in_provider = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[provider]":
            in_provider = True
            continue
        if stripped.startswith("["):
            in_provider = False
            continue
        if in_provider:
            m = re.match(r'(\w+)\s*=\s*"([^"]*)"', stripped)
            if m:
                values[m.group(1)] = m.group(2)
    return ProviderConfig(
        api_key=values.get("api_key", ""),
        base_url=values.get("base_url", _DEFAULT_BASE_URL),
        default_model=values.get("default_model", _DEFAULT_MODEL),
    )
