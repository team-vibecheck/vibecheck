from __future__ import annotations

import chromadb

from core.competence_store import default_competence_model
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


from chromadb import EmbeddingFunction
from chromadb.api.types import Documents, Embeddings


class _FakeEmbeddingFunction(EmbeddingFunction[Documents]):
    """Avoids network calls to OpenAI in tests.

    Properly subclasses EmbeddingFunction so chromadb 1.5.x can call
    embed_query() during collection.query().
    """

    def __init__(self) -> None:
        pass

    def __call__(self, input: Documents) -> Embeddings:
        return [[0.0] * 8 for _ in input]

    @staticmethod
    def name() -> str:
        return "fake"

    def get_config(self) -> dict:  # type: ignore[override]
        return {}

    @staticmethod
    def build_from_config(config: dict) -> "_FakeEmbeddingFunction":  # type: ignore[override]
        return _FakeEmbeddingFunction()


def _ephemeral_collection() -> chromadb.Collection:
    client = chromadb.EphemeralClient()
    return client.get_or_create_collection(
        "competence_events",
        embedding_function=_FakeEmbeddingFunction(),
    )


def _install_fake_llm(monkeypatch, evaluations: list[AnswerEvaluation]) -> None:
    from qa import llm_wrapper as llm_wrapper_module
    from qa.llm_wrapper import GeneratedQuestion

    class _FakeLLMClient:
        def __init__(self) -> None:
            self._index = 0

        def generate_question(self, gate_decision, attempt_number, competence_entries=None):
            seed = gate_decision.qa_packet.prompt_seed if gate_decision.qa_packet else ""
            return GeneratedQuestion(
                question=f"Attempt {attempt_number}: {seed}",
                distractors=[],
                hint="",
            )

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
                old_content="",
                new_content="value = 1\n",
            )
        ],
        unified_diff="+value = 1",
        diff_stats=DiffStats(files_changed=1, additions=1, deletions=0),
        created_at="2026-04-14T00:00:00Z",
    )


def _make_gate_decision() -> GateDecision:
    return GateDecision(
        decision="block",
        reasoning="Variable assignment may not be understood in context.",
        confidence=0.6,
        relevant_concepts=["python_basics"],
        competence_gap=CompetenceGap(size="medium", rationale="Test gap."),
        qa_packet=QAPacket(
            question_type="plain_english",
            prompt_seed="Why is this assignment safe?",
            context_excerpt="+value = 1",
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_chroma_ingestion_on_pass(tmp_path, monkeypatch) -> None:
    _install_fake_llm(monkeypatch, [AnswerEvaluation(passed=True, feedback="Good!")])
    collection = _ephemeral_collection()

    loop = QALoop(
        renderer=_FakeRenderer(["correct answer"]),
        chroma_collection=collection,
    )
    result = loop.run(
        proposal=_make_proposal("prop-pass"),
        gate_decision=_make_gate_decision(),
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )

    assert result.passed is True

    docs = collection.get(ids=["prop-pass"])
    assert len(docs["ids"]) == 1

    meta = docs["metadatas"][0]
    assert meta["event_type"] == "user_correction"
    assert meta["qa_pass"] is True
    assert meta["file_path"] == "core/example.py"
    assert meta["language"] == "python"
    assert "timestamp" in meta

    assert "core/example.py" in docs["documents"][0]
    assert "python_basics" in docs["documents"][0]


def test_chroma_ingestion_on_fail(tmp_path, monkeypatch) -> None:
    _install_fake_llm(
        monkeypatch,
        [
            AnswerEvaluation(passed=False, feedback="No."),
            AnswerEvaluation(passed=False, feedback="No."),
            AnswerEvaluation(passed=False, feedback="No."),
        ],
    )
    collection = _ephemeral_collection()

    loop = QALoop(
        renderer=_FakeRenderer(["bad", "bad", "bad"]),
        chroma_collection=collection,
    )
    result = loop.run(
        proposal=_make_proposal("prop-fail"),
        gate_decision=_make_gate_decision(),
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )

    assert result.passed is False

    docs = collection.get(ids=["prop-fail"])
    assert len(docs["ids"]) == 1

    meta = docs["metadatas"][0]
    assert meta["event_type"] == "qa_fail"
    assert meta["qa_pass"] is False
    assert meta["file_path"] == "core/example.py"
    assert meta["language"] == "python"
    assert "timestamp" in meta

    assert "Epistemic debt" in docs["documents"][0]


def test_chroma_document_contains_gate_reasoning(tmp_path, monkeypatch) -> None:
    _install_fake_llm(monkeypatch, [AnswerEvaluation(passed=True, feedback="Good!")])
    collection = _ephemeral_collection()

    loop = QALoop(
        renderer=_FakeRenderer(["answer"]),
        chroma_collection=collection,
    )
    loop.run(
        proposal=_make_proposal("prop-reasoning"),
        gate_decision=_make_gate_decision(),
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )

    docs = collection.get(ids=["prop-reasoning"])
    document = docs["documents"][0]
    # Gate reasoning should be embedded in the document string
    assert "Variable assignment may not be understood in context." in document


def test_chroma_metadata_timestamp_is_iso_format(tmp_path, monkeypatch) -> None:
    from datetime import datetime

    _install_fake_llm(monkeypatch, [AnswerEvaluation(passed=True, feedback="Good!")])
    collection = _ephemeral_collection()

    loop = QALoop(
        renderer=_FakeRenderer(["answer"]),
        chroma_collection=collection,
    )
    loop.run(
        proposal=_make_proposal("prop-ts"),
        gate_decision=_make_gate_decision(),
        competence_model=default_competence_model(),
        competence_path=tmp_path / "cm.yaml",
        state_dir=tmp_path / "state",
    )

    docs = collection.get(ids=["prop-ts"])
    ts = docs["metadatas"][0]["timestamp"]
    # Should parse without error
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.year >= 2026
