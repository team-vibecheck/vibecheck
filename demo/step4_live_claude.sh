#!/bin/bash
set -euo pipefail

# VibeCheck Demo — Step 4: Live Claude Code Integration
# This is the "wow" moment — runs Claude Code with the VibeCheck hook active.
#
# Prerequisites:
#   - Claude Code CLI installed (claude command available)
#   - ANTHROPIC_API_KEY or Claude Code already authenticated
#   - OPENROUTER_API_KEY set (for VibeCheck's LLM gate)
#   - Steps 0 + 2 already run (auth configured, low competence model)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== VibeCheck Demo — Step 4: Live Claude Code ==="
echo ""
echo "This step runs Claude Code against the sample project."
echo "The VibeCheck PreToolUse hook will intercept any code mutations."
echo "With low competence scores, expect the QA loop to fire."
echo ""

# Ensure low competence for dramatic effect
echo "Ensuring low competence model..."
uv run vibecheck cm init --preset min
echo ""

# Clear previous artifacts
rm -f state/logs/events.jsonl
rm -rf state/qa/pending/* state/qa/results/* state/agg/*

# Check if claude is available
if ! command -v claude &> /dev/null; then
    echo "ERROR: 'claude' command not found."
    echo "Install Claude Code CLI first: https://docs.anthropic.com/en/docs/claude-code"
    echo ""
    echo "As an alternative, run step 3 (step3_low_competence.py) for a"
    echo "Python-driven simulation of the same flow."
    exit 1
fi

echo "Launching Claude Code..."
echo "Prompt: Add logging and input validation to calculator.py"
echo ""
echo "Watch for the VibeCheck QA prompt to appear in the terminal."
echo "================================================================"
echo ""

cd demo/sample_project
claude --print "Add error logging with the logging module to the Calculator class in calculator.py. Add a logger at module level and log each operation with its arguments and result. Also add input validation that raises TypeError for non-numeric inputs."
