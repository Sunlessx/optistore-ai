"""Programmatic Alembic upgrade used at application start-up."""

from __future__ import annotations

import logging

from alembic import command
from alembic.config import Config

from app.core.config import BACKEND_DIR, get_settings

logger = logging.getLogger(__name__)


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    return cfg


def upgrade_database() -> None:
    logger.info("Applying database migrations")
    command.upgrade(alembic_config(), "head")
