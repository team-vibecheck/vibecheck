#!/usr/bin/env python3
"""VibeCheck Demo — Step 3: Low competence (expect BLOCK + QA loop).

Runs the hook with the same payload but now the competence model is set to min.
The gate should block the change and trigger the QA loop.

THIS SCRIPT WILL BLOCK waiting for terminal input during the QA loop.
Answer the questions in the terminal to proceed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from hooks.pre_tool_use import handle_pre_tool_use  # noqa: E402


def main() -> None:
    payload_path = PROJECT_ROOT / "demo" / "payloads" / "add_logging.json"
    state_dir = PROJECT_ROOT / "state"

    print("=== VibeCheck Demo — Step 3: Low Competence ===")
    print(f"Payload: {payload_path}")
    print(f"State:   {state_dir}")
    print()
    print("If the gate blocks this change, you will be asked QA questions.")
    print("Answer them in the terminal to proceed.")
    print()

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    response = handle_pre_tool_use(payload, state_dir=state_dir)

    print()
    print("--- Hook Response ---")
    print(json.dumps(response, indent=2))
    print()

    # Show updated competence model
    cm_path = state_dir / "competence_model.yaml"
    if cm_path.exists():
        print("--- Updated Competence Model ---")
        print(cm_path.read_text(encoding="utf-8")[:800])
        print()

    # Show event log
    log_path = state_dir / "logs" / "events.jsonl"
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        print(f"--- Event Log ({len(lines)} entries) ---")
        for line in lines:
            event = json.loads(line)
            print(f"  {event.get('event', '?'):30s}  {event.get('status', '')}")
        print()

    # Show QA result if available
    qa_results_dir = state_dir / "qa" / "results"
    if qa_results_dir.exists():
        for result_file in sorted(qa_results_dir.glob("*.yaml")):
            print(f"--- QA Result: {result_file.name} ---")
            print(result_file.read_text(encoding="utf-8")[:600])
            print()

    meta = response.get("metadata", {})
    if meta.get("qa_passed") is True:
        print("RESULT: QA loop PASSED — change allowed after knowledge check")
    elif meta.get("qa_passed") is False:
        print("RESULT: QA loop FAILED — change allowed with competence penalty")
    else:
        decision = response.get("hookSpecificOutput", {}).get("permissionDecision", "?")
        print(f"RESULT: Gate decision was '{decision}'")


if __name__ == "__main__":
    main()
