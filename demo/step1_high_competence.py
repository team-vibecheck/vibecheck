#!/usr/bin/env python3
"""VibeCheck Demo — Step 1: High competence (expect ALLOW).

Runs the hook with a crafted payload while the competence model is set to max.
The gate should allow the change without triggering a QA loop.
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

    print("=== VibeCheck Demo — Step 1: High Competence ===")
    print(f"Payload: {payload_path}")
    print(f"State:   {state_dir}")
    print()

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    response = handle_pre_tool_use(payload, state_dir=state_dir)

    print("--- Hook Response ---")
    print(json.dumps(response, indent=2))
    print()

    # Show competence model state
    cm_path = state_dir / "competence_model.yaml"
    if cm_path.exists():
        print("--- Competence Model ---")
        print(cm_path.read_text(encoding="utf-8")[:800])
        print()

    # Show event log tail
    log_path = state_dir / "logs" / "events.jsonl"
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        print(f"--- Event Log (last {min(5, len(lines))} entries) ---")
        for line in lines[-5:]:
            event = json.loads(line)
            print(f"  {event.get('event', '?'):30s}  {event.get('status', '')}")
        print()

    decision = response.get("hookSpecificOutput", {}).get("permissionDecision", "?")
    if decision == "allow":
        print("RESULT: Change was ALLOWED (high competence, as expected)")
    else:
        print(f"RESULT: Decision was '{decision}' (unexpected for high competence)")


if __name__ == "__main__":
    main()
