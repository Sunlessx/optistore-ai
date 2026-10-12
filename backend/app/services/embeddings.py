"""Text normalisation, chunking and embedding backends.

Two backends are available:

* ``sentence-transformers`` – a pretrained transformer (default
  ``all-MiniLM-L6-v2``, 384 dims, CPU friendly). Loaded lazily, once per process.
* ``hashing`` – a deterministic, dependency-light lexical fallback
  (scikit-learn ``HashingVectorizer``). It captures word overlap, *not* meaning,
  and is used for fast tests or when the transformer model cannot be loaded.

The active backend is reported by ``/api/health`` so the UI can tell users
which kind of similarity they are looking at.
"""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass

import numpy as np

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_WS = re.compile(r"[ \t\f\v]+")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n{1,}")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sentences(text: str) -> list[str]:
    """Split text into sentence-like units (lines and sentences)."""
    units = []
    for part in _SENT_SPLIT.split(normalize_text(text)):
        part = part.strip(" -*#>\t")
        if len(part) >= 3:
            units.append(part)
    return units


@dataclass
class Chunk:
    index: int
    start: int
    text: str


def chunk_text(text: str, size: int | None = None, overlap: int | None = None) -> list[Chunk]:
    """Split long text into overlapping chunks, preferring paragraph/sentence boundaries."""
    settings = get_settings()
    size = size or settings.chunk_size_chars
    overlap = settings.chunk_overlap_chars if overlap is None else overlap
    text = normalize_text(text)
    if not text:
        return []
    if len(text) <= size:
        return [Chunk(0, 0, text)]
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
            if cut > size * 0.5:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(Chunk(len(chunks), start, piece))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


class Embedder:
    name: str
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError


class HashingEmbedder(Embedder):
    """Lexical fallback: L2-normalised hashed word uni/bi-gram counts."""

    def __init__(self, dim: int = 2048):
        from sklearn.feature_extraction.text import HashingVectorizer

        self.dim = dim
        self.name = f"hashing-{dim}"
        self._vec = HashingVectorizer(
            n_features=dim,
            ngram_range=(1, 2),
            alternate_sign=False,
            norm="l2",
            lowercase=True,
            token_pattern=r"(?u)\b\w+\b",
        )

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return self._vec.transform(texts).toarray().astype(np.float32)


class SentenceTransformerEmbedder(Embedder):
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name, device="cpu")
        self.name = model_name
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vectors = self._model.encode(
            texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True
        )
        return vectors.astype(np.float32)


_lock = threading.Lock()
_embedder: Embedder | None = None
_status: dict[str, str | None] = {"backend": None, "model": None, "error": None}


def get_embedder() -> Embedder:
    """Return the process-wide embedder, loading it lazily on first use."""
    global _embedder
    if _embedder is not None:
        return _embedder
    with _lock:
        if _embedder is not None:
            return _embedder
        settings = get_settings()
        if settings.embedding_backend == "sentence-transformers":
            try:
                _embedder = SentenceTransformerEmbedder(settings.embedding_model)
                _status.update(backend="sentence-transformers", model=_embedder.name, error=None)
                return _embedder
            except Exception as exc:  # noqa: BLE001 - any load failure triggers the fallback
                logger.warning("Could not load embedding model %s: %s", settings.embedding_model, exc)
                _status["error"] = f"Model load failed ({exc.__class__.__name__}); using lexical fallback."
        _embedder = HashingEmbedder()
        _status.update(backend="hashing", model=_embedder.name)
        return _embedder


def embedder_status() -> dict[str, str | None]:
    loaded = _embedder is not None
    settings = get_settings()
    return {
        "configured_backend": settings.embedding_backend,
        "configured_model": settings.embedding_model,
        "loaded": str(loaded).lower(),
        "active_backend": _status["backend"],
        "active_model": _status["model"],
        "error": _status["error"],
        "semantic": "true" if _status["backend"] == "sentence-transformers" else ("false" if loaded else None),
    }


def reset_embedder() -> None:
    """Testing hook."""
    global _embedder
    with _lock:
        _embedder = None
        _status.update(backend=None, model=None, error=None)


def to_bytes(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype="<f4").tobytes()


def from_bytes(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")
