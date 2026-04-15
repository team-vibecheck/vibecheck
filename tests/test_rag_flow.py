"""End-to-end tests for the RAG competence pipeline.

Flow under test:
  QA loop fails → outcome embedded in ChromaDB
  → retrieve_relevant_events returns the stored event on a subsequent call
  → build_aggregated_context includes the event in the markdown
  → KnowledgeGate receives a prompt that contains the historical instruction
"""

from __future__ import annotations

import hashlib

import chromadb
import pytest
from chromadb import EmbeddingFunction
from chromadb.api.types import Documents, Embeddings

from core.competence_store import default_competence_model, retrieve_relevant_events
from core.context_aggregation import build_aggregated_context
from core.gate import KnowledgeGate
from core.models import (
    ChangeProposal,
    ChangeTarget,
    CompetenceGap,
    DiffStats,
    GateDecision,
    QAPacket,
)
from qa.evaluation import AnswerEvaluation
from qa.loop import QALoop


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _HashEmbeddingFunction(EmbeddingFunction[Documents]):
    """Deterministic, non-zero fake embedding function safe for retrieval tests.

    Properly subclasses EmbeddingFunction so chromadb 1.5.x can call
    embed_query() during collection.query().
    """

    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:
        vecs = []
        for s in input:
            digest = hashlib.md5(s.encode()).digest()
            vecs.append([b / 255.0 for b in digest[:8]])
        return vecs

    @staticmethod
    def name() -> str:
        return "hash_fake"

    def get_config(self) -> dict:  # type: ignore[override]
        return {}

    @staticmethod
    def build_from_config(config: dict) -> "_HashEmbeddingFunction":  # type: ignore[override]
        return _HashEmbeddingFunction()


def _persistent_chroma_factory(tmp_path):
    """Return a get_chroma_collection replacement that shares one client instance.

    Creating a new PersistentClient per call can cause cross-instance cache
    visibility issues in tests (the second client doesn't immediately see data
    written by the first).  Using a single client throughout each test guarantees
    that save and retrieve operate on the same in-memory state.
    """
    fake_ef = _HashEmbeddingFunction()
    db_path = tmp_path / "_shared_chroma"
    db_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(db_path))
    shared_collection = client.get_or_create_collection(
        "competence_events",
        embedding_function=fake_ef,
    )

    def _factory(ignored_db_path, embedding_function=None) -> chromadb.Collection:
        # Always return the same collection regardless of the requested path.
        return shared_collection

    return _factory


def _install_fake_llm(monkeypatch, evaluations: list[AnswerEvaluation]) -> None:
    from qa import llm_wrapper as llm_wrapper_module
    from qa.llm_wrapper import GeneratedQuestion

    class _FakeLLMClient:
        def __init__(self) -> None:
            self._index = 0

        def generate_question(self, gate_decision, attempt_number, competence_entries=None):
            seed = gate_decision.qa_packet.prompt_seed if gate_decision.qa_packet else ""
            return GeneratedQuestion(question=f"Q{attempt_number}: {seed}", distractors=[], hint="")

        def evaluate_answer(self, question, answer, question_type, context_excerpt, attempt_number):
            ev = evaluations[self._index]
            self._index += 1
            return ev

    monkeypatch.setattr(llm_wrapper_module, "_client", _FakeLLMClient())


class _FakeRenderer:
    def __init__(self, answers: list[str]) -> None:
        self._answers = answers
        self._index = 0

    def ask(self, question: str, attempt_number: int, packet: QAPacket) -> str:
        del question, attempt_number, packet
        answer = self._answers[self._index]
        self._index += 1
        return answer


def _make_proposal(proposal_id: str = "prop-1") -> ChangeProposal:
    return ChangeProposal(
        proposal_id=proposal_id,
        session_id="session-1",
        tool_use_id="tool-1",
        tool_name="Write",
        cwd="/repo",
        targets=[
            ChangeTarget(
                path="core/example.py",
                language="python",
                old_content="x = 1\n",
                new_content="x = 2\n",
            )
        ],
        unified_diff="-x = 1\n+x = 2",
        diff_stats=DiffStats(files_changed=1, additions=1, deletions=1),
        created_at="2026-04-14T00:00:00Z",
    )


def _make_gate_decision() -> GateDecision:
    return GateDecision(
        decision="block",
        reasoning="Variable mutation may not be understood.",
        confidence=0.6,
        relevant_concepts=["python_basics"],
        competence_gap=CompetenceGap(size="low", rationale="Simple assignment."),
        qa_packet=QAPacket(
            question_type="true_false",
            prompt_seed="Is this assignment safe?",
            context_excerpt="-x = 1\n+x = 2",
        ),
    )


# ---------------------------------------------------------------------------
# E2E: block → embed → retrieve
# ---------------------------------------------------------------------------


def test_blocked_event_persisted_and_retrieved(tmp_path, monkeypatch) -> None:
    """A failed QA loop saves the outcome; a subsequent retrieve call finds it."""

    # Override get_chroma_collection for this test to use a real PersistentClient
    # at tmp_path.  This overrides the autouse conftest patch so both the QALoop
    # (which calls get_chroma_collection via loop.run when no collection is injected)
    # and retrieve_relevant_events share the same on-disk store.
    monkeypatch.setattr(
        "core.competence_store.get_chroma_collection",
        _persistent_chroma_factory(tmp_path),
    )

    _install_fake_llm(
        monkeypatch,
        [
            AnswerEvaluation(passed=False, feedback="No."),
            AnswerEvaluation(passed=False, feedback="No."),
            AnswerEvaluation(passed=False, feedback="No."),
        ],
    )

    proposal = _make_proposal("prop-rag-fail")
    loop = QALoop(renderer=_FakeRenderer(["bad", "bad", "bad"]))

    result = loop.run(
        proposal=proposal,
        gate_decision=_make_gate_decision(),
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )

    assert result.passed is False  # sanity: QA failed

    # Now retrieve using the same file_path and a semantically similar diff
    events = retrieve_relevant_events(
        "core/example.py",
        "-x = 1\n+x = 2",
        db_path=tmp_path / "state" / "chroma_db",
    )

    assert len(events) == 1, f"Expected 1 event, got {len(events)}: {events}"

    event = events[0]
    assert event["metadata"]["event_type"] == "qa_fail"
    assert event["metadata"]["qa_pass"] is False
    assert event["metadata"]["file_path"] == "core/example.py"
    assert event["metadata"]["language"] == "python"
    assert "timestamp" in event["metadata"]
    assert "core/example.py" in event["document"]
    assert "python_basics" in event["document"]


def test_retrieved_events_appear_in_aggregated_context(tmp_path, monkeypatch) -> None:
    """Historical events from retrieve_relevant_events are rendered in the markdown."""

    monkeypatch.setattr(
        "core.competence_store.get_chroma_collection",
        _persistent_chroma_factory(tmp_path),
    )

    _install_fake_llm(
        monkeypatch,
        [
            AnswerEvaluation(passed=False, feedback="No."),
            AnswerEvaluation(passed=False, feedback="No."),
            AnswerEvaluation(passed=False, feedback="No."),
        ],
    )

    proposal = _make_proposal("prop-rag-ctx")
    QALoop(renderer=_FakeRenderer(["bad", "bad", "bad"])).run(
        proposal=proposal,
        gate_decision=_make_gate_decision(),
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )

    events = retrieve_relevant_events(
        "core/example.py",
        "-x = 1\n+x = 2",
        db_path=tmp_path / "state" / "chroma_db",
    )
    assert len(events) == 1

    # Format events the same way pre_tool_use does and pass them to aggregation
    from hooks.pre_tool_use import _format_historical_events

    formatted = _format_historical_events(events)
    aggregated = build_aggregated_context(
        _make_proposal("prop-rag-ctx-2"),
        tmp_path / "state2",
        historical_context=formatted,
    )

    assert "## Historical Competence Events" in aggregated.markdown
    assert "qa_fail" in aggregated.markdown
    assert "core/example.py" in aggregated.markdown


# ---------------------------------------------------------------------------
# Gate prompt: instruction and historical context
# ---------------------------------------------------------------------------


def test_gate_prompt_contains_historical_instruction(tmp_path) -> None:
    """The system prompt sent to the LLM must carry the strict blocking instruction."""
    from unittest.mock import MagicMock

    mock_client = MagicMock()
    mock_client.create_response.return_value = (
        '{"decision": "allow", "reasoning": "Fine.", "confidence": 0.9, "relevant_concepts": []}'
    )

    proposal = _make_proposal("prop-gate-instr")
    aggregated = build_aggregated_context(proposal, tmp_path)

    KnowledgeGate(client=mock_client).evaluate(
        proposal, aggregated, default_competence_model()
    )

    call_kwargs = mock_client.create_response.call_args.kwargs
    input_messages = call_kwargs["input_data"]
    system_msg = next(m for m in input_messages if m.role == "system")

    assert "historical lessons" in system_msg.content
    assert "BLOCK the change" in system_msg.content
    assert "previous mistakes" in system_msg.content


def test_gate_prompt_references_historical_events_section(tmp_path) -> None:
    """The user prompt must explicitly point the model at the historical section."""
    from unittest.mock import MagicMock

    mock_client = MagicMock()
    mock_client.create_response.return_value = (
        '{"decision": "allow", "reasoning": "Fine.", "confidence": 0.9, "relevant_concepts": []}'
    )

    proposal = _make_proposal("prop-gate-hist-ref")
    aggregated = build_aggregated_context(
        proposal,
        tmp_path,
        historical_context="### Past Event 1\n- event_type: qa_fail\n- lesson: x was wrong",
    )

    KnowledgeGate(client=mock_client).evaluate(
        proposal, aggregated, default_competence_model()
    )

    call_kwargs = mock_client.create_response.call_args.kwargs
    input_messages = call_kwargs["input_data"]
    user_msg = next(m for m in input_messages if m.role == "user")

    assert "## Historical Competence Events" in user_msg.content
    assert "x was wrong" in user_msg.content  # historical content flows into user prompt
    assert "MUST set decision to" in user_msg.content


@pytest.mark.parametrize("qa_pass", [True, False])
def test_both_pass_and_fail_outcomes_are_retrievable(tmp_path, monkeypatch, qa_pass) -> None:
    """Both user_correction and qa_fail events can be stored and retrieved."""

    monkeypatch.setattr(
        "core.competence_store.get_chroma_collection",
        _persistent_chroma_factory(tmp_path),
    )

    evaluations = (
        [AnswerEvaluation(passed=True, feedback="Good!")]
        if qa_pass
        else [
            AnswerEvaluation(passed=False, feedback="No."),
            AnswerEvaluation(passed=False, feedback="No."),
            AnswerEvaluation(passed=False, feedback="No."),
        ]
    )
    answers = ["correct"] if qa_pass else ["bad", "bad", "bad"]

    _install_fake_llm(monkeypatch, evaluations)

    proposal_id = f"prop-param-{'pass' if qa_pass else 'fail'}"
    QALoop(renderer=_FakeRenderer(answers)).run(
        proposal=_make_proposal(proposal_id),
        gate_decision=_make_gate_decision(),
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )

    events = retrieve_relevant_events(
        "core/example.py",
        "-x = 1\n+x = 2",
        db_path=tmp_path / "state" / "chroma_db",
    )

    assert len(events) == 1
    expected_type = "user_correction" if qa_pass else "qa_fail"
    assert events[0]["metadata"]["event_type"] == expected_type
    assert events[0]["metadata"]["qa_pass"] is qa_pass
