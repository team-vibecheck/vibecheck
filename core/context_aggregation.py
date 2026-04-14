from __future__ import annotations

from pathlib import Path

from core.models import AggregatedContext, ChangeProposal


def build_aggregated_context(
    proposal: ChangeProposal,
    state_dir: Path,
    *,
    user_prompt_excerpt: str = "",
    transcript_excerpt: str = "",
    surrounding_code: str = "",
    repo_notes: str = "",
    historical_context: str = "",
) -> AggregatedContext:
    artifact_path = state_dir / "agg" / "current_attempt.md"
    markdown = render_aggregated_context(
        proposal,
        user_prompt_excerpt=user_prompt_excerpt,
        transcript_excerpt=transcript_excerpt,
        surrounding_code=surrounding_code,
        repo_notes=repo_notes,
        historical_context=historical_context,
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(markdown, encoding="utf-8")
    return AggregatedContext(
        proposal_id=proposal.proposal_id,
        markdown=markdown,
        artifact_path=artifact_path,
    )


def render_aggregated_context(
    proposal: ChangeProposal,
    *,
    user_prompt_excerpt: str,
    transcript_excerpt: str,
    surrounding_code: str,
    repo_notes: str,
    historical_context: str = "",
) -> str:
    old_code = "\n\n".join(target.old_content or "<new file>" for target in proposal.targets)
    new_code = "\n\n".join(target.new_content for target in proposal.targets)
    metadata_lines = [
        f"- proposal_id: {proposal.proposal_id}",
        f"- session_id: {proposal.session_id}",
        f"- tool_use_id: {proposal.tool_use_id}",
        f"- tool_name: {proposal.tool_name}",
        f"- cwd: {proposal.cwd}",
        f"- files_changed: {proposal.diff_stats.files_changed}",
        f"- additions: {proposal.diff_stats.additions}",
        f"- deletions: {proposal.diff_stats.deletions}",
    ]

    return "\n".join(
        [
            "# VibeCheck Aggregated Context",
            "",
            "## Metadata",
            *metadata_lines,
            "",
            "## User Prompt Excerpt",
            user_prompt_excerpt or "<missing>",
            "",
            "## Old Code",
            "```text",
            old_code,
            "```",
            "",
            "## New Code",
            "```text",
            new_code,
            "```",
            "",
            "## Unified Diff",
            "```diff",
            proposal.unified_diff or "<empty diff>",
            "```",
            "",
            "## Surrounding Code",
            "```text",
            surrounding_code or "<missing>",
            "```",
            "",
            "## Relevant Transcript Slice",
            transcript_excerpt or "<missing>",
            "",
            "## Repo-Local Notes",
            repo_notes or "<none>",
            "",
            "## Historical Competence Events",
            historical_context or "<none>",
        ]
    )
