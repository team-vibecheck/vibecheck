# VibeCheck Unified MVP Spec

## Product Thesis

VibeCheck is a competence-aware guardrail for AI-assisted coding. It sits directly in the Claude Code mutation path, inspects every proposed code change before it lands, estimates whether the user likely understands the change well enough, and either allows the mutation immediately or blocks it behind a targeted QA loop. The MVP is intentionally narrow: one code agent, one synchronous gate, one lightweight competence model, and one adaptive QA loop.

## MVP Scope

The MVP consists of exactly three components:

1. `Code agent interface`
2. `Knowledge gate`
3. `QA loop`

The system targets Claude Code first and is Python-first end to end, with TypeScript or Bun permitted only where a browser-based QA interface is clearly useful.

## Core Invariants

- All code mutations are in scope for the MVP.
- The interception boundary is Claude Code `PreToolUse`.
- No intercepted code mutation reaches disk unless VibeCheck returns `allow`.
- The original tool call is suspended while VibeCheck runs.
- The hook remains blocking until one of these happens:
  - the knowledge gate allows immediately
  - the QA loop completes with a passing outcome
  - the QA loop reaches the fail limit and policy allows continuation with a competence penalty
- VibeCheck does not depend on a multi-turn orchestration engine or a long-lived agent state object.
- State is kept simple and explicit through request-local objects plus persisted artifacts on disk.

## Component 1: Code Agent Interface

### Goal

Capture Claude Code mutation events, normalize them into a single change representation, gather surrounding context, and hand the packet to the knowledge gate.

### Target Runtime

- Primary target: Claude Code
- Primary hook: `PreToolUse`
- Initial matcher scope: all code mutation tools that are feasible to intercept cleanly in Claude Code, not just `Write`
- Non-goal for MVP: broad destructive shell-command policing or full behavioral sandboxing

### Responsibilities

- Receive Claude hook payload from stdin
- Detect whether the tool invocation is a code mutation
- Normalize the proposed mutation into a shared `ChangeProposal`
- Gather surrounding context needed for evaluation
- Invoke the knowledge gate synchronously
- If needed, invoke the QA loop synchronously before returning control to Claude Code
- Return a final Claude-compatible `allow` or `deny` decision

### Canonical Mutation Object

The interface should normalize all intercepted code mutations into a single structure:

```yaml
ChangeProposal:
  proposal_id: string
  session_id: string
  tool_use_id: string
  tool_name: string
  cwd: string
  targets:
    - path: string
      language: string | null
      old_content: string | null
      new_content: string
  unified_diff: string
  diff_stats:
    files_changed: int
    additions: int
    deletions: int
  created_at: iso8601
```

### Surrounding Context Aggregation

The interface must build an aggregated context packet that includes, at minimum:

- latest relevant user prompt excerpt
- recent Claude conversation slice relevant to the mutation
- surrounding code near the touched region
- file path and language metadata
- repo-local context notes if available
- normalized diff and old/new content views

This aggregation should be persisted in a human-readable format for auditability and debugging.

## Component 2: Knowledge Gate

### Goal

Decide whether the proposed change should pass immediately or be blocked behind a targeted knowledge check.

### Inputs

The knowledge gate receives:

- the full `ChangeProposal`
- the aggregated surrounding context
- the full competence model

### Competence Model

The MVP competence model is a simple text file that is both easy for Python to query and easy to ingest whole into an LLM. The canonical format for MVP is `YAML`.

Recommended file:

- `state/competence_model.yaml`

Recommended shape:

```yaml
user_id: local_default
updated_at: 2026-03-21T00:00:00Z
concepts:
  async_programming:
    score: 0.42
    notes:
      - Understands basic await usage
      - Struggles with error propagation across async boundaries
    evidence:
      - timestamp: 2026-03-20T12:00:00Z
        outcome: pass_after_2
        note: Explained why await was required before reading response data
  recursion:
    score: 0.78
    notes:
      - Strong on base cases
      - Usually recognizes stack growth tradeoffs
```

### Knowledge Gate Model

The gate is model-driven in the MVP. The evaluator model is the primary decider, not just an advisory helper.

Model selection criteria:

- strong large-context performance
- acceptable cost for frequent gating calls
- good structured-output reliability

Implementation guidance:

- wrap model calls behind a Python adapter
- use LangChain utilities for structured outputs, retries, and provider abstraction if helpful
- do not introduce LangGraph or any heavyweight multi-turn orchestration layer

### Knowledge Gate Output

The knowledge gate returns only two top-level decisions:

- `allow`
- `block`

If it blocks, it must also return the information needed to drive the QA loop.

Recommended structured output:

```yaml
GateDecision:
  decision: allow | block
  reasoning: string
  confidence: float
  relevant_concepts:
    - string
  relevant_competence_entries:
    - concept: string
      score: float
      notes:
        - string
  competence_gap:
    size: high | medium | low
    rationale: string
  qa_packet:
    question_type: faded_example | plain_english | true_false
    prompt_seed: string
    context_excerpt: string
```

### Decision Semantics

- `allow` means the suspended tool call may proceed immediately
- `block` means the tool call remains suspended while the QA loop runs
- the knowledge gate should pass only the most relevant competence entries into the QA loop, not the whole competence model

## Component 3: QA Loop

### Goal

When a change is blocked, validate or improve user understanding through a targeted question flow and then resolve the suspended tool call.

### Inputs

The QA loop receives:

- gate reasoning
- relevant competence entries only
- aggregated context excerpt
- question seed
- competence gap size

### Interaction Model

The QA loop is still part of the blocking path.

- The original Claude tool call remains suspended.
- VibeCheck does not return final `allow` or `deny` to Claude until the QA loop resolves.
- The QA loop should use the best available interface for the selected question type.

### Rendering Policy

If Claude Code offers a reliable native GUI or TUI interaction surface for this flow, use it. Otherwise, use an auxiliary interface.

Preferred policy:

- use terminal-native interaction for `plain_english` and `true_false` when feasible
- use a GUI or web view for `faded_example` or fill-in-the-blank coding questions when that materially improves usability
- if both terminal and GUI are feasible, choose by question type rather than forcing a single UI mode
- if a browser UI is required, a lightweight Bun-based local web app is acceptable

### Adaptive Question Types

Question type is chosen based on the competence gap:

- `high gap` -> fill-in-the-blank or faded example implementation task
- `medium gap` -> plain-English interactive reasoning about program logic
- `low gap` -> true/false or similarly lightweight mechanism check

### QA Loop Behavior

1. Generate an initial targeted question from the gate packet.
2. Present the question in the selected interface.
3. Evaluate the user response.
4. If the user passes:
   - update the competence model positively
   - allow the suspended tool call to proceed
5. If the user fails:
   - feed the failed answer back into question generation
   - generate guided feedback or a more scaffolded version of the question
   - retry up to a maximum of 3 attempts
6. If the user eventually passes after retries:
   - allow the suspended tool call to proceed
   - update competence positively, but less strongly than a first-try pass
   - record that the user needed support
7. If the user fails 3 times:
   - allow the suspended tool call to proceed anyway
   - reduce relevant competence scores
   - add a note warning about potential epistemic debt

### Pedagogical Rules

Questions should test mechanism, not trivia.

- Ask why the change works, not just what syntax appears in the diff.
- Use distractors or follow-ups that reflect plausible misunderstandings.
- Become more scaffolded after each failure.
- Use the returned relevant competence slice to target the misunderstanding precisely.

## End-to-End Control Flow

```text
User prompt
  -> Claude Code proposes code mutation
  -> PreToolUse hook intercepts
  -> Code agent interface normalizes proposal and aggregates context
  -> Knowledge gate evaluates proposal + full competence model
    -> if allow: return allow immediately, Claude mutation executes
    -> if block: keep tool call suspended and enter QA loop
      -> run adaptive QA
      -> if pass at any attempt: update competence, return allow, Claude mutation executes
      -> if fail 3x: dock competence, record epistemic debt, return allow, Claude mutation executes
```

## Persisted Artifacts

The MVP should prefer simple file-based state over databases.

Recommended artifacts:

- `state/competence_model.yaml`
- `state/agg/current_attempt.md`
- `state/qa/pending/<proposal_id>.yaml`
- `state/qa/results/<proposal_id>.yaml`
- `state/logs/events.jsonl`

### Aggregation Packet

Recommended format:

- Markdown for readability and whole-context LLM ingestion

Suggested sections:

- metadata
- user prompt excerpt
- old code
- new code
- unified diff
- surrounding code
- relevant transcript slice
- repo-local notes

## Implementation Constraints

- Python-first core implementation
- TypeScript/Bun only when needed for richer GUI interaction
- LangChain utilities are acceptable for structured output handling and provider wrappers
- No LangGraph
- No long-lived orchestration state object passed between turns
- Control flow should remain simple, mostly synchronous, and easy to debug

## Non-Goals for MVP

- multi-agent support beyond Claude Code
- a complex policy engine with many deterministic hard rules
- a full web dashboard
- CI, PR, or worktree orchestration as first-class flows
- destructive-operation governance across the entire terminal surface
- team-shared cloud competence profiles

## Open Questions to Refine Later

- Which exact Claude Code mutation tools should be intercepted on day one if not all are equally accessible?
- What is the best reliable mechanism for suspending a hook while a browser-based QA flow completes?
- Which model is the default gate evaluator in the first shipping version?
- How should we score free-form plain-English answers in a way that is consistent enough for MVP?

## MVP Summary

The unified MVP is a synchronous Claude Code mutation gate. Every code change is normalized, paired with surrounding context, and evaluated against a full concept-based competence model. If the model allows the change, Claude proceeds normally. If it blocks the change, the same suspended tool call enters an adaptive QA loop that uses only the most relevant competence entries, updates the competence model based on performance, and then releases the tool call. The implementation stays Python-first, file-based, and simple in control flow.
