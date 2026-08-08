"""Canonical first-slice database model for AUVRA v2.

Every row is explicitly assigned to the ``app`` or ``ops`` schema.  This lets
v2 coexist with the legacy public-schema tables while migration is rehearsed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.v2.domain.enums import (
    IdempotencyState,
    JobState,
    MediaStatus,
    OnboardingStatus,
    OutboxState,
    PlanItemStatus,
    PlanStatus,
    UserStatus,
)
from app.v2.persistence.base import APP_SCHEMA, OPS_SCHEMA, V2Base


def enum_values(enum_type: type) -> list[str]:
    return [member.value for member in enum_type]


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class User(TimestampMixin, V2Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("auth_provider", "auth_subject"),
        CheckConstraint(
            "status IN ('active','disabled','deletion_pending','deleted')",
            name="valid_status",
        ),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    auth_provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default="firebase", server_default="firebase"
    )
    auth_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=UserStatus.ACTIVE.value,
        server_default=UserStatus.ACTIVE.value,
    )

    profile: Mapped[UserProfile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class UserProfile(TimestampMixin, V2Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint("version > 0", name="positive_version"),
        {"schema": APP_SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    display_name: Mapped[str | None] = mapped_column(String(160))
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    locale: Mapped[str] = mapped_column(
        String(16), nullable=False, default="en", server_default="en"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    user: Mapped[User] = relationship(back_populates="profile")


class ConsentRecord(V2Base):
    __tablename__ = "consent_records"
    __table_args__ = (
        UniqueConstraint("user_id", "consent_type", "document_version"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    consent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    document_version: Mapped[str] = mapped_column(String(64), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OnboardingSession(TimestampMixin, V2Base):
    __tablename__ = "onboarding_sessions"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            "status IN ('active','claimed','expired','revoked')",
            name="valid_status",
        ),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    proof_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=OnboardingStatus.ACTIVE.value,
        server_default=OnboardingStatus.ACTIVE.value,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    claimed_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.users.id", ondelete="RESTRICT"),
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assessments: Mapped[list[OnboardingAssessment]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class OnboardingAssessment(TimestampMixin, V2Base):
    __tablename__ = "onboarding_assessments"
    __table_args__ = (
        UniqueConstraint("session_id", "version"),
        CheckConstraint("version >= 0", name="nonnegative_version"),
        Index(
            "uq_onboarding_assessments_current_session",
            "session_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.users.id", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        # An assessment is current when recorded; a later assessment replaces it
        # under the partial-current-session index in the canonical migration.
        default=True,
        server_default=text("true"),
    )
    validated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped[OnboardingSession] = relationship(back_populates="assessments")


class GenerationJob(TimestampMixin, V2Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 100", name="valid_progress"),
        CheckConstraint("attempt_count >= 0", name="nonnegative_attempts"),
        CheckConstraint("max_attempts > 0", name="positive_max_attempts"),
        CheckConstraint(
            "state IN ('queued','running','retry_wait','ready','failed','cancelled','dead_letter')",
            name="valid_state",
        ),
        Index("ix_generation_jobs_state_available", "state", "available_at"),
        {"schema": OPS_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=JobState.QUEUED.value,
        server_default=JobState.QUEUED.value,
    )
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    phase: Mapped[str | None] = mapped_column(String(64))
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEvent(TimestampMixin, V2Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="nonnegative_attempts"),
        CheckConstraint("max_attempts > 0", name="positive_max_attempts"),
        CheckConstraint(
            "state IN ('pending','running','published','failed')", name="valid_state"
        ),
        Index("ix_outbox_events_state_available", "state", "available_at"),
        {"schema": OPS_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.users.id", ondelete="SET NULL"),
        index=True,
    )
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=OutboxState.PENDING.value,
        server_default=OutboxState.PENDING.value,
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyRecord(TimestampMixin, V2Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("scope", "subject", "idempotency_key"),
        CheckConstraint("state IN ('started','completed')", name="valid_state"),
        CheckConstraint(
            "(state = 'started' AND response_status IS NULL AND response_body IS NULL) "
            "OR (state = 'completed' AND response_status IS NOT NULL AND response_body IS NOT NULL)",
            name="valid_response_state",
        ),
        {"schema": OPS_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scope: Mapped[str] = mapped_column(String(96), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=IdempotencyState.STARTED.value,
        server_default=IdempotencyState.STARTED.value,
    )
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MediaAsset(TimestampMixin, V2Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint("storage_provider", "bucket", "object_key"),
        UniqueConstraint("public_url"),
        CheckConstraint("width IS NULL OR width > 0", name="positive_width"),
        CheckConstraint("height IS NULL OR height > 0", name="positive_height"),
        CheckConstraint("public_url LIKE 'https://%'", name="https_public_url"),
        CheckConstraint(
            "status IN ('pending','ready','failed','deleted')", name="valid_status"
        ),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.users.id", ondelete="SET NULL"),
        index=True,
    )
    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{OPS_SCHEMA}.generation_jobs.id", ondelete="RESTRICT"),
        index=True,
    )
    storage_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    public_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    alt_text: Mapped[str] = mapped_column(String(500), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=MediaStatus.PENDING.value,
        server_default=MediaStatus.PENDING.value,
    )


class ActionPlan(TimestampMixin, V2Base):
    __tablename__ = "action_plans"
    __table_args__ = (
        UniqueConstraint("user_id", "local_date", "revision"),
        CheckConstraint("revision > 0", name="positive_revision"),
        CheckConstraint("status IN ('ready','archived')", name="valid_status"),
        CheckConstraint(
            "(is_current AND status = 'ready') OR (NOT is_current)",
            name="current_plan_is_ready",
        ),
        Index(
            "uq_action_plans_current_user_date",
            "user_id",
            "local_date",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{OPS_SCHEMA}.generation_jobs.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=PlanStatus.READY.value,
        server_default=PlanStatus.READY.value,
    )
    cycle_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[ActionPlanItem]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="ActionPlanItem.slot",
    )


class ActionPlanItem(TimestampMixin, V2Base):
    __tablename__ = "action_plan_items"
    __table_args__ = (
        UniqueConstraint("plan_id", "slot"),
        CheckConstraint("slot >= 1 AND slot <= 4", name="valid_slot"),
        CheckConstraint(
            "status IN ('active','replaced','retired')", name="valid_status"
        ),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.action_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    hero_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.media_assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    supersedes_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.action_plan_items.id", ondelete="SET NULL"),
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=PlanItemStatus.ACTIVE.value,
        server_default=PlanItemStatus.ACTIVE.value,
    )

    plan: Mapped[ActionPlan] = relationship(back_populates="items")
    hero_asset: Mapped[MediaAsset] = relationship(foreign_keys=[hero_asset_id])
    variants: Mapped[list[ActionPlanItemVariant]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class ActionPlanItemVariant(TimestampMixin, V2Base):
    __tablename__ = "action_plan_item_variants"
    __table_args__ = (
        UniqueConstraint("item_id", "variant_type"),
        {"schema": APP_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.action_plan_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variant_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{APP_SCHEMA}.media_assets.id", ondelete="RESTRICT"),
        nullable=False,
    )

    item: Mapped[ActionPlanItem] = relationship(back_populates="variants")
    asset: Mapped[MediaAsset] = relationship(foreign_keys=[asset_id])


__all__ = [
    "User",
    "UserProfile",
    "ConsentRecord",
    "OnboardingSession",
    "OnboardingAssessment",
    "GenerationJob",
    "OutboxEvent",
    "IdempotencyRecord",
    "MediaAsset",
    "ActionPlan",
    "ActionPlanItem",
    "ActionPlanItemVariant",
    "enum_values",
]
