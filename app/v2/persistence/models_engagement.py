"""Canonical v2 engagement, evidence, AI-governance, and retention records.

These deliberately contain facts and ledgers rather than mutable counters.  The
application layer derives user-visible metrics from them inside a Unit of Work.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.v2.persistence.base import APP_SCHEMA, OPS_SCHEMA, V2Base
from app.v2.persistence.models import TimestampMixin


class ActionEvent(V2Base):
    __tablename__ = "action_item_events"
    __table_args__ = (
        UniqueConstraint("user_id", "client_event_id"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.action_plan_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    client_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    decision_local_date: Mapped[date] = mapped_column(Date, nullable=False)
    decision_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DailyReview(TimestampMixin, V2Base):
    __tablename__ = "daily_reviews"
    __table_args__ = (
        UniqueConstraint("plan_id"),
        CheckConstraint(
            "(status = 'open' AND completed_at IS NULL) OR (status = 'completed' AND completed_at IS NOT NULL)",
            name="completion_state",
        ),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.action_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="open"
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailyReviewItem(V2Base):
    __tablename__ = "daily_review_items"
    __table_args__ = (
        UniqueConstraint("daily_review_id", "plan_item_id"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('completed','skipped','not_done')",
            name="valid_outcome",
        ),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    daily_review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.daily_reviews.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.action_plan_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    outcome: Mapped[str | None] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(Text)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanRefresh(TimestampMixin, V2Base):
    __tablename__ = "plan_refreshes"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key"),
        UniqueConstraint("old_item_id"),
        UniqueConstraint("new_item_id"),
        CheckConstraint("btrim(reason) <> ''", name="nonempty_reason"),
        CheckConstraint("btrim(timezone) <> ''", name="timezone_nonempty"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app.action_plans.id", ondelete="SET NULL")
    )
    old_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app.action_plan_items.id", ondelete="SET NULL")
    )
    new_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app.action_plan_items.id", ondelete="SET NULL")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StreakLedger(V2Base):
    __tablename__ = "streak_days"
    __table_args__ = (
        UniqueConstraint("user_id", "local_date", "kind"),
        UniqueConstraint("evidence_type", "evidence_id"),
        CheckConstraint("kind = 'daily'", name="ck_streak_days_kind"),
        CheckConstraint(
            "evidence_type IN ('daily_review','freeze')",
            name="ck_streak_days_evidence",
        ),
        CheckConstraint(
            "adjudication_state IN ('earned','frozen','missed')",
            name="ck_streak_days_state",
        ),
        CheckConstraint(
            "(adjudication_state IN ('earned','missed') "
            "AND evidence_type = 'daily_review') OR "
            "(adjudication_state = 'frozen' AND evidence_type = 'freeze')",
            name="state_evidence",
        ),
        CheckConstraint("btrim(timezone) <> ''", name="timezone_nonempty"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    adjudication_state: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="earned"
    )
    earned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RewardLedger(V2Base):
    __tablename__ = "reward_ledger"
    __table_args__ = (
        UniqueConstraint("user_id", "source_type", "source_id"),
        CheckConstraint(
            "event_type IN ('grant','redeem','expire')", name="valid_event_type"
        ),
        CheckConstraint(
            "asset_type IN ('points','freeze','entitlement')",
            name="valid_asset_type",
        ),
        CheckConstraint("quantity <> 0", name="nonzero_quantity"),
        CheckConstraint(
            "(event_type = 'grant' AND quantity > 0) OR (event_type IN ('redeem','expire') AND quantity < 0)",
            name="quantity_sign",
        ),
        CheckConstraint(
            "(asset_type = 'points' AND asset_key IS NULL) "
            "OR (asset_type <> 'points' AND asset_key IS NOT NULL "
            "AND btrim(asset_key) <> '')",
            name="asset_key_presence",
        ),
        CheckConstraint(
            "asset_type <> 'entitlement' "
            "OR (event_type = 'grant' AND quantity = 1)",
            name="entitlement_quantity",
        ),
        # One claim of one entitlement per user, ever. Partial so it constrains
        # only entitlements and leaves points and freezes free to repeat.
        Index(
            "uq_reward_ledger_entitlement",
            "user_id",
            "asset_key",
            unique=True,
            postgresql_where=text("asset_type = 'entitlement'"),
        ),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Names the specific non-fungible asset that moved. NULL only for points,
    #: which are fungible and need no identity.
    asset_key: Mapped[str | None] = mapped_column(String(64))
    catalog_version: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="engagement.v1"
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Conversation(TimestampMixin, V2Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("status IN ('active','closed')", name="valid_status"),
        CheckConstraint(
            "thread_type IN ('general','care_plan','weekly_checkin','symptom_checkin','support')",
            name="valid_thread_type",
        ),
        CheckConstraint("revision > 0", name="positive_revision"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="active"
    )
    thread_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="general"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class ConversationMessage(V2Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence"),
        UniqueConstraint("conversation_id", "client_message_id"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    client_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConversationSummary(V2Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = (
        UniqueConstraint("conversation_id", "through_message_id"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    through_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.conversation_messages.id", ondelete="RESTRICT"),
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WeeklyCheckin(TimestampMixin, V2Base):
    __tablename__ = "weekly_checkins"
    __table_args__ = (
        UniqueConstraint("user_id", "week_start"),
        UniqueConstraint("conversation_id"),
        CheckConstraint(
            "EXTRACT(ISODOW FROM week_start) = 1", name="monday_week_start"
        ),
        CheckConstraint("revision > 0", name="positive_revision"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    definition_version: Mapped[str] = mapped_column(String(64), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.conversations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WeeklyQuestion(V2Base):
    __tablename__ = "weekly_checkin_questions"
    __table_args__ = (UniqueConstraint("version", "ordinal"), {"schema": APP_SCHEMA})
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    answer_type: Mapped[str] = mapped_column(String(24), nullable=False)
    answer_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class WeeklyResponse(V2Base):
    __tablename__ = "weekly_checkin_responses"
    __table_args__ = (
        UniqueConstraint("weekly_checkin_id", "question_id"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    weekly_checkin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.weekly_checkins.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.weekly_checkin_questions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResearchSource(TimestampMixin, V2Base):
    __tablename__ = "research_sources"
    __table_args__ = (
        CheckConstraint("canonical_url LIKE 'https://%'", name="https_canonical_url"),
        UniqueConstraint("source_type", "source_external_id"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pubmed"
    )
    source_external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[date | None] = mapped_column(Date)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )


class ResearchCitation(V2Base):
    __tablename__ = "action_item_citations"
    __table_args__ = (
        UniqueConstraint("source_id", "plan_item_id"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.research_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    plan_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.action_plan_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim: Mapped[str] = mapped_column(Text, nullable=False)


class AiInvocation(V2Base):
    __tablename__ = "ai_invocations"
    __table_args__ = (
        CheckConstraint("input_tokens >= 0", name="nonnegative_input_tokens"),
        CheckConstraint("output_tokens >= 0", name="nonnegative_output_tokens"),
        CheckConstraint("cost_minor >= 0", name="nonnegative_cost_minor"),
        CheckConstraint("latency_ms >= 0", name="nonnegative_latency_ms"),
        CheckConstraint(
            "result_status IN ('succeeded','failed','blocked')",
            name="valid_result_status",
        ),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app.users.id", ondelete="SET NULL")
    )
    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ops.generation_jobs.id", ondelete="SET NULL"),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default="USD"
    )
    price_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="unknown", server_default="unknown"
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_status: Mapped[str] = mapped_column(String(24), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AiEvaluation(V2Base):
    __tablename__ = "plan_evaluations"
    __table_args__ = (
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 100", name="score_range"),
        {"schema": APP_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invocation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.ai_invocations.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.action_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    evaluator: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEvent(V2Base):
    __tablename__ = "audit_events"
    __table_args__ = ({"schema": APP_SCHEMA},)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app.users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DeletionRequest(V2Base):
    __tablename__ = "deletion_requests"
    __table_args__ = (
        # A subject may make many historical requests, but never more than one
        # active request.  The partial index which enforces that rule is added
        # by the v2 retention migration (SQLAlchemy cannot express it portably
        # with a changing state predicate here).
        CheckConstraint(
            "state IN ('requested','running','retry_wait','completed','failed')",
            name="valid_state",
        ),
        CheckConstraint("subject_hash ~ '^[0-9a-f]{64}$'", name="subject_hash_format"),
        CheckConstraint("attempt_count >= 0", name="nonnegative_attempts"),
        Index(
            "uq_deletion_requests_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("state IN ('requested','running','retry_wait')"),
        ),
        Index("ix_deletion_requests_state_requested", "state", "requested_at"),
        {"schema": OPS_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app.users.id", ondelete="SET NULL")
    )
    subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="requested"
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ops.generation_jobs.id", ondelete="SET NULL"),
        unique=True,
    )
    current_step: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    verification_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )


class AccountExport(TimestampMixin, V2Base):
    """One portable-export request; object references are private, never URLs."""

    __tablename__ = "account_exports"
    __table_args__ = (
        CheckConstraint(
            "state IN ('requested','running','ready','failed','expired')",
            name="valid_state",
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            "(state = 'ready' AND storage_provider IS NOT NULL AND bucket IS NOT NULL "
            "AND object_key IS NOT NULL AND manifest_sha256 IS NOT NULL AND ready_at IS NOT NULL) "
            "OR state <> 'ready'",
            name="ready_has_private_manifest",
        ),
        CheckConstraint(
            "manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="manifest_sha256_format",
        ),
        {"schema": OPS_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ops.generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="requested"
    )
    storage_provider: Mapped[str | None] = mapped_column(String(32))
    bucket: Mapped[str | None] = mapped_column(String(128))
    object_key: Mapped[str | None] = mapped_column(String(512))
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))


class DeletionStep(V2Base):
    """Redacted, resumable proof for a fixed account-erasure step."""

    __tablename__ = "deletion_steps"
    __table_args__ = (
        UniqueConstraint("deletion_request_id", "step_name"),
        CheckConstraint(
            "step_name IN ('identity_revoked','private_storage_erased',"
            "'runtime_checkpoints_erased','cache_erased','postgres_graph_erased')",
            name="valid_step_name",
        ),
        CheckConstraint(
            "state IN ('pending','running','verified','failed')", name="valid_state"
        ),
        CheckConstraint("attempt_count >= 0", name="nonnegative_attempts"),
        CheckConstraint(
            "(state = 'verified' AND verified_at IS NOT NULL) OR "
            "(state <> 'verified' AND verified_at IS NULL)",
            name="verified_at_state",
        ),
        {"schema": OPS_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    deletion_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ops.deletion_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_name: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DeletionReceipt(V2Base):
    """A durable pseudonymous final receipt retained after app-user erasure."""

    __tablename__ = "deletion_receipts"
    __table_args__ = (
        UniqueConstraint("deletion_request_id"),
        CheckConstraint("subject_hash ~ '^[0-9a-f]{64}$'", name="subject_hash_format"),
        CheckConstraint(
            "ops.is_redacted_deletion_summary(verification_summary)",
            name="redacted_summary",
        ),
        {"schema": OPS_SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    deletion_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ops.deletion_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    verification_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
