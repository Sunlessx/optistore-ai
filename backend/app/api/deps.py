from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db  # noqa: F401 - re-exported for routers


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Enforce the optional shared API key (enabled when API_KEY is set)."""
    expected = get_settings().api_key
    if expected and not (x_api_key and secrets.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid X-API-Key header.")


def get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {obj_id} not found.")
    return obj
