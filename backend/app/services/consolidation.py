"""Information-aware document consolidation.

Given several related documents, build one draft that keeps every distinct
statement once, merges repeated statements (with attribution to all sources),
and surfaces conflicting facts instead of silently choosing one.

The pipeline is deterministic (no external LLM):

1. Extract "Key: value" facts and sentence units from each source.
2. Merge facts with the same normalised key; differing values become conflicts.
3. Cluster near-identical sentences by embedding similarity.  A sentence is
   only treated as redundant when all of its salient tokens (figures, dates,
   names, e-mails) are already present in the kept sentence; otherwise both
   are kept, and differing figures are flagged as potential conflicts.
4. Render Markdown with source tags, then run a token-level preservation
   check of every source's salient tokens against the output.

The result is a *draft for human review*. It is an information-management
operation, not lossless compression, and the originals are never modified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Consolidation, FileRecord
from app.services import facts, runtime_settings
from app.services.embeddings import get_embedder, split_sentences

MIN_WORDS = 3


@dataclass
class Unit:
    text: str
    source: int  # 1-based source index
    tokens: set[str]
    numbers: set[str]


@dataclass
class Cluster:
    rep: Unit
    sources: set[int] = field(default_factory=set)
    variants: list[Unit] = field(default_factory=list)
    merged: int = 0


def _units_for(text: str, source: int, kv_lines: set[str]) -> list[Unit]:
    units = []
    for sentence in split_sentences(text):
        if sentence.strip() in kv_lines:
            continue
        tokens = facts.salient_tokens(sentence)
        if len(sentence.split()) < MIN_WORDS and not tokens:
            continue
        units.append(Unit(sentence, source, tokens, facts.numeric_tokens(sentence)))
    return units


def build_consolidation(db: Session, records: list[FileRecord], title: str) -> dict:
    if len(records) < 2:
        raise ValueError("Select at least two documents to consolidate.")
    missing_text = [r.name for r in records if not (r.text_content or "").strip()]
    if missing_text:
        raise ValueError(f"No extractable text in: {', '.join(missing_text)}")
    threshold = max(float(runtime_settings.get(db, "sentence_match_threshold")), 0.85)

    # 1–2. Key/value facts -------------------------------------------------------------------
    fact_groups: dict[str, dict] = {}
    kv_lines_by_source: dict[int, set[str]] = {}
    for idx, record in enumerate(records, start=1):
        kv = facts.extract_kv_facts(record.text_content or "")
        kv_lines_by_source[idx] = {f.line.strip(" -*\u2022#>\t") for f in kv}
        for fact in kv:
            group = fact_groups.setdefault(fact.key, {"label": fact.label, "values": {}})
            entry = group["values"].setdefault(fact.norm_value, {"value": fact.value, "sources": set()})
            entry["sources"].add(idx)

    # 3. Sentence clustering ---------------------------------------------------------------
    units: list[Unit] = []
    for idx, record in enumerate(records, start=1):
        units.extend(_units_for(record.text_content or "", idx, kv_lines_by_source[idx]))
    vectors = get_embedder().encode([u.text for u in units]) if units else None
    clusters: list[Cluster] = []
    rep_vectors: list = []
    sentence_conflicts: list[dict] = []
    for i, unit in enumerate(units):
        best, best_score = None, -1.0
        norm = facts.normalize_value(unit.text)
        for c_idx, cluster in enumerate(clusters):
            if facts.normalize_value(cluster.rep.text) == norm:
                best, best_score = c_idx, 1.0
                break
            score = float(rep_vectors[c_idx] @ vectors[i])
            if score > best_score:
                best, best_score = c_idx, score
        if best is None or best_score < threshold:
            clusters.append(Cluster(rep=unit, sources={unit.source}))
            rep_vectors.append(vectors[i])
            continue
        cluster = clusters[best]
        rep = cluster.rep
        if unit.tokens <= rep.tokens and unit.numbers <= rep.numbers:
            cluster.sources.add(unit.source)  # fully covered: merge
            cluster.merged += 1
        elif rep.tokens <= unit.tokens and rep.numbers <= unit.numbers:
            cluster.sources.add(unit.source)  # new unit is a superset: it becomes the representative
            cluster.rep = unit
            rep_vectors[best] = vectors[i]
            cluster.merged += 1
        else:
            cluster.variants.append(unit)  # carries different details: keep both
            if unit.numbers and rep.numbers and unit.numbers != rep.numbers:
                sentence_conflicts.append(
                    {
                        "kind": "differing_figures",
                        "statements": [
                            {"text": rep.text, "sources": sorted(cluster.sources)},
                            {"text": unit.text, "sources": [unit.source]},
                        ],
                    }
                )

    # 4. Render ---------------------------------------------------------------------------
    consensus = []
    field_conflicts = []
    for key, group in fact_groups.items():
        values = list(group["values"].values())
        if len(values) == 1:
            consensus.append((group["label"], values[0]["value"], sorted(values[0]["sources"])))
        else:
            field_conflicts.append(
                {
                    "kind": "field_value",
                    "field": group["label"],
                    "key": key,
                    "values": [{"value": v["value"], "sources": sorted(v["sources"])} for v in values],
                }
            )

    def tag(sources) -> str:
        return " ".join(f"[S{s}]" for s in sorted(sources))

    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# {title}",
        "",
        f"> Consolidated draft generated by OptiStore AI on {generated} from {len(records)} source documents.",
        "> This is a draft for human review. Similar wording does not prove factual equivalence;"
        " conflicting facts are listed below rather than resolved. Original files are unchanged.",
        "",
        "## Sources",
        "",
    ]
    for idx, record in enumerate(records, start=1):
        lines.append(f"- [S{idx}] `{record.rel_path}` ({record.size_bytes} bytes, sha256 {(record.sha256 or '')[:12]}…)")
    if consensus:
        lines += ["", "## Key facts", "", "| Field | Value | Sources |", "| --- | --- | --- |"]
        for label, value, sources in consensus:
            lines.append(f"| {_cell(label)} | {_cell(value)} | {tag(sources)} |")
    if field_conflicts or sentence_conflicts:
        lines += ["", "## Conflicts requiring review", ""]
        for conflict in field_conflicts:
            options = " vs ".join(f'"{v["value"]}" {tag(v["sources"])}' for v in conflict["values"])
            lines.append(f"- **{conflict['field']}**: {options}")
        for conflict in sentence_conflicts:
            a, b = conflict["statements"]
            lines.append(f'- Differing figures: "{a["text"]}" {tag(a["sources"])} vs "{b["text"]}" {tag(b["sources"])}')
    lines += ["", "## Content", ""]
    for cluster in clusters:
        lines.append(f"- {cluster.rep.text} {tag(cluster.sources)}")
        for variant in cluster.variants:
            lines.append(f"  - Variant: {variant.text} {tag([variant.source])}")
    markdown = "\n".join(lines) + "\n"

    report = {
        "sources": [{"index": i, "file_id": r.id, "path": r.rel_path} for i, r in enumerate(records, start=1)],
        "units_total": len(units),
        "units_kept": len(clusters) + sum(len(c.variants) for c in clusters),
        "units_merged": sum(c.merged for c in clusters),
        "facts_consensus": len(consensus),
        "conflicts": field_conflicts + sentence_conflicts,
        "conflict_count": len(field_conflicts) + len(sentence_conflicts),
        "similarity_threshold": threshold,
        "embedding_model": get_embedder().name,
        "preservation": preservation_check(records, markdown),
    }
    return {"markdown": markdown, "report": report}


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def preservation_check(records: list[FileRecord], output: str) -> dict:
    """Token-level check: which salient tokens of the sources are absent from the output?

    This validates figures, dates, e-mails and capitalised names only; it cannot
    prove that every nuance of meaning was preserved.
    """
    haystack = facts.normalised_for_search(output)
    missing: dict[str, list[str]] = {}
    checked = 0
    for idx, record in enumerate(records, start=1):
        tokens = facts.salient_tokens(record.text_content or "")
        checked += len(tokens)
        absent = sorted(t for t in tokens if not facts.token_present(facts.normalised_for_search(t), haystack))
        if absent:
            missing[f"S{idx}"] = absent[:50]
    total_missing = sum(len(v) for v in missing.values())
    return {
        "method": "salient-token presence (figures, dates, e-mails, capitalised names)",
        "tokens_checked": checked,
        "tokens_missing": total_missing,
        "missing_by_source": missing,
        "all_detected_tokens_present": total_missing == 0,
        "note": "A passing check means no detected figure/date/name was dropped; it is not proof that all meaning was preserved.",
    }


def create(db: Session, file_ids: list[int], title: str) -> Consolidation:
    records = db.query(FileRecord).filter(FileRecord.id.in_(file_ids)).all()
    by_id = {r.id: r for r in records}
    missing = [fid for fid in file_ids if fid not in by_id]
    if missing:
        raise LookupError(f"Unknown file ids: {missing}")
    ordered = [by_id[fid] for fid in dict.fromkeys(file_ids)]
    built = build_consolidation(db, ordered, title)
    consolidation = Consolidation(
        title=title,
        source_file_ids=[r.id for r in ordered],
        draft_markdown=built["markdown"],
        report=built["report"],
        original_bytes=sum(r.size_bytes for r in ordered),
        output_bytes=len(built["markdown"].encode("utf-8")),
    )
    db.add(consolidation)
    db.commit()
    return consolidation


def save_edit(db: Session, consolidation: Consolidation, markdown: str) -> Consolidation:
    records = db.query(FileRecord).filter(FileRecord.id.in_(consolidation.source_file_ids)).all()
    consolidation.edited_markdown = markdown
    consolidation.output_bytes = len(markdown.encode("utf-8"))
    consolidation.report = {**consolidation.report, "preservation": preservation_check(records, markdown)}
    consolidation.status = "reviewed"
    db.commit()
    return consolidation


def _to_plain_text(markdown: str) -> str:
    text = re.sub(r"^#+\s*", "", markdown, flags=re.M)
    text = re.sub(r"^>\s?", "", text, flags=re.M)
    text = text.replace("**", "").replace("`", "")
    return text


def export(db: Session, consolidation: Consolidation, fmt: str) -> Path:
    if fmt not in {"md", "txt"}:
        raise ValueError("Export format must be 'md' or 'txt'.")
    content = consolidation.edited_markdown or consolidation.draft_markdown
    if fmt == "txt":
        content = _to_plain_text(content)
    out_dir = get_settings().outputs_dir / "consolidations"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", consolidation.title.lower()).strip("-")[:60] or "consolidation"
    path = out_dir / f"{consolidation.id}-{slug}.{fmt}"
    path.write_text(content, encoding="utf-8")
    consolidation.export_path = str(path)
    consolidation.export_format = fmt
    consolidation.output_bytes = path.stat().st_size  # measured size of the actual exported file
    consolidation.status = "exported"
    db.commit()
    return path
