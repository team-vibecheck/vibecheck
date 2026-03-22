from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.diffs import build_unified_diff, count_diff_stats, detect_language
from core.errors import HookPayloadError, UnsupportedMutationError
from core.models import ChangeProposal, ChangeTarget

CODE_MUTATION_TOOLS = frozenset({"Edit", "MultiEdit", "NotebookEdit", "Write"})


def is_code_mutation_tool(tool_name: str | None) -> bool:
    return tool_name in CODE_MUTATION_TOOLS


def normalize_mutation_payload(
    payload: Mapping[str, Any],
    *,
    cwd: Path | None = None,
) -> ChangeProposal:
    tool_name = _first_string(payload, "tool_name", "tool")
    if not is_code_mutation_tool(tool_name):
        raise UnsupportedMutationError(f"Unsupported mutation tool: {tool_name!r}")

    raw_input = payload.get("tool_input") or payload.get("input") or {}
    if not isinstance(raw_input, Mapping):
        raise HookPayloadError("Expected tool input to be an object.")

    base_dir = cwd or _cwd_from_payload(payload)
    targets = _normalize_targets(tool_name, raw_input, base_dir)
    diffs = [
        build_unified_diff(target.old_content, target.new_content, target.path)
        for target in targets
    ]
    unified_diff_text = "\n".join(diff for diff in diffs if diff)

    return ChangeProposal(
        proposal_id=str(payload.get("proposal_id") or f"proposal-{uuid4().hex}"),
        session_id=str(payload.get("session_id") or "unknown_session"),
        tool_use_id=str(payload.get("tool_use_id") or f"tool-{uuid4().hex}"),
        tool_name=tool_name,
        cwd=str(base_dir),
        targets=targets,
        unified_diff=unified_diff_text,
        diff_stats=count_diff_stats(unified_diff_text, files_changed=len(targets)),
        created_at=_utc_now_iso(),
    )


def _normalize_targets(
    tool_name: str,
    raw_input: Mapping[str, Any],
    cwd: Path,
) -> list[ChangeTarget]:
    if tool_name == "NotebookEdit":
        raise UnsupportedMutationError("NotebookEdit normalization is not implemented yet.")

    if "targets" in raw_input:
        raw_targets = raw_input["targets"]
        if not isinstance(raw_targets, Sequence) or isinstance(raw_targets, str):
            raise HookPayloadError("Expected targets to be a list of file mutations.")
        return [_build_target(target, cwd) for target in raw_targets]

    if tool_name == "Write":
        return [_build_write_target(raw_input, cwd)]
    if tool_name == "Edit":
        return [_build_edit_target(raw_input, cwd)]
    if tool_name == "MultiEdit":
        return [_build_multiedit_target(raw_input, cwd)]

    return [_build_target(raw_input, cwd)]


def _build_target(raw_target: Any, cwd: Path) -> ChangeTarget:
    if not isinstance(raw_target, Mapping):
        raise HookPayloadError("Each target must be an object.")

    path = _extract_path(raw_target)
    if not path:
        raise HookPayloadError("Mutation target is missing a file path.")

    new_content = raw_target.get("new_content")
    if new_content is None:
        new_content = raw_target.get("content")
    if not isinstance(new_content, str):
        raise HookPayloadError("Mutation target is missing new content.")

    old_content = raw_target.get("old_content")
    if old_content is None:
        old_content = _read_existing_content(cwd, path)
    elif not isinstance(old_content, str):
        raise HookPayloadError("old_content must be a string when provided.")

    return ChangeTarget(
        path=path,
        language=detect_language(path),
        old_content=old_content,
        new_content=new_content,
    )


def _build_write_target(raw_input: Mapping[str, Any], cwd: Path) -> ChangeTarget:
    path = _extract_path(raw_input)
    new_content = raw_input.get("content")
    if new_content is None:
        new_content = raw_input.get("new_content")
    if not isinstance(new_content, str):
        raise HookPayloadError("Write mutation is missing string content.")

    old_content = raw_input.get("old_content")
    if old_content is None:
        old_content = _read_existing_content(cwd, path)
    elif not isinstance(old_content, str):
        raise HookPayloadError("Write old_content must be a string when provided.")

    return ChangeTarget(
        path=path,
        language=detect_language(path),
        old_content=old_content,
        new_content=new_content,
    )


def _build_edit_target(raw_input: Mapping[str, Any], cwd: Path) -> ChangeTarget:
    path = _extract_path(raw_input)
    old_content = _require_existing_content(cwd, path, tool_name="Edit")
    new_content = _apply_edit(old_content, raw_input)
    return ChangeTarget(
        path=path,
        language=detect_language(path),
        old_content=old_content,
        new_content=new_content,
    )


def _build_multiedit_target(raw_input: Mapping[str, Any], cwd: Path) -> ChangeTarget:
    path = _extract_path(raw_input)
    old_content = _require_existing_content(cwd, path, tool_name="MultiEdit")
    edits = raw_input.get("edits")
    if not isinstance(edits, Sequence) or isinstance(edits, str) or not edits:
        raise HookPayloadError("MultiEdit mutation requires a non-empty edits list.")

    new_content = old_content
    for edit in edits:
        if not isinstance(edit, Mapping):
            raise HookPayloadError("Each MultiEdit edit must be an object.")
        new_content = _apply_edit(new_content, edit)

    return ChangeTarget(
        path=path,
        language=detect_language(path),
        old_content=old_content,
        new_content=new_content,
    )


def _apply_edit(content: str, edit: Mapping[str, Any]) -> str:
    old_string = edit.get("old_string")
    new_string = edit.get("new_string")
    replace_all = edit.get("replace_all", False)

    if not isinstance(old_string, str) or not old_string:
        raise HookPayloadError("Edit mutations require a non-empty old_string.")
    if not isinstance(new_string, str):
        raise HookPayloadError("Edit mutations require a string new_string.")
    if not isinstance(replace_all, bool):
        raise HookPayloadError("replace_all must be a boolean when provided.")
    if old_string not in content:
        raise HookPayloadError("Edit old_string was not found in the current file content.")

    if replace_all:
        return content.replace(old_string, new_string)
    return content.replace(old_string, new_string, 1)


def _extract_path(raw_target: Mapping[str, Any]) -> str:
    path = raw_target.get("path") or raw_target.get("file_path")
    if isinstance(path, str) and path:
        return path
    raise HookPayloadError("Mutation target is missing a file path.")


def _cwd_from_payload(payload: Mapping[str, Any]) -> Path:
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        return Path(cwd)
    return Path.cwd()


def _read_existing_content(cwd: Path, path: str) -> str | None:
    file_path = _resolve_path(cwd, path)
    if not file_path.exists() or not file_path.is_file():
        return None
    return file_path.read_text(encoding="utf-8")


def _require_existing_content(cwd: Path, path: str, *, tool_name: str) -> str:
    content = _read_existing_content(cwd, path)
    if content is None:
        raise HookPayloadError(f"{tool_name} mutation requires an existing file at {path!r}.")
    return content


def _resolve_path(cwd: Path, path: str) -> Path:
    target = Path(path)
    if target.is_absolute():
        return target
    return cwd / target


def _first_string(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    raise HookPayloadError(f"Expected one of {keys!r} in hook payload.")


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
