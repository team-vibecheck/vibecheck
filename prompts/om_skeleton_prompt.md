You are an expert Python systems engineer. Build a terminal-based developer tool with the following architecture and behavior.

## High-Level Goal
Create a "Knowledge Gate" CLI tool that integrates with Claude Code via hooks. The tool monitors code changes and ensures the user understands generated code before it is applied.

---

## Core Components

### 1. Entry Point (CLI)
- Build a Python CLI app (use typer or argparse).
- This is invoked by a Claude Code hook when code changes are proposed.
- All interaction happens in the terminal (stdin/stdout).

---

### 2. Competence Model
- Store user competence in a local markdown file: `competence.md`
- This file should:
  - Track topics (e.g., async, decorators, SQL, etc.)
  - Track a score per topic (0–100)
  - Track history of attempts and outcomes

Example structure:

Competence Model
Topics
Python Basics: 85
Async Programming: 40
Git Diff Understanding: 60
History
[timestamp] Topic: Async | Result: fail | Attempts: 3

- Provide functions:
  - load_competence()
  - update_competence(topic, delta, attempts)
  - infer_topic_from_diff(diff)

---

### 3. Knowledge Gate Logic

#### Input
The tool receives:
- Original user prompt
- Proposed code diff
- Surrounding code context

Store this in a markdown file: `context.md`

Example:

User Prompt

...

Code Diff

...

Surrounding Code

...


---

### 4. Difficulty Detection

- Analyze the code diff
- Map it to one or more topics
- Compare required competence vs user competence

Implement a heuristic system:
- If diff includes async/await → requires Async Programming ≥ 60
- If complex list comprehensions → Python ≥ 70
- If unfamiliar imports → lower confidence

If competence is sufficient → allow change immediately

If NOT:
→ Trigger Knowledge Gate

---

### 5. Knowledge Gate Flow

When triggered:

1. Block the code change
2. Generate explanation + knowledge gaps
   - Call Gemini API (mock if needed)
   - Input: diff + competence
   - Output:
     - Explanation
     - Identified gaps

3. Generate a multiple-choice question
   - Based on the diff
   - 4 options
   - 1 correct answer

---

### 6. Question Loop

- Give user up to 3 attempts

Flow:
- Ask question
- If correct:
  - Allow code change
  - Increase competence
- If incorrect:
  - Retry with simpler version of the question
- After 3 failures:
  - Allow code change anyway
  - Decrease competence

---

### 7. Competence Updates

- 1 try → +10 competence
- 2 tries → +5 competence
- 3 tries → -5 competence
- Fail → -10 competence

Persist updates to `competence.md`

---

### 8. Claude Code Hook Integration

- Design the tool to be triggered via a Claude Code hook
- The hook should:
  - Detect code diffs
  - Pass data into this CLI tool

Define a function:


def handle_code_event(prompt, diff, context):
...


---

### 9. Code Structure

Organize into modules:

- cli.py
- competence.py
- diff_analyzer.py
- knowledge_gate.py
- question_engine.py
- gemini_client.py (mockable)
- hook_handler.py

---

### 10. Requirements

- Python 3.10+
- No heavy frameworks
- Clean, readable code
- Type hints everywhere
- Include docstrings
- Include example run

---

### 11. Output

Provide:
1. Full project structure
2. All Python files
3. Example `competence.md`
4. Example `context.md`
5. Example CLI session

---

## Important Constraints

- Keep everything local-first (files, no DB)
- Make Gemini optional (fallback to local logic)
- Design for extensibility
- Make heuristics easy to modify

---

## Bonus (if time permits)

- Add colored terminal output
- Add logging
- Add confidence scoring system
- Add plugin system for new topics

---

Now implement the full system.