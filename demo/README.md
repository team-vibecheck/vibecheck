# VibeCheck Demo

A step-by-step demo showing VibeCheck's competence-aware code gating in action.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) installed
- `OPENROUTER_API_KEY` environment variable set ([get one here](https://openrouter.ai/keys))
- For Step 4 only: Claude Code CLI installed and authenticated

## Quick Start

```bash
# Install dependencies
uv sync

# Export your OpenRouter key
export OPENROUTER_API_KEY="sk-or-v1-your-key-here"

# Run the demo steps in order
bash demo/step0_setup.sh
python demo/step1_high_competence.py
bash demo/step2_reset_low.sh
python demo/step3_low_competence.py
```

## Steps

### Step 0: Setup (`step0_setup.sh`)
Configures auth, seeds the competence model at **maximum** skill level, and bootstraps the Claude Code hook.

**What it does:**
- `vibecheck auth --from-env` — saves your OpenRouter key
- `vibecheck cm init --preset max` — all 8 concepts at score 0.9
- `vibecheck cc init` — generates `.claude/settings.json`

### Step 1: High Competence (`step1_high_competence.py`)
Simulates a code change (adding logging to `calculator.py`) with the user at max competence.

**Expected behavior:** The gate **allows** the change — high competence means no QA needed.

**Inspect after:**
- `state/competence_model.yaml` — scores unchanged
- `state/logs/events.jsonl` — gate_decision_made with status=allow

### Step 2: Reset (`step2_reset_low.sh`)
Restores the sample project and resets the competence model to **minimum** skill level.

**What it does:**
- Restores `demo/sample_project/calculator.py` to original
- Clears event logs and QA artifacts
- `vibecheck cm init --preset min` — all concepts at score 0.1

### Step 3: Low Competence (`step3_low_competence.py`)
Runs the **same change** but now with min competence. **This step is interactive.**

**Expected behavior:** The gate **blocks** the change and triggers the QA loop. You'll be asked questions in the terminal about the code change. Answer them to proceed.

**Interactive:** The script blocks waiting for your answers. This is the core VibeCheck experience — proving you understand the code before it's allowed.

**Inspect after:**
- `state/competence_model.yaml` — scores updated based on QA results
- `state/logs/events.jsonl` — full flow including qa_attempt_started, qa_answer_evaluated
- `state/qa/results/` — YAML files with your answers and evaluation

### Step 4: Live Claude Code (`step4_live_claude.sh`)
The real deal — launches Claude Code with the VibeCheck hook active. Claude makes a real edit, the hook intercepts it, and the QA loop fires in the terminal.

**Requires:** Claude Code CLI (`claude` command) installed and authenticated.

**Expected behavior:** Claude proposes an edit to `calculator.py`. VibeCheck intercepts, evaluates your competence, and asks you a question. You answer in the terminal. If you pass, the edit goes through.

## Artifacts to Inspect

After running the demo, check these files:

| Path | Description |
|------|-------------|
| `state/competence_model.yaml` | Your competence scores for each concept |
| `state/logs/events.jsonl` | Event log of all hook activity |
| `state/agg/current_attempt.md` | Aggregated context for the last change |
| `state/qa/pending/*.yaml` | QA questions that were asked |
| `state/qa/results/*.yaml` | QA results with your answers |
| `.claude/settings.json` | Claude Code hook configuration |
| `~/.vibecheck/config.toml` | Your saved API key (0600 perms) |

## Troubleshooting

**No OPENROUTER_API_KEY:** If you already ran `vibecheck auth`, runtime falls back to `~/.vibecheck/config.toml`. If neither env nor config is available, the gate and QA loop fail fast until credentials are configured.

**Claude Code not installed:** Steps 0-3 work without it. Step 4 is optional and requires `claude` CLI.

**QA loop doesn't fire in step 3:** If the gate allows the change even at min competence, the LLM may have judged it as safe. Try step 3 again — LLM responses can vary.
