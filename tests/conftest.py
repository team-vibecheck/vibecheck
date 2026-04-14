from __future__ import annotations

import chromadb
import pytest


class _FakeEmbeddingFunction:
    """Minimal embedding function stub for tests that avoids any network calls."""

    def __call__(self, input: list[str]) -> list[list[float]]:
        return [[0.0] * 8 for _ in input]

    def name(self) -> str:
        return "fake"

    def is_legacy(self) -> bool:
        return False


@pytest.fixture(autouse=True)
def patch_chroma(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch get_chroma_collection for all tests.

    Tests that inject their own chroma_collection into QALoop never call
    get_chroma_collection at all, so this patch is harmlessly applied but
    not invoked for those tests.  Tests that don't inject a collection (all
    the pre-existing qa_loop tests) use this stub instead of hitting OpenAI.
    """

    def _fake_get_chroma_collection(db_path: object, embedding_function: object = None) -> chromadb.Collection:
        del db_path, embedding_function
        client = chromadb.EphemeralClient()
        return client.get_or_create_collection(
            "competence_events",
            embedding_function=_FakeEmbeddingFunction(),
        )

    monkeypatch.setattr("core.competence_store.get_chroma_collection", _fake_get_chroma_collection)
