#!/bin/bash
set -euo pipefail

# VibeCheck Demo — Step 0: Setup
# Requires: OPENROUTER_API_KEY environment variable

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== VibeCheck Demo Setup ==="
echo ""

# 1. Auth — save API key from environment
echo "[1/3] Configuring OpenRouter API key..."
uv run vibecheck auth --from-env
echo ""

# 2. Init competence model — max competence
echo "[2/3] Initializing competence model (max preset)..."
uv run vibecheck cm init --preset max
echo ""

# 3. Bootstrap Claude Code hook
echo "[3/3] Bootstrapping Claude Code hook..."
uv run vibecheck cc init
echo ""

echo "=== Setup complete. Ready for step 1. ==="
