from __future__ import annotations

import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from core.models import CompetenceEntry, CompetenceEvidence, CompetenceModel


def load_competence_model(path: Path) -> CompetenceModel:
    if not path.exists():
        model = default_competence_model()
        save_competence_model(model, path)
        return model

    raw_data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    concepts: dict[str, CompetenceEntry] = {}
    for concept_name, raw_entry in dict(raw_data.get("concepts", {})).items():
        raw_entry = dict(raw_entry)
        evidence = [
            CompetenceEvidence(
                timestamp=str(item.get("timestamp", _utc_now_iso())),
                outcome=str(item.get("outcome", "unknown")),
                note=str(item.get("note", "")),
            )
            for item in raw_entry.get("evidence", [])
        ]
        concepts[str(concept_name)] = CompetenceEntry(
            score=float(raw_entry.get("score", 0.5)),
            notes=[str(note) for note in raw_entry.get("notes", [])],
            evidence=evidence,
        )

    return CompetenceModel(
        user_id=str(raw_data.get("user_id", "local_default")),
        updated_at=str(raw_data.get("updated_at", _utc_now_iso())),
        concepts=concepts,
    )


def save_competence_model(model: CompetenceModel, path: Path) -> None:
    payload = {
        "user_id": model.user_id,
        "updated_at": model.updated_at,
        "concepts": {
            concept: {
                "score": entry.score,
                "notes": entry.notes,
                "evidence": [asdict(item) for item in entry.evidence],
            }
            for concept, entry in model.concepts.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def update_competence_entry(
    model: CompetenceModel,
    *,
    concept: str,
    delta: float,
    note: str,
    outcome: str,
) -> CompetenceModel:
    entry = model.concepts.get(concept)
    if entry is None:
        entry = CompetenceEntry(score=0.5)
        model.concepts[concept] = entry

    entry.score = min(1.0, max(0.0, round(entry.score + delta, 2)))
    if note:
        entry.notes.append(note)
    entry.evidence.append(
        CompetenceEvidence(
            timestamp=_utc_now_iso(),
            outcome=outcome,
            note=note,
        )
    )
    model.updated_at = _utc_now_iso()
    return model


def default_competence_model() -> CompetenceModel:
    return CompetenceModel(
        user_id="local_default",
        updated_at=_utc_now_iso(),
        concepts={
            "python_basics": CompetenceEntry(
                score=0.5,
                notes=["Initial scaffold entry for Python changes."],
                evidence=[],
            )
        },
    )


def retrieve_relevant_events(
    file_path: str,
    proposed_diff: str,
    *,
    db_path: Path,
    embedding_function: Any | None = None,
    n_results: int = 3,
) -> list[dict[str, Any]]:
    """Semantic search over past QA outcomes, filtered to the given file path.

    Returns up to ``n_results`` entries from the ``competence_events``
    collection whose ``file_path`` metadata matches the supplied value.
    The search text is the ``proposed_diff``, so results are ranked by how
    semantically similar past events are to the current change.

    Returns an empty list when the collection is empty or no matching
    events exist — never raises.
    """
    collection = get_chroma_collection(db_path, embedding_function)

    try:
        total = collection.count()
        if total == 0:
            return []

        result = collection.query(
            query_texts=[proposed_diff],
            n_results=min(n_results, total),
            where={"file_path": file_path},
        )
    except Exception:  # noqa: BLE001 — retrieval must not crash the hook
        return []

    docs: list[str] = (result.get("documents") or [[]])[0] or []
    metas: list[dict[str, Any]] = (result.get("metadatas") or [[]])[0] or []
    return [
        {"document": doc, "metadata": meta or {}}
        for doc, meta in zip(docs, metas)
    ]


def get_chroma_collection(
    db_path: Path,
    embedding_function: Any | None = None,
) -> Any:
    """Return a persistent ChromaDB collection for competence events.

    Uses OpenAI text-embedding-3-small by default.  Pass a custom
    ``embedding_function`` (any chromadb-compatible callable) to override —
    useful in tests to avoid hitting the OpenAI API.
    """
    import chromadb
    from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

    db_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(db_path))

    if embedding_function is None:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        embedding_function = OpenAIEmbeddingFunction(
            api_key=api_key,
            model_name="text-embedding-3-small",
        )

    return client.get_or_create_collection(
        name="competence_events",
        embedding_function=embedding_function,
    )


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
