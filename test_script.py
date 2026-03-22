#!/usr/bin/env python3
"""Interactive VibeCheck demo script.

This script simulates a VibeCheck knowledge gate + QA loop interaction:
1. Performs static analysis on the codebase
2. Randomly generates a simulated code change (without implementing it)
3. Runs through the knowledge gate
4. If blocked, runs the actual QA loop with interactive terminal input
5. Outputs the final competence model
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.competence_store import load_competence_model, save_competence_model
from core.context_aggregation import build_aggregated_context
from core.gate import evaluate_change
from core.models import (
    ChangeProposal,
    ChangeTarget,
    CompetenceModel,
    DiffStats,
    GateDecision,
    QAPacket,
    QAResult,
)
from qa.competence_updates import apply_qa_outcome
from qa.evaluation import evaluate_answer
from qa.question_generation import build_question_prompt

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


SIMULATED_CHANGES: list[dict[str, Any]] = [
    {
        "description": "Add ThreadPoolExecutor for parallel processing",
        "old": "def fetch_all(urls):\n    results = []\n    for url in urls:\n        results.append(requests.get(url))\n    return results",
        "new": "from concurrent.futures import ThreadPoolExecutor\n\ndef fetch_all(urls):\n    with ThreadPoolExecutor(max_workers=10) as executor:\n        return list(executor.map(requests.get, urls))",
    },
    {
        "description": "Add thread-safe counter with Lock",
        "old": "counter = 0\n\ndef increment():\n    global counter\n    counter += 1",
        "new": "import threading\n\ncounter = 0\ncounter_lock = threading.Lock()\n\ndef increment():\n    global counter\n    with counter_lock:\n        counter += 1",
    },
    {
        "description": "Convert blocking I/O to async/await",
        "old": "import requests\n\ndef get_data(url):\n    return requests.get(url).json()\n\ndef main():\n    results = [get_data(url) for url in urls]",
        "new": "import aiohttp\n\nasync def get_data(session, url):\n    async with session.get(url) as response:\n        return await response.json()\n\nasync def main():\n    async with aiohttp.ClientSession() as session:\n        tasks = [get_data(session, url) for url in urls]\n        results = await asyncio.gather(*tasks)",
    },
    {
        "description": "Add asyncio queue for producer-consumer pattern",
        "old": "def process_items(items):\n    results = []\n    for item in items:\n        results.append(transform(item))\n    return results",
        "new": "import asyncio\nfrom asyncio import Queue\n\nasync def producer(queue, items):\n    for item in items:\n        await queue.put(item)\n    await queue.join()\n\nasync def consumer(queue):\n    while True:\n        item = await queue.get()\n        result = transform(item)\n        queue.task_done()\n\nasync def process_items(items):\n    queue = Queue()\n    await asyncio.gather(producer(queue, items), consumer(queue))",
    },
    {
        "description": "Add multiprocessing Pool for CPU-bound work",
        "old": "def process_data(items):\n    return [heavy_computation(item) for item in items]",
        "new": "from multiprocessing import Pool\n\ndef process_data(items):\n    with Pool(processes=4) as pool:\n        return pool.map(heavy_computation, items)",
    },
    {
        "description": "Add thread-local storage for request context",
        "old": "current_request = None\n\ndef get_request():\n    return current_request\n\ndef set_request(req):\n    global current_request\n    current_request = req",
        "new": "import contextvars\n\nrequest_context: contextvars.ContextVar[Request] = contextvars.ContextVar('request')\n\ndef get_request():\n    return request_context.get()\n\nasync def handle_request(req):\n    token = request_context.set(req)\n    try:\n        await process()\n    finally:\n        request_context.reset(token)",
    },
    {
        "description": "AddRLock for reentrant locking in recursive calls",
        "old": "class Cache:\n    def __init__(self):\n        self._data = {}\n\n    def get_or_compute(self, key, func):\n        if key not in self._data:\n            self._data[key] = func()\n        return self._data[key]",
        "new": "import threading\n\nclass Cache:\n    def __init__(self):\n        self._data = {}\n        self._lock = threading.RLock()\n\n    def get_or_compute(self, key, func):\n        with self._lock:\n            if key not in self._data:\n                self._data[key] = func()\n            return self._data[key]",
    },
    {
        "description": "Add asyncio.Event for cross-task synchronization",
        "old": "def wait_for_ready():\n    while not is_ready:\n        time.sleep(0.1)",
        "new": "import asyncio\n\nready_event = asyncio.Event()\n\nasync def wait_for_ready():\n    await ready_event.wait()\n\ndef signal_ready():\n    ready_event.set()",
    },
    {
        "description": "Add asyncio.Condition for complex coordination",
        "old": "def process_with_threshold(items, threshold):\n    while len(items) >= threshold:\n        batch = items[:threshold]\n        process_batch(batch)\n        items = items[threshold:]",
        "new": "import asyncio\n\nclass BatchProcessor:\n    def __init__(self, threshold):\n        self.threshold = threshold\n        self.items = []\n        self.condition = asyncio.Condition()\n\n    async def add_item(self, item):\n        async with self.condition:\n            self.items.append(item)\n            if len(self.items) >= self.threshold:\n                self.condition.notify()\n\n    async def wait_for_batch(self):\n        async with self.condition:\n            await self.condition.wait_for(lambda: len(self.items) >= self.threshold)\n            batch = self.items[:self.threshold]\n            self.items = self.items[self.threshold:]\n            return batch",
    },
    {
        "description": "Add ProcessPoolExecutor with shared memory",
        "old": "def parallel_sum(numbers):\n    return sum(numbers)",
        "new": "from concurrent.futures import ProcessPoolExecutor\nfrom multiprocessing import shared_memory\nimport numpy as np\n\ndef parallel_sum(shm_name, shape, dtype):\n    existing_shm = shared_memory.SharedMemory(name=shm_name)\n    arr = np.ndarray(shape, dtype=dtype, buffer=existing_shm.buf)\n    return arr.sum()\n\ndef parallel_sum(numbers):\n    shm = shared_memory.SharedMemory(create=True, size=numbers.nbytes)\n    arr = np.ndarray(numbers.shape, dtype=numbers.dtype, buffer=shm.buf)\n    arr[:] = numbers[:]\n    with ProcessPoolExecutor(max_workers=4) as executor:\n        chunk_size = len(numbers) // 4\n        results = list(executor.map(parallel_sum, [shm.name]*4, [arr.shape]*4, [arr.dtype]*4))\n    shm.close()\n    shm.unlink()\n    return sum(results)",
    },
    {
        "description": "Add threading.Barrier for synchronization points",
        "old": "def run_phases(workers):\n    for phase in phases:\n        for worker in workers:\n            worker.do_phase(phase)",
        "new": "import threading\n\nbarrier = threading.Barrier(num_workers)\n\ndef worker_loop():\n    for phase in phases:\n        worker.prepare_phase(phase)\n        barrier.wait()\n        barrier.wait()",
    },
    {
        "description": "Add with_timeout decorator using asyncio",
        "old": "async def long_running():\n    await asyncio.sleep(1000)",
        "new": "async def with_timeout(coro, seconds):\n    try:\n        return await asyncio.wait_for(coro, timeout=seconds)\n    except asyncio.TimeoutError:\n        raise TimeoutError(f'Operation timed out after {seconds}s')\n\nasync def long_running():\n    await with_timeout(asyncio.sleep(1000), 5)",
    },
]


def find_python_files(root: Path, max_files: int = 20) -> list[Path]:
    python_files = []
    for path in root.rglob("*.py"):
        if any(part.startswith(".") for part in path.parts):
            continue
        if "venv" in path.parts or "__pycache__" in path.parts:
            continue
        python_files.append(path)
        if len(python_files) >= max_files:
            break
    return python_files


def select_file_content(python_files: list[Path]) -> tuple[Path, str]:
    selected = random.choice(python_files)
    content = selected.read_text(encoding="utf-8")
    return selected, content


def generate_simulated_proposal(
    python_files: list[Path],
    session_id: str,
    tool_use_id: str,
) -> ChangeProposal:
    change = random.choice(SIMULATED_CHANGES)
    selected_file, _ = select_file_content(python_files)

    rel_path = selected_file.relative_to(PROJECT_ROOT)

    diff_lines = []
    diff_lines.append(f"--- a/{rel_path}")
    diff_lines.append(f"+++ b/{rel_path}")
    diff_lines.append("@@ -1,3 +1,3 @@")

    old_lines = change["old"].split("\n")
    new_lines = change["new"].split("\n")

    for line in old_lines:
        diff_lines.append(f"-{line}")
    for line in new_lines:
        diff_lines.append(f"+{line}")

    unified_diff = "\n".join(diff_lines)

    additions = len(new_lines)
    deletions = len(old_lines)

    return ChangeProposal(
        proposal_id=f"sim-{tool_use_id[:8]}",
        session_id=session_id,
        tool_use_id=tool_use_id,
        tool_name="apply_patch",
        cwd=str(PROJECT_ROOT),
        targets=[
            ChangeTarget(
                path=str(rel_path),
                language="python",
                old_content=change["old"],
                new_content=change["new"],
            )
        ],
        unified_diff=unified_diff,
        diff_stats=DiffStats(files_changed=1, additions=additions, deletions=deletions),
        created_at=datetime.now(UTC).isoformat(),
    )


class InteractiveTerminalRenderer:
    def ask(self, question: str, attempt_number: int, packet: QAPacket) -> str:
        print("\n" + "=" * 60)
        print(f"  VIBECHECK - ATTEMPT {attempt_number}")
        print("=" * 60)
        print(f"\n[Proposed Change: {packet.prompt_seed}]\n")
        print(question)
        print("-" * 40)
        answer = input("\nYour answer: ").strip()
        while not answer:
            print("Please provide an answer.")
            answer = input("Your answer: ").strip()
        return answer


def run_qa_loop_interactive(
    gate_decision: GateDecision,
    proposal: ChangeProposal,
    competence_model: CompetenceModel,
    competence_path: Path,
    state_dir: Path,
) -> QAResult:
    if gate_decision.qa_packet is None:
        raise ValueError("Cannot run QA loop without a QA packet.")

    renderer = InteractiveTerminalRenderer()
    max_attempts = 3
    qa_packet = gate_decision.qa_packet

    context_excerpt = qa_packet.context_excerpt or ""

    for attempt_number in range(1, max_attempts + 1):
        question = build_question_prompt(
            gate_decision,
            attempt_number=attempt_number,
            competence_entries=gate_decision.relevant_competence_entries,
        )
        answer = renderer.ask(question, attempt_number, qa_packet)

        evaluation = evaluate_answer(
            question=question,
            answer=answer,
            question_type=qa_packet.question_type,
            context_excerpt=context_excerpt,
            attempt_number=attempt_number,
        )

        print("\n" + "-" * 40)
        print(f"Feedback: {evaluation.feedback}")
        print(f"Result: {'PASSED' if evaluation.passed else 'NEEDS IMPROVEMENT'}")

        if evaluation.passed:
            print("\n" + "=" * 60)
            print("  VIBECHECK - PASSED!")
            print("=" * 60)
            print(f"\n{evaluation.feedback}\n")

            apply_qa_outcome(
                competence_model,
                concepts=gate_decision.relevant_concepts,
                passed=True,
                attempt_count=attempt_number,
            )
            save_competence_model(competence_model, competence_path)

            return QAResult(
                proposal_id=proposal.proposal_id,
                final_decision="allow",
                passed=True,
                attempt_count=attempt_number,
                attempts=[],
                summary="QA loop passed; allowing the suspended mutation to continue.",
            )

    apply_qa_outcome(
        competence_model,
        concepts=gate_decision.relevant_concepts,
        passed=False,
        attempt_count=max_attempts,
    )
    save_competence_model(competence_model, competence_path)

    print("\n" + "=" * 60)
    print("  VIBECHECK - COMPLETED (Max attempts reached)")
    print("=" * 60)
    print("\nThe change is being allowed with a competence penalty applied.\n")

    return QAResult(
        proposal_id=proposal.proposal_id,
        final_decision="allow",
        passed=False,
        attempt_count=max_attempts,
        attempts=[],
        summary="QA loop reached the fail limit; allowing the mutation with a competence penalty.",
    )


def print_competence_model(model: CompetenceModel) -> None:
    print("\n" + "=" * 60)
    print("  COMPETENCE MODEL")
    print("=" * 60)
    print(f"\nUser: {model.user_id}")
    print(f"Updated: {model.updated_at}\n")

    for concept, entry in model.concepts.items():
        print(f"  {concept}:")
        print(f"    Score: {entry.score:.2f}")
        if entry.notes:
            print(f"    Notes: {'; '.join(entry.notes)}")
        print()


def main() -> None:
    print("\n" + "=" * 60)
    print("  VIBECHECK - INTERACTIVE DEMO")
    print("=" * 60)
    print("\nThis demo simulates a VibeCheck knowledge gate + QA loop.")
    print("A simulated code change will be generated and evaluated.\n")

    python_files = find_python_files(PROJECT_ROOT)
    if not python_files:
        print("Error: No Python files found for analysis.")
        sys.exit(1)

    print(f"Found {len(python_files)} Python files for static analysis.\n")

    session_id = f"demo-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    tool_use_id = f"tool-use-{random.randint(1000, 9999)}"

    proposal = generate_simulated_proposal(python_files, session_id, tool_use_id)

    print("-" * 40)
    print("SIMULATED CODE CHANGE:")
    print("-" * 40)
    for target in proposal.targets:
        print(f"\nFile: {target.path}")
        print(f"Change: {target.new_content[:100]}...")
    print(f"\nDiff stats: +{proposal.diff_stats.additions} / -{proposal.diff_stats.deletions}\n")

    state_dir = PROJECT_ROOT / "state"
    state_dir.mkdir(exist_ok=True)

    print("Building aggregated context...")
    aggregated_context = build_aggregated_context(proposal, state_dir)

    competence_path = state_dir / "competence_model.yaml"
    competence_model = load_competence_model(competence_path)

    print("Running knowledge gate evaluation...\n")

    gate_decision = evaluate_change(proposal, aggregated_context, competence_model)

    print("-" * 40)
    print("GATE DECISION:")
    print("-" * 40)
    print(f"\nDecision: {gate_decision.decision.upper()}")
    print(f"Reasoning: {gate_decision.reasoning}")
    print(f"Confidence: {gate_decision.confidence:.2f}")

    if gate_decision.relevant_concepts:
        print(f"Relevant concepts: {', '.join(gate_decision.relevant_concepts)}")

    if gate_decision.competence_gap:
        print(f"Gap size: {gate_decision.competence_gap.size}")
        print(f"Gap rationale: {gate_decision.competence_gap.rationale}")

    if gate_decision.qa_packet:
        print("\nQA Packet:")
        print(f"  Question type: {gate_decision.qa_packet.question_type}")
        print(f"  Prompt seed: {gate_decision.qa_packet.prompt_seed}")

    if gate_decision.decision == "allow":
        print("\n" + "=" * 60)
        print("  CHANGE ALLOWED BY GATE")
        print("=" * 60)
        print("\nNo QA needed - the change passes the knowledge gate.\n")
    else:
        print("\n" + "=" * 60)
        print("  QA LOOP REQUIRED")
        print("=" * 60)
        print("\nThe change requires demonstration of understanding.")
        proceed = input("\nStart interactive QA loop? (y/n): ").strip().lower()
        if proceed != "y":
            print("\nQA loop skipped.")
            return

        qa_result = run_qa_loop_interactive(
            gate_decision=gate_decision,
            proposal=proposal,
            competence_model=competence_model,
            competence_path=competence_path,
            state_dir=state_dir,
        )

        print(f"\nQA Result: {'PASSED' if qa_result.passed else 'FAILED'}")
        print(f"Attempts used: {qa_result.attempt_count}")

    print_competence_model(competence_model)

    print("=" * 60)
    print("  DEMO COMPLETE")
    print("=" * 60)
    print(f"\nCompetence model saved to: {competence_path}\n")


if __name__ == "__main__":
    main()
