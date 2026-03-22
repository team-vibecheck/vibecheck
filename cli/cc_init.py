"""``vibecheck cc init`` — bootstrap Claude Code hook configuration."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from core.competence_store import default_competence_model, save_competence_model

_STATE_SUBDIRS = ["logs", "qa/pending", "qa/results", "agg"]


def run_cc_init() -> None:
    project_root = Path.cwd()
    claude_dir = project_root / ".claude"
    settings_path = claude_dir / "settings.json"
    state_dir = project_root / "state"
    hook_command = _hook_command()

    # 1. Create/merge .claude/settings.json
    claude_dir.mkdir(exist_ok=True)
    settings = _load_or_empty(settings_path)
    _merge_hook(settings, hook_command)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"  Wrote hook config to {settings_path}")

    # 2. Create state directory structure
    state_dir.mkdir(exist_ok=True)
    for subdir in _STATE_SUBDIRS:
        (state_dir / subdir).mkdir(parents=True, exist_ok=True)
    print(f"  Created state directories under {state_dir}/")

    # 3. Create default competence model if missing
    cm_path = state_dir / "competence_model.yaml"
    if not cm_path.exists():
        model = default_competence_model()
        save_competence_model(model, cm_path)
        print(f"  Created default competence model at {cm_path}")
    else:
        print(f"  Competence model already exists at {cm_path}")

    print("\nVibeCheck is ready. Claude Code will use the PreToolUse hook")
    print(f"to gate Edit, Write, and MultiEdit calls via: {hook_command}")


def _load_or_empty(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _hook_command() -> str:
    return f"{shlex.quote(sys.executable)} -m hooks.pre_tool_use"


def _merge_hook(settings: dict, hook_command: str | None = None) -> None:
    """Add the VibeCheck PreToolUse hook without clobbering existing hooks."""
    command = hook_command or _hook_command()
    hooks = settings.setdefault("hooks", {})
    pre_tool_use: list = hooks.setdefault("PreToolUse", [])

    # Check if a vibecheck hook already exists
    for entry in pre_tool_use:
        entry_hooks = entry.get("hooks", [])
        for h in entry_hooks:
            if "hooks.pre_tool_use" in h.get("command", "") or h.get("command", "") == command:
                return  # Already configured

    pre_tool_use.append(
        {
            "matcher": "Edit|Write|MultiEdit",
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": 30,
                }
            ],
        }
    )
