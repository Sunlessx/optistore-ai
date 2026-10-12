"""Application configuration loaded from environment variables / .env.

Every setting can be overridden with an environment variable of the same
name (case-insensitive), e.g. ``DATABASE_URL`` or ``ALLOWED_SCAN_ROOTS``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OptiStore AI"
    environment: str = "development"

    # --- Persistence -------------------------------------------------------
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'optistore.db').as_posix()}"
    data_dir: Path = BACKEND_DIR / "data"

    # --- Security ----------------------------------------------------------
    # Directories that may be registered as scan locations. Anything outside
    # these roots is rejected. Separate multiple roots with ";" or ",".
    allowed_scan_roots: Annotated[list[Path], NoDecode] = Field(default_factory=lambda: [PROJECT_DIR / "sample_data"])
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    # Optional shared secret. When set, every /api request (except /api/health)
    # must send the header ``X-API-Key``.
    api_key: str | None = None

    # --- Scanning limits ---------------------------------------------------
    max_file_size_mb: int = 512
    max_text_extract_mb: int = 25
    max_text_chars: int = 400_000
    max_files_per_scan: int = 50_000

    # --- AI ----------------------------------------------------------------
    # "sentence-transformers" (default) or "hashing" (lexical fallback, no model download).
    embedding_backend: str = "sentence-transformers"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chunk_size_chars: int = 900
    chunk_overlap_chars: int = 150
    # "auto" uses FAISS when installed, otherwise exact NumPy search; "faiss" / "numpy" force one.
    vector_backend: str = "auto"

    # --- OCR (Tesseract) and video (ffmpeg) -----------------------------------
    # Both are optional system tools. When missing, the features report themselves unavailable.
    ocr_enabled: bool = True
    tesseract_cmd: str | None = None
    ocr_languages: str = "eng"
    ocr_max_pages: int = 20
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    video_transcode_max_mb: int = 500
    video_transcode_timeout_s: int = 600

    # --- AWS ---------------------------------------------------------------
    aws_profile: str | None = None
    aws_region: str = "us-east-1"
    # Buckets that may be analysed. Empty list = any bucket the credentials can list.
    allowed_s3_buckets: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Cloud write operations (storage-class transitions) are disabled unless this is true.
    s3_write_enabled: bool = False
    s3_max_download_mb: int = 10
    pricing_file: Path | None = None

    @field_validator("allowed_scan_roots", "cors_origins", "allowed_s3_buckets", mode="before")
    @classmethod
    def _split_list(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                import json

                return json.loads(value)
            parts = value.replace(";", ",").split(",")
            return [p.strip() for p in parts if p.strip()]
        return value

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def max_text_extract_bytes(self) -> int:
        return self.max_text_extract_mb * 1024 * 1024

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    def resolved_scan_roots(self) -> list[Path]:
        return [Path(r).expanduser().resolve() for r in self.allowed_scan_roots]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
