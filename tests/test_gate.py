from unittest.mock import MagicMock

from core.competence_store import default_competence_model
from core.context_aggregation import build_aggregated_context
from core.gate import KnowledgeGate
from core.normalize import normalize_mutation_payload


def test_gate_allows_small_change(tmp_path, monkeypatch) -> None:
    mock_client = MagicMock()
    mock_client.create_response.return_value = '{"decision": "allow", "reasoning": "Small change.", "confidence": 0.9, "relevant_concepts": []}'
    monkeypatch.setattr("core.gate.OpenRouterClient", lambda: mock_client)

    proposal = normalize_mutation_payload(
        {
            "tool_name": "Write",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "cwd": "/repo",
            "input": {
                "path": "core/example.py",
                "old_content": "x = 1\n",
                "new_content": "x = 2\n",
            },
        }
    )
    aggregated = build_aggregated_context(proposal, tmp_path)

    decision = KnowledgeGate(client=mock_client).evaluate(
        proposal, aggregated, default_competence_model()
    )

    assert decision.decision == "allow"
    assert decision.qa_packet is None


def test_gate_blocks_larger_change(tmp_path, monkeypatch) -> None:
    mock_client = MagicMock()
    mock_client.create_response.return_value = """{
  "decision": "block",
  "reasoning": "Large change requires validation.",
  "confidence": 0.7,
  "relevant_concepts": ["python_basics"],
  "competence_gap": {
    "size": "medium",
    "rationale": "Control flow changed."
  },
  "prompt_seed": "Explain the control flow."
}"""
    monkeypatch.setattr("core.gate.OpenRouterClient", lambda: mock_client)

    big_new_content = "\n".join(f"line_{index} = {index}" for index in range(30))
    proposal = normalize_mutation_payload(
        {
            "tool_name": "Write",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "cwd": "/repo",
            "input": {
                "path": "core/example.py",
                "old_content": "",
                "new_content": big_new_content,
            },
        }
    )
    aggregated = build_aggregated_context(proposal, tmp_path)

    decision = KnowledgeGate(client=mock_client).evaluate(
        proposal, aggregated, default_competence_model()
    )

    assert decision.decision == "block"
    assert decision.qa_packet is not None
    assert decision.qa_packet.question_type == "plain_english"


def test_gate_uses_model_structured_output_when_client_available(tmp_path) -> None:
    mock_client = MagicMock()
    mock_client.create_response.return_value = """{
  "decision": "block",
  "reasoning": "The patch introduces behavior beyond demonstrated competence.",
  "confidence": 0.73,
  "relevant_concepts": ["python_basics"],
  "competence_gap": {
    "size": "medium",
    "rationale": "Control flow changed and requires mechanism validation."
  },
  "prompt_seed": "Explain how the updated control flow avoids regressions."
}"""

    big_new_content = "\n".join(f"line_{index} = {index}" for index in range(30))
    proposal = normalize_mutation_payload(
        {
            "tool_name": "Write",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "cwd": "/repo",
            "input": {
                "path": "core/example.py",
                "old_content": "",
                "new_content": big_new_content,
            },
        }
    )
    aggregated = build_aggregated_context(proposal, tmp_path)

    decision = KnowledgeGate(client=mock_client).evaluate(
        proposal,
        aggregated,
        default_competence_model(),
    )

    assert decision.decision == "block"
    assert decision.confidence == 0.73
    assert decision.competence_gap is not None
    assert decision.competence_gap.size == "medium"
    assert decision.qa_packet is not None
    assert "control flow" in decision.qa_packet.prompt_seed.lower()
