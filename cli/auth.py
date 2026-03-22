"""``vibecheck auth`` — configure OpenRouter API key."""

from __future__ import annotations

import contextlib
import os
import sys
import webbrowser

from core.config import ProviderConfig, save_config

_OPENROUTER_KEYS_URL = "https://openrouter.ai/keys"


def run_auth(*, from_env: bool = False) -> None:
    if from_env:
        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            print("OPENROUTER_API_KEY environment variable is not set.", file=sys.stderr)
            sys.exit(1)
    else:
        print("VibeCheck uses OpenRouter for LLM access.")
        print(f"Generate an API key at: {_OPENROUTER_KEYS_URL}")
        print()

        with contextlib.suppress(Exception):
            webbrowser.open(_OPENROUTER_KEYS_URL)

        api_key = input("Paste your OpenRouter API key: ").strip()
        if not api_key:
            print("No key provided. Aborting.", file=sys.stderr)
            sys.exit(1)

    if not api_key.startswith("sk-or-"):
        print(
            "Warning: key does not start with 'sk-or-'. "
            "Saving anyway — you may be using a custom provider.",
            file=sys.stderr,
        )

    cfg = ProviderConfig(api_key=api_key)
    saved_path = save_config(cfg)
    print(f"Config saved to {saved_path} (permissions: 0600)")
    if not from_env:
        print("You can now use 'vibecheck cm init' to set up your competence model.")
