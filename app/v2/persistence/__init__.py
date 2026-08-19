"""SQLAlchemy persistence adapter for AUVRA v2."""

from app.v2.persistence.base import V2Base
from app.v2.persistence.models import (
    ActionPlan,
    ActionPlanItem,
    ActionPlanItemVariant,
    ConsentRecord,
    GenerationJob,
    IdempotencyRecord,
    MediaAsset,
    OnboardingAssessment,
    OnboardingSession,
    OutboxEvent,
    User,
    UserProfile,
)
from app.v2.persistence.models_observations import UserObservation
from app.v2.persistence.models_engagement import (
    ActionEvent,
    AiEvaluation,
    AiInvocation,
    AuditEvent,
    Conversation,
    ConversationMessage,
    ConversationSummary,
    DailyReview,
    DailyReviewItem,
    AccountExport,
    DeletionReceipt,
    DeletionRequest,
    DeletionStep,
    PlanRefresh,
    ResearchCitation,
    ResearchSource,
    RewardLedger,
    StreakLedger,
    WeeklyCheckin,
    WeeklyQuestion,
    WeeklyResponse,
)

# The migrations are the physical-schema contract.  Keep explicit metadata
# parity here so ``alembic check`` detects a real drift rather than proposing
# to rename established constraints/indexes or remove database-side defaults.
from sqlalchemy import DefaultClause, Index, UniqueConstraint, text


_DEFAULTS = {
    ("app", "action_item_events", "payload"): "'{}'::jsonb",
    ("app", "ai_invocations", "input_tokens"): "0",
    ("app", "ai_invocations", "output_tokens"): "0",
    ("app", "ai_invocations", "cost_minor"): "0",
    ("app", "ai_invocations", "latency_ms"): "0",
    ("app", "audit_events", "data"): "'{}'::jsonb",
    ("app", "conversation_messages", "metadata_json"): "'{}'::jsonb",
    ("app", "plan_evaluations", "result"): "'{}'::jsonb",
    ("app", "research_sources", "metadata_json"): "'{}'::jsonb",
    ("ops", "deletion_receipts", "verification_summary"): "'{}'::jsonb",
    ("ops", "deletion_requests", "verification_summary"): "'{}'::jsonb",
}

_UNIQUE_NAMES = {
    (
        "app",
        "action_item_citations",
        ("source_id", "plan_item_id"),
    ): "uq_action_item_citations_source_item",
    (
        "app",
        "action_item_events",
        ("user_id", "client_event_id"),
    ): "uq_action_item_events_user_client",
    (
        "app",
        "conversation_messages",
        ("conversation_id", "sequence"),
    ): "uq_conversation_messages_sequence",
    (
        "app",
        "conversation_messages",
        ("conversation_id", "client_message_id"),
    ): "uq_conversation_messages_client",
    (
        "app",
        "conversation_summaries",
        ("conversation_id", "through_message_id"),
    ): "uq_conversation_summaries_through",
    (
        "app",
        "daily_review_items",
        ("daily_review_id", "plan_item_id"),
    ): "uq_daily_review_items_review_item",
    ("app", "daily_reviews", ("plan_id",)): "uq_daily_reviews_plan",
    (
        "app",
        "plan_refreshes",
        ("user_id", "idempotency_key"),
    ): "uq_plan_refreshes_user_key",
    (
        "app",
        "research_sources",
        ("source_type", "source_external_id"),
    ): "uq_research_sources_type_external",
    ("app", "research_sources", ("canonical_url",)): "uq_research_sources_url",
    (
        "app",
        "reward_ledger",
        ("user_id", "source_type", "source_id"),
    ): "uq_reward_ledger_source",
    (
        "app",
        "streak_days",
        ("user_id", "local_date", "kind"),
    ): "uq_streak_days_user_day_kind",
    (
        "app",
        "weekly_checkin_questions",
        ("version", "ordinal"),
    ): "uq_weekly_questions_version_ordinal",
    (
        "app",
        "weekly_checkin_responses",
        ("weekly_checkin_id", "question_id"),
    ): "uq_weekly_responses_checkin_question",
    (
        "app",
        "weekly_checkins",
        ("conversation_id",),
    ): "uq_weekly_checkins_conversation",
    (
        "app",
        "weekly_checkins",
        ("user_id", "week_start"),
    ): "uq_weekly_checkins_user_week",
    (
        "ops",
        "account_exports",
        ("generation_job_id",),
    ): "uq_account_exports_generation_job",
    (
        "ops",
        "deletion_receipts",
        ("deletion_request_id",),
    ): "uq_deletion_receipts_request",
    (
        "ops",
        "deletion_requests",
        ("generation_job_id",),
    ): "uq_deletion_requests_generation_job",
    (
        "ops",
        "deletion_steps",
        ("deletion_request_id", "step_name"),
    ): "uq_deletion_steps_request_step",
}

_INDEXES = (
    (
        "app",
        "action_item_events",
        ("user_id", "decision_local_date"),
        "ix_action_item_events_user_decision_day",
    ),
    (
        "app",
        "action_item_events",
        ("user_id", "occurred_at"),
        "ix_action_item_events_user_occurred",
    ),
    ("app", "conversations", ("user_id",), "ix_conversations_user"),
    (
        "app",
        "reward_ledger",
        ("user_id", "asset_type", "created_at"),
        "ix_reward_ledger_user_id_asset_type_created_at",
    ),
    (
        "app",
        "plan_refreshes",
        ("user_id", "local_date"),
        "ix_plan_refreshes_accepted_day",
    ),
    (
        "app",
        "user_observations",
        ("user_id", "code", "observed_at"),
        "ix_user_observations_user_code_time",
    ),
    (
        "app",
        "user_observations",
        ("user_id", "observation_type", "observed_local_date"),
        "ix_user_observations_type_day",
    ),
    (
        "ops",
        "account_exports",
        ("state", "expires_at"),
        "ix_account_exports_state_expires",
    ),
    (
        "ops",
        "deletion_steps",
        ("deletion_request_id", "state"),
        "ix_deletion_steps_request_state",
    ),
)

_UNINTENDED_INDEXES = (
    ("app", "action_item_events", "ix_action_item_events_user_id"),
    ("app", "conversations", "ix_conversations_user_id"),
    ("app", "user_observations", "ix_user_observations_user_id"),
)


def _apply_defaults(tables) -> None:
    for table in tables.values():
        if "id" in table.c:
            table.c.id.server_default = DefaultClause(text("gen_random_uuid()"))
    for (schema, table_name, column), value in _DEFAULTS.items():
        tables[f"{schema}.{table_name}"].c[column].server_default = DefaultClause(text(value))


def _rename_unique_constraints(tables) -> None:
    for (schema, table_name, columns), name in _UNIQUE_NAMES.items():
        for constraint in tables[f"{schema}.{table_name}"].constraints:
            if (
                isinstance(constraint, UniqueConstraint)
                and tuple(column.name for column in constraint.columns) == columns
            ):
                constraint.name = name


def _align_indexes(tables) -> None:
    for schema, table_name, columns, name in _INDEXES:
        table = tables[f"{schema}.{table_name}"]
        if not any(index.name == name for index in table.indexes):
            Index(name, *(table.c[column] for column in columns))
    for schema, table_name, index_name in _UNINTENDED_INDEXES:
        table = tables[f"{schema}.{table_name}"]
        for index in tuple(table.indexes):
            if index.name == index_name:
                table.indexes.remove(index)


def _align_metadata_to_head() -> None:
    tables = V2Base.metadata.tables
    _apply_defaults(tables)
    _rename_unique_constraints(tables)
    _align_indexes(tables)


_align_metadata_to_head()

__all__ = [
    "V2Base",
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
    "ActionEvent",
    "DailyReview",
    "DailyReviewItem",
    "PlanRefresh",
    "StreakLedger",
    "RewardLedger",
    "Conversation",
    "ConversationMessage",
    "ConversationSummary",
    "WeeklyCheckin",
    "WeeklyQuestion",
    "WeeklyResponse",
    "UserObservation",
    "ResearchSource",
    "ResearchCitation",
    "AiInvocation",
    "AiEvaluation",
    "AuditEvent",
    "DeletionRequest",
    "DeletionStep",
    "DeletionReceipt",
    "AccountExport",
]
