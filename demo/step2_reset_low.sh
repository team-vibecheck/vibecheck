#!/bin/bash
set -euo pipefail

# VibeCheck Demo — Step 2: Reset to Low Competence
# Restores sample project and seeds competence model with minimum values.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== VibeCheck Demo — Step 2: Reset ==="
echo ""

# 1. Restore the sample project to its original state
echo "[1/3] Restoring sample project..."
git checkout -- demo/sample_project/ 2>/dev/null || true
echo "  demo/sample_project/ restored."

# 2. Clear state artifacts from previous run
echo "[2/3] Clearing previous state artifacts..."
rm -f state/logs/events.jsonl
rm -rf state/qa/pending/* state/qa/results/* state/agg/*
echo "  State artifacts cleared."

# 3. Reset competence model to minimum
echo "[3/3] Resetting competence model (min preset)..."
uv run vibecheck cm init --preset min
echo ""

echo "=== Reset complete. Competence set to minimum. Ready for step 3. ==="
