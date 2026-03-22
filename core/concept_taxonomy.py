"""Load and parse the default concept taxonomy from YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

import yaml

_DEFAULT_TAXONOMY_RESOURCE = files("core.resources").joinpath("default_concept_graph.yaml")


@dataclass(slots=True)
class ConceptDefinition:
    name: str
    category: str
    prerequisites: list[str] = field(default_factory=list)


def load_taxonomy(path: Path | None = None) -> list[ConceptDefinition]:
    """Load the concept taxonomy from YAML, returning ordered concept definitions."""
    raw_text = (
        path.read_text(encoding="utf-8")
        if path is not None
        else _DEFAULT_TAXONOMY_RESOURCE.read_text(encoding="utf-8")
    )
    raw = yaml.safe_load(raw_text)
    concepts: list[ConceptDefinition] = []
    for entry in raw.get("concepts", []):
        concepts.append(
            ConceptDefinition(
                name=str(entry["name"]),
                category=str(entry.get("category", "general")),
                prerequisites=[str(p) for p in entry.get("prerequisites", [])],
            )
        )
    return concepts
