from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

GateTopLevelDecision = Literal["allow", "block"]
QuestionType = Literal["faded_example", "plain_english", "true_false"]
CompetenceGapSize = Literal["high", "medium", "low"]
FinalToolDecision = Literal["allow", "deny"]


@dataclass(slots=True)
class DiffStats:
    files_changed: int
    additions: int
    deletions: int


@dataclass(slots=True)
class ChangeTarget:
    path: str
    language: str | None
    old_content: str | None
    new_content: str


@dataclass(slots=True)
class ChangeProposal:
    proposal_id: str
    session_id: str
    tool_use_id: str
    tool_name: str
    cwd: str
    targets: list[ChangeTarget]
    unified_diff: str
    diff_stats: DiffStats
    created_at: str


@dataclass(slots=True)
class AggregatedContext:
    proposal_id: str
    markdown: str
    qa_context_excerpt: str
    artifact_path: Path


@dataclass(slots=True)
class CompetenceEvidence:
    timestamp: str
    outcome: str
    note: str


@dataclass(slots=True)
class CompetenceEntry:
    score: float
    notes: list[str] = field(default_factory=list)
    evidence: list[CompetenceEvidence] = field(default_factory=list)


@dataclass(slots=True)
class CompetenceModel:
    user_id: str
    updated_at: str
    concepts: dict[str, CompetenceEntry] = field(default_factory=dict)


@dataclass(slots=True)
class RelevantCompetenceEntry:
    concept: str
    score: float
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CompetenceGap:
    size: CompetenceGapSize
    rationale: str


@dataclass(slots=True)
class QAPacket:
    question_type: QuestionType
    prompt_seed: str
    context_excerpt: str


@dataclass(slots=True)
class GateDecision:
    decision: GateTopLevelDecision
    reasoning: str
    confidence: float
    relevant_concepts: list[str] = field(default_factory=list)
    relevant_competence_entries: list[RelevantCompetenceEntry] = field(default_factory=list)
    competence_gap: CompetenceGap | None = None
    qa_packet: QAPacket | None = None


@dataclass(slots=True)
class QAAttempt:
    attempt_number: int
    question: str
    answer: str
    passed: bool
    feedback: str


@dataclass(slots=True)
class QAResult:
    proposal_id: str
    final_decision: FinalToolDecision
    passed: bool
    attempt_count: int
    attempts: list[QAAttempt]
    summary: str
