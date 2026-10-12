"""Exact duplicate detection based on SHA-256 content hashes.

Two files are *exact duplicates* only when their SHA-256 digests (and sizes)
match. This is deliberately kept separate from semantic similarity.
Duplicates are reported, never deleted automatically.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.db.models import DuplicateGroup, FileRecord


def recompute_duplicate_groups(db: Session) -> list[DuplicateGroup]:
    files = (
        db.query(FileRecord)
        .filter(FileRecord.sha256.is_not(None), FileRecord.status != "missing", FileRecord.size_bytes > 0)
        .all()
    )
    by_hash: dict[tuple[str, int], list[FileRecord]] = defaultdict(list)
    for record in files:
        by_hash[(record.sha256, record.size_bytes)].append(record)

    db.query(FileRecord).update({FileRecord.duplicate_group_id: None})
    db.query(DuplicateGroup).delete()
    db.flush()

    groups: list[DuplicateGroup] = []
    for (digest, size), members in by_hash.items():
        if len(members) < 2:
            continue
        group = DuplicateGroup(
            sha256=digest,
            size_bytes=size,
            file_count=len(members),
            redundant_bytes=size * (len(members) - 1),
        )
        db.add(group)
        db.flush()
        for record in members:
            record.duplicate_group_id = group.id
        groups.append(group)
    db.commit()
    return groups


def duplicate_summary(db: Session) -> dict[str, int]:
    groups = db.query(DuplicateGroup).all()
    return {
        "groups": len(groups),
        "duplicate_files": sum(g.file_count for g in groups),
        "redundant_copies": sum(g.file_count - 1 for g in groups),
        "redundant_bytes": sum(g.redundant_bytes for g in groups),
    }


def keeper_for_group(files: list[FileRecord]) -> FileRecord:
    """Suggest which copy to keep: the oldest first-seen, then shortest path (a heuristic, not a rule)."""
    return sorted(files, key=lambda f: (f.first_seen_at, len(f.rel_path), f.rel_path))[0]
