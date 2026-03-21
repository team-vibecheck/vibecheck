# 📂 Project Specification: LogicGuard

**Architecture:** Plannotator (Documentation-as-State)  
**Objective:** Intercept agent actions, evaluate "Knowledge Weight" vs. "User Competence," and gatekeep execution via Socratic QA.

---

## 1. Global Scope: Persistent Memory
**Location:** `~/.logicguard/`  
This directory acts as the global source of truth across all projects on the machine.

### `global.md` (The Competence Model)
A persistent Markdown table tracking verified skills. Use a scale of $1-10$.

| Skill | Score | Last Tested | Notes |
| :--- | :--- | :--- | :--- |
| `react_hooks` | 7 | 2026-03-20 | Understands `useEffect` dependencies. |
| `sql_safety` | 4 | 2026-03-15 | Needs work on parameterized queries. |
| `async_logic` | 8 | 2026-03-21 | High proficiency in Python `asyncio`. |

### `config.json` (System Settings)
%%%json
{
  "gemini_api_key": "YOUR_KEY_HERE",
  "challenge_threshold": 2,
  "default_model": "gemini-2.0-flash",
  "socratic_mode": "tui" 
}
%%%

---

## 2. Project Scope: Interceptor & Logic
**Location:** `./` (Current Repository)

### Configuration: `.claude/settings.json`
Registers the hooks required to intercept Claude Code's tool usage.
%%%json
{
  "hooks": {
    "preToolUse": "python3 .logicguard/gatekeeper.py",
    "permissionRequest": "python3 .logicguard/gatekeeper.py --mode permission"
  }
}
%%%

### State: `.logicguard/agg.md`
The **Interaction Log**. This file is dynamically generated for every proposed change.
* **Original Prompt:** The user's request.
* **Git Diff:** The proposed code patch.
* **Reasoning:** Why the agent is making this specific change.

---

## 3. Implementation Logic (The "Gatekeeper")

### The Decider Formula
The system evaluates whether to trigger a Socratic challenge based on the following comparison:

$$Trigger = \begin{cases} \text{Socratic Loop} & \text{if } W > C + \delta \\ \text{Execute} & \text{otherwise} \end{cases}$$

Where:
* $W$: Knowledge Weight of the proposed diff (determined by Gemini).
* $C$: User Competence score from `global.md`.
* $\delta$: The `challenge_threshold` (e.g., $2$).

### Core Scripts
* **`gatekeeper.py`**: Captures `stdin` (the tool input/diff), writes to `agg.md`, and calls the decider.
* **`gemini_decider.py`**: Uses the Gemini API to analyze `agg.md` and generates 3 multiple-choice questions if the threshold is met.

---

## 🤖 The Master Build Prompt
Run this command inside your agent to scaffold the foundation.

> **Objective:** Build LogicGuard (Plannotator-Style Architecture)
>
> **Step 1: Setup Global Memory**
> * Create `~/.logicguard/`.
> * Initialize `~/.logicguard/global.md` with a skill tracking table.
> * Create `~/.logicguard/config.json`.
>
> **Step 2: Create Interceptor**
> * Build `logicguard/gatekeeper.py` to intercept `PreToolUse`.
> * Capture `git diff` and user prompt into `.logicguard/agg.md`.
>
> **Step 3: Implementation of "Decider"**
> * Create `gemini_decider.py`.
> * Logic: If Knowledge Weight ($W$) > User Competence ($C$) + 2, block execution.
> * Generate 3 Socratic questions based on the code diff.
>
> **Step 4: Hook Registration**
> * Update `.claude/settings.json` to pipe tool inputs to `gatekeeper.py`.
>
> **Constraint:** Markdown is the source of truth. Documentation is the state.
