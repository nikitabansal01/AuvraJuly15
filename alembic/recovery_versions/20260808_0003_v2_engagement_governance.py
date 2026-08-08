"""Canonical engagement and governance tables (no legacy public schema).

Revision ID: 20260808_0003
Revises: 20260801_0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20260808_0003"
down_revision = "20260801_0002"
branch_labels = depends_on = None


def id_column():
    return sa.Column(
        "id",
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def created():
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.create_table(
        "action_item_events",
        id_column(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.action_plan_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("client_event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id", "client_event_id", name="uq_action_item_events_user_client"
        ),
        sa.CheckConstraint(
            "event_type IN ('completed','skipped','feedback')",
            name="ck_action_item_events_type",
        ),
        schema="app",
    )
    op.create_index(
        "ix_action_item_events_user_occurred",
        "action_item_events",
        ["user_id", "occurred_at"],
        schema="app",
    )
    op.create_table(
        "daily_reviews",
        id_column(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.action_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        created(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("plan_id", name="uq_daily_reviews_plan"),
        sa.CheckConstraint(
            "status IN ('open','completed')", name="ck_daily_reviews_status"
        ),
        sa.CheckConstraint(
            "(status = 'open' AND completed_at IS NULL) OR (status = 'completed' AND completed_at IS NOT NULL)",
            name="ck_daily_reviews_completion_state",
        ),
        schema="app",
    )
    op.create_table(
        "daily_review_items",
        id_column(),
        sa.Column(
            "daily_review_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.daily_reviews.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.action_plan_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(24)),
        sa.Column("note", sa.Text()),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "daily_review_id", "plan_item_id", name="uq_daily_review_items_review_item"
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('completed','skipped','not_done')",
            name="ck_daily_review_items_outcome",
        ),
        schema="app",
    )
    op.create_table(
        "plan_refreshes",
        id_column(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "completed_plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.action_plans.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "old_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.action_plan_items.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "new_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.action_plan_items.id", ondelete="SET NULL"),
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        created(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_plan_refreshes_user_key"
        ),
        schema="app",
    )
    op.create_table(
        "streak_days",
        id_column(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("evidence_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "adjudication_state", sa.String(24), nullable=False, server_default="earned"
        ),
        sa.Column(
            "earned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id", "local_date", "kind", name="uq_streak_days_user_day_kind"
        ),
        sa.CheckConstraint("kind IN ('daily')", name="ck_streak_days_kind"),
        sa.CheckConstraint(
            "evidence_type IN ('event','review','freeze')",
            name="ck_streak_days_evidence",
        ),
        sa.CheckConstraint(
            "adjudication_state IN ('earned','frozen','revoked')",
            name="ck_streak_days_state",
        ),
        schema="app",
    )
    op.create_table(
        "reward_ledger",
        id_column(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("asset_type", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        created(),
        sa.UniqueConstraint(
            "user_id", "source_type", "source_id", name="uq_reward_ledger_source"
        ),
        sa.CheckConstraint(
            "event_type IN ('grant','redeem','expire')", name="ck_reward_ledger_event"
        ),
        sa.CheckConstraint(
            "asset_type IN ('points','freeze')", name="ck_reward_ledger_asset"
        ),
        sa.CheckConstraint("quantity <> 0", name="ck_reward_ledger_quantity"),
        sa.CheckConstraint(
            "(event_type = 'grant' AND quantity > 0) OR (event_type IN ('redeem','expire') AND quantity < 0)",
            name="ck_reward_ledger_quantity_sign",
        ),
        schema="app",
    )
    _conversations()
    _weekly()
    _evidence_ai()
    _retention()
    _review_completion_trigger()


def _conversations() -> None:
    op.create_table(
        "conversations",
        id_column(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column(
            "thread_type", sa.String(32), nullable=False, server_default="general"
        ),
        created(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('active','closed')", name="ck_conversations_status"
        ),
        sa.CheckConstraint(
            "thread_type IN ('general','care_plan','weekly_checkin','symptom_checkin','support')",
            name="ck_conversations_type",
        ),
        schema="app",
    )
    op.create_index("ix_conversations_user", "conversations", ["user_id"], schema="app")
    op.create_table(
        "conversation_messages",
        id_column(),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("client_message_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        created(),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_conversation_messages_sequence"
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_conversation_messages_client",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_conversation_messages_sequence"),
        sa.CheckConstraint(
            "role IN ('user','assistant','system')",
            name="ck_conversation_messages_role",
        ),
        schema="app",
    )
    op.create_table(
        "conversation_summaries",
        id_column(),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "through_message_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.conversation_messages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        created(),
        sa.UniqueConstraint(
            "conversation_id",
            "through_message_id",
            name="uq_conversation_summaries_through",
        ),
        schema="app",
    )


def _weekly() -> None:
    op.create_table(
        "weekly_checkins",
        id_column(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("definition_version", sa.String(64), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        created(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id", "week_start", name="uq_weekly_checkins_user_week"
        ),
        sa.CheckConstraint(
            "EXTRACT(ISODOW FROM week_start) = 1", name="ck_weekly_checkins_monday"
        ),
        schema="app",
    )
    op.create_table(
        "weekly_checkin_questions",
        id_column(),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "version", "ordinal", name="uq_weekly_questions_version_ordinal"
        ),
        sa.CheckConstraint("ordinal > 0", name="ck_weekly_questions_ordinal"),
        schema="app",
    )
    op.create_table(
        "weekly_checkin_responses",
        id_column(),
        sa.Column(
            "weekly_checkin_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.weekly_checkins.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.weekly_checkin_questions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("answer", JSONB, nullable=False),
        sa.UniqueConstraint(
            "weekly_checkin_id",
            "question_id",
            name="uq_weekly_responses_checkin_question",
        ),
        schema="app",
    )
    op.create_table(
        "symptom_observations",
        id_column(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symptom_code", sa.String(64), nullable=False),
        sa.Column("severity", sa.Integer()),
        sa.Column("note", sa.Text()),
        sa.CheckConstraint(
            "severity IS NULL OR severity BETWEEN 0 AND 10",
            name="ck_symptom_observations_severity",
        ),
        schema="app",
    )
    op.create_index(
        "ix_symptom_observations_user_time",
        "symptom_observations",
        ["user_id", "observed_at"],
        schema="app",
    )


def _evidence_ai() -> None:
    op.create_table(
        "research_sources",
        id_column(),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("published_at", sa.Date()),
        sa.Column(
            "metadata_json",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        created(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("canonical_url", name="uq_research_sources_url"),
        sa.CheckConstraint(
            "canonical_url LIKE 'https://%'", name="ck_research_sources_url"
        ),
        schema="app",
    )
    op.create_table(
        "action_item_citations",
        id_column(),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.research_sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "plan_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.action_plan_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "source_id", "plan_item_id", name="uq_action_item_citations_source_item"
        ),
        schema="app",
    )
    op.create_table(
        "ai_invocations",
        id_column(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.users.id", ondelete="SET NULL"),
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("task", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_status", sa.String(24), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64)),
        created(),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cost_minor >= 0 AND latency_ms >= 0",
            name="ck_ai_invocations_values",
        ),
        sa.CheckConstraint(
            "result_status IN ('succeeded','failed','blocked')",
            name="ck_ai_invocations_status",
        ),
        schema="app",
    )
    op.create_table(
        "plan_evaluations",
        id_column(),
        sa.Column(
            "invocation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.ai_invocations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.action_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evaluator", sa.String(64), nullable=False),
        sa.Column("evaluator_version", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("score", sa.Integer()),
        sa.Column(
            "result", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        created(),
        sa.CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 100", name="ck_plan_evaluations_score"
        ),
        schema="app",
    )


def _retention() -> None:
    op.create_table(
        "audit_events",
        id_column(),
        sa.Column(
            "actor_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.users.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True)),
        sa.Column("data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        created(),
        schema="app",
    )
    op.create_table(
        "deletion_requests",
        id_column(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.users.id", ondelete="SET NULL"),
        ),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="requested"),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "subject_hash", "state", name="uq_deletion_requests_subject_state"
        ),
        sa.CheckConstraint(
            "state IN ('requested','running','completed','failed')",
            name="ck_deletion_requests_state",
        ),
        schema="ops",
    )


def _review_completion_trigger() -> None:
    op.execute(
        """
        CREATE FUNCTION app.assert_completed_review(p_review_id uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
          v_status text;
          v_plan_id uuid;
          v_user_id uuid;
          v_plan_count integer;
          v_review_count integer;
          v_valid_count integer;
        BEGIN
          SELECT status, plan_id, user_id INTO v_status, v_plan_id, v_user_id
          FROM app.daily_reviews WHERE id = p_review_id;
          IF NOT FOUND OR v_status <> 'completed' THEN
            RETURN;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM app.action_plans
            WHERE id = v_plan_id AND user_id = v_user_id
          ) THEN
            RAISE EXCEPTION 'completed review must be owned by its plan owner'
              USING ERRCODE = '23514';
          END IF;
          SELECT count(*) INTO v_plan_count
          FROM app.action_plan_items WHERE plan_id = v_plan_id;
          SELECT count(*) INTO v_review_count
          FROM app.daily_review_items WHERE daily_review_id = p_review_id;
          SELECT count(*) INTO v_valid_count
          FROM app.daily_review_items review_item
          JOIN app.action_plan_items plan_item ON plan_item.id = review_item.plan_item_id
          WHERE review_item.daily_review_id = p_review_id
            AND plan_item.plan_id = v_plan_id
            AND review_item.outcome IS NOT NULL
            AND review_item.answered_at IS NOT NULL;
          IF v_review_count <> v_plan_count OR v_valid_count <> v_plan_count THEN
            RAISE EXCEPTION 'completed review must answer exactly its plan items'
              USING ERRCODE = '23514';
          END IF;
        END;
        $$;

        CREATE FUNCTION app.check_review_header() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM app.assert_completed_review(NEW.id);
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION app.check_review_item() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            PERFORM app.assert_completed_review(OLD.daily_review_id);
          END IF;
          IF TG_OP <> 'DELETE' THEN
            PERFORM app.assert_completed_review(NEW.daily_review_id);
          END IF;
          RETURN COALESCE(NEW, OLD);
        END;
        $$;

        CREATE FUNCTION app.check_reviewed_plan_item() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          v_review_id uuid;
          v_item_id uuid;
          v_old_plan_id uuid;
          v_new_plan_id uuid;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            v_item_id := NEW.id;
            v_new_plan_id := NEW.plan_id;
          ELSE
            v_item_id := OLD.id;
            v_old_plan_id := OLD.plan_id;
            IF TG_OP = 'UPDATE' THEN
              v_new_plan_id := NEW.plan_id;
            END IF;
          END IF;
          FOR v_review_id IN
            SELECT DISTINCT review.id
            FROM app.daily_reviews review
            LEFT JOIN app.daily_review_items review_item
              ON review_item.daily_review_id = review.id
            WHERE review.plan_id = v_old_plan_id
               OR review.plan_id = v_new_plan_id
               OR review_item.plan_item_id = v_item_id
          LOOP
            PERFORM app.assert_completed_review(v_review_id);
          END LOOP;
          RETURN COALESCE(NEW, OLD);
        END;
        $$;

        CREATE FUNCTION app.guard_completed_review_item() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          v_review_id uuid;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            v_review_id := NEW.daily_review_id;
          ELSE
            v_review_id := OLD.daily_review_id;
          END IF;
          IF EXISTS (
            SELECT 1 FROM app.daily_reviews
            WHERE id = v_review_id AND status = 'completed'
          ) THEN
            RAISE EXCEPTION 'completed review items are immutable'
              USING ERRCODE = '55000';
          END IF;
          RETURN COALESCE(NEW, OLD);
        END;
        $$;

        CREATE FUNCTION app.guard_reviewed_plan_item() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          v_item_id uuid;
          v_old_plan_id uuid;
          v_new_plan_id uuid;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            v_item_id := NEW.id;
            v_new_plan_id := NEW.plan_id;
          ELSE
            v_item_id := OLD.id;
            v_old_plan_id := OLD.plan_id;
            IF TG_OP = 'UPDATE' THEN
              v_new_plan_id := NEW.plan_id;
            END IF;
          END IF;
          IF EXISTS (
            SELECT 1 FROM app.daily_reviews review
            LEFT JOIN app.daily_review_items review_item
              ON review_item.daily_review_id = review.id
            WHERE review.status = 'completed'
              AND (
                review.plan_id = v_old_plan_id
                OR review.plan_id = v_new_plan_id
                OR review_item.plan_item_id = v_item_id
              )
          ) THEN
            RAISE EXCEPTION 'reviewed plan membership is immutable'
              USING ERRCODE = '55000';
          END IF;
          RETURN COALESCE(NEW, OLD);
        END;
        $$;

        CREATE FUNCTION app.guard_completed_review_header() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status = 'completed' AND (
            NEW.user_id IS DISTINCT FROM OLD.user_id
            OR NEW.plan_id IS DISTINCT FROM OLD.plan_id
            OR NEW.local_date IS DISTINCT FROM OLD.local_date
            OR NEW.timezone IS DISTINCT FROM OLD.timezone
            OR NEW.status IS DISTINCT FROM OLD.status
            OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
          ) THEN
            RAISE EXCEPTION 'completed review header is immutable'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER ck_completed_review_coverage
        AFTER INSERT OR UPDATE ON app.daily_reviews
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION app.check_review_header();

        CREATE CONSTRAINT TRIGGER ck_completed_review_items_coverage
        AFTER INSERT OR UPDATE OR DELETE ON app.daily_review_items
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION app.check_review_item();

        CREATE CONSTRAINT TRIGGER ck_reviewed_plan_items_coverage
        AFTER INSERT OR UPDATE OR DELETE ON app.action_plan_items
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION app.check_reviewed_plan_item();

        CREATE TRIGGER guard_completed_review_items
        BEFORE INSERT OR UPDATE OR DELETE ON app.daily_review_items
        FOR EACH ROW EXECUTE FUNCTION app.guard_completed_review_item();

        CREATE TRIGGER guard_reviewed_plan_items
        BEFORE INSERT OR UPDATE OF plan_id OR DELETE ON app.action_plan_items
        FOR EACH ROW EXECUTE FUNCTION app.guard_reviewed_plan_item();

        CREATE TRIGGER guard_completed_review_header
        BEFORE UPDATE OF user_id, plan_id, local_date, timezone, status, completed_at
        ON app.daily_reviews
        FOR EACH ROW EXECUTE FUNCTION app.guard_completed_review_header();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS guard_completed_review_header ON app.daily_reviews"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS guard_reviewed_plan_items ON app.action_plan_items"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS guard_completed_review_items ON app.daily_review_items"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS ck_reviewed_plan_items_coverage "
        "ON app.action_plan_items"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS ck_completed_review_items_coverage "
        "ON app.daily_review_items"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS ck_completed_review_coverage ON app.daily_reviews"
    )
    op.execute("DROP FUNCTION IF EXISTS app.check_reviewed_plan_item()")
    op.execute("DROP FUNCTION IF EXISTS app.guard_reviewed_plan_item()")
    op.execute("DROP FUNCTION IF EXISTS app.guard_completed_review_item()")
    op.execute("DROP FUNCTION IF EXISTS app.guard_completed_review_header()")
    op.execute("DROP FUNCTION IF EXISTS app.check_review_item()")
    op.execute("DROP FUNCTION IF EXISTS app.check_review_header()")
    op.execute("DROP FUNCTION IF EXISTS app.assert_completed_review(uuid)")
    for schema, table in (
        ("ops", "deletion_requests"),
        ("app", "audit_events"),
        ("app", "plan_evaluations"),
        ("app", "ai_invocations"),
        ("app", "action_item_citations"),
        ("app", "research_sources"),
        ("app", "symptom_observations"),
        ("app", "weekly_checkin_responses"),
        ("app", "weekly_checkin_questions"),
        ("app", "weekly_checkins"),
        ("app", "conversation_summaries"),
        ("app", "conversation_messages"),
        ("app", "conversations"),
        ("app", "reward_ledger"),
        ("app", "streak_days"),
        ("app", "plan_refreshes"),
        ("app", "daily_review_items"),
        ("app", "daily_reviews"),
        ("app", "action_item_events"),
    ):
        op.drop_table(table, schema=schema)
