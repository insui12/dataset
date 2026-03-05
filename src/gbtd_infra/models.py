from __future__ import annotations

from datetime import datetime
import enum
import uuid
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class TrackerTier(str, enum.Enum):
    core = "core"
    extended = "extended"
    special = "special"
    legacy = "legacy"
    excluded = "excluded"


class CollectionMode(str, enum.Enum):
    instance_exhaustive = "instance_exhaustive"
    manifest_exhaustive = "manifest_exhaustive"
    conditional = "conditional"


class DatasetRole(str, enum.Enum):
    software_product = "software_product"
    library_runtime = "library_runtime"
    os_kernel = "os_kernel"
    infra_tool = "infra_tool"
    desktop_app = "desktop_app"
    community_process = "community_process"
    graveyard_legacy = "graveyard_legacy"
    security_restricted = "security_restricted"
    unsupported = "unsupported"


class ProtocolType(str, enum.Enum):
    REST = "REST"
    GRAPHQL = "GRAPHQL"
    JSON_RPC = "JSON-RPC"
    XML_RPC = "XML-RPC"
    SOAP = "SOAP"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


class Visibility(str, enum.Enum):
    public = "public"
    auth_required = "auth_required"
    restricted = "restricted"
    blocked = "blocked"


class RegistryStatus(str, enum.Enum):
    active = "active"
    legacy = "legacy"
    unknown = "unknown"


class RegistryEntryKind(str, enum.Enum):
    instance = "instance"
    project = "project"
    product = "product"
    repo = "repo"
    component = "component"
    module = "module"


class JobType(str, enum.Enum):
    capability_probe = "capability_probe"
    count_snapshot = "count_snapshot"
    list_page_fetch = "list_page_fetch"
    issue_detail_fetch = "issue_detail_fetch"
    comments_fetch = "comments_fetch"
    attachments_fetch = "attachments_fetch"
    incremental_sync = "incremental_sync"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    dead = "dead"


class CountMode(str, enum.Enum):
    exact = "exact"
    approximate = "approximate"
    enumerated = "enumerated"
    offset_probe = "offset_probe"


class RunStatus(str, enum.Enum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    aborted = "aborted"


class BlockReason(str, enum.Enum):
    ok = "ok"
    blocked = "blocked"
    auth_required = "auth_required"
    unsupported = "unsupported"


class TrackerFamily(Base, TimestampMixin):
    __tablename__ = "tracker_families"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    default_protocol: Mapped[ProtocolType] = mapped_column(nullable=False, default=ProtocolType.UNKNOWN)
    notes: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    instances: Mapped[list["TrackerInstance"]] = relationship(back_populates="family")
    entries: Mapped[list["RegistryEntry"]] = relationship(back_populates="family")
    capability_probes: Mapped[list["CapabilityProbe"]] = relationship(back_populates="family")


class TrackerInstance(Base, TimestampMixin):
    __tablename__ = "tracker_instances"
    __table_args__ = (
        UniqueConstraint("family_id", "canonical_name", name="uq_tracker_instances_family_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("tracker_families.id", ondelete="RESTRICT"))
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_base_url: Mapped[Optional[str]] = mapped_column(String(512))
    region: Mapped[Optional[str]] = mapped_column(String(64))
    tier: Mapped[TrackerTier] = mapped_column(nullable=False)
    collection_mode: Mapped[CollectionMode] = mapped_column(nullable=False)
    dataset_role: Mapped[DatasetRole] = mapped_column(nullable=False)
    protocol: Mapped[ProtocolType] = mapped_column(nullable=False)
    visibility: Mapped[Visibility] = mapped_column(nullable=False)
    status: Mapped[RegistryStatus] = mapped_column(nullable=False, default=RegistryStatus.unknown)

    family: Mapped[TrackerFamily] = relationship(back_populates="instances")
    entries: Mapped[list["RegistryEntry"]] = relationship(back_populates="instance")
    raw_payloads: Mapped[list["RawApiPayload"]] = relationship(back_populates="instance")
    probe_results: Mapped[list["CapabilityProbe"]] = relationship(back_populates="instance")


class RegistryEntry(Base, TimestampMixin):
    __tablename__ = "registry_entries"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "entry_kind",
            "tracker_native_id",
            name="uq_registry_entries_instance_kind_native_id",
        ),
        Index("ix_registry_entries_instance_kind", "instance_id", "entry_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("tracker_families.id", ondelete="RESTRICT"))
    instance_id: Mapped[int] = mapped_column(ForeignKey("tracker_instances.id", ondelete="RESTRICT"))
    parent_entry_id: Mapped[Optional[int]] = mapped_column(ForeignKey("registry_entries.id", ondelete="SET NULL"))

    entry_kind: Mapped[RegistryEntryKind] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255))
    tracker_native_id: Mapped[Optional[str]] = mapped_column(String(255))
    tracker_key: Mapped[Optional[str]] = mapped_column(String(255))
    tracker_api_key: Mapped[Optional[str]] = mapped_column(String(255))
    tracker_url: Mapped[Optional[str]] = mapped_column(String(512))
    api_url: Mapped[Optional[str]] = mapped_column(String(512))

    tier: Mapped[TrackerTier] = mapped_column(nullable=False)
    collection_mode: Mapped[CollectionMode] = mapped_column(nullable=False)
    dataset_role: Mapped[DatasetRole] = mapped_column(nullable=False)
    protocol: Mapped[ProtocolType] = mapped_column(nullable=False)
    visibility: Mapped[Visibility] = mapped_column(nullable=False)
    status: Mapped[RegistryStatus] = mapped_column(nullable=False, default=RegistryStatus.unknown)
    is_bounded_instance: Mapped[bool] = mapped_column(Boolean, default=True)

    manifest_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("manifest_versions.id"))
    parent: Mapped[Optional["RegistryEntry"]] = relationship(remote_side="RegistryEntry.id")
    family: Mapped[TrackerFamily] = relationship(back_populates="entries")
    instance: Mapped[TrackerInstance] = relationship(back_populates="entries")
    components: Mapped[list["RegistryComponent"]] = relationship(back_populates="entry")

    issues: Mapped[list["Issue"]] = relationship(back_populates="registry_entry")
    jobs: Mapped[list["CollectionJob"]] = relationship(back_populates="registry_entry")
    probes: Mapped[list["CapabilityProbe"]] = relationship(back_populates="registry_entry")
    count_snapshots: Mapped[list["CountSnapshot"]] = relationship(back_populates="registry_entry")
    sync_watermarks: Mapped[list["SyncWatermark"]] = relationship(back_populates="registry_entry")


class RegistryComponent(Base, TimestampMixin):
    __tablename__ = "registry_components"

    id: Mapped[int] = mapped_column(primary_key=True)
    registry_entry_id: Mapped[int] = mapped_column(ForeignKey("registry_entries.id", ondelete="CASCADE"))
    component_name: Mapped[str] = mapped_column(String(255), nullable=False)
    component_key: Mapped[Optional[str]] = mapped_column(String(255))
    tracker_component_id: Mapped[Optional[str]] = mapped_column(String(255))
    tracker_component_url: Mapped[Optional[str]] = mapped_column(String(512))
    metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    entry: Mapped[RegistryEntry] = relationship(back_populates="components")


class CollectionPolicy(Base, TimestampMixin):
    __tablename__ = "collection_policies"
    __table_args__ = (UniqueConstraint("registry_entry_id", name="uq_collection_policies_registry_entry_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    registry_entry_id: Mapped[int] = mapped_column(ForeignKey("registry_entries.id", ondelete="CASCADE"))
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class CapabilityProbe(Base, TimestampMixin):
    __tablename__ = "capability_probes"

    id: Mapped[int] = mapped_column(primary_key=True)
    registry_entry_id: Mapped[Optional[int]] = mapped_column(ForeignKey("registry_entries.id", ondelete="SET NULL"))
    family_id: Mapped[int] = mapped_column(ForeignKey("tracker_families.id"))
    instance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tracker_instances.id", ondelete="SET NULL"))

    probe_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    protocol: Mapped[ProtocolType] = mapped_column(nullable=False, default=ProtocolType.UNKNOWN)
    protocol_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    pagination_scheme: Mapped[Optional[str]] = mapped_column(String(64))
    count_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_required: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_limit_expected: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    probe_payload_sample: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    block_reason: Mapped[BlockReason] = mapped_column(default=BlockReason.ok)
    blocked_fields: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    raw_response_status: Mapped[Optional[int]] = mapped_column(Integer)
    raw_response_body_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("raw_api_payloads.id", ondelete="SET NULL"))

    family: Mapped[TrackerFamily] = relationship()
    entry: Mapped[Optional[RegistryEntry]] = relationship(back_populates="probes")
    instance: Mapped[Optional[TrackerInstance]] = relationship()


class CountSnapshot(Base, TimestampMixin):
    __tablename__ = "count_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "registry_entry_id",
            "query_signature",
            "manifest_version_id",
            name="uq_count_snapshots_entry_query_manifest",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    registry_entry_id: Mapped[int] = mapped_column(ForeignKey("registry_entries.id", ondelete="CASCADE"))
    manifest_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("manifest_versions.id", ondelete="SET NULL"))
    query_signature: Mapped[str] = mapped_column(String(255), nullable=False)
    count_mode: Mapped[CountMode] = mapped_column(nullable=False)
    count_method: Mapped[str] = mapped_column(String(128), nullable=False)
    count_value: Mapped[Optional[int]] = mapped_column(BigInteger)
    count_upper_bound: Mapped[Optional[int]] = mapped_column(BigInteger)
    notes: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    comparator: Mapped[Optional[float]] = mapped_column()

    registry_entry: Mapped[RegistryEntry] = relationship(back_populates="count_snapshots")


class CollectionJob(Base, TimestampMixin):
    __tablename__ = "collection_jobs"
    __table_args__ = (
        Index("ix_collection_jobs_status_run", "status", "next_run_at", "priority"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[JobType] = mapped_column(nullable=False)
    family_id: Mapped[int] = mapped_column(ForeignKey("tracker_families.id", ondelete="RESTRICT"))
    instance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tracker_instances.id", ondelete="SET NULL"))
    registry_entry_id: Mapped[Optional[int]] = mapped_column(ForeignKey("registry_entries.id", ondelete="SET NULL"))
    parent_job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("collection_jobs.id", ondelete="SET NULL"))

    priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[JobStatus] = mapped_column(nullable=False, default=JobStatus.pending)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[Optional[str]] = mapped_column(String(64))
    lease_owner: Mapped[Optional[str]] = mapped_column(String(128))
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)

    registry_entry: Mapped[Optional[RegistryEntry]] = relationship(back_populates="jobs")

    parent: Mapped[Optional["CollectionJob"]] = relationship(
        "CollectionJob",
        remote_side="CollectionJob.id",
    )


class JobLease(Base, TimestampMixin):
    __tablename__ = "job_leases"
    __table_args__ = (Index("ix_job_leases_expires", "expires_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    collection_job_id: Mapped[int] = mapped_column(ForeignKey("collection_jobs.id", ondelete="CASCADE"))
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(32), default="active")


class SyncWatermark(Base, TimestampMixin):
    __tablename__ = "sync_watermarks"
    __table_args__ = (
        UniqueConstraint("registry_entry_id", "resource", name="uq_sync_watermarks_entry_resource"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    registry_entry_id: Mapped[int] = mapped_column(ForeignKey("registry_entries.id", ondelete="CASCADE"))
    family_id: Mapped[int] = mapped_column(ForeignKey("tracker_families.id", ondelete="RESTRICT"), nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    cursor_name: Mapped[str] = mapped_column(String(128), nullable=False)
    cursor_value: Mapped[Optional[str]] = mapped_column(String(255))
    cursor_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    registry_entry: Mapped[RegistryEntry] = relationship(back_populates="sync_watermarks")


class RawApiPayload(Base, TimestampMixin):
    __tablename__ = "raw_api_payloads"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    family_id: Mapped[int] = mapped_column(ForeignKey("tracker_families.id", ondelete="RESTRICT"))
    instance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tracker_instances.id", ondelete="SET NULL"))
    registry_entry_id: Mapped[Optional[int]] = mapped_column(ForeignKey("registry_entries.id", ondelete="SET NULL"))
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    api_url: Mapped[Optional[str]] = mapped_column(String(2048))
    http_method: Mapped[str] = mapped_column(String(16), nullable=False)
    request_params_hash: Mapped[str] = mapped_column(String(128))
    request_headers_hash: Mapped[Optional[str]] = mapped_column(String(128))
    response_status_code: Mapped[int] = mapped_column(Integer)
    response_headers: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    response_body_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    response_body_raw: Mapped[Optional[str]] = mapped_column(Text)
    response_body_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    request_idempotency_key: Mapped[Optional[str]] = mapped_column(String(64))

    instance: Mapped[Optional[TrackerInstance]] = relationship(back_populates="raw_payloads")


class RawApiPage(Base, TimestampMixin):
    __tablename__ = "raw_api_pages"
    __table_args__ = (UniqueConstraint("source_url", "request_params_hash", name="uq_raw_api_pages_dedupe"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    registry_entry_id: Mapped[int] = mapped_column(ForeignKey("registry_entries.id", ondelete="CASCADE"), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    page_type: Mapped[str] = mapped_column(String(64), nullable=False)
    request_params: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    request_params_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_payload_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_api_payloads.id", ondelete="CASCADE"), nullable=False)
    next_cursor: Mapped[Optional[str]] = mapped_column(String(255))
    page_index: Mapped[int] = mapped_column(Integer, default=0)
    is_last_page: Mapped[bool] = mapped_column(Boolean, default=False)


class CollectionError(Base, TimestampMixin):
    __tablename__ = "collection_errors"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingestion_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
    family_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tracker_families.id", ondelete="SET NULL"))
    instance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tracker_instances.id", ondelete="SET NULL"))
    registry_entry_id: Mapped[Optional[int]] = mapped_column(ForeignKey("registry_entries.id", ondelete="SET NULL"))
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("collection_jobs.id", ondelete="SET NULL"))
    raw_payload_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("raw_api_payloads.id", ondelete="SET NULL"))

    source_url: Mapped[Optional[str]] = mapped_column(String(2048))
    error_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status_code: Mapped[Optional[int]] = mapped_column(Integer)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[Optional[str]] = mapped_column(Text)
    detail: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)


class RateLimitEvent(Base, TimestampMixin):
    __tablename__ = "rate_limit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("tracker_families.id", ondelete="CASCADE"))
    instance_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tracker_instances.id", ondelete="SET NULL"))
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[Optional[str]] = mapped_column(String(1024))
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_after_seconds: Mapped[Optional[float]] = mapped_column()
    bucket_before: Mapped[Optional[float]] = mapped_column()
    bucket_after: Mapped[Optional[float]] = mapped_column()
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    request_idempotency_key: Mapped[Optional[str]] = mapped_column(String(64))


class Issue(Base, TimestampMixin):
    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("tracker_instance_id", "tracker_issue_id", name="uq_issues_instance_issue_id"),
        Index("ix_issues_state_closed", "is_closed"),
        Index("ix_issues_closed_at", "closed_at"),
        Index("ix_issues_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_family_id: Mapped[int] = mapped_column(ForeignKey("tracker_families.id", ondelete="RESTRICT"))
    tracker_instance_id: Mapped[int] = mapped_column(ForeignKey("tracker_instances.id", ondelete="RESTRICT"))
    registry_entry_id: Mapped[int] = mapped_column(ForeignKey("registry_entries.id", ondelete="RESTRICT"))
    tracker_issue_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tracker_issue_key: Mapped[Optional[str]] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    body_raw: Mapped[Optional[str]] = mapped_column(Text)
    body_plaintext: Mapped[Optional[str]] = mapped_column(Text)
    issue_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    api_url: Mapped[str] = mapped_column(String(2048), nullable=False)

    issue_type_raw: Mapped[Optional[str]] = mapped_column(String(128))
    state_raw: Mapped[Optional[str]] = mapped_column(String(128))
    resolution_raw: Mapped[Optional[str]] = mapped_column(String(255))
    close_reason_raw: Mapped[Optional[str]] = mapped_column(String(255))

    created_at_tracker: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at_tracker: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    reporter_raw: Mapped[Optional[str]] = mapped_column(String(255))
    assignee_raw: Mapped[Optional[str]] = mapped_column(String(255))
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pull_request: Mapped[bool] = mapped_column(Boolean, default=False)
    is_private_restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_payload_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("raw_api_payloads.id", ondelete="SET NULL"), nullable=True)

    registry_entry: Mapped[RegistryEntry] = relationship(back_populates="issues")
    comments: Mapped[list["IssueComment"]] = relationship(back_populates="issue")
    events: Mapped[list["IssueEvent"]] = relationship(back_populates="issue")


class IssueComment(Base, TimestampMixin):
    __tablename__ = "issue_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    comment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    body_raw: Mapped[Optional[str]] = mapped_column(Text)
    body_plaintext: Mapped[Optional[str]] = mapped_column(Text)
    author_raw: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_system_note: Mapped[bool] = mapped_column(Boolean, default=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_payload_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("raw_api_payloads.id", ondelete="SET NULL"))

    issue: Mapped[Issue] = relationship(back_populates="comments")
    __table_args__ = (UniqueConstraint("issue_id", "comment_id", name="uq_issue_comments_issue_comment"),)


class IssueEvent(Base, TimestampMixin):
    __tablename__ = "issue_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_raw: Mapped[Optional[str]] = mapped_column(String(255))
    event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    event_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    issue: Mapped[Issue] = relationship(back_populates="events")
    __table_args__ = (UniqueConstraint("issue_id", "event_id", name="uq_issue_events_issue_event"),)


class IssueAttachment(Base, TimestampMixin):
    __tablename__ = "issue_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    attachment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attachment_url: Mapped[Optional[str]] = mapped_column(String(2048))
    filename: Mapped[Optional[str]] = mapped_column(String(255))
    mime_type: Mapped[Optional[str]] = mapped_column(String(128))
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    author_raw: Mapped[Optional[str]] = mapped_column(String(255))
    metadata_raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    __table_args__ = (UniqueConstraint("issue_id", "attachment_id", name="uq_issue_attachments_issue_attachment"),)


class IssueLabel(Base, TimestampMixin):
    __tablename__ = "issue_labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    label_name: Mapped[str] = mapped_column(String(255), nullable=False)
    label_key: Mapped[Optional[str]] = mapped_column(String(255))
    color: Mapped[Optional[str]] = mapped_column(String(32))
    creator_raw: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("issue_id", "label_name", name="uq_issue_labels_issue_label"),)


class IssueLink(Base, TimestampMixin):
    __tablename__ = "issue_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    linked_issue_key: Mapped[str] = mapped_column(String(255), nullable=False)
    link_type: Mapped[str] = mapped_column(String(128), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    target_source_family_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tracker_families.id", ondelete="SET NULL"))
    target_issue_url: Mapped[Optional[str]] = mapped_column(String(2048))


class IssueAssignee(Base, TimestampMixin):
    __tablename__ = "issue_assignees"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    assignee_raw: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    unassigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (UniqueConstraint("issue_id", "assignee_raw", "assigned_at", name="uq_issue_assignees_issue_assignee_time"),)


class IssueCustomField(Base, TimestampMixin):
    __tablename__ = "issue_custom_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    field_key: Mapped[Optional[str]] = mapped_column(String(255))
    field_value_raw: Mapped[dict[str, Any]] = mapped_column(JSONB)

    __table_args__ = (UniqueConstraint("issue_id", "field_name", "field_key", name="uq_issue_custom_fields"),)


class IngestionRun(Base, TimestampMixin):
    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_type: Mapped[str] = mapped_column(String(64), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[RunStatus] = mapped_column(nullable=False, default=RunStatus.running)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    total_jobs: Mapped[int] = mapped_column(Integer, default=0)
    success_jobs: Mapped[int] = mapped_column(Integer, default=0)
    failed_jobs: Mapped[int] = mapped_column(Integer, default=0)
    config_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)


class SchemaVersion(Base, TimestampMixin):
    __tablename__ = "schema_versions"
    __table_args__ = (UniqueConstraint("revision", name="uq_schema_version_revision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ManifestVersion(Base, TimestampMixin):
    __tablename__ = "manifest_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    manifest_name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_file: Mapped[str] = mapped_column(String(255), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notes: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)

    __table_args__ = (UniqueConstraint("manifest_name", "version", name="uq_manifest_name_version"),)
