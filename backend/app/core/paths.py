"""Safe path handling.

All filesystem access performed on behalf of the user goes through these
helpers, which guarantee that resolved paths stay inside an explicitly
authorised root directory (no ``..`` traversal, no symlink escapes).
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings


class PathNotAllowedError(ValueError):
    """Raised when a path is outside every authorised root."""


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_scan_root(raw_path: str) -> Path:
    """Return the resolved directory if it lies inside an allowed scan root."""
    if not raw_path or "\x00" in raw_path:
        raise PathNotAllowedError("Path is empty or contains invalid characters.")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise PathNotAllowedError("Path must be absolute.")
    resolved = path.resolve()
    roots = get_settings().resolved_scan_roots()
    if not any(_is_within(resolved, root) for root in roots):
        allowed = ", ".join(str(r) for r in roots)
        raise PathNotAllowedError(
            f"'{resolved}' is outside the authorised scan roots ({allowed}). Add it to ALLOWED_SCAN_ROOTS to authorise it."
        )
    if not resolved.is_dir():
        raise PathNotAllowedError(f"'{resolved}' is not an existing directory.")
    return resolved


def safe_join(root: Path, rel_path: str) -> Path:
    """Join ``rel_path`` to ``root`` and refuse anything that escapes ``root``."""
    if "\x00" in rel_path:
        raise PathNotAllowedError("Invalid path.")
    root = root.resolve()
    candidate = (root / rel_path).resolve()
    if not _is_within(candidate, root):
        raise PathNotAllowedError("Path escapes the authorised location.")
    return candidate


def ensure_within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not _is_within(resolved, root.resolve()):
        raise PathNotAllowedError("Path escapes the authorised location.")
    return resolved
