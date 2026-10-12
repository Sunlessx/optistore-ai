"""Vector index abstraction: FAISS when available, exact NumPy otherwise.

Both backends perform *exact* inner-product search on L2-normalised vectors
(i.e. cosine similarity), so results are identical; FAISS is simply faster
on large collections (SIMD, multi-threading) and keeps memory compact.
"""

from __future__ import annotations

import logging

import numpy as np

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _faiss():
    backend = get_settings().vector_backend
    if backend == "numpy":
        return None
    try:
        import faiss

        return faiss
    except ImportError:
        if backend == "faiss":
            raise
        return None


def active_backend() -> str:
    return "faiss" if _faiss() is not None else "numpy"


class VectorIndex:
    def __init__(self, vectors: np.ndarray):
        self.vectors = np.ascontiguousarray(vectors, dtype=np.float32)
        self.size = len(self.vectors)
        faiss = _faiss()
        self._faiss_index = None
        if faiss is not None and self.size:
            index = faiss.IndexFlatIP(self.vectors.shape[1])
            index.add(self.vectors)
            self._faiss_index = index
        self.backend = "faiss" if self._faiss_index is not None else "numpy"

    def search(self, queries: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        """Top-k (scores, indices) per query row, best first. Indices are -1 when fewer than k vectors exist."""
        queries = np.ascontiguousarray(np.atleast_2d(queries), dtype=np.float32)
        k = max(1, min(k, self.size)) if self.size else 1
        if self.size == 0:
            return np.zeros((len(queries), 0), np.float32), np.zeros((len(queries), 0), np.int64)
        if self._faiss_index is not None:
            return self._faiss_index.search(queries, k)
        scores = queries @ self.vectors.T
        idx = (
            np.argpartition(-scores, k - 1, axis=1)[:, :k] if k < self.size else np.tile(np.arange(self.size), (len(queries), 1))
        )
        part = np.take_along_axis(scores, idx, axis=1)
        order = np.argsort(-part, axis=1)
        return np.take_along_axis(part, order, axis=1), np.take_along_axis(idx, order, axis=1)

    def scores_all(self, query: np.ndarray) -> np.ndarray:
        """Cosine score of one query against every vector (used to combine with keyword scores)."""
        if self.size == 0:
            return np.zeros(0, np.float32)
        if self._faiss_index is not None:
            scores, idx = self._faiss_index.search(np.ascontiguousarray(query[None, :], dtype=np.float32), self.size)
            out = np.empty(self.size, np.float32)
            out[idx[0]] = scores[0]
            return out
        return self.vectors @ query.astype(np.float32)
