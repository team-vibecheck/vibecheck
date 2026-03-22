from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from core.errors import HookPayloadError


def read_hook_payload(raw_text: str | None = None) -> dict[str, Any]:
    payload_text = sys.stdin.read() if raw_text is None else raw_text
    if not payload_text.strip():
        raise HookPayloadError("Received an empty Claude hook payload.")

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise HookPayloadError("Claude hook payload was not valid JSON.") from exc

    if not isinstance(payload, dict):
        raise HookPayloadError("Claude hook payload must decode to an object.")

    return payload


def get_tool_name(payload: dict[str, Any]) -> str | None:
    for key in ("tool_name", "tool"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def get_tool_input(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("tool_input", "input"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    raise HookPayloadError("Claude hook payload is missing a tool_input object.")


def get_cwd(payload: Mapping[str, Any]) -> Path:
    value = payload.get("cwd")
    if isinstance(value, str) and value:
        return Path(value)
    return Path.cwd()


def extract_user_prompt_excerpt(payload: Mapping[str, Any], transcript_excerpt: str = "") -> str:
    for key in ("user_prompt_excerpt", "prompt", "latest_user_message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for line in reversed(transcript_excerpt.splitlines()):
        stripped = line.strip()
        if stripped.lower().startswith("user:"):
            return stripped.partition(":")[2].strip()
    return ""


def extract_transcript_excerpt(
    payload: Mapping[str, Any],
    *,
    max_messages: int = 6,
    max_chars: int = 2000,
) -> str:
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return ""

    path = Path(transcript_path)
    if not path.exists() or not path.is_file():
        return ""

    raw_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".jsonl", ".json"}:
        messages = _extract_transcript_messages(raw_text)
        if messages:
            excerpt = "\n".join(messages[-max_messages:])
            return excerpt[-max_chars:]

    return raw_text[-max_chars:].strip()


def discover_repo_notes(cwd: Path, *, max_chars_per_file: int = 1200) -> str:
    note_paths: list[Path] = []
    for directory in (cwd, *cwd.parents):
        for name in ("AGENTS.md", "CLAUDE.md"):
            candidate = directory / name
            if candidate.is_file() and candidate not in note_paths:
                note_paths.append(candidate)
        readme = directory / "README.md"
        if readme.is_file() and readme not in note_paths:
            note_paths.append(readme)
        if (directory / ".git").exists():
            break

    chunks: list[str] = []
    for path in note_paths[:3]:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        chunks.append(f"## {path.name}\n{text[:max_chars_per_file]}")
    return "\n\n".join(chunks)


def _extract_transcript_messages(raw_text: str) -> list[str]:
    messages: list[str] = []
    stripped = raw_text.strip()
    if not stripped:
        return messages

    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        decoded = None

    if decoded is not None:
        messages.extend(_messages_from_json(decoded))
        return messages

    for line in raw_text.splitlines():
        stripped_line = line.strip()
        if not stripped_line:
            continue
        try:
            decoded_line = json.loads(stripped_line)
        except json.JSONDecodeError:
            continue
        messages.extend(_messages_from_json(decoded_line))
    return messages


def _messages_from_json(value: Any) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        messages: list[str] = []
        for item in value:
            messages.extend(_messages_from_json(item))
        return messages

    if not isinstance(value, Mapping):
        return []

    nested_message = value.get("message")
    if nested_message is not None:
        nested_messages = _messages_from_json(nested_message)
        if nested_messages:
            return nested_messages

    role = value.get("role")
    content = _content_to_text(value.get("content"))
    if isinstance(role, str) and content:
        return [f"{role}: {content}"]

    parts: list[str] = []
    for nested_key in ("messages", "entries", "items"):
        nested_value = value.get(nested_key)
        if nested_value is not None:
            parts.extend(_messages_from_json(nested_value))
    return parts


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    pieces.append(text.strip())
            elif isinstance(item, str) and item.strip():
                pieces.append(item.strip())
        return " ".join(pieces)
    return ""
