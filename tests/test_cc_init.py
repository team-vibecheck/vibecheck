"""Tests for cli.cc_init — Claude Code hook bootstrap."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from unittest import mock

from cli.cc_init import _hook_command, _load_or_empty, _merge_hook, run_cc_init


class TestMergeHook:
    def test_adds_hook_to_empty(self) -> None:
        settings: dict = {}
        _merge_hook(settings, "python -m hooks.pre_tool_use")
        assert "hooks" in settings
        assert "PreToolUse" in settings["hooks"]
        assert len(settings["hooks"]["PreToolUse"]) == 1
        hook = settings["hooks"]["PreToolUse"][0]
        assert hook["matcher"] == "Edit|Write|MultiEdit"
        assert hook["hooks"][0]["command"] == "python -m hooks.pre_tool_use"
        assert hook["hooks"][0]["timeout"] == 600

    def test_no_duplicate(self) -> None:
        settings: dict = {}
        _merge_hook(settings, "python -m hooks.pre_tool_use")
        _merge_hook(settings, "python -m hooks.pre_tool_use")
        assert len(settings["hooks"]["PreToolUse"]) == 1

    def test_preserves_existing_hooks(self) -> None:
        settings = {
            "hooks": {
                "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "echo done"}]}]
            }
        }
        _merge_hook(settings, "python -m hooks.pre_tool_use")
        assert "Stop" in settings["hooks"]
        assert "PreToolUse" in settings["hooks"]


class TestHookCommand:
    def test_uses_current_python_executable(self) -> None:
        command = _hook_command()
        assert "hooks.pre_tool_use" in command

    def test_can_target_repo_root_from_other_directory(self) -> None:
        source_root = Path("/repo")

        command = _hook_command(source_root)

        assert command.startswith("sh -lc '")
        assert shlex.quote(str(source_root)) in command
        assert shlex.quote(sys.executable) in command


class TestLoadOrEmpty:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert _load_or_empty(tmp_path / "nope.json") == {}

    def test_valid_json(self, tmp_path: Path) -> None:
        f = tmp_path / "s.json"
        f.write_text('{"foo": 1}', encoding="utf-8")
        assert _load_or_empty(f) == {"foo": 1}

    def test_invalid_json(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        assert _load_or_empty(f) == {}


class TestRunCcInit:
    def test_creates_settings_and_state(self, tmp_path: Path, monkeypatch: mock.Mock) -> None:
        monkeypatch.chdir(tmp_path)
        run_cc_init()

        settings_path = tmp_path / ".claude" / "settings.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "PreToolUse" in settings["hooks"]
        assert "hooks.pre_tool_use" in settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]

        assert (tmp_path / "state" / "logs").is_dir()
        assert (tmp_path / "state" / "qa" / "pending").is_dir()
        assert (tmp_path / "state" / "competence_model.yaml").exists()

    def test_supports_target_dir(self, tmp_path: Path, monkeypatch: mock.Mock) -> None:
        monkeypatch.chdir(tmp_path)
        target_dir = tmp_path / "demo" / "sample_project"
        target_dir.mkdir(parents=True)

        run_cc_init(target_dir=str(target_dir))

        settings_path = target_dir / ".claude" / "settings.json"
        state_dir = target_dir / "state"

        assert settings_path.exists()
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert shlex.quote(str(Path(__file__).resolve().parents[1])) in command
        assert "hooks.pre_tool_use" in command
        assert state_dir.joinpath("logs").is_dir()
        assert state_dir.joinpath("competence_model.yaml").exists()
