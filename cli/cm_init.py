"""``vibecheck cm init`` — initialize competence model in the terminal."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import CompetenceModel

STATE_DIR = Path("state")


def run_cm_init(*, preset: str | None = None) -> None:
    output_path = STATE_DIR / "competence_model.yaml"

    if preset:
        model = _preset_model(output_path, preset)
    else:
        if output_path.exists():
            answer = input(
                f"Competence model already exists at {output_path}. Overwrite? [y/N] "
            ).strip()
            if answer.lower() != "y":
                print("Aborted.")
                sys.exit(0)

        model = _terminal_survey(output_path)

    if model is None:
        print("Survey timed out. No changes made.", file=sys.stderr)
        sys.exit(1)

    print(f"Competence model saved to {output_path}")
    print(f"Concepts initialized: {len(model.concepts)}")
    for name, entry in model.concepts.items():
        label = name.replace("_", " ").title()
        print(f"  {label}: {entry.score:.1f}")


def _preset_model(output_path: Path, preset: str) -> CompetenceModel:
    """Build a competence model with all concepts at max or min scores."""
    from datetime import UTC, datetime

    from core.competence_store import save_competence_model
    from core.concept_taxonomy import load_taxonomy
    from core.models import CompetenceEntry, CompetenceEvidence, CompetenceModel

    score = 0.9 if preset == "max" else 0.1
    label = "maximum" if preset == "max" else "minimum"
    concepts = load_taxonomy()
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    entries: dict[str, CompetenceEntry] = {}
    for concept in concepts:
        entries[concept.name] = CompetenceEntry(
            score=score,
            notes=[],
            evidence=[
                CompetenceEvidence(
                    timestamp=now,
                    outcome="self_assessment",
                    note=f"Preset {label} ({score})",
                )
            ],
        )

    model = CompetenceModel(user_id="local_default", updated_at=now, concepts=entries)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_competence_model(model, output_path)
    return model


def _terminal_survey(output_path: Path) -> CompetenceModel | None:
    from datetime import UTC, datetime

    from core.competence_store import save_competence_model
    from core.concept_taxonomy import load_taxonomy
    from core.models import CompetenceEntry, CompetenceEvidence, CompetenceModel

    score_map = {1: 0.1, 2: 0.3, 3: 0.5, 4: 0.7, 5: 0.9}
    concepts = load_taxonomy()
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    print("\nRate your confidence with each concept (1-5):")
    print("  1 = No experience  |  5 = Very confident\n")

    entries: dict[str, CompetenceEntry] = {}
    for concept in concepts:
        label = concept.name.replace("_", " ").title()
        while True:
            raw = input(f"  {label}: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= 5:
                rating = int(raw)
                break
            print("    Please enter a number 1-5.")

        entries[concept.name] = CompetenceEntry(
            score=score_map.get(rating, 0.5),
            notes=[],
            evidence=[
                CompetenceEvidence(
                    timestamp=now,
                    outcome="self_assessment",
                    note=f"User rated {rating}/5",
                )
            ],
        )

    model = CompetenceModel(user_id="local_default", updated_at=now, concepts=entries)
    save_competence_model(model, output_path)
    return model
