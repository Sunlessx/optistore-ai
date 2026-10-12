from datetime import UTC, datetime


def as_utc(dt: datetime | None) -> datetime | None:
    """Normalise datetimes (SQLite returns naive values) to aware UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def now_utc() -> datetime:
    return datetime.now(UTC)
