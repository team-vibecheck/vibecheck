# VibeCheck Prompt Sources

## Restore fenced code blocks

```bash
perl -0pi -e 's/^```/```/mg' promptsources.md
```

## Recommended usage note

```text
Do not overengineer this. Prefer a working MVP with clean interfaces over a fully generalized framework.
```

## 1) Bootstrap the repo and architecture

```text
Build an MVP called VibeCheck: a mentor-gated coding workflow for Claude Code.

Goal:
Create a local CLI-first system that intercepts AI-proposed code edits, analyzes the patch, checks whether the user likely understands the concepts involved, and either:
1. allows execution,
2. asks a short mechanism question,
3. or redirects into a guided QA loop before execution.

Architecture constraints:
- The system must follow this flow:
  User prompt -> Claude Code -> Hook check -> Python script -> competence model/global.md -> Gemini decision -> allow exec OR block to QA loop -> React page or terminal QA -> pass/fail -> exec/result -> update competence model.
- Use a single edit/mutation chokepoint.
- Use unified diffs as the canonical patch representation.
- Keep the MVP modular and local-first.
- Start with Claude Code integration first.
- Design so OpenCode could be added later, but do not implement OpenCode yet.

Tech stack:
- TypeScript/Node for orchestration
- Python for patch analysis + competence model operations if helpful
- React + Vite for the question UI
- simple JSON or markdown persistence for MVP
- no database required in v1 unless absolutely necessary

Deliverables:
1. Create a clean repo structure
2. Add a README explaining the system
3. Add a short architecture.md describing all modules and their data flow
4. Create stub implementations for each major component:
   - hook adapter
   - patch normalizer
   - policy engine
   - competence store
   - aggregate knowledge store
   - Gemini decision client
   - QA loop service
   - React UI
   - exec/apply layer
   - event logger
5. Add TODO comments for anything not yet implemented
6. Keep the codebase runnable locally

Use clear file names and keep the design simple, explicit, and debuggable.
```

## 2) Claude Code hook interception

```text
Implement the Claude Code interception layer for VibeCheck.

Requirements:
- Add a Claude Code PreToolUse hook for Edit and Write operations.
- The hook must act as the primary mutation chokepoint.
- The hook should:
  1. receive tool input from Claude Code,
  2. detect whether the action is a patch/edit action,
  3. if it is not a patch, allow it immediately,
  4. if it is a patch, forward the request into the VibeCheck pipeline.

Behavior:
- Non-patch flow: return allow immediately.
- Patch flow: collect the tool name, tool input, cwd, session id if available, and target file paths.
- Normalize the attempted mutation into an internal request object.
- Do not apply the patch yet.
- Log the intercepted attempt.

Deliverables:
1. Implement the hook handler code
2. Add sample Claude hook config for this project
3. Add a clear adapter boundary so all later logic receives a normalized patch proposal instead of raw Claude payloads
4. Add tests or a local simulation script for the hook input/output

Important:
- Keep this implementation thin.
- The hook should only intercept and route.
- The actual policy decision should live in a separate service/module.
```

## 3) Patch normalization and unified diff generation

```text
Implement VibeCheck patch normalization.

Goal:
Convert intercepted Claude Code edit/write attempts into a canonical PatchProposal object that the rest of the system can consume.

Requirements:
- Create a PatchProposal type/interface with:
  - proposalId
  - timestamp
  - agent
  - toolName
  - cwd
  - sessionId if available
  - touchedPaths
  - file change intents
  - unifiedDiff
  - raw tool input
  - provenance metadata
- For each edit/write request:
  - snapshot the current file contents before mutation
  - compute the would-be after state
  - generate a strict unified diff
- Use unified diff as the canonical interchange format
- Handle create/modify/replace cases
- If trustworthy diff reconstruction fails, surface a failure status and do not auto-apply

Deliverables:
1. PatchProposal type
2. patch-normalizer module
3. unified diff generator
4. tests for small edits, full overwrite, and multi-file changes
5. a debug CLI command that prints the normalized PatchProposal

Important:
- The rest of the system should never depend on raw Claude tool payloads.
- Keep the diff generation deterministic and debuggable.
```

## 4) Python analysis script

```text
Implement the Python patch-analysis script for VibeCheck.

Purpose:
This script receives the user prompt plus the old and new code or unified diff, then extracts signals needed by the policy engine and competence system.

Inputs:
- user prompt
- unified diff
- old code / new code where available
- file paths

Outputs:
Return a structured JSON analysis object containing:
- patch summary
- changed files
- changed constructs if detectable
- rough concept tags
- risk flags
- size/scope metrics
- novelty hints
- likely affected programming concepts
- whether this appears to be a bug fix, feature, refactor, or config/security-sensitive change

Heuristics for MVP:
- detect auth / secrets / crypto / schema / infra / concurrency / destructive operations
- detect diff size and file count
- detect broad refactors or unsolicited scope expansion
- detect common language concepts from changed code patterns
- keep rules interpretable and simple

Deliverables:
1. Python CLI script
2. JSON schema for the output
3. test fixtures using sample diffs
4. README on how the hook/service invokes it

Important:
- Use heuristic, rule-based signals for MVP.
- Structure the code so ML/LLM concept tagging can be added later.
```

## 5) Competence store: `global.md`

```text
Implement the competence model store for VibeCheck using a lightweight MVP design.

Goal:
Track what the user has demonstrated understanding of over time, without building a full tutoring system.

Storage:
- Use a human-readable file named global.md plus a machine-readable companion file if needed.
- Keep the source of truth simple and inspectable.

Track per concept:
- concept name
- mastery score or confidence score
- evidence count
- last demonstrated timestamp
- last failed timestamp
- recent examples/evidence
- notes on explanation quality if available

Update rules for MVP:
- increase confidence when the user answers a mechanism question correctly
- increase confidence when the user successfully completes guided implementation
- decrease or hold low confidence after repeated failures
- do not mark something as mastered from one success alone
- keep uncertainty explicit

Deliverables:
1. competence store module
2. parser/writer for global.md
3. functions:
   - getConceptState
   - updateConceptState
   - listRelevantConcepts
   - recordEvidence
4. clear markdown format with examples
5. tests for read/write/update logic

Important:
- The model should be lightweight, interpretable, and easy to evolve later.
- Prefer simple heuristic confidence updates over heavy knowledge tracing models for MVP.
```

## 6) Aggregate knowledge store: `Agg.md`

```text
Implement the aggregate knowledge store for VibeCheck.

Purpose:
Store reusable memory that is not user mastery itself, but useful context for policy and tutoring.

Use a file named Agg.md plus optional machine-readable support files.

Store items like:
- prior similar patches
- prior mentor decisions
- accepted explanations
- common failure patterns
- reusable question patterns by concept
- repo-specific conventions
- risky path rules
- examples of previous fixes

Requirements:
- support append-only logging of useful episodes
- support retrieval by concept tags, file path, and patch similarity metadata
- keep entries compact and human-readable
- do not dump raw transcripts; summarize them into reusable memory units

Deliverables:
1. aggregate memory module
2. markdown schema for Agg.md entries
3. retrieval helpers:
   - getRelevantMemoryForPatch
   - getQuestionPatternsForConcepts
   - getRepoConventions
4. example seed entries
5. tests

Important:
- Keep this store distinct from the competence model.
- Competence is about the user.
- Aggregate memory is about reusable knowledge and prior cases.
```

## 7) Policy engine / Gemini routing

```text
Implement the VibeCheck policy engine and Gemini routing layer.

Goal:
Given:
- PatchProposal
- Python patch analysis
- relevant competence state from global.md
- relevant memory from Agg.md

decide one of:
- allow
- ask
- question_gate
- guide
- deny

MVP decision policy:
- allow when patch risk is low, scope is small, and relevant competence is strong enough
- question_gate when risk is moderate or concept novelty is moderate
- guide when patch is high-risk, broad, under-tested, or touches concepts the user likely does not understand
- deny for hard-stop cases if needed
- optionally support ask for lightweight confirmation without pedagogy

Hard-stop candidates:
- auth / secrets / crypto
- schema or migration files
- infra / deployment config
- concurrency primitives
- unsafe or destructive operations
- large multi-file edits touching sensitive paths

Gemini usage:
- Use Gemini as a decision support or explanation synthesis layer, not as opaque policy authority
- Gemini may:
  - summarize why a patch is risky
  - infer likely concepts
  - suggest question themes
- But final policy output should remain structured and interpretable

Deliverables:
1. MentorDecision type
2. policy-engine module
3. Gemini client wrapper
4. a clear rule-first decision function
5. prompt templates for Gemini that produce structured JSON
6. tests with example low/moderate/high risk patches

Important:
- Keep hard rules outside Gemini.
- Gemini can assist classification and explanation, but should not override hard-stop policy.
```

## 8) Question generation

```text
Implement the question generation system for VibeCheck.

Goal:
When the policy engine returns question_gate, generate 1-2 short, high-value mechanism questions that test whether the user understands how the patch works.

Question design requirements:
- prefer semantic/mechanism questions over surface questions
- ask about:
  - how the patch changes behavior
  - what assumptions it relies on
  - edge cases or failure modes
  - why this fix/approach works
- avoid trivia and paraphrasing
- avoid long quizzes
- keep it to the minimum needed to reveal understanding

Inputs:
- PatchProposal
- patch analysis
- likely concept tags
- relevant competence data
- relevant memory/examples

Outputs:
- structured question set
- expected answer rubric
- grading hints
- retry guidance if the user fails

Deliverables:
1. question generator module
2. templates by patch type:
   - bug fix
   - feature
   - refactor
   - performance
   - config/security-sensitive
3. grading rubric structure
4. example outputs for several patches
5. tests

Important:
- Questions should be short enough to answer in under 30 seconds each.
- Start with 1 question by default and only generate a second if needed.
```

## 9) React page / terminal QA loop UI

```text
Implement the VibeCheck QA loop UI.

Goal:
When the policy engine returns question_gate or guide, open a lightweight local UI that shows:
- patch summary
- unified diff
- the mentor question(s)
- answer input
- pass/fail feedback
- retry or continue actions

Requirements:
- Build a React UI with Vite
- Also support a terminal fallback mode if UI/browser opening is not feasible
- Use an ephemeral local server pattern:
  - start local server
  - serve UI
  - expose GET /api/mutation
  - expose POST /api/decision
  - block the agent loop until the frontend submits a decision
- Add remote/devcontainer-safe behavior:
  - fixed port option
  - disable auto-open when remote mode is enabled
  - print a clickable URL instead
- Persist lightweight UI preferences in cookies, not localStorage, so they survive different localhost ports

Deliverables:
1. React question page
2. ephemeral local server
3. terminal fallback flow
4. remote mode support
5. diff viewer component
6. decision submission flow that resolves the blocked promise
7. tests or manual demo script

Important:
- Keep the UI fast and minimal.
- The main use case is a short interruption, not a big dashboard.
```

## 10) Grading the answer and retry loop

```text
Implement the VibeCheck answer grading and retry loop.

Behavior:
- After the user submits an answer, grade it against the expected mechanism/rationale rubric.
- Output:
  - pass
  - fail
  - fail_with_retry_guidance
- If pass:
  - continue to execution
- If fail:
  - provide a brief hint and allow another attempt
- After 3 failures:
  - exit the QA loop and return a result that execution should not proceed automatically
  - recommend guided implementation mode instead

Requirements:
- Use simple rubric-based grading first
- Optional LLM judge may assist, but the system must remain auditable
- Store answer outcomes for later competence updates

Deliverables:
1. grading service
2. retry controller
3. max-3-attempt rule
4. result objects for pass/fail/pass-after-retry/fail-3x
5. tests with sample answers

Important:
- Feedback should be concise and mechanism-focused.
- Do not turn this into a long tutoring conversation in MVP.
```

## 11) Guided mode instead of just blocking

```text
Implement VibeCheck guided mode.

Goal:
When the patch is too risky or the user fails the question gate repeatedly, do not just deny. Redirect into a scaffolded implementation workflow.

Guide mode should support:
1. patch splitting:
   - separate safe hunks from risky hunks
2. faded implementation:
   - show safe context and leave TODOs/blanks for the user to implement core logic
3. evidence-first workflow:
   - require tests, static checks, or reasoning steps before applying risky changes

For MVP:
- implement a guided mode that produces:
  - a short plan
  - one small implementation step at a time
  - TODO-marked code skeletons where appropriate
  - validation after each step

Deliverables:
1. guide-mode module
2. patch splitting helper
3. TODO/faded-code generator
4. guided step runner
5. tests and examples

Important:
- Guide mode should still feel productive, not like a hard refusal.
- Apply low-risk hunks automatically only if the design remains safe and simple.
```

## 12) Exec / apply layer

```text
Implement the VibeCheck execution/apply layer.

Goal:
Only apply a mutation after the policy engine and QA/guided flow permit it.

Requirements:
- Accept an approved PatchProposal
- Apply the change safely
- Support:
  - modify existing file
  - create file
  - overwrite file where allowed
- After application, run post-apply verification:
  - syntax parse where possible
  - lint if configured
  - typecheck if configured
  - optional targeted tests
- If post-apply verification fails:
  - record failure
  - return a structured error
  - do not silently continue

Deliverables:
1. apply service
2. post-apply verification module
3. rollback or failure-safe handling if apply/checks fail
4. structured apply result
5. tests

Important:
- Never bypass the gate.
- Execution must only happen after a final allow/pass outcome.
```

## 13) Result logging and audit trail

```text
Implement append-only event logging for VibeCheck.

Log these event types:
- PatchAttemptEvent
- PatchDecisionEvent
- PatchAppliedEvent
- QuestionAnswerEvent
- CompetenceUpdateEvent

Minimum fields:
- proposal id
- timestamp
- agent
- tool name
- cwd/worktree
- file list
- diff hash
- decision
- reason
- whether applied
- post-check outcomes
- question outcomes if any

Requirements:
- logs must be easy to inspect locally
- use newline-delimited JSON or similarly simple format
- add helper utilities to read and summarize logs

Deliverables:
1. event types
2. append-only logger
3. log reader utilities
4. one summary CLI command
5. tests

Important:
- Keep logging local-first and explicit.
- Make sure logs support later evaluation of friction, safety, and learning outcomes.
```

## 14) Update the competence model from outcomes

```text
Implement the logic that updates the competence model after each completed flow.

Inputs:
- patch concepts involved
- policy decision
- question answers and grading results
- whether the patch was applied
- post-apply verification outcomes
- whether guided implementation succeeded
- whether the patch later failed or was reverted if that signal exists

Update logic:
- if the user explains a concept correctly, increase evidence/confidence for that concept
- if they pass after retry, increase slightly but mark uncertainty
- if they fail repeatedly, do not mark mastery
- if they succeed in guided implementation, increase confidence more than from explanation alone
- if a patch is applied but later fails checks or is reverted, reduce confidence in any “successful” interpretation

Deliverables:
1. competence update service
2. evidence weighting rules
3. integration with global.md
4. tests

Important:
- Keep updates gradual.
- Do not treat one success as mastery.
```

## 15) End-to-end integration

```text
Wire the full VibeCheck MVP together end-to-end.

Final flow to implement:
1. Claude Code proposes Edit/Write
2. Hook intercepts
3. PatchProposal is created
4. Python analyzer runs
5. competence + aggregate memory are retrieved
6. policy engine decides
7. if allow -> apply + verify + log + update competence
8. if question_gate -> open React UI or terminal fallback -> pass/fail/retry -> then apply or redirect
9. if guide -> launch scaffolded guided mode
10. always log outcomes and update stores

Deliverables:
1. working end-to-end local demo
2. one command to run the backend/services
3. one command to run the React UI in dev mode if needed
4. sample fixtures and demo scenarios:
   - low-risk small patch -> allow
   - moderate-risk patch -> question_gate
   - high-risk patch -> guide
5. final README section: “How the full pipeline works”

Important:
- Make the MVP actually runnable.
- Prefer a clean, working, simple system over speculative abstractions.
```

## 16) Final hardening pass

```text
Review the whole VibeCheck MVP and improve it for reliability and clarity.

Tasks:
- remove dead code
- strengthen type safety
- improve module boundaries
- add missing error handling
- ensure fail-closed behavior for risky edits and fail-open only for explicitly low-risk cases
- improve README setup steps
- add inline comments only where useful
- add a short “known limitations” section
- make the project pleasant for a hackathon demo

Also:
- verify the end-to-end flow with sample scenarios
- make sure the diff-based gating behavior is easy to understand
- ensure the codebase reflects the architecture docs

Return a summary of what you improved and any remaining limitations.
```

## Suggested execution order

1. Bootstrap repo and architecture
2. Claude Code hook interception
3. Patch normalization and unified diff generation
4. Python analysis script
5. Competence store (`global.md`)
6. Aggregate knowledge store (`Agg.md`)
7. Policy engine / Gemini routing
8. Question generation
9. React page / terminal QA loop UI
10. Grading the answer and retry loop
11. Guided mode instead of just blocking
12. Exec / apply layer
13. Result logging and audit trail
14. Update the competence model from outcomes
15. End-to-end integration
16. Final hardening pass
