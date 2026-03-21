You are working on a Python-based backend service that implements a mentor-gated coding workflow. Your task is to implement or refine a QA loop that sits between a “block decision” (from a competence-aware policy) and the execution of a code patch.

This QA loop must run entirely in the terminal (CLI), integrating cleanly with the same terminal session as the Claude Code CLI. Do NOT create any web UI, React app, or external server. This must be synchronous, blocking, and text-based.

---

## Core Functionality

Implement a function (or set of functions) with roughly the following behavior:

INPUTS:
- block_reasoning: string
  (Explanation for why the code change was blocked; includes concepts, risks, or knowledge gaps)
- competence_state: dict or structured object
  (Represents user competence signals or concept mastery estimates)

OUTPUT:
- returns control flow decision (implicitly by calling exec or not)
- side effect: asks user questions in terminal

---

## Behavior: QA Loop

1. When invoked, the QA loop:
   - Uses the block_reasoning + competence_state to generate a multiple-choice question.
   - The question MUST test understanding of the *mechanism* of the code change (not surface facts).
   - Prefer questions that require reasoning about behavior, edge cases, or why the change works.

   (Important: prioritize “why/how” questions over “what is X” questions.)

2. Display in terminal:
   - Clear question
   - 3–5 multiple choice options (A, B, C, D…)
   - Prompt user for input

3. Accept user input synchronously from stdin.

---

## Answer Evaluation

- If correct:
    - Print a short confirmation message
    - Call:
        exec()
        update_competence_values(competence_state, num_attempts)
    - Exit loop

- If incorrect:
    - Increment attempt counter
    - If attempts < 3:
        - Generate a NEW question:
            - Slightly easier than previous
            - More scaffolded (e.g., narrower scope, fewer steps, more concrete)
        - Repeat loop

    - If attempts == 3:
        - Print message: “Proceeding, but you should review this concept.”
        - Call:
            exec()
            update_competence_values(competence_state, num_attempts)
        - Exit loop

---

## Required Design Constraints

### 1. Terminal-native interaction
- Use standard input/output only (print + input)
- No GUI, no browser, no HTTP server
- Should feel like a natural CLI interaction

### 2. Deterministic control flow
- The QA loop must block execution until:
    - user answers correctly OR
    - max attempts reached

### 3. Question generation abstraction
- Encapsulate question generation into a function like:
    generate_question(block_reasoning, competence_state, difficulty_level)

- difficulty_level should decrease after each failed attempt

### 4. Dummy execution hooks (for now)
Implement placeholder functions:

    def exec():
        pass

    def update_competence_values(competence_state, attempts):
        pass

Do NOT implement real logic yet.

---

## Pedagogical Requirements (IMPORTANT)

Your questions MUST:

- Focus on mechanism (how the code works)
- Test reasoning, not memorization
- Include plausible distractors (wrong answers that reflect common misunderstandings)
- Gradually scaffold:
    - Attempt 1 → conceptual + mechanism
    - Attempt 2 → more guided / narrower
    - Attempt 3 → almost direct hinting

Avoid trivial or purely syntactic questions.

This aligns with research showing that mechanism-focused and self-explanation questions significantly improve learning outcomes. :contentReference[oaicite:0]{index=0}

---

## Code Quality Expectations

- Clean, modular Python
- Clear separation:
    - QA loop controller
    - question generation
    - input handling
    - evaluation logic
- No overengineering (keep MVP-simple)
- Easy to plug into a larger pipeline later

---

## Output

Produce:
1. Full Python implementation
2. Brief explanation of structure (short, not verbose)

Do not include unnecessary abstractions or frameworks.
Focus on clarity and correctness.