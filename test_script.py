from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from core.gate import KnowledgeGate
from core.models import (
    AggregatedContext,
    ChangeProposal,
    ChangeTarget,
    CompetenceEntry,
    CompetenceModel,
    DiffStats,
)


def build_dummy_change_proposal() -> ChangeProposal:
    created_at = datetime.now(UTC).isoformat()
    return ChangeProposal(
        proposal_id="dummy-proposal-001",
        session_id="dummy-session-001",
        tool_use_id="dummy-tool-use-001",
        tool_name="apply_patch",
        cwd=str(Path.cwd()),
        targets=[
            ChangeTarget(
                path="core/example.py",
                language="python",
                old_content="def greet(name):\n    return name\n",
                new_content="def greet(name):\n    return f'Hello, {name}'\n",
            )
        ],
        unified_diff=(
            "--- a/core/example.py\n"
            "+++ b/core/example.py\n"
            "@@\n"
            "-def greet(name):\n"
            "-    return name\n"
            "+def greet(name):\n"
            "+    return f'Hello, {name}'\n"
        ),
        diff_stats=DiffStats(files_changed=1, additions=1, deletions=1),
        created_at=created_at,
    )


def build_dummy_inputs() -> tuple[ChangeProposal, AggregatedContext, CompetenceModel]:
    created_at = datetime.now(UTC).isoformat()
    proposal = build_dummy_change_proposal()

    aggregated_context = AggregatedContext(
        proposal_id=proposal.proposal_id,
        markdown=(
            "### Change Summary\n"
            "- Update greet() to include a friendly greeting prefix.\n"
            "\n"
            "### Risk\n"
            "- Low: behavior change is small and localized.\n"
            "\n"
            "### Target Files\n"
            "- core/example.py\n"
        ),
        artifact_path=Path("state/agg/current_attempt.md"),
    )

    competence_model = CompetenceModel(
        user_id="dummy-user",
        updated_at=created_at,
        concepts={
            "python.functions": CompetenceEntry(
                score=0.78,
                notes=["Comfortable with simple function edits."],
            ),
            "python.strings": CompetenceEntry(
                score=0.64,
                notes=["Learning f-string formatting."],
            ),
        },
    )

    return proposal, aggregated_context, competence_model


if __name__ == "__main__":
    gate = KnowledgeGate()
    proposal, aggregated_context, competence_model = build_dummy_inputs()
    print(f"Dummy proposal: {proposal.proposal_id}")
    print(f"Diff stats: +{proposal.diff_stats.additions} / -{proposal.diff_stats.deletions}")
    decision = gate.evaluate(
        proposal=proposal,
        aggregated_context=aggregated_context,
        competence_model=competence_model,
    )
    print(decision)

