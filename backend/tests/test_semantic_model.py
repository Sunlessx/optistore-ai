"""Checks with the real Sentence-Transformers model (skipped when it is not available offline)."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def model():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        from app.services.embeddings import SentenceTransformerEmbedder

        return SentenceTransformerEmbedder("sentence-transformers/all-MiniLM-L6-v2")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"embedding model unavailable: {exc}")


def test_paraphrases_closer_than_unrelated(model):
    vecs = model.encode(
        [
            "The project budget is fifty thousand dollars.",
            "We have $50,000 allocated to the project.",
            "Mash the bananas and bake the bread for an hour.",
        ]
    )
    assert float(vecs[0] @ vecs[1]) > float(vecs[0] @ vecs[2]) + 0.3


def test_semantic_match_without_shared_words(model):
    vecs = model.encode(["customer complaints about late deliveries", "clients unhappy that orders arrived late"])
    assert float(vecs[0] @ vecs[1]) > 0.5
