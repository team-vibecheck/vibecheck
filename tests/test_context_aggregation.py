from core.context_aggregation import build_aggregated_context
from core.normalize import normalize_mutation_payload


def test_build_aggregated_context_persists_markdown(tmp_path) -> None:
    proposal = normalize_mutation_payload(
        {
            "tool_name": "Write",
            "session_id": "session-1",
            "tool_use_id": "tool-1",
            "cwd": "/repo",
            "input": {
                "path": "core/example.py",
                "new_content": "print('hello')\n",
            },
        }
    )

    aggregated = build_aggregated_context(
        proposal,
        tmp_path,
        user_prompt_excerpt="Add a greeting.",
        transcript_excerpt="Claude proposed a tiny change.",
        surrounding_code="def greet():\n    pass\n",
        repo_notes="No special notes.",
    )

    assert aggregated.artifact_path.exists()
    assert "## User Prompt Excerpt" in aggregated.markdown
    assert "Add a greeting." in aggregated.artifact_path.read_text(encoding="utf-8")
    assert "## New Code" in aggregated.qa_context_excerpt
    assert "## Unified Diff" in aggregated.qa_context_excerpt
    assert "- primary_path: core/example.py" in aggregated.qa_context_excerpt
    assert "- primary_language: python" in aggregated.qa_context_excerpt
    assert "## Relevant Transcript Slice" not in aggregated.qa_context_excerpt
    assert "## Repo-Local Notes" not in aggregated.qa_context_excerpt
