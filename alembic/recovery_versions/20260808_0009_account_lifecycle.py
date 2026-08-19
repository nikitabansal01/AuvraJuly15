"""Make account export and erasure durable, private, and resumable.

Revision ID: 20260808_0009
Revises: 20260808_0008

This migration deliberately creates one narrowly scoped trusted erasure
procedure.  It is not a generic FK/trigger bypass: it accepts only a running
deletion request bound to the supplied user, sets a transaction-local flag for
the existing immutability guards, and leaves a pseudonymous receipt behind.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "20260808_0009"
down_revision = "20260808_0008"
branch_labels = depends_on = None


def upgrade() -> None:
    _extend_deletion_requests()
    _add_outbox_owner()
    _create_export_and_receipt_tables()
    _replace_immutable_guards_for_trusted_erasure()
    _create_trusted_erasure_procedure()


def _extend_deletion_requests() -> None:
    op.drop_constraint("uq_deletion_requests_subject_state", "deletion_requests", schema="ops")
    op.drop_constraint(
        "ck_deletion_requests_ck_deletion_requests_state",
        "deletion_requests",
        schema="ops",
    )
    op.add_column(
        "deletion_requests",
        sa.Column("generation_job_id", UUID(as_uuid=True)),
        schema="ops",
    )
    op.add_column("deletion_requests", sa.Column("current_step", sa.String(64)), schema="ops")
    op.add_column(
        "deletion_requests",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        schema="ops",
    )
    op.add_column("deletion_requests", sa.Column("last_error_code", sa.String(64)), schema="ops")
    op.add_column(
        "deletion_requests",
        sa.Column(
            "verification_summary",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="ops",
    )
    op.create_foreign_key(
        "fk_deletion_requests_generation_job",
        "deletion_requests",
        "generation_jobs",
        ["generation_job_id"],
        ["id"],
        source_schema="ops",
        referent_schema="ops",
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_deletion_requests_generation_job",
        "deletion_requests",
        ["generation_job_id"],
        schema="ops",
    )
    op.create_check_constraint(
        "ck_deletion_requests_state",
        "deletion_requests",
        "state IN ('requested','running','retry_wait','completed','failed')",
        schema="ops",
    )
    op.create_check_constraint(
        "ck_deletion_requests_subject_hash_format",
        "deletion_requests",
        "subject_hash ~ '^[0-9a-f]{64}$'",
        schema="ops",
    )
    op.create_check_constraint(
        "ck_deletion_requests_nonnegative_attempts",
        "deletion_requests",
        "attempt_count >= 0",
        schema="ops",
    )
    op.create_index(
        "uq_deletion_requests_active_user",
        "deletion_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('requested','running','retry_wait')"),
        schema="ops",
    )
    op.create_index(
        "ix_deletion_requests_state_requested",
        "deletion_requests",
        ["state", "requested_at"],
        schema="ops",
    )


def _add_outbox_owner() -> None:
    """Give every new user-owned event a deterministic erasure owner."""

    op.add_column("outbox_events", sa.Column("owner_user_id", UUID(as_uuid=True)), schema="ops")
    op.create_foreign_key(
        "fk_outbox_events_owner_user",
        "outbox_events",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema="ops",
        referent_schema="app",
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_outbox_events_owner_user_id",
        "outbox_events",
        ["owner_user_id"],
        schema="ops",
    )


def _create_export_and_receipt_tables() -> None:
    op.execute(
        """
        CREATE FUNCTION ops.is_redacted_deletion_summary(p_summary jsonb) RETURNS boolean
        IMMUTABLE LANGUAGE sql AS $$
          SELECT jsonb_typeof(p_summary) = 'object'
             AND p_summary->>'verified_step_count' = '5'
             AND p_summary->>'receipt_version' = 'v1'
             AND (p_summary - 'verified_step_count' - 'receipt_version') = '{}'::jsonb;
        $$;
        """
    )
    op.create_table(
        "account_exports",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "generation_job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ops.generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(24), nullable=False, server_default="requested"),
        sa.Column("storage_provider", sa.String(32)),
        sa.Column("bucket", sa.String(128)),
        sa.Column("object_key", sa.String(512)),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("generation_job_id", name="uq_account_exports_generation_job"),
        sa.CheckConstraint(
            "state IN ('requested','running','ready','failed','expired')",
            name="ck_account_exports_state",
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_account_exports_expiry_after_creation"
        ),
        sa.CheckConstraint(
            "(state = 'ready' AND storage_provider IS NOT NULL AND bucket IS NOT NULL "
            "AND object_key IS NOT NULL AND manifest_sha256 IS NOT NULL AND ready_at IS NOT NULL) "
            "OR state <> 'ready'",
            name="ck_account_exports_ready_has_private_manifest",
        ),
        sa.CheckConstraint(
            "manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_account_exports_manifest_sha256_format",
        ),
        schema="ops",
    )

    op.create_index("ix_account_exports_user_id", "account_exports", ["user_id"], schema="ops")
    op.create_index(
        "ix_account_exports_state_expires",
        "account_exports",
        ["state", "expires_at"],
        schema="ops",
    )
    op.create_table(
        "deletion_steps",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "deletion_request_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ops.deletion_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_name", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "deletion_request_id", "step_name", name="uq_deletion_steps_request_step"
        ),
        sa.CheckConstraint(
            "step_name IN ('identity_revoked','private_storage_erased','runtime_checkpoints_erased','cache_erased','postgres_graph_erased')",
            name="ck_deletion_steps_name",
        ),
        sa.CheckConstraint(
            "state IN ('pending','running','verified','failed')",
            name="ck_deletion_steps_state",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_deletion_steps_nonnegative_attempts"),
        sa.CheckConstraint(
            "(state = 'verified' AND verified_at IS NOT NULL) OR (state <> 'verified' AND verified_at IS NULL)",
            name="ck_deletion_steps_verified_at",
        ),
        schema="ops",
    )
    op.create_index(
        "ix_deletion_steps_request_state",
        "deletion_steps",
        ["deletion_request_id", "state"],
        schema="ops",
    )
    op.create_table(
        "deletion_receipts",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "deletion_request_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ops.deletion_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column(
            "verification_summary",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("deletion_request_id", name="uq_deletion_receipts_request"),
        sa.CheckConstraint(
            "subject_hash ~ '^[0-9a-f]{64}$'",
            name="ck_deletion_receipts_subject_hash_format",
        ),
        sa.CheckConstraint(
            "ops.is_redacted_deletion_summary(verification_summary)",
            name="ck_deletion_receipts_redacted_summary",
        ),
        schema="ops",
    )


def _replace_immutable_guards_for_trusted_erasure() -> None:
    # Only the transaction-local flag set inside ops.erase_account_graph can
    # pass these guards; ordinary application mutations remain immutable.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.guard_completed_review_item() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE v_review_id uuid;
        BEGIN
          IF current_setting('app.trusted_erasure', true) = 'on' THEN RETURN COALESCE(NEW, OLD); END IF;
          IF TG_OP = 'INSERT' THEN v_review_id := NEW.daily_review_id; ELSE v_review_id := OLD.daily_review_id; END IF;
          IF EXISTS (SELECT 1 FROM app.daily_reviews WHERE id = v_review_id AND status = 'completed') THEN
            RAISE EXCEPTION 'completed review items are immutable' USING ERRCODE = '55000';
          END IF;
          RETURN COALESCE(NEW, OLD);
        END; $$;
        CREATE OR REPLACE FUNCTION app.guard_reviewed_plan_item() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE v_item_id uuid; v_old_plan_id uuid; v_new_plan_id uuid;
        BEGIN
          IF current_setting('app.trusted_erasure', true) = 'on' THEN RETURN COALESCE(NEW, OLD); END IF;
          IF TG_OP = 'INSERT' THEN v_item_id := NEW.id; v_new_plan_id := NEW.plan_id;
          ELSE v_item_id := OLD.id; v_old_plan_id := OLD.plan_id; IF TG_OP = 'UPDATE' THEN v_new_plan_id := NEW.plan_id; END IF; END IF;
          IF EXISTS (SELECT 1 FROM app.daily_reviews review LEFT JOIN app.daily_review_items review_item ON review_item.daily_review_id = review.id
                     WHERE review.status = 'completed' AND (review.plan_id = v_old_plan_id OR review.plan_id = v_new_plan_id OR review_item.plan_item_id = v_item_id)) THEN
            RAISE EXCEPTION 'reviewed plan membership is immutable' USING ERRCODE = '55000';
          END IF;
          RETURN COALESCE(NEW, OLD);
        END; $$;
        CREATE OR REPLACE FUNCTION app.guard_completed_review_header() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF current_setting('app.trusted_erasure', true) = 'on' THEN RETURN NEW; END IF;
          IF OLD.status = 'completed' AND (NEW.user_id IS DISTINCT FROM OLD.user_id OR NEW.plan_id IS DISTINCT FROM OLD.plan_id OR NEW.local_date IS DISTINCT FROM OLD.local_date OR NEW.timezone IS DISTINCT FROM OLD.timezone OR NEW.status IS DISTINCT FROM OLD.status OR NEW.completed_at IS DISTINCT FROM OLD.completed_at) THEN
            RAISE EXCEPTION 'completed review header is immutable' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END; $$;
        """
    )


def _create_trusted_erasure_procedure() -> None:
    op.execute(
        """
        CREATE FUNCTION ops.erase_account_graph(p_request_id uuid, p_user_id uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE v_subject_hash text; v_verified integer;
        BEGIN
          SELECT subject_hash INTO v_subject_hash FROM ops.deletion_requests
           WHERE id = p_request_id AND user_id = p_user_id AND state IN ('requested','running','retry_wait') FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'trusted erasure requires its active bound deletion request' USING ERRCODE = '23514'; END IF;
          SELECT count(*) INTO v_verified FROM ops.deletion_steps WHERE deletion_request_id = p_request_id AND state = 'verified';
          IF v_verified <> 4 OR EXISTS (SELECT 1 FROM ops.deletion_steps WHERE deletion_request_id = p_request_id AND step_name = 'postgres_graph_erased' AND state = 'verified') THEN
            RAISE EXCEPTION 'trusted erasure requires four completed external steps' USING ERRCODE = '23514';
          END IF;
          PERFORM set_config('app.trusted_erasure', 'on', true);
          UPDATE ops.deletion_requests SET state = 'running', current_step = 'postgres_graph_erased', last_error_code = NULL WHERE id = p_request_id;
          DELETE FROM ops.outbox_events event
           WHERE event.owner_user_id = p_user_id
              OR event.aggregate_id = p_request_id
              OR (event.owner_user_id IS NULL AND (
                event.aggregate_id IN (SELECT id FROM ops.generation_jobs WHERE user_id = p_user_id)
                OR event.aggregate_id IN (SELECT id FROM app.action_plans WHERE user_id = p_user_id)
                OR event.aggregate_id IN (SELECT id FROM app.conversations WHERE user_id = p_user_id)
              ));
          DELETE FROM app.conversation_summaries summary USING app.conversations conversation WHERE summary.conversation_id = conversation.id AND conversation.user_id = p_user_id;
          DELETE FROM app.weekly_checkins WHERE user_id = p_user_id;
          DELETE FROM app.conversations WHERE user_id = p_user_id;
          DELETE FROM app.daily_review_items item USING app.daily_reviews review WHERE item.daily_review_id = review.id AND review.user_id = p_user_id;
          DELETE FROM app.daily_reviews WHERE user_id = p_user_id;
          DELETE FROM app.action_item_events WHERE user_id = p_user_id;
          DELETE FROM app.action_plans WHERE user_id = p_user_id;
          DELETE FROM ops.idempotency_keys
           WHERE subject = p_user_id::text
              OR subject IN (SELECT id::text FROM app.onboarding_sessions WHERE claimed_user_id = p_user_id);
          DELETE FROM app.onboarding_sessions WHERE claimed_user_id = p_user_id;
          DELETE FROM app.media_assets WHERE owner_user_id = p_user_id;
          DELETE FROM ops.account_exports WHERE user_id = p_user_id;
          UPDATE app.audit_events SET actor_user_id = NULL WHERE actor_user_id = p_user_id;
          UPDATE ops.deletion_requests SET generation_job_id = NULL WHERE id = p_request_id;
          DELETE FROM app.users WHERE id = p_user_id;
          UPDATE ops.deletion_steps SET state = 'verified', verified_at = now(), error_code = NULL WHERE deletion_request_id = p_request_id AND step_name = 'postgres_graph_erased';
          INSERT INTO ops.deletion_receipts (id, deletion_request_id, subject_hash, verification_summary)
            VALUES (gen_random_uuid(), p_request_id, v_subject_hash, jsonb_build_object('verified_step_count', 5, 'receipt_version', 'v1'));
          UPDATE ops.deletion_requests SET user_id = NULL, generation_job_id = NULL, state = 'completed', completed_at = now(), current_step = NULL,
            verification_summary = jsonb_build_object('verified_step_count', 5, 'receipt_version', 'v1') WHERE id = p_request_id;
        END; $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM ops.account_exports) OR EXISTS (SELECT 1 FROM ops.deletion_steps)
             OR EXISTS (SELECT 1 FROM ops.deletion_receipts)
             OR EXISTS (SELECT 1 FROM ops.deletion_requests WHERE generation_job_id IS NOT NULL OR attempt_count <> 0) THEN
            RAISE EXCEPTION 'cannot downgrade account lifecycle schema after lifecycle facts exist' USING ERRCODE = '23514';
          END IF;
        END $$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS ops.erase_account_graph(uuid, uuid)")
    op.drop_table("deletion_receipts", schema="ops")
    op.execute("DROP FUNCTION IF EXISTS ops.is_redacted_deletion_summary(jsonb)")
    op.drop_index("ix_deletion_steps_request_state", "deletion_steps", schema="ops")
    op.drop_table("deletion_steps", schema="ops")
    op.drop_index("ix_account_exports_state_expires", "account_exports", schema="ops")
    op.drop_index("ix_account_exports_user_id", "account_exports", schema="ops")
    op.drop_table("account_exports", schema="ops")
    op.drop_index("ix_outbox_events_owner_user_id", "outbox_events", schema="ops")
    op.drop_constraint(
        "fk_outbox_events_owner_user", "outbox_events", schema="ops", type_="foreignkey"
    )
    op.drop_column("outbox_events", "owner_user_id", schema="ops")
    op.drop_index("ix_deletion_requests_state_requested", "deletion_requests", schema="ops")
    op.drop_index("uq_deletion_requests_active_user", "deletion_requests", schema="ops")
    op.drop_constraint(
        "ck_deletion_requests_ck_deletion_requests_nonnegative_attempts",
        "deletion_requests",
        schema="ops",
    )
    op.drop_constraint(
        "ck_deletion_requests_ck_deletion_requests_subject_hash_format",
        "deletion_requests",
        schema="ops",
    )
    op.drop_constraint(
        "ck_deletion_requests_ck_deletion_requests_state",
        "deletion_requests",
        schema="ops",
    )
    op.create_check_constraint(
        "ck_deletion_requests_state",
        "deletion_requests",
        "state IN ('requested','running','completed','failed')",
        schema="ops",
    )
    op.drop_constraint("uq_deletion_requests_generation_job", "deletion_requests", schema="ops")
    op.drop_constraint(
        "fk_deletion_requests_generation_job",
        "deletion_requests",
        schema="ops",
        type_="foreignkey",
    )
    op.drop_column("deletion_requests", "verification_summary", schema="ops")
    op.drop_column("deletion_requests", "last_error_code", schema="ops")
    op.drop_column("deletion_requests", "attempt_count", schema="ops")
    op.drop_column("deletion_requests", "current_step", schema="ops")
    op.drop_column("deletion_requests", "generation_job_id", schema="ops")
    op.create_unique_constraint(
        "uq_deletion_requests_subject_state",
        "deletion_requests",
        ["subject_hash", "state"],
        schema="ops",
    )
