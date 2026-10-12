"""Pydantic request/response schemas for the REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.timeutil import as_utc

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("*", mode="after")
    @classmethod
    def _utc_datetimes(cls, value):
        # SQLite returns naive datetimes; all stored times are UTC.
        return as_utc(value) if isinstance(value, datetime) else value


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class ErrorResponse(BaseModel):
    detail: str


# --- Locations & scans ----------------------------------------------------------------------


class LocationCreate(BaseModel):
    kind: Literal["local", "s3"]
    name: str | None = Field(default=None, max_length=200)
    path: str | None = Field(default=None, max_length=1024)
    bucket: str | None = Field(default=None, max_length=255, pattern=r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")
    prefix: str | None = Field(default=None, max_length=1024)


class LocationOut(ORM):
    id: int
    name: str
    kind: str
    path: str | None
    bucket: str | None
    prefix: str | None
    created_at: datetime
    file_count: int = 0
    total_bytes: int = 0
    last_scan_status: str | None = None


class BrowseEntry(BaseModel):
    name: str
    path: str


class BrowseOut(BaseModel):
    roots: list[str]
    current: str | None
    parent: str | None
    directories: list[BrowseEntry]


class ScanCreate(BaseModel):
    location_id: int


class ScanOut(ORM):
    id: int
    location_id: int
    status: str
    phase: str
    total_files: int
    processed_files: int
    error_count: int
    skipped_count: int
    cancel_requested: bool
    message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


# --- Files ----------------------------------------------------------------------------------


class FileOut(ORM):
    id: int
    location_id: int
    rel_path: str
    name: str
    extension: str
    mime_type: str
    category: str
    size_bytes: int
    sha256: str | None
    modified_at: datetime | None
    accessed_at: datetime | None
    accessed_at_reliable: bool
    storage_class: str | None
    text_chars: int
    status: str
    error: str | None
    duplicate_group_id: int | None
    embedding_model: str | None
    last_scanned_at: datetime


class CompressionOut(ORM):
    id: int
    file_id: int
    method: str
    lossless: bool
    status: str
    original_bytes: int
    output_bytes: int | None
    duration_ms: float | None
    quality_note: str | None
    quality_metric: dict
    restore_method: str | None
    output_path: str | None
    is_best: bool
    ratio: float | None
    percent_saved: float | None
    created_at: datetime


class PredictionOut(ORM):
    id: int
    file_id: int
    evaluation_id: int | None
    horizon_days: int
    probability: float | None
    reliability: str
    reason: str | None
    history_source: str | None
    created_at: datetime


class FileDetail(FileOut):
    metadata_json: dict
    text_preview: str | None = None
    chunk_count: int = 0
    duplicates: list[FileOut] = []
    similar: list[dict] = []
    compression: list[CompressionOut] = []
    prediction: PredictionOut | None = None
    access_event_count: int = 0
    location_name: str | None = None


class FileStats(BaseModel):
    total_files: int
    total_bytes: int
    by_category: dict[str, dict[str, int]]
    by_status: dict[str, int]
    by_extension: dict[str, dict[str, int]]
    largest: list[FileOut]


# --- Duplicates & similarity ------------------------------------------------------------------


class DuplicateGroupOut(ORM):
    id: int
    sha256: str
    size_bytes: int
    file_count: int
    redundant_bytes: int
    files: list[FileOut]
    suggested_keep_id: int | None = None


class DuplicateSummary(BaseModel):
    groups: int
    duplicate_files: int
    redundant_copies: int
    redundant_bytes: int


class SimilarityPairOut(BaseModel):
    id: int
    score: float
    coverage_a: float
    coverage_b: float
    embedding_model: str
    file_a: FileOut
    file_b: FileOut


class ComparisonOut(BaseModel):
    file_a: FileOut
    file_b: FileOut
    document_similarity: float | None
    exact_duplicate: bool
    embedding_model: str
    sentence_threshold: float
    shared: list[dict]
    unique_a: list[dict]
    unique_b: list[dict]
    conflicts: list[dict]
    shared_ratio_a: float
    shared_ratio_b: float
    warnings: list[str]
    disclaimer: str


# --- Consolidation ------------------------------------------------------------------------------


class ConsolidationCreate(BaseModel):
    file_ids: list[int] = Field(min_length=2, max_length=20)
    title: str = Field(default="Consolidated document", min_length=1, max_length=300)


class ConsolidationUpdate(BaseModel):
    markdown: str = Field(max_length=2_000_000)


class ConsolidationExport(BaseModel):
    format: Literal["md", "txt"] = "md"


class ConsolidationOut(ORM):
    id: int
    title: str
    source_file_ids: list[int]
    draft_markdown: str
    edited_markdown: str | None
    report: dict
    status: str
    original_bytes: int
    output_bytes: int
    export_path: str | None
    export_format: str | None
    created_at: datetime
    updated_at: datetime


# --- Compression -------------------------------------------------------------------------------


class CompressionBatch(BaseModel):
    location_id: int | None = None
    limit: int = Field(default=50, ge=1, le=500)
    categories: list[str] = ["document", "data", "image", "video", "archive"]


# --- ML -------------------------------------------------------------------------------------------


class TrainRequest(BaseModel):
    dataset: Literal["synthetic", "observed"] = "synthetic"
    n_files: int = Field(default=1500, ge=200, le=20000)
    horizon_days: int = Field(default=30, ge=7, le=180)
    seed: int = 42


class EvaluationOut(ORM):
    id: int
    dataset_kind: str
    horizon_days: int
    params: dict
    metrics: dict
    selected_model: str
    artifact_path: str | None
    created_at: datetime


class PredictRequest(BaseModel):
    file_ids: list[int] | None = None


class FetchS3LogsRequest(BaseModel):
    log_bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")
    prefix: str = Field(default="", max_length=1024)
    max_objects: int = Field(default=1000, ge=1, le=5000)


class DemoEventsRequest(BaseModel):
    location_id: int
    seed: int = 7


# --- Costs / plans --------------------------------------------------------------------------------


Strategy = Literal["conservative", "balanced", "aggressive"]


class SimulateRequest(BaseModel):
    strategy: Strategy = "balanced"
    location_id: int | None = None
    horizon_months: int | None = Field(default=None, ge=1, le=60)
    region: str | None = None


class WhatIfRequest(BaseModel):
    """Hypothetical workload for the cost simulator (independent of the inventory)."""

    object_size_mb: float = Field(gt=0, le=5_000_000)
    object_count: int = Field(default=1, ge=1, le=1_000_000_000)
    reads_per_object_per_month: float = Field(default=0.1, ge=0, le=100_000)
    current_class: str = "STANDARD"
    horizon_months: int = Field(default=12, ge=1, le=60)
    strategy: Strategy = "balanced"
    region: str | None = None


class PlanCreate(BaseModel):
    strategy: Strategy = "balanced"
    location_id: int | None = None


class PlanActionOut(ORM):
    id: int
    plan_id: int
    priority: int
    action_type: str
    title: str
    reason: str
    evidence: dict
    target_file_ids: list[int]
    params: dict
    expected_bytes_saved: int
    expected_monthly_savings_usd: float
    estimated_cost_usd: float
    risk_level: str
    risks: list[str]
    reversible: bool
    reversibility_note: str
    executable: bool
    status: str
    result: dict
    decided_at: datetime | None
    executed_at: datetime | None


class PlanOut(ORM):
    id: int
    location_id: int | None
    strategy: str
    status: str
    summary: dict
    created_at: datetime
    actions: list[PlanActionOut] = []


class PlanListItem(ORM):
    id: int
    location_id: int | None
    strategy: str
    status: str
    summary: dict
    created_at: datetime


class DecisionRequest(BaseModel):
    approve: bool
    acknowledge_risks: bool = False


class ExecuteRequest(BaseModel):
    dry_run: bool = True
    action_ids: list[int] | None = None


class OperationOut(ORM):
    id: int
    timestamp: datetime
    actor: str
    operation: str
    target: str | None
    status: str
    dry_run: bool
    details: dict


class SettingsUpdate(BaseModel):
    values: dict[str, Any]

    @field_validator("values")
    @classmethod
    def _not_empty(cls, v):
        if not v:
            raise ValueError("No settings supplied.")
        return v
