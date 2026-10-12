"""Semantic indexing, document similarity, detailed comparison and search.

Similarity scores describe how close two texts are in embedding space.
They are *evidence for review*, never proof that two documents are
interchangeable or factually equivalent.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from sqlalchemy.orm import Session

from app.db.models import DocumentChunk, FileRecord, SimilarityPair
from app.services import facts, runtime_settings
from app.services.embeddings import (
    chunk_text,
    from_bytes,
    get_embedder,
    normalize_text,
    split_sentences,
    to_bytes,
)
from app.services.vector_index import VectorIndex

SIMILARITY_STORAGE_FLOOR = 0.5  # pairs below this are not stored at all
SIMILARITY_NEIGHBOURS = 50  # nearest documents examined per document
MAX_SENTENCES = 400


def _indexable(record: FileRecord, min_chars: int) -> bool:
    return (
        record.status == "analyzed"
        and record.category in {"document", "data", "image"}  # images carry text only when OCR found some
        and bool(record.text_content)
        and len(normalize_text(record.text_content or "")) >= min_chars
    )


def index_file(db: Session, record: FileRecord, force: bool = False) -> int:
    """(Re)compute chunk embeddings for one file. Returns number of chunks written."""
    embedder = get_embedder()
    if not force and record.embedded_sha256 == record.sha256 and record.embedding_model == embedder.name and record.chunks:
        return 0
    db.query(DocumentChunk).filter(DocumentChunk.file_id == record.id).delete()
    chunks = chunk_text(record.text_content or "")
    if not chunks:
        record.embedded_sha256 = None
        return 0
    # The filename gives useful context (e.g. "meeting_notes_october") to every chunk.
    title = record.name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
    vectors = embedder.encode([f"{title}\n{c.text}" for c in chunks])
    for chunk, vector in zip(chunks, vectors, strict=True):
        db.add(
            DocumentChunk(
                file_id=record.id,
                chunk_index=chunk.index,
                start_char=chunk.start,
                text=chunk.text,
                embedding=to_bytes(vector),
                embedding_model=embedder.name,
            )
        )
    record.embedded_sha256 = record.sha256
    record.embedding_model = embedder.name
    _search_cache.invalidate()
    return len(chunks)


def index_location(
    db: Session, location_id: int | None = None, force: bool = False, cancel_check: Callable[[], None] | None = None
) -> dict[str, int]:
    min_chars = int(runtime_settings.get(db, "min_document_chars"))
    query = db.query(FileRecord).filter(FileRecord.status != "missing")
    if location_id is not None:
        query = query.filter(FileRecord.location_id == location_id)
    indexed = chunks = skipped = 0
    for i, record in enumerate(query.all()):
        if cancel_check and i % 10 == 0:
            cancel_check()
        if not _indexable(record, min_chars):
            if record.chunks:
                db.query(DocumentChunk).filter(DocumentChunk.file_id == record.id).delete()
                record.embedded_sha256 = None
            skipped += 1
            continue
        written = index_file(db, record, force=force)
        if written:
            indexed += 1
            chunks += written
        db.commit()
    db.commit()
    _search_cache.invalidate()
    return {"indexed_files": indexed, "chunks": chunks, "not_indexable": skipped}


# --- Similarity ------------------------------------------------------------------------------


def _doc_matrix(db: Session, model: str) -> tuple[list[int], np.ndarray, dict[int, np.ndarray]]:
    rows = (
        db.query(DocumentChunk.file_id, DocumentChunk.embedding)
        .join(FileRecord, FileRecord.id == DocumentChunk.file_id)
        .filter(DocumentChunk.embedding_model == model, FileRecord.status == "analyzed")
        .order_by(DocumentChunk.file_id, DocumentChunk.chunk_index)
        .all()
    )
    per_file: dict[int, list[np.ndarray]] = {}
    for file_id, blob in rows:
        per_file.setdefault(file_id, []).append(from_bytes(blob))
    ids = sorted(per_file)
    chunk_mats = {fid: np.vstack(per_file[fid]) for fid in ids}
    if not ids:
        return [], np.zeros((0, 0), dtype=np.float32), {}
    doc_vecs = []
    for fid in ids:
        mean = chunk_mats[fid].mean(axis=0)
        norm = np.linalg.norm(mean)
        doc_vecs.append(mean / norm if norm else mean)
    return ids, np.vstack(doc_vecs), chunk_mats


def recompute_similarity(db: Session) -> int:
    """Recompute all document pairs above the storage floor. Returns number of pairs stored."""
    model = get_embedder().name
    chunk_threshold = float(runtime_settings.get(db, "chunk_match_threshold"))
    ids, doc_mat, chunk_mats = _doc_matrix(db, model)
    db.query(SimilarityPair).delete()
    if len(ids) < 2:
        db.commit()
        return 0
    hashes = dict(db.query(FileRecord.id, FileRecord.sha256).filter(FileRecord.id.in_(ids)).all())
    # k-nearest neighbours per document instead of all n^2 pairs (FAISS when available).
    index = VectorIndex(doc_mat)
    scores, neighbours = index.search(doc_mat, min(len(ids), SIMILARITY_NEIGHBOURS + 1))
    candidates: dict[tuple[int, int], float] = {}
    for i, (row_scores, row_idx) in enumerate(zip(scores, neighbours, strict=True)):
        for score, j in zip(row_scores, row_idx, strict=True):
            if j < 0 or j == i or score < SIMILARITY_STORAGE_FLOOR:
                continue
            candidates[(min(i, int(j)), max(i, int(j)))] = float(score)
    stored = 0
    for (i, j), score in sorted(candidates.items()):
        a, b = ids[i], ids[j]
        if hashes.get(a) and hashes.get(a) == hashes.get(b):
            continue  # exact duplicates are reported by the duplicate detector, not here
        cross = chunk_mats[a] @ chunk_mats[b].T
        db.add(
            SimilarityPair(
                file_a_id=a,
                file_b_id=b,
                score=round(score, 4),
                coverage_a=round(float((cross.max(axis=1) >= chunk_threshold).mean()), 4),
                coverage_b=round(float((cross.max(axis=0) >= chunk_threshold).mean()), 4),
                embedding_model=model,
            )
        )
        stored += 1
    db.commit()
    return stored


def similarity_groups(pairs: list[SimilarityPair], threshold: float) -> list[set[int]]:
    """Connected components of the 'similar above threshold' graph (union-find)."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in pairs:
        if pair.score >= threshold:
            parent[find(pair.file_a_id)] = find(pair.file_b_id)
    groups: dict[int, set[int]] = {}
    for node in list(parent):
        groups.setdefault(find(node), set()).add(node)
    return [g for g in groups.values() if len(g) > 1]


# --- Detailed comparison ---------------------------------------------------------------------


@dataclass
class SentenceAlignment:
    a_sentences: list[str]
    b_sentences: list[str]
    sims: np.ndarray  # shape (len(a), len(b))


def align_sentences(text_a: str, text_b: str) -> SentenceAlignment:
    a = split_sentences(text_a)[:MAX_SENTENCES]
    b = split_sentences(text_b)[:MAX_SENTENCES]
    if not a or not b:
        return SentenceAlignment(a, b, np.zeros((len(a), len(b)), dtype=np.float32))
    embedder = get_embedder()
    vectors = embedder.encode(a + b)
    return SentenceAlignment(a, b, vectors[: len(a)] @ vectors[len(a) :].T)


def detect_conflicts(text_a: str, text_b: str, alignment: SentenceAlignment, related_floor: float = 0.6):
    """Find potentially contradictory facts between two texts.

    Two deterministic signals are used:
    1. Key/value lines with the same normalised key but different values (e.g. ``Budget: $50,000``).
    2. Closely related sentences that mention different figures/dates.
    These are *potential* conflicts for a human to review.
    """
    conflicts: list[dict] = []
    seen_keys: set[str] = set()
    facts_a = {f.key: f for f in facts.extract_kv_facts(text_a)}
    for fact_b in facts.extract_kv_facts(text_b):
        fact_a = facts_a.get(fact_b.key)
        if fact_a and fact_a.norm_value != fact_b.norm_value:
            seen_keys.add(fact_b.key)
            conflicts.append(
                {
                    "kind": "field_value",
                    "field": fact_a.label,
                    "value_a": fact_a.value,
                    "value_b": fact_b.value,
                    "excerpt_a": fact_a.line,
                    "excerpt_b": fact_b.line,
                }
            )
    for i, sent_a in enumerate(alignment.a_sentences):
        if alignment.sims.shape[1] == 0:
            break
        j = int(alignment.sims[i].argmax())
        score = float(alignment.sims[i, j])
        if score < related_floor:
            continue
        sent_b = alignment.b_sentences[j]
        if any(k and k in facts.normalize_key(sent_a) for k in seen_keys):
            continue
        nums_a, nums_b = facts.numeric_tokens(sent_a), facts.numeric_tokens(sent_b)
        if nums_a and nums_b and nums_a != nums_b and facts.normalize_value(sent_a) != facts.normalize_value(sent_b):
            conflicts.append(
                {
                    "kind": "differing_figures",
                    "field": None,
                    "value_a": ", ".join(sorted(nums_a - nums_b)) or ", ".join(sorted(nums_a)),
                    "value_b": ", ".join(sorted(nums_b - nums_a)) or ", ".join(sorted(nums_b)),
                    "excerpt_a": sent_a,
                    "excerpt_b": sent_b,
                    "similarity": round(score, 3),
                }
            )
    return conflicts


def compare_documents(db: Session, file_a: FileRecord, file_b: FileRecord) -> dict:
    settings = runtime_settings.get_all(db)
    threshold = float(settings["sentence_match_threshold"])
    text_a, text_b = file_a.text_content or "", file_b.text_content or ""
    warnings: list[str] = []
    if not text_a.strip() or not text_b.strip():
        warnings.append("At least one document has no extractable text; comparison is limited.")
    alignment = align_sentences(text_a, text_b)
    shared, unique_a, unique_b = [], [], []
    matched_b: set[int] = set()
    for i, sentence in enumerate(alignment.a_sentences):
        if alignment.sims.shape[1]:
            j = int(alignment.sims[i].argmax())
            score = float(alignment.sims[i, j])
        else:
            j, score = -1, 0.0
        exact = j >= 0 and facts.normalize_value(sentence) == facts.normalize_value(alignment.b_sentences[j])
        if j >= 0 and (exact or score >= threshold):
            shared.append({"a": sentence, "b": alignment.b_sentences[j], "score": round(score, 3), "exact": exact})
            matched_b.add(j)
        else:
            unique_a.append({"text": sentence, "best_score": round(score, 3)})
    for j, sentence in enumerate(alignment.b_sentences):
        if j in matched_b:
            continue
        best = float(alignment.sims[:, j].max()) if alignment.sims.shape[0] else 0.0
        if best >= threshold:
            continue  # matched by some A sentence that was itself aligned elsewhere
        unique_b.append({"text": sentence, "best_score": round(best, 3)})

    pair = (
        db.query(SimilarityPair)
        .filter(
            ((SimilarityPair.file_a_id == file_a.id) & (SimilarityPair.file_b_id == file_b.id))
            | ((SimilarityPair.file_a_id == file_b.id) & (SimilarityPair.file_b_id == file_a.id))
        )
        .one_or_none()
    )
    doc_score = pair.score if pair else _direct_doc_score(db, file_a, file_b)
    exact_duplicate = bool(file_a.sha256 and file_a.sha256 == file_b.sha256)
    n_a, n_b = max(len(alignment.a_sentences), 1), max(len(alignment.b_sentences), 1)
    return {
        "file_a_id": file_a.id,
        "file_b_id": file_b.id,
        "document_similarity": doc_score,
        "exact_duplicate": exact_duplicate,
        "embedding_model": get_embedder().name,
        "sentence_threshold": threshold,
        "shared": shared,
        "unique_a": unique_a,
        "unique_b": unique_b,
        "conflicts": detect_conflicts(text_a, text_b, alignment),
        "shared_ratio_a": round(len(shared) / n_a, 3),
        "shared_ratio_b": round(len(matched_b) / n_b, 3),
        "warnings": warnings,
        "disclaimer": (
            "Similarity is measured in embedding space and indicates related wording or topics. "
            "It does not prove the documents are factually equivalent or interchangeable."
        ),
    }


def _direct_doc_score(db: Session, file_a: FileRecord, file_b: FileRecord) -> float | None:
    vecs = []
    for record in (file_a, file_b):
        chunks = db.query(DocumentChunk).filter(DocumentChunk.file_id == record.id).all()
        if not chunks:
            return None
        mean = np.vstack([from_bytes(c.embedding) for c in chunks]).mean(axis=0)
        vecs.append(mean / (np.linalg.norm(mean) or 1.0))
    return round(float(vecs[0] @ vecs[1]), 4)


# --- Search -------------------------------------------------------------------------------------


class _SearchCache:
    """In-memory matrices for search, rebuilt when the chunk table changes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._data: dict | None = None

    def invalidate(self):
        with self._lock:
            self._key, self._data = None, None

    def get(self, db: Session, model: str) -> dict:
        key = (
            model,
            db.query(DocumentChunk).count(),
            db.query(DocumentChunk.id).order_by(DocumentChunk.id.desc()).limit(1).scalar(),
        )
        with self._lock:
            if self._key == key and self._data is not None:
                return self._data
        rows = (
            db.query(DocumentChunk.id, DocumentChunk.file_id, DocumentChunk.embedding, DocumentChunk.text, FileRecord.name)
            .join(FileRecord, FileRecord.id == DocumentChunk.file_id)
            .filter(DocumentChunk.embedding_model == model)
            .all()
        )
        data: dict = {
            "chunk_ids": [r[0] for r in rows],
            "file_ids": [r[1] for r in rows],
            "file_names": {r[1]: r[4] for r in rows},
        }
        if rows:
            from sklearn.feature_extraction.text import TfidfVectorizer

            data["index"] = VectorIndex(np.vstack([from_bytes(r[2]) for r in rows]))
            docs = [f"{r[4].rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ')} {r[3]}" for r in rows]
            vectorizer = TfidfVectorizer(stop_words="english", sublinear_tf=True, token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9]+\b")
            try:
                data["tfidf"] = (vectorizer, vectorizer.fit_transform(docs))
            except ValueError:  # empty vocabulary
                data["tfidf"] = None
        with self._lock:
            self._key, self._data = key, data
        return data


_search_cache = _SearchCache()
SEMANTIC_WEIGHT = 0.55
KEYWORD_WEIGHT = 0.20
TITLE_WEIGHT = 0.25
_QUERY_FILLER = {
    "find",
    "locate",
    "show",
    "files",
    "file",
    "documents",
    "document",
    "related",
    "discussing",
    "about",
    "me",
    "the",
    "all",
}
_TOKEN = re.compile(r"[a-z0-9]+")


def _title_score(query_terms: set[str], filename: str) -> float:
    """Share of the query's content words that appear in the file name."""
    if not query_terms:
        return 0.0
    name_terms = set(_TOKEN.findall(filename.lower()))
    return len(query_terms & name_terms) / len(query_terms)


def search(db: Session, query: str, limit: int = 10, category: str | None = None, location_id: int | None = None):
    """Hybrid search, best chunk per file.

    score = 0.55 x embedding cosine + 0.20 x TF-IDF keyword cosine + 0.25 x file-name term match.
    Each component is returned so results remain explainable.
    """
    embedder = get_embedder()
    data = _search_cache.get(db, embedder.name)
    if not data["chunk_ids"]:
        return []
    semantic_scores = data["index"].scores_all(embedder.encode([query])[0])
    keyword_scores = np.zeros_like(semantic_scores)
    if data.get("tfidf"):
        vectorizer, tfidf = data["tfidf"]
        keyword_scores = (tfidf @ vectorizer.transform([query]).T).toarray().ravel().astype(np.float32)
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    query_terms = {t for t in _TOKEN.findall(query.lower()) if t not in ENGLISH_STOP_WORDS and t not in _QUERY_FILLER}
    title_by_file = {fid: _title_score(query_terms, name) for fid, name in data["file_names"].items()}
    title_scores = np.array([title_by_file[fid] for fid in data["file_ids"]], dtype=np.float32)
    scores = SEMANTIC_WEIGHT * semantic_scores + KEYWORD_WEIGHT * keyword_scores + TITLE_WEIGHT * title_scores
    best_per_file: dict[int, int] = {}
    for idx in np.argsort(-scores):
        fid = data["file_ids"][idx]
        if fid not in best_per_file:
            best_per_file[fid] = int(idx)
    files = {
        f.id: f for f in db.query(FileRecord).filter(FileRecord.id.in_(best_per_file), FileRecord.status == "analyzed").all()
    }
    min_score = 0.12 if embedder.name.startswith("hashing") else 0.18
    results = []
    for fid, idx in sorted(best_per_file.items(), key=lambda kv: -scores[kv[1]]):
        record = files.get(fid)
        if record is None or scores[idx] < min_score:
            continue
        if category and record.category != category:
            continue
        if location_id and record.location_id != location_id:
            continue
        chunk = db.get(DocumentChunk, data["chunk_ids"][idx])
        results.append(
            {
                "file_id": fid,
                "name": record.name,
                "path": record.rel_path,
                "location_id": record.location_id,
                "category": record.category,
                "score": round(float(scores[idx]), 4),
                "semantic_score": round(float(semantic_scores[idx]), 4),
                "keyword_score": round(float(keyword_scores[idx]), 4),
                "title_score": round(float(title_scores[idx]), 4),
                "excerpt": _excerpt(chunk.text, query),
                "chunk_index": chunk.chunk_index,
                "start_char": chunk.start_char,
            }
        )
        if len(results) >= limit:
            break
    return results


def _excerpt(text: str, query: str, width: int = 320) -> str:
    words = [w.lower() for w in query.split() if len(w) > 3]
    lower = text.lower()
    pos = min((lower.find(w) for w in words if lower.find(w) >= 0), default=0)
    start = max(0, pos - width // 3)
    snippet = text[start : start + width].replace("\n", " ")
    return ("…" if start else "") + snippet + ("…" if start + width < len(text) else "")
