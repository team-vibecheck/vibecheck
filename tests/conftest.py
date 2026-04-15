from __future__ import annotations

import chromadb
import pytest
from chromadb import EmbeddingFunction
from chromadb.api.types import Documents, Embeddings


class _FakeEmbeddingFunction(EmbeddingFunction[Documents]):
    """Minimal embedding function stub for tests that avoids any network calls.

    Must subclass EmbeddingFunction so chromadb 1.5.x can call embed_query()
    on it during collection.query() — the base class provides that method as
    a delegate to __call__.
    """

    def __init__(self) -> None:
        pass  # suppress DeprecationWarning from base __init__

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
