"""Add the isolated AUVRA v2 application and operations schemas.

Revision ID: 20260801_0002
Revises: 20260723_0001
Create Date: 2026-08-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260801_0002"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS app"))
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ops"))

    op.create_table(
        "users",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "auth_provider",
            sa.String(32),
            nullable=False,
            server_default="firebase",
        ),
        sa.Column("auth_subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('active','disabled','deletion_pending','deleted')",
            name="ck_users_valid_status",
        ),
        sa.UniqueConstraint(
            "auth_provider", "auth_subject", name="uq_users_auth_provider_auth_subject"
        ),
        schema="app",
    )

    op.create_table(
        "user_profiles",
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("display_name", sa.String(160)),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("locale", sa.String(16), nullable=False, server_default="en"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.CheckConstraint("version > 0", name="ck_user_profiles_positive_version"),
        schema="app",
    )

    op.create_table(
        "consent_records",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consent_type", sa.String(64), nullable=False),
        sa.Column("document_version", sa.String(64), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id",
            "consent_type",
            "document_version",
            name="uq_consent_records_user_id_consent_type_document_version",
        ),
        schema="app",
    )
    op.create_index(
        "ix_consent_records_user_id", "consent_records", ["user_id"], schema="app"
    )

    op.create_table(
        "onboarding_sessions",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("proof_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "claimed_user_id",
            UUID,
            sa.ForeignKey("app.users.id", ondelete="RESTRICT"),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_onboarding_sessions_expiry_after_creation",
        ),
        sa.CheckConstraint(
            "status IN ('active','claimed','expired','revoked')",
            name="ck_onboarding_sessions_valid_status",
        ),
        schema="app",
    )

    op.create_table(
        "onboarding_assessments",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("app.onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID, sa.ForeignKey("app.users.id", ondelete="CASCADE")),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("answers", JSONB, nullable=False),
        sa.Column(
            "is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "validated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "version > 0", name="ck_onboarding_assessments_positive_version"
        ),
        sa.UniqueConstraint(
            "session_id",
            "version",
            name="uq_onboarding_assessments_session_id_version",
        ),
        schema="app",
    )
    op.create_index(
        "ix_onboarding_assessments_session_id",
        "onboarding_assessments",
        ["session_id"],
        schema="app",
    )
    op.create_index(
        "ix_onboarding_assessments_user_id",
        "onboarding_assessments",
        ["user_id"],
        schema="app",
    )
    op.create_index(
        "uq_onboarding_assessments_current_session",
        "onboarding_assessments",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
        schema="app",
    )

    op.create_table(
        "generation_jobs",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase", sa.String(64)),
        sa.Column("request_payload", JSONB, nullable=False),
        sa.Column("result_payload", JSONB),
        sa.Column("error_code", sa.String(64)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_owner", sa.String(160)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_generation_jobs_valid_progress",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_generation_jobs_nonnegative_attempts"
        ),
        sa.CheckConstraint(
            "max_attempts > 0", name="ck_generation_jobs_positive_max_attempts"
        ),
        sa.CheckConstraint(
            "state IN ('queued','running','retry_wait','ready','failed','cancelled','dead_letter')",
            name="ck_generation_jobs_valid_state",
        ),
        schema="ops",
    )
    op.create_index(
        "ix_generation_jobs_user_id", "generation_jobs", ["user_id"], schema="ops"
    )
    op.create_index(
        "ix_generation_jobs_state_available",
        "generation_jobs",
        ["state", "available_at"],
        schema="ops",
    )

    op.create_table(
        "outbox_events",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", UUID, nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="pending"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_outbox_events_nonnegative_attempts"
        ),
        sa.CheckConstraint(
            "state IN ('pending','published','failed')",
            name="ck_outbox_events_valid_state",
        ),
        schema="ops",
    )
    op.create_index(
        "ix_outbox_events_state_available",
        "outbox_events",
        ["state", "available_at"],
        schema="ops",
    )

    op.create_table(
        "idempotency_keys",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("scope", sa.String(96), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="started"),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", JSONB),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "state IN ('started','completed')",
            name="ck_idempotency_keys_valid_state",
        ),
        sa.CheckConstraint(
            "(state = 'started' AND response_status IS NULL AND response_body IS NULL) "
            "OR (state = 'completed' AND response_status IS NOT NULL AND response_body IS NOT NULL)",
            name="ck_idempotency_keys_valid_response_state",
        ),
        sa.UniqueConstraint(
            "scope",
            "subject",
            "idempotency_key",
            name="uq_idempotency_keys_scope_subject_idempotency_key",
        ),
        schema="ops",
    )

    op.create_table(
        "media_assets",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "owner_user_id",
            UUID,
            sa.ForeignKey("app.users.id", ondelete="SET NULL"),
        ),
        sa.Column("storage_provider", sa.String(32), nullable=False),
        sa.Column("bucket", sa.String(128), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("public_url", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("alt_text", sa.String(500), nullable=False),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        *_timestamps(),
        sa.CheckConstraint(
            "width IS NULL OR width > 0", name="ck_media_assets_positive_width"
        ),
        sa.CheckConstraint(
            "height IS NULL OR height > 0", name="ck_media_assets_positive_height"
        ),
        sa.CheckConstraint(
            "public_url LIKE 'https://%'", name="ck_media_assets_https_public_url"
        ),
        sa.CheckConstraint(
            "status IN ('pending','ready','failed','deleted')",
            name="ck_media_assets_valid_status",
        ),
        sa.UniqueConstraint(
            "storage_provider",
            "bucket",
            "object_key",
            name="uq_media_assets_storage_provider_bucket_object_key",
        ),
        sa.UniqueConstraint("content_sha256", name="uq_media_assets_content_sha256"),
        sa.UniqueConstraint("public_url", name="uq_media_assets_public_url"),
        schema="app",
    )
    op.create_index(
        "ix_media_assets_owner_user_id",
        "media_assets",
        ["owner_user_id"],
        schema="app",
    )

    op.create_table(
        "action_plans",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "user_id",
            UUID,
            sa.ForeignKey("app.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "generation_job_id",
            UUID,
            sa.ForeignKey("ops.generation_jobs.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="ready"),
        sa.Column("cycle_snapshot", JSONB, nullable=False),
        sa.Column("context_snapshot", JSONB, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint("revision > 0", name="ck_action_plans_positive_revision"),
        sa.CheckConstraint(
            "status IN ('ready','archived')", name="ck_action_plans_valid_status"
        ),
        sa.CheckConstraint(
            "(is_current AND status = 'ready') OR (NOT is_current)",
            name="ck_action_plans_current_plan_is_ready",
        ),
        sa.UniqueConstraint(
            "user_id",
            "local_date",
            "revision",
            name="uq_action_plans_user_id_local_date_revision",
        ),
        schema="app",
    )
    op.create_index(
        "ix_action_plans_user_id", "action_plans", ["user_id"], schema="app"
    )
    op.create_index(
        "uq_action_plans_current_user_date",
        "action_plans",
        ["user_id", "local_date"],
        unique=True,
        postgresql_where=sa.text("is_current AND status = 'ready'"),
        schema="app",
    )

    op.create_table(
        "action_plan_items",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "plan_id",
            UUID,
            sa.ForeignKey("app.action_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("instructions", JSONB, nullable=False),
        sa.Column(
            "hero_asset_id",
            UUID,
            sa.ForeignKey("app.media_assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "supersedes_item_id",
            UUID,
            sa.ForeignKey("app.action_plan_items.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        *_timestamps(),
        sa.CheckConstraint(
            "slot >= 1 AND slot <= 4", name="ck_action_plan_items_valid_slot"
        ),
        sa.CheckConstraint(
            "status IN ('active','replaced','retired')",
            name="ck_action_plan_items_valid_status",
        ),
        sa.UniqueConstraint(
            "plan_id", "slot", name="uq_action_plan_items_plan_id_slot"
        ),
        schema="app",
    )
    op.create_index(
        "ix_action_plan_items_plan_id",
        "action_plan_items",
        ["plan_id"],
        schema="app",
    )

    op.create_table(
        "action_plan_item_variants",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "item_id",
            UUID,
            sa.ForeignKey("app.action_plan_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("variant_type", sa.String(32), nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column(
            "asset_id",
            UUID,
            sa.ForeignKey("app.media_assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        *_timestamps(),
        sa.UniqueConstraint(
            "item_id",
            "variant_type",
            name="uq_action_plan_item_variants_item_id_variant_type",
        ),
        schema="app",
    )
    op.create_index(
        "ix_action_plan_item_variants_item_id",
        "action_plan_item_variants",
        ["item_id"],
        schema="app",
    )

    _create_ready_plan_constraint_triggers()


def _create_ready_plan_constraint_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION app.assert_ready_plan_complete(p_plan_id uuid)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
            v_status text;
            v_published_at timestamptz;
            v_item_count integer;
            v_active_item_count integer;
            v_variant_count integer;
            v_asset_count integer;
            v_distinct_asset_count integer;
            v_invalid_asset_count integer;
        BEGIN
            SELECT status, published_at
              INTO v_status, v_published_at
              FROM app.action_plans
             WHERE id = p_plan_id;
            IF NOT FOUND OR v_status <> 'ready' THEN
                RETURN;
            END IF;

            SELECT count(*), count(*) FILTER (WHERE status = 'active')
              INTO v_item_count, v_active_item_count
              FROM app.action_plan_items
             WHERE plan_id = p_plan_id;

            SELECT count(*)
              INTO v_variant_count
              FROM app.action_plan_item_variants v
              JOIN app.action_plan_items i ON i.id = v.item_id
             WHERE i.plan_id = p_plan_id;

            WITH referenced_assets AS (
                SELECT i.hero_asset_id AS asset_id
                  FROM app.action_plan_items i
                 WHERE i.plan_id = p_plan_id
                UNION ALL
                SELECT v.asset_id
                  FROM app.action_plan_item_variants v
                  JOIN app.action_plan_items i ON i.id = v.item_id
                 WHERE i.plan_id = p_plan_id
            )
            SELECT count(*), count(DISTINCT r.asset_id),
                   count(*) FILTER (
                       WHERE a.status <> 'ready' OR a.public_url NOT LIKE 'https://%'
                   )
              INTO v_asset_count, v_distinct_asset_count, v_invalid_asset_count
              FROM referenced_assets r
              JOIN app.media_assets a ON a.id = r.asset_id;

            IF v_published_at IS NULL
               OR v_item_count <> 4
               OR v_active_item_count <> 4
               OR v_variant_count <> 12
               OR EXISTS (
                    SELECT 1
                      FROM app.action_plan_items i
                      LEFT JOIN app.action_plan_item_variants v ON v.item_id = i.id
                     WHERE i.plan_id = p_plan_id
                     GROUP BY i.id
                    HAVING count(v.id) <> 3
               )
               OR v_asset_count <> 16
               OR v_distinct_asset_count <> 16
               OR v_invalid_asset_count <> 0 THEN
                RAISE EXCEPTION 'ready plan % violates the four-item/sixteen-image publication invariant', p_plan_id
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$;

        CREATE FUNCTION app.check_ready_plan_row()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM app.assert_ready_plan_complete(COALESCE(NEW.id, OLD.id));
            RETURN COALESCE(NEW, OLD);
        END;
        $$;

        CREATE FUNCTION app.check_ready_item_row()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                PERFORM app.assert_ready_plan_complete(OLD.plan_id);
            END IF;
            IF TG_OP <> 'DELETE' AND (TG_OP = 'INSERT' OR NEW.plan_id IS DISTINCT FROM OLD.plan_id) THEN
                PERFORM app.assert_ready_plan_complete(NEW.plan_id);
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$;

        CREATE FUNCTION app.check_ready_variant_row()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            v_plan_id uuid;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                SELECT plan_id INTO v_plan_id FROM app.action_plan_items WHERE id = OLD.item_id;
                PERFORM app.assert_ready_plan_complete(v_plan_id);
            END IF;
            IF TG_OP <> 'DELETE' AND (TG_OP = 'INSERT' OR NEW.item_id IS DISTINCT FROM OLD.item_id) THEN
                SELECT plan_id INTO v_plan_id FROM app.action_plan_items WHERE id = NEW.item_id;
                PERFORM app.assert_ready_plan_complete(v_plan_id);
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$;

        CREATE FUNCTION app.check_ready_media_row()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            v_plan_id uuid;
        BEGIN
            FOR v_plan_id IN
                SELECT DISTINCT i.plan_id
                  FROM app.action_plan_items i
                  LEFT JOIN app.action_plan_item_variants v ON v.item_id = i.id
                 WHERE i.hero_asset_id = COALESCE(NEW.id, OLD.id)
                    OR v.asset_id = COALESCE(NEW.id, OLD.id)
            LOOP
                PERFORM app.assert_ready_plan_complete(v_plan_id);
            END LOOP;
            RETURN COALESCE(NEW, OLD);
        END;
        $$;

        CREATE CONSTRAINT TRIGGER ck_ready_plan_complete
        AFTER INSERT OR UPDATE ON app.action_plans
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION app.check_ready_plan_row();

        CREATE CONSTRAINT TRIGGER ck_ready_plan_items_complete
        AFTER INSERT OR UPDATE OR DELETE ON app.action_plan_items
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION app.check_ready_item_row();

        CREATE CONSTRAINT TRIGGER ck_ready_plan_variants_complete
        AFTER INSERT OR UPDATE OR DELETE ON app.action_plan_item_variants
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION app.check_ready_variant_row();

        CREATE CONSTRAINT TRIGGER ck_ready_plan_media_complete
        AFTER UPDATE OF status, public_url ON app.media_assets
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION app.check_ready_media_row();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS ck_ready_plan_media_complete ON app.media_assets"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS ck_ready_plan_variants_complete ON app.action_plan_item_variants"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS ck_ready_plan_items_complete ON app.action_plan_items"
    )
    op.execute("DROP TRIGGER IF EXISTS ck_ready_plan_complete ON app.action_plans")
    op.execute("DROP FUNCTION IF EXISTS app.check_ready_media_row()")
    op.execute("DROP FUNCTION IF EXISTS app.check_ready_variant_row()")
    op.execute("DROP FUNCTION IF EXISTS app.check_ready_item_row()")
    op.execute("DROP FUNCTION IF EXISTS app.check_ready_plan_row()")
    op.execute("DROP FUNCTION IF EXISTS app.assert_ready_plan_complete(uuid)")

    op.drop_table("action_plan_item_variants", schema="app")
    op.drop_table("action_plan_items", schema="app")
    op.drop_table("action_plans", schema="app")
    op.drop_table("media_assets", schema="app")
    op.drop_table("idempotency_keys", schema="ops")
    op.drop_table("outbox_events", schema="ops")
    op.drop_table("generation_jobs", schema="ops")
    op.drop_table("onboarding_assessments", schema="app")
    op.drop_table("onboarding_sessions", schema="app")
    op.drop_table("consent_records", schema="app")
    op.drop_table("user_profiles", schema="app")
    op.drop_table("users", schema="app")
    op.execute(sa.text("DROP SCHEMA IF EXISTS ops"))
    op.execute(sa.text("DROP SCHEMA IF EXISTS app"))
