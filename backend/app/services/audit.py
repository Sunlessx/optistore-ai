"""Append-only operation log helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import OperationLog


def log_operation(
    db: Session,
    operation: str,
    status: str,
    target: str | None = None,
    details: dict[str, Any] | None = None,
    dry_run: bool = False,
    actor: str = "local-user",
    commit: bool = True,
) -> OperationLog:
    entry = OperationLog(
        operation=operation,
        status=status,
        target=target,
        details=details or {},
        dry_run=dry_run,
        actor=actor,
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry
