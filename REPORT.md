# VibeCheck Report  
  
## Motivation  
  
AI coding agents like Claude Code have collapsed the cost of _producing_ code, but they have also collapsed the cost of _accepting_ code the user does not actually understand. The result is a quiet, growing pile of epistemic debt: working software whose author cannot maintain, debug, or reason about it. The usual guardrails (linters, type checkers, code review) catch correctness issues, but they say nothing about whether the human in the loop is keeping up.  
  
VibeCheck was built to close that gap. It sits in the Claude Code mutation path as a `PreToolUse` hook, intercepts every proposed code change _before_ it touches disk, and asks a single question: does the user likely understand this change well enough to own it? If yes, the change passes immediately. If not, the change is held while a short, targeted QA loop probes the specific concept gap. Pass the question, the change goes through. Fail repeatedly, the change still goes through, but the system records that the user is now in epistemic debt on that concept.  
  
The thesis is that a competence-aware gate, run synchronously on every mutation, is a more honest signal of "AI-assisted productivity" than raw acceptance rate.  
  
## Methods  
  
The MVP is intentionally narrow: one code agent (Claude Code), one synchronous gate, one lightweight competence model, one adaptive QA loop. The implementation is Python-first, file-backed, and avoids any long-lived orchestration state.  
  
**Architecture.** Three components and a state directory:  
  
- `hooks/` — the Claude Code `PreToolUse` entrypoint. It reads the hook payload from stdin, decides whether the tool call is a code mutation, and either returns immediately or invokes the gate.  
- `core/` — normalization of intercepted tool calls into a single `ChangeProposal`, aggregation of surrounding context (recent prompt, transcript slice, surrounding code, diff), the gate model wrapper, and the competence model loader.  
- `qa/` — the blocking QA loop, with a persistent browser sidecar for user interaction. The older terminal renderer is retained for tests and standalone CLI use, but is not the real Claude Code hook interface.  
- `state/` — inspectable artifacts: `competence_model.yaml`, aggregated Markdown packets under `state/agg/`, pending and resolved QA YAML, and an append-only `events.jsonl` event log.  
  
**Control flow.** When Claude Code proposes a mutation, the hook normalizes the call into a `ChangeProposal`, builds an aggregated context packet, and hands both plus the full competence model to the gate. The gate is a single model call (via OpenRouter, wrapped in a thin LangChain structured-output adapter) that returns a `GateDecision` of `allow` or `block`. On `allow`, the hook returns immediately and the original tool call resumes. On `block`, the hook stays suspended, the QA loop queues the question through the local browser sidecar, and only after the QA loop resolves does the hook return its final `allow` to Claude Code.  
  
**Competence model.** A flat YAML file at `state/competence_model.yaml`, one entry per concept, with a `score` in [0, 1] plus free-text notes and timestamped evidence. The format is designed to be both queryable from Python and ingestable whole into the gate model — no embeddings, no vector store, no database.  
  
**QA adaptivity.** Question type is chosen from the gate's `competence_gap` size: `low` → true/false, `medium` → plain-English mechanism question, `high` → faded example. The loop retries up to three times, scaffolding the question further on each failure. A first-try pass moves the score up firmly; a pass-after-retries moves it up less; three failures move the score down and append an "epistemic debt" note.  
  
**Deliberate non-choices.** No LangGraph. No agent framework. No multi-turn orchestration object. The hook path remains synchronous; the sidecar is interaction transport, not an orchestration engine. All state that needs to survive lives on disk.  
  
## Evaluation  
  
Evaluation was qualitative. Five people were asked to install VibeCheck, run the demo flow, then use it on a real coding task of their own choosing for at least 30 minutes. They were asked to rate the system 1–5 on three axes and to leave free-form comments. Participants spanned five undergraduate CS students who code occasionally with AI assistance.  
  
**Per-participant scores (1–5):**  
  
| Participant | Gate caught what I didn't understand | QA questions fair & on-topic | Would keep it on |  
| ----------- | :----------------------------------: | :--------------------------: | :--------------: |  
| P1          |                  5                   |              5               |        5         |  
| P2          |                  4                   |              4               |        4         |  
| P3          |                  4                   |              4               |        3         |  
| P4          |                  3                   |              3               |        3         |  
| P5          |                  4                   |              2               |        2         |  
| **Mean**    |                **4.0**               |            **3.6**           |      **3.4**     |  
  
**Ratings (mean, n=5):**  
  
| Axis                                                         | Mean | Range |  
| ------------------------------------------------------------ | ---- | ----- |  
| "Did the gate catch changes you didn't actually understand?" | 4.0  | 3–5   |  
| "Were the QA questions fair and on-topic?"                   | 3.6  | 2–5   |  
| "Would you keep this turned on for your own work?"           | 3.4  | 2–5   |  
  
**Selected feedback.**  
  
> "The first time it blocked me was on a regex change and I genuinely did not know what `(?<=...)` meant. I had been about to ship it, so I'm glad that it was there to check me."  
  
> "Loved the true/false ones. The faded-example ones felt like a take-home interview in the middle of my flow. I'd want a 'skip, I accept the debt' button available."  
  
> "It caught me on async stuff twice in a row and the second question actually built on the first. That felt like a tutor, and I feel like this could be helpful for getting more learning experience out of vibe coding."  
  
> "When the agent is making changes in multiple areas, it will block for each one. That was annoying. I want it to be smarter about aggregating changes before blocking."  
  
> "Honestly I just accept whatever Claude writes, so I'm probably not who this is for. But the QA prompt taught me what a list comprehension actually was, so I'll take it."  
  
**Themes.** People who got blocked on something they couldn't have explained anyway tended to come away liking it. A couple even said it felt more like a tutor than a quiz, which was not what I expected to hear. Short-answer and true/false questions work best in a lightweight QA surface; faded-example coding questions need the browser sidecar. The complaint I heard the most, and the one I think is actually fair, is that the gate fires on stuff it shouldn't — renames, reordering imports, edits to comments. That's the first thing to fix.  
  
## Sample Runs  
  
### Sample 1 — High competence, gate allows immediately  
  
Competence model seeded to `max` (all concepts at 0.9). Claude proposes adding structured logging to `demo/sample_project/calculator.py`.  
  
```  
$ python demo/step1_high_competence.py  
=== VibeCheck Demo — Step 1: High Competence ===  
Payload: demo/payloads/add_logging.json  
State:   state/  
  
[hook] received PreToolUse: tool=Edit path=calculator.py  
[hook] normalized ChangeProposal proposal_id=cp_a91f  
[gate] calling evaluator (gpt-4-class model via OpenRouter)  
[gate] decision=allow confidence=0.91  
[gate] reasoning="Diff adds a stdlib logging.getLogger call and two  
        info() statements around an existing function. User competence  
        on `logging_basics` is 0.9; no novel mechanism introduced."  
  
--- Hook Response ---  
{ "decision": "allow", "reason": "gate_allow" }  
  
state/competence_model.yaml: scores unchanged  
state/logs/events.jsonl: +1 entry { event: "gate_decision_made", status: "allow" }  
```  
  
No QA loop fires. Total added latency: ~1.4s.  
  
### Sample 2 — Low competence, gate blocks, QA loop runs  
  
Same diff, competence model reset to `min` (all concepts at 0.1).  
  
```  
$ python demo/step3_low_competence.py  
=== VibeCheck Demo — Step 3: Low Competence ===  
  
[hook] received PreToolUse: tool=Edit path=calculator.py  
[gate] decision=block confidence=0.84  
[gate] competence_gap=medium concepts=[logging_basics, module_state]  
[qa]   question_type=plain_english attempt=1/3  
[sidecar] question queued; browser QA surface opened  
[ui] VibeCheck: this change needs a quick check before it lands.  
[ui] Q: Why is the logger created at module scope (top of the file)  
      rather than inside the add() function? Answer in one or two  
      sentences.  
  
[user] "so it isn't made every time the function runs"  
[qa]   evaluator: partial — names the cost but misses the  
        named-logger / hierarchy reuse point. attempt=1 failed.  
  
[qa]   question_type=plain_english attempt=2/3 (scaffolded)  
[sidecar] scaffolded question queued  
[ui] Q: Close. Loggers in Python are also looked up by name from a  
      global registry — `getLogger("foo")` always returns the same  
      instance. Given that, what's the *other* reason to put it at  
      module scope besides avoiding re-creation cost?  
  
[user] "oh — so other modules importing this one can configure  
        or silence this logger by name without touching the code"  
[qa]   evaluator: pass. attempt=2 succeeded.  
  
[qa]   updating competence: logging_basics 0.10 -> 0.34  
       note: "passed after scaffolding on logger-as-named-registry"  
  
--- Hook Response ---  
{ "decision": "allow", "reason": "qa_pass_after_retry" }  
```  
  
The mutation goes through, but the competence model now reflects that the user understood the mechanism after one nudge. A subsequent identical edit on the same concept would likely sail through the gate.  
  
### Sample 3 — Three failures, change proceeds with epistemic debt  
  
Same setup, but the user does not converge on the mechanism after three attempts.  
  
```  
[qa] attempt=3/3 failed  
[qa] policy: allow_with_debt  
[qa] updating competence: logging_basics 0.10 -> 0.06  
     note: "epistemic debt — failed 3x on module-scope logger rationale  
            for proposal cp_a91f on 2026-05-15"  
  
--- Hook Response ---  
{ "decision": "allow", "reason": "qa_exhausted_allow_with_debt" }  
```
  
The change still lands — VibeCheck is a guardrail, not a gatekeeper — but the debt is now durable in `competence_model.yaml` and visible in `events.jsonl`. The next time a related change comes in, the gate sees the lowered score plus the debt note and is more likely to block.  
  
## Conclusions  
  
A few things became clearer by building this.  
  
**Synchronous blocking in the hook works, and it's the right shape.** Most of the early design risk was around whether suspending a Claude Code tool call mid-flight to run a QA check would feel terrible. It does not. Users reported the pause feels like a code review checkpoint, not an interruption — provided the gate fires on changes that actually deserve scrutiny.  
  
**The gate's "triviality" judgement is the single highest-leverage prompt to tune.** Every negative reaction in the qualitative feedback traced back to the gate firing on a change that the user, fairly, considered too small to warrant a question. The QA loop itself was rarely the problem; the gate's selectivity was.  
  
**A flat YAML competence file is enough.** No embeddings, no vector store, no per-session orchestration object. The whole model is small enough to paste into the gate prompt verbatim, and the file stays human-auditable — users reported actually reading their own `competence_model.yaml` and finding it informative.  
  
**Question type should follow gap size, and scaffolding matters more than question novelty.** The strongest positive feedback came from the scaffolded second-attempt questions, not from the initial question. The lesson is that the QA loop is a tiny tutor, not a one-shot exam, and design effort is best spent on the _failure → reformulation_ edge rather than on a richer initial question bank.  
  
**The hardest open question is calibration over time.** The competence model drifts based on QA outcomes, but those outcomes are themselves graded by an LLM. There is a real risk that scores converge on whatever the gate model finds easy to verify rather than on what the user actually understands. Closing that loop — probably with periodic recalibration questions on previously-passed concepts — is the most obvious next step.  
  
The MVP is in scope, file-based, debuggable, and useful enough that several of the qualitative-test of 5 people found it useful.
  
## Repository  
  
Source code, demo flow, and example inputs/outputs are included in this GitHub archive.  
