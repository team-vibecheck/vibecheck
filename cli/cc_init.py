"""``vibecheck cc init`` — bootstrap Claude Code hook configuration."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

from core.competence_store import default_competence_model, save_competence_model

_STATE_SUBDIRS = ["logs", "qa/pending", "qa/results", "agg"]
_HOOK_TIMEOUT_SECONDS = 600


def run_cc_init(*, target_dir: str | None = None) -> None:
    source_root = Path(__file__).resolve().parents[1]
    project_root = Path(target_dir).resolve() if target_dir else Path.cwd()
    claude_dir = project_root / ".claude"
    settings_path = claude_dir / "settings.json"
    state_dir = project_root / "state"
    pre_tool_command = _hook_command(source_root, project_root, module="hooks.pre_tool_use")
    user_prompt_submit_command = _hook_command(
        source_root,
        project_root,
        module="hooks.user_prompt_submit",
    )
    session_start_command = _hook_command(source_root, project_root, module="hooks.session_start")
    session_end_command = _hook_command(source_root, project_root, module="hooks.session_end")

    # 1. Create/merge .claude/settings.json
    claude_dir.mkdir(exist_ok=True)
    settings = _load_or_empty(settings_path)
    _merge_hook(settings, pre_tool_command)
    _merge_session_hook(settings, "UserPromptSubmit", user_prompt_submit_command)
    _merge_session_hook(settings, "SessionStart", session_start_command)
    _merge_session_hook(settings, "SessionEnd", session_end_command)
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
    print(f"to gate Edit, Write, and MultiEdit calls via: {pre_tool_command}")


def _load_or_empty(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _hook_command(
    source_root: Path | None = None,
    project_root: Path | None = None,
    *,
    module: str,
) -> str:
    root = source_root or Path(__file__).resolve().parents[1]
    target = project_root or Path.cwd()
    state_dir = target / "state"
    return (
        "sh -lc "
        f"'cd {shlex.quote(str(root))} && "
        f"VIBECHECK_STATE_DIR={shlex.quote(str(state_dir))} "
        f"{shlex.quote(sys.executable)} -m {shlex.quote(module)}'"
    )


def _merge_hook(settings: dict, hook_command: str | None = None) -> None:
    """Add the VibeCheck PreToolUse hook without clobbering existing hooks."""
    command = hook_command or _hook_command(module="hooks.pre_tool_use")
    hooks = settings.setdefault("hooks", {})
    pre_tool_use: list[dict[str, Any]] = hooks.setdefault("PreToolUse", [])

    # If a VibeCheck hook already exists, repair/update it in place.
    for entry in pre_tool_use:
        entry_hooks = entry.get("hooks", [])
        for h in entry_hooks:
            existing_command = h.get("command", "")
            if (
                "hooks.pre_tool_use" in existing_command
                or "pre_tool_use.py" in existing_command
                or existing_command == command
            ):
                h["type"] = "command"
                h["command"] = command
                h["timeout"] = _HOOK_TIMEOUT_SECONDS
                if not entry.get("matcher"):
                    entry["matcher"] = "Edit|Write|MultiEdit"
                return

    pre_tool_use.append(
        {
            "matcher": "Edit|Write|MultiEdit",
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": _HOOK_TIMEOUT_SECONDS,
                }
            ],
        }
    )


def _merge_session_hook(settings: dict, event_name: str, command: str) -> None:
    hooks = settings.setdefault("hooks", {})
    event_hooks: list[dict[str, Any]] = hooks.setdefault(event_name, [])

    for entry in event_hooks:
        entry_hooks = entry.get("hooks", [])
        for hook in entry_hooks:
            existing = hook.get("command", "")
            if existing == command or _hook_matches_event(existing, event_name):
                hook["type"] = "command"
                hook["command"] = command
                hook["timeout"] = _HOOK_TIMEOUT_SECONDS
                if event_name == "SessionStart" and not entry.get("matcher"):
                    entry["matcher"] = "startup|resume"
                return

    entry: dict[str, Any] = {
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": _HOOK_TIMEOUT_SECONDS,
            }
        ]
    }
    if event_name == "SessionStart":
        entry["matcher"] = "startup|resume"
    event_hooks.append(entry)


def _hook_matches_event(existing_command: str, event_name: str) -> bool:
    module_map = {
        "SessionStart": "hooks.session_start",
        "SessionEnd": "hooks.session_end",
        "UserPromptSubmit": "hooks.user_prompt_submit",
    }
    marker = module_map.get(event_name, "")
    return bool(marker and marker in existing_command)
