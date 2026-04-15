"""Tests for the ChromaDB RAG competence model.

Structured in four sections matching the task spec:
  1. Fixtures   — EphemeralClient + unittest.mock for OpenAI embeddings
  2. Ingestion  — save QA outcome and assert document + metadata schema
  3. Retrieval  — file_path filtering via retrieve_relevant_events
  4. Gate       — historical context strings present in the LLM prompt payload
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import chromadb
import pytest
from chromadb import EmbeddingFunction
from chromadb.api.types import Documents, Embeddings
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

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
    QAResult,
)
from qa.loop import _save_qa_outcome_to_chroma

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Dimensionality used by text-embedding-3-small.
DUMMY_VECTOR: list[float] = [0.1] * 1536


# ---------------------------------------------------------------------------
# Section 1 — Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def openai_mock():
    """Patch openai.OpenAI so no real network calls are made.

    Configures the mock so that embeddings.create returns DUMMY_VECTOR for
    every input string, regardless of batch size.  Yields the mock embeddings
    client so individual tests can inspect call counts if needed.
    """

    def _make_response(**kwargs):
        texts = kwargs.get("input", [])
        n = len(texts) if isinstance(texts, list) else 1
        return MagicMock(data=[MagicMock(embedding=list(DUMMY_VECTOR)) for _ in range(n)])

    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = _make_response
        MockOpenAI.return_value = mock_client
        yield mock_client


@pytest.fixture
def ephemeral_collection(openai_mock) -> chromadb.Collection:
    """Return an in-memory ChromaDB collection wired to the mocked OpenAI EF.

    Uses chromadb.EphemeralClient so nothing is written to disk.
    The openai_mock fixture ensures both __call__ (add) and embed_query
    (query) return DUMMY_VECTOR without hitting the real API.
    """
    ef = OpenAIEmbeddingFunction(api_key="test-key", model_name="text-embedding-3-small")
    client = chromadb.EphemeralClient()
    return client.get_or_create_collection(f"test_{uuid.uuid4().hex}", embedding_function=ef)


def _collection_with_mock(openai_mock) -> chromadb.Collection:
    """Create an isolated ephemeral collection for a single test.

    chromadb.EphemeralClient instances share one in-memory backend per process,
    so collections with the same name accumulate data across tests.  Using a
    UUID-suffixed name gives each call a fresh, empty namespace.
    """
    ef = OpenAIEmbeddingFunction(api_key="test-key", model_name="text-embedding-3-small")
    client = chromadb.EphemeralClient()
    return client.get_or_create_collection(
        f"test_{uuid.uuid4().hex}",
        embedding_function=ef,
    )


# ---------------------------------------------------------------------------
# Shared object builders
# ---------------------------------------------------------------------------


def _make_proposal(proposal_id: str = "test-prop") -> ChangeProposal:
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
        created_at="2026-04-15T00:00:00Z",
    )


def _make_gate_decision() -> GateDecision:
    return GateDecision(
        decision="block",
        reasoning="Mutation pattern not previously demonstrated.",
        confidence=0.7,
        relevant_concepts=["python_basics"],
        competence_gap=CompetenceGap(size="medium", rationale="Pattern unseen."),
        qa_packet=QAPacket(
            question_type="plain_english",
            prompt_seed="Why is this assignment safe?",
            context_excerpt="-x = 1\n+x = 2",
        ),
    )


def _make_qa_result(proposal_id: str, *, passed: bool, attempt_count: int = 1) -> QAResult:
    return QAResult(
        proposal_id=proposal_id,
        final_decision="allow",
        passed=passed,
        attempt_count=attempt_count,
        attempts=[],
        summary="Test result.",
    )


# ---------------------------------------------------------------------------
# Section 2 — Ingestion
# ---------------------------------------------------------------------------


def test_ingestion_pass_writes_correct_document_and_metadata(
    tmp_path, ephemeral_collection
) -> None:
    """A passing QA result is stored with event_type='user_correction' and all schema fields."""
    _save_qa_outcome_to_chroma(
        ephemeral_collection,
        _make_proposal("ingest-pass"),
        _make_gate_decision(),
        _make_qa_result("ingest-pass", passed=True),
    )

    docs = ephemeral_collection.get(ids=["ingest-pass"])
    assert len(docs["ids"]) == 1

    document = docs["documents"][0]
    meta = docs["metadatas"][0]

    # Document encodes file, language, gate reasoning, and lesson
    assert "core/example.py" in document
    assert "python" in document
    assert "Mutation pattern not previously demonstrated." in document
    assert "python_basics" in document

    # All required metadata keys present with correct types
    assert meta["event_type"] == "user_correction"
    assert meta["qa_pass"] is True
    assert meta["file_path"] == "core/example.py"
    assert meta["language"] == "python"
    assert "timestamp" in meta

    # Timestamp is a parseable ISO-8601 string
    parsed = datetime.fromisoformat(meta["timestamp"].replace("Z", "+00:00"))
    assert parsed.year >= 2026


def test_ingestion_fail_writes_qa_fail_event_type(tmp_path, ephemeral_collection) -> None:
    """A failed QA result produces event_type='qa_fail', qa_pass=False, and epistemic debt note."""
    _save_qa_outcome_to_chroma(
        ephemeral_collection,
        _make_proposal("ingest-fail"),
        _make_gate_decision(),
        _make_qa_result("ingest-fail", passed=False, attempt_count=3),
    )

    docs = ephemeral_collection.get(ids=["ingest-fail"])
    meta = docs["metadatas"][0]

    assert meta["event_type"] == "qa_fail"
    assert meta["qa_pass"] is False
    assert "Epistemic debt" in docs["documents"][0]


def test_ingestion_invokes_openai_embeddings_create(openai_mock, ephemeral_collection) -> None:
    """Adding a document triggers the mocked OpenAI embeddings.create call."""
    _save_qa_outcome_to_chroma(
        ephemeral_collection,
        _make_proposal("ingest-api-check"),
        _make_gate_decision(),
        _make_qa_result("ingest-api-check", passed=True),
    )

    assert openai_mock.embeddings.create.called, (
        "Expected openai.OpenAI().embeddings.create() to be called during document ingestion"
    )


def test_ingestion_dummy_vector_has_correct_dimensionality(openai_mock) -> None:
    """The mocked OpenAI API returns 1536-dimensional vectors (text-embedding-3-small size)."""
    ef = OpenAIEmbeddingFunction(api_key="test-key", model_name="text-embedding-3-small")
    embeddings = ef(["some text"])  # triggers __call__ → openai_mock

    assert len(embeddings) == 1
    assert len(embeddings[0]) == 1536


# ---------------------------------------------------------------------------
# Section 3 — Retrieval
# ---------------------------------------------------------------------------


def test_retrieval_filters_by_file_path(tmp_path, monkeypatch, openai_mock) -> None:
    """retrieve_relevant_events returns only events whose file_path metadata matches."""
    collection = _collection_with_mock(openai_mock)

    # Insert events for two different files
    collection.add(
        documents=[
            "Change to core/example.py (python). Lesson: user failed pattern check.",
            "Change to tests/test_other.py (python). Lesson: user passed import check.",
        ],
        metadatas=[
            {
                "file_path": "core/example.py",
                "event_type": "qa_fail",
                "timestamp": "2026-04-15T00:00:00Z",
                "language": "python",
                "qa_pass": False,
            },
            {
                "file_path": "tests/test_other.py",
                "event_type": "user_correction",
                "timestamp": "2026-04-15T00:00:00Z",
                "language": "python",
                "qa_pass": True,
            },
        ],
        ids=["event-target", "event-other"],
    )

    monkeypatch.setattr(
        "core.competence_store.get_chroma_collection",
        lambda db_path, embedding_function=None: collection,
    )

    events = retrieve_relevant_events(
        "core/example.py",
        "-x = 1\n+x = 2",
        db_path=tmp_path / "unused",
    )

    assert len(events) == 1
    assert events[0]["metadata"]["file_path"] == "core/example.py"
    assert events[0]["metadata"]["event_type"] == "qa_fail"
    assert "pattern check" in events[0]["document"]


def test_retrieval_returns_empty_when_no_file_path_matches(
    tmp_path, monkeypatch, openai_mock
) -> None:
    """retrieve_relevant_events returns [] when no events match the given file_path."""
    collection = _collection_with_mock(openai_mock)
    collection.add(
        documents=["Change to other/module.py (python). Lesson: user passed."],
        metadatas=[
            {
                "file_path": "other/module.py",
                "event_type": "user_correction",
                "timestamp": "2026-04-15T00:00:00Z",
                "language": "python",
                "qa_pass": True,
            }
        ],
        ids=["event-other"],
    )

    monkeypatch.setattr(
        "core.competence_store.get_chroma_collection",
        lambda db_path, embedding_function=None: collection,
    )

    events = retrieve_relevant_events(
        "core/example.py",  # not in the collection
        "-x = 1\n+x = 2",
        db_path=tmp_path / "unused",
    )

    assert events == []


def test_retrieval_returns_empty_for_empty_collection(
    tmp_path, monkeypatch, openai_mock
) -> None:
    """retrieve_relevant_events returns [] gracefully on an empty collection."""
    collection = _collection_with_mock(openai_mock)

    monkeypatch.setattr(
        "core.competence_store.get_chroma_collection",
        lambda db_path, embedding_function=None: collection,
    )

    events = retrieve_relevant_events(
        "core/example.py",
        "any diff",
        db_path=tmp_path / "unused",
    )

    assert events == []


def test_retrieval_respects_n_results_cap(tmp_path, monkeypatch, openai_mock) -> None:
    """retrieve_relevant_events returns at most n_results items."""
    collection = _collection_with_mock(openai_mock)

    for i in range(5):
        collection.add(
            documents=[f"Event {i} for core/example.py"],
            metadatas=[
                {
                    "file_path": "core/example.py",
                    "event_type": "qa_fail",
                    "timestamp": "2026-04-15T00:00:00Z",
                    "language": "python",
                    "qa_pass": False,
                }
            ],
            ids=[f"event-cap-{i}"],
        )

    monkeypatch.setattr(
        "core.competence_store.get_chroma_collection",
        lambda db_path, embedding_function=None: collection,
    )

    events = retrieve_relevant_events(
        "core/example.py",
        "-x = 1\n+x = 2",
        db_path=tmp_path / "unused",
        n_results=2,
    )

    assert len(events) <= 2


def test_retrieval_result_has_document_and_metadata_keys(
    tmp_path, monkeypatch, openai_mock
) -> None:
    """Each entry returned by retrieve_relevant_events has 'document' and 'metadata' keys."""
    collection = _collection_with_mock(openai_mock)
    collection.add(
        documents=["Change to core/example.py (python). Lesson: user failed."],
        metadatas=[
            {
                "file_path": "core/example.py",
                "event_type": "qa_fail",
                "timestamp": "2026-04-15T00:00:00Z",
                "language": "python",
                "qa_pass": False,
            }
        ],
        ids=["event-shape"],
    )

    monkeypatch.setattr(
        "core.competence_store.get_chroma_collection",
        lambda db_path, embedding_function=None: collection,
    )

    events = retrieve_relevant_events(
        "core/example.py",
        "-x = 1\n+x = 2",
        db_path=tmp_path / "unused",
    )

    assert len(events) == 1
    assert "document" in events[0]
    assert "metadata" in events[0]
    assert isinstance(events[0]["document"], str)
    assert isinstance(events[0]["metadata"], dict)


# ---------------------------------------------------------------------------
# Section 4 — Gate integration
# ---------------------------------------------------------------------------


def test_gate_prompt_contains_historical_context_strings(tmp_path) -> None:
    """Historical events passed via build_aggregated_context appear in the user prompt."""
    mock_or_client = MagicMock()
    mock_or_client.create_response.return_value = (
        '{"decision": "allow", "reasoning": "OK.", "confidence": 0.9, "relevant_concepts": []}'
    )

    past_events = (
        "### Past Event 1\n"
        "- file_path: core/example.py\n"
        "- event_type: qa_fail\n"
        "- timestamp: 2026-04-15T00:00:00Z\n"
        "- qa_pass: False\n"
        "- lesson: Change to core/example.py (python). User failed 3 times; epistemic debt.\n"
    )

    aggregated = build_aggregated_context(
        _make_proposal("gate-hist"),
        tmp_path,
        historical_context=past_events,
    )

    KnowledgeGate(client=mock_or_client).evaluate(
        _make_proposal("gate-hist"),
        aggregated,
        default_competence_model(),
    )

    messages = mock_or_client.create_response.call_args.kwargs["input_data"]
    user_msg = next(m for m in messages if m.role == "user")

    # The historical section and its content must be present in the user prompt
    assert "## Historical Competence Events" in user_msg.content
    assert "qa_fail" in user_msg.content
    assert "epistemic debt" in user_msg.content
    assert "core/example.py" in user_msg.content


def test_gate_system_prompt_carries_strict_block_instruction(tmp_path) -> None:
    """The system prompt must include the required historical-lesson blocking instruction."""
    mock_or_client = MagicMock()
    mock_or_client.create_response.return_value = (
        '{"decision": "allow", "reasoning": "OK.", "confidence": 0.9, "relevant_concepts": []}'
    )

    aggregated = build_aggregated_context(_make_proposal("gate-sys"), tmp_path)
    KnowledgeGate(client=mock_or_client).evaluate(
        _make_proposal("gate-sys"), aggregated, default_competence_model()
    )

    messages = mock_or_client.create_response.call_args.kwargs["input_data"]
    system_msg = next(m for m in messages if m.role == "system")

    assert "previous mistakes" in system_msg.content
    assert "BLOCK the change" in system_msg.content
    assert "historical lessons" in system_msg.content


def test_gate_user_prompt_references_historical_section_note(tmp_path) -> None:
    """The user prompt Notes section explicitly points the model at the historical section."""
    mock_or_client = MagicMock()
    mock_or_client.create_response.return_value = (
        '{"decision": "allow", "reasoning": "OK.", "confidence": 0.9, "relevant_concepts": []}'
    )

    aggregated = build_aggregated_context(
        _make_proposal("gate-note"),
        tmp_path,
        historical_context="### Past Event 1\n- event_type: qa_fail",
    )
    KnowledgeGate(client=mock_or_client).evaluate(
        _make_proposal("gate-note"), aggregated, default_competence_model()
    )

    messages = mock_or_client.create_response.call_args.kwargs["input_data"]
    user_msg = next(m for m in messages if m.role == "user")

    # The gate notes must tell the model it MUST block on matching history
    assert "MUST set decision to" in user_msg.content
    assert "## Historical Competence Events" in user_msg.content


def test_gate_placeholder_shown_when_no_historical_events(tmp_path) -> None:
    """When no RAG events were found the historical section renders '<none>'."""
    mock_or_client = MagicMock()
    mock_or_client.create_response.return_value = (
        '{"decision": "allow", "reasoning": "OK.", "confidence": 0.9, "relevant_concepts": []}'
    )

    # No historical_context kwarg — defaults to ""
    aggregated = build_aggregated_context(_make_proposal("gate-none"), tmp_path)
    KnowledgeGate(client=mock_or_client).evaluate(
        _make_proposal("gate-none"), aggregated, default_competence_model()
    )

    messages = mock_or_client.create_response.call_args.kwargs["input_data"]
    user_msg = next(m for m in messages if m.role == "user")

    assert "## Historical Competence Events" in user_msg.content
    assert "<none>" in user_msg.content
