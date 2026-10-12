"""Persistent data model.

Tables are grouped by feature area:

* Inventory: ``StorageLocation``, ``ScanJob``, ``FileRecord``, ``DocumentChunk``, ``StorageSnapshot``
* Redundancy: ``DuplicateGroup``, ``SimilarityPair``, ``Consolidation``
* Optimisation: ``CompressionResult``, ``StorageRecommendation``, ``OptimizationPlan``, ``PlanAction``
* ML: ``AccessEvent``, ``ModelEvaluation``, ``AccessPrediction``
* Governance: ``OperationLog``, ``AppSetting``
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class StorageLocation(Base):
    """An explicitly authorised place the user wants analysed (local dir or S3 bucket/prefix)."""

    __tablename__ = "storage_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # "local" | "s3"
    path: Mapped[str | None] = mapped_column(String(1024))  # local absolute path
    bucket: Mapped[str | None] = mapped_column(String(255))
    prefix: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    files: Mapped[list[FileRecord]] = relationship(back_populates="location", cascade="all, delete-orphan")
    scans: Mapped[list[ScanJob]] = relationship(back_populates="location", cascade="all, delete-orphan")


class ScanJob(Base):
    __tablename__ = "scan_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("storage_locations.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|completed|failed|cancelled
    phase: Mapped[str] = mapped_column(String(40), default="queued")
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    processed_files: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    location: Mapped[StorageLocation] = relationship(back_populates="scans")


class FileRecord(Base):
    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("location_id", "rel_path", name="uq_file_location_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("storage_locations.id", ondelete="CASCADE"), index=True)
    rel_path: Mapped[str] = mapped_column(String(2048))  # path relative to location, or S3 key
    name: Mapped[str] = mapped_column(String(512))
    extension: Mapped[str] = mapped_column(String(32), default="")
    mime_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    category: Mapped[str] = mapped_column(String(32), default="other")  # document|data|image|video|archive|other
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accessed_at_reliable: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at_fs: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_class: Mapped[str | None] = mapped_column(String(40))  # S3 only
    etag: Mapped[str | None] = mapped_column(String(128))
    text_content: Mapped[str | None] = mapped_column(Text)
    text_chars: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="analyzed")  # analyzed|skipped|error|missing
    error: Mapped[str | None] = mapped_column(Text)
    duplicate_group_id: Mapped[int | None] = mapped_column(ForeignKey("duplicate_groups.id", ondelete="SET NULL"), index=True)
    embedded_sha256: Mapped[str | None] = mapped_column(String(64))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    location: Mapped[StorageLocation] = relationship(back_populates="files")
    chunks: Mapped[list[DocumentChunk]] = relationship(back_populates="file", cascade="all, delete-orphan")
    duplicate_group: Mapped[DuplicateGroup | None] = relationship(back_populates="files")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    start_char: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)  # float32 little-endian, L2-normalised
    embedding_model: Mapped[str] = mapped_column(String(200))

    file: Mapped[FileRecord] = relationship(back_populates="chunks")


class StorageSnapshot(Base):
    """Measured storage usage at a point in time (one per completed scan)."""

    __tablename__ = "storage_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("storage_locations.id", ondelete="CASCADE"))
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    total_bytes: Mapped[int] = mapped_column(BigInteger)
    file_count: Mapped[int] = mapped_column(Integer)


class DuplicateGroup(Base):
    __tablename__ = "duplicate_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    file_count: Mapped[int] = mapped_column(Integer)
    redundant_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    files: Mapped[list[FileRecord]] = relationship(back_populates="duplicate_group")


class SimilarityPair(Base):
    """A semantic relationship between two documents (never implies factual equivalence)."""

    __tablename__ = "similarity_pairs"
    __table_args__ = (UniqueConstraint("file_a_id", "file_b_id", name="uq_similarity_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    file_a_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    file_b_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float)  # document-level cosine similarity
    coverage_a: Mapped[float] = mapped_column(Float, default=0.0)  # share of A's chunks matched in B
    coverage_b: Mapped[float] = mapped_column(Float, default=0.0)
    embedding_model: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    file_a: Mapped[FileRecord] = relationship(foreign_keys=[file_a_id])
    file_b: Mapped[FileRecord] = relationship(foreign_keys=[file_b_id])


class Consolidation(Base):
    __tablename__ = "consolidations"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    source_file_ids: Mapped[list] = mapped_column(JSON)
    draft_markdown: Mapped[str] = mapped_column(Text)
    edited_markdown: Mapped[str | None] = mapped_column(Text)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|reviewed|exported
    original_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    output_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    export_path: Mapped[str | None] = mapped_column(String(1024))
    export_format: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CompressionResult(Base):
    __tablename__ = "compression_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(60))
    lossless: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(20))  # measured|skipped|error|recommended
    original_bytes: Mapped[int] = mapped_column(BigInteger)
    output_bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    quality_note: Mapped[str | None] = mapped_column(Text)
    quality_metric: Mapped[dict] = mapped_column(JSON, default=dict)
    restore_method: Mapped[str | None] = mapped_column(Text)
    output_path: Mapped[str | None] = mapped_column(String(1024))  # only set when an output copy is kept
    is_best: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    file: Mapped[FileRecord] = relationship()

    @property
    def ratio(self) -> float | None:
        if not self.output_bytes:
            return None
        return self.original_bytes / self.output_bytes

    @property
    def percent_saved(self) -> float | None:
        if self.output_bytes is None or not self.original_bytes:
            return None
        return 100.0 * (1 - self.output_bytes / self.original_bytes)


class AccessEvent(Base):
    """An observed (or explicitly synthetic) access to a file."""

    __tablename__ = "access_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(30))  # imported_log|s3_access_log|synthetic_demo
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelEvaluation(Base):
    __tablename__ = "model_evaluations"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_kind: Mapped[str] = mapped_column(String(30))  # synthetic|observed
    horizon_days: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # per model name
    selected_model: Mapped[str] = mapped_column(String(60))
    artifact_path: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AccessPrediction(Base):
    __tablename__ = "access_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    evaluation_id: Mapped[int | None] = mapped_column(ForeignKey("model_evaluations.id", ondelete="SET NULL"))
    horizon_days: Mapped[int] = mapped_column(Integer)
    probability: Mapped[float | None] = mapped_column(Float)
    reliability: Mapped[str] = mapped_column(String(20))  # ok|low|unavailable
    reason: Mapped[str | None] = mapped_column(Text)
    history_source: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StorageRecommendation(Base):
    __tablename__ = "storage_recommendations"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    strategy: Mapped[str] = mapped_column(String(20))
    current_class: Mapped[str] = mapped_column(String(40))
    recommended_class: Mapped[str] = mapped_column(String(40))
    current_cost: Mapped[float] = mapped_column(Float)  # USD over horizon
    projected_cost: Mapped[float] = mapped_column(Float)  # USD over horizon incl. transition + retrieval
    horizon_months: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OptimizationPlan(Base):
    __tablename__ = "optimization_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("storage_locations.id", ondelete="SET NULL"))
    strategy: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|partially_executed|executed
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    actions: Mapped[list[PlanAction]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="PlanAction.priority"
    )


class PlanAction(Base):
    __tablename__ = "plan_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("optimization_plans.id", ondelete="CASCADE"), index=True)
    priority: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    target_file_ids: Mapped[list] = mapped_column(JSON, default=list)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    expected_bytes_saved: Mapped[int] = mapped_column(BigInteger, default=0)
    expected_monthly_savings_usd: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(10))  # low|medium|high
    risks: Mapped[list] = mapped_column(JSON, default=list)
    reversible: Mapped[bool] = mapped_column(Boolean, default=True)
    reversibility_note: Mapped[str] = mapped_column(Text, default="")
    executable: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    # proposed|approved|rejected|executed|failed|acknowledged
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plan: Mapped[OptimizationPlan] = relationship(back_populates="actions")


class OperationLog(Base):
    """Append-only audit trail of every state-changing operation."""

    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(100), default="local-user")
    operation: Mapped[str] = mapped_column(String(80), index=True)
    target: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(20))  # success|failure|dry_run|info
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
