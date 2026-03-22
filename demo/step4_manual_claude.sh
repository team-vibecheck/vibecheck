#!/bin/bash
set -euo pipefail

# VibeCheck Demo — Step 4 (Manual): Live Claude Code Integration
# Prepares the repo for the live demo, then prints instructions so the user can
# run Claude Code manually and interact with the QA loop themselves.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== VibeCheck Demo — Step 4: Manual Claude Code ==="
echo ""
echo "This step prepares the repo for a live Claude Code run, but does not"
echo "launch Claude automatically. You will do that yourself in a second shell."
echo ""

echo "[1/4] Ensuring Claude Code hook is configured..."
uv run python -m cli.main cc init
echo ""

echo "[2/4] Ensuring low competence model..."
uv run python -m cli.main cm init --preset min
echo ""

echo "[3/4] Clearing previous demo artifacts..."
rm -f state/logs/events.jsonl
rm -rf state/qa/pending/* state/qa/results/* state/agg/*
echo ""

echo "[4/4] Checking Claude Code availability..."
if ! command -v claude &> /dev/null; then
    echo "ERROR: 'claude' command not found."
    echo "Install Claude Code CLI first: https://docs.anthropic.com/en/docs/claude-code"
    exit 1
fi
echo "Claude Code is available."
echo ""

echo "=== Manual Demo Instructions ==="
echo ""
echo "1. In another terminal, go to the sample project:"
echo "   cd '$PROJECT_ROOT/demo/sample_project'"
echo ""
echo "2. Start Claude Code manually in that directory. For example:"
echo "   claude"
echo ""
echo "3. Paste this prompt into Claude Code:"
echo ""
echo "   Add error logging with the logging module to the Calculator class in"
echo "   calculator.py. Add a logger at module level and log each operation with"
echo "   its arguments and result. Also add input validation that raises TypeError"
echo "   for non-numeric inputs."
echo ""
echo "4. When Claude attempts the file edit, VibeCheck should intercept it."
echo "   Because competence is set low, expect the QA loop to appear in the terminal."
echo "   If it does not, verify that .claude/settings.json exists in the repo root."
echo ""
echo "5. Answer the QA prompt manually. If you pass, the edit should proceed."
echo ""
echo "6. After the run, inspect these artifacts from the repo root:"
echo "   - state/logs/events.jsonl"
echo "   - state/competence_model.yaml"
echo "   - state/agg/current_attempt.md"
echo "   - state/qa/results/"
echo ""
echo "Ready. Nothing else will run automatically from this script."
