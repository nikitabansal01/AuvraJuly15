"""Harden canonical conversations and weekly check-ins.

Revision ID: 20260808_0007
Revises: 20260808_0006
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "20260808_0007"
down_revision = "20260808_0006"
branch_labels = depends_on = None


def upgrade() -> None:
    _conversation_revisions()
    _weekly_checkin_shape()
    _seed_weekly_checkin_v1()
    _weekly_checkin_invariants()


def _conversation_revisions() -> None:
    op.add_column(
        "conversations",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        schema="app",
    )
    op.create_check_constraint(
        "ck_conversations_positive_revision",
        "conversations",
        "revision > 0",
        schema="app",
    )


def _weekly_checkin_shape() -> None:
    op.add_column(
        "weekly_checkins",
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        schema="app",
    )
    op.add_column(
        "weekly_checkins",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        schema="app",
    )
    op.add_column(
        "weekly_checkin_questions",
        sa.Column("answer_type", sa.String(24), nullable=True),
        schema="app",
    )
    op.add_column(
        "weekly_checkin_questions",
        sa.Column("answer_schema", JSONB, nullable=True),
        schema="app",
    )
    op.add_column(
        "weekly_checkin_questions",
        sa.Column(
            "required", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        schema="app",
    )
    op.add_column(
        "weekly_checkin_responses",
        sa.Column(
            "answered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="app",
    )
    _backfill_checkin_conversations()
    op.alter_column("weekly_checkins", "conversation_id", nullable=False, schema="app")
    op.alter_column(
        "weekly_checkin_questions", "answer_type", nullable=False, schema="app"
    )
    op.alter_column(
        "weekly_checkin_questions", "answer_schema", nullable=False, schema="app"
    )
    op.create_unique_constraint(
        "uq_weekly_checkins_conversation",
        "weekly_checkins",
        ["conversation_id"],
        schema="app",
    )
    op.create_foreign_key(
        "fk_weekly_checkins_conversation",
        "weekly_checkins",
        "conversations",
        ["conversation_id"],
        ["id"],
        source_schema="app",
        referent_schema="app",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_weekly_checkins_positive_revision",
        "weekly_checkins",
        "revision > 0",
        schema="app",
    )
    op.create_check_constraint(
        "ck_weekly_questions_answer_type",
        "weekly_checkin_questions",
        "answer_type IN ('scale','choice','text','boolean','multi_select')",
        schema="app",
    )


def _backfill_checkin_conversations() -> None:
    """Backfill only metadata; answers remain in their original check-in rows."""
    op.execute(
        "UPDATE app.weekly_checkins SET conversation_id = gen_random_uuid() WHERE conversation_id IS NULL"
    )
    op.execute(
        """
        INSERT INTO app.conversations (id, user_id, status, thread_type, revision, created_at, updated_at)
        SELECT checkin.conversation_id, checkin.user_id, 'active', 'weekly_checkin', 1,
               checkin.created_at, checkin.updated_at
        FROM app.weekly_checkins checkin
        LEFT JOIN app.conversations conversation ON conversation.id = checkin.conversation_id
        WHERE conversation.id IS NULL;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM app.weekly_checkin_questions
            WHERE answer_type IS NULL OR answer_schema IS NULL
          ) THEN
            RAISE EXCEPTION 'legacy weekly question lacks an explicit typed answer definition'
              USING ERRCODE = '23514';
          END IF;
        END $$;
        """
    )


def _seed_weekly_checkin_v1() -> None:
    """Seed the source-controlled definition only on a fresh check-in question table."""
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM app.weekly_checkin_questions) THEN
            RETURN;
          END IF;
          INSERT INTO app.weekly_checkin_questions
            (id, version, ordinal, prompt, answer_type, answer_schema, required)
          VALUES
            (gen_random_uuid(), 'weekly-checkin.v1', 1, 'How has your energy been this week?',
             'scale', '{"minimum": 0, "maximum": 10}'::jsonb, true),
            (gen_random_uuid(), 'weekly-checkin.v1', 2, 'How has your mood been this week?',
             'scale', '{"minimum": 0, "maximum": 10}'::jsonb, true),
            (gen_random_uuid(), 'weekly-checkin.v1', 3, 'What would you like to reflect on?',
             'text', '{"max_length": 500}'::jsonb, true),
            (gen_random_uuid(), 'weekly-checkin.v1', 4, 'Which supports helped you this week?',
             'multi_select', '{"choices": ["sleep", "nutrition", "movement", "stress_support"], "max_selections": 3}'::jsonb, false);
        END $$;
        """
    )


def _weekly_checkin_invariants() -> None:
    op.execute(
        """
        CREATE FUNCTION app.assert_weekly_checkin(p_checkin_id uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
          v_user_id uuid;
          v_conversation_id uuid;
          v_version text;
          v_completed_at timestamptz;
          v_required integer;
          v_answered integer;
        BEGIN
          SELECT user_id, conversation_id, definition_version, completed_at
          INTO v_user_id, v_conversation_id, v_version, v_completed_at
          FROM app.weekly_checkins WHERE id = p_checkin_id;
          IF NOT FOUND THEN RETURN; END IF;
          IF NOT EXISTS (
            SELECT 1 FROM app.conversations
            WHERE id = v_conversation_id AND user_id = v_user_id
              AND thread_type = 'weekly_checkin'
          ) THEN
            RAISE EXCEPTION 'weekly check-in must own a same-user weekly-checkin conversation'
              USING ERRCODE = '23514';
          END IF;
          IF EXISTS (
            SELECT 1 FROM app.weekly_checkin_responses response
            JOIN app.weekly_checkin_questions question ON question.id = response.question_id
            WHERE response.weekly_checkin_id = p_checkin_id AND question.version <> v_version
          ) THEN
            RAISE EXCEPTION 'weekly response question must belong to its check-in definition version'
              USING ERRCODE = '23514';
          END IF;
          IF v_completed_at IS NULL THEN RETURN; END IF;
          SELECT count(*) INTO v_required FROM app.weekly_checkin_questions
            WHERE version = v_version AND required;
          SELECT count(*) INTO v_answered FROM app.weekly_checkin_responses response
            JOIN app.weekly_checkin_questions question ON question.id = response.question_id
            WHERE response.weekly_checkin_id = p_checkin_id
              AND question.version = v_version AND question.required;
          IF v_required = 0 OR v_answered <> v_required THEN
            RAISE EXCEPTION 'completed weekly check-in must answer every required definition question'
              USING ERRCODE = '23514';
          END IF;
        END;
        $$;

        CREATE FUNCTION app.check_weekly_checkin() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF TG_OP <> 'DELETE' THEN PERFORM app.assert_weekly_checkin(NEW.id); END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END; $$;

        CREATE FUNCTION app.check_weekly_response() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF TG_OP <> 'INSERT' THEN PERFORM app.assert_weekly_checkin(OLD.weekly_checkin_id); END IF;
          IF TG_OP <> 'DELETE' THEN PERFORM app.assert_weekly_checkin(NEW.weekly_checkin_id); END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END; $$;

        CREATE FUNCTION app.check_weekly_question() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE v_version text;
        DECLARE v_checkin_id uuid;
        BEGIN
          IF TG_OP = 'DELETE' THEN v_version := OLD.version; ELSE v_version := NEW.version; END IF;
          FOR v_checkin_id IN
            SELECT id FROM app.weekly_checkins WHERE definition_version = v_version
          LOOP
            PERFORM app.assert_weekly_checkin(v_checkin_id);
          END LOOP;
          IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END; $$;

        CREATE FUNCTION app.guard_weekly_checkin() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF OLD.completed_at IS NOT NULL
             AND current_setting('app.trusted_erasure', true) IS DISTINCT FROM 'on' THEN
            RAISE EXCEPTION 'completed weekly check-in is immutable' USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END; $$;

        CREATE FUNCTION app.guard_weekly_response() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF current_setting('app.trusted_erasure', true) IS DISTINCT FROM 'on' THEN
            RAISE EXCEPTION 'weekly check-in response is immutable' USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END; $$;

        CREATE FUNCTION app.guard_weekly_question() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM app.weekly_checkins
            WHERE definition_version = OLD.version
          ) AND current_setting('app.trusted_erasure', true) IS DISTINCT FROM 'on' THEN
            RAISE EXCEPTION 'weekly check-in definition referenced by a check-in is immutable'
              USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END; $$;

        CREATE CONSTRAINT TRIGGER ct_weekly_checkins_valid
          AFTER INSERT OR UPDATE OR DELETE ON app.weekly_checkins
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.check_weekly_checkin();
        CREATE CONSTRAINT TRIGGER ct_weekly_responses_valid
          AFTER INSERT OR UPDATE OR DELETE ON app.weekly_checkin_responses
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.check_weekly_response();
        CREATE CONSTRAINT TRIGGER ct_weekly_questions_valid
          AFTER INSERT OR UPDATE OR DELETE ON app.weekly_checkin_questions
          DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.check_weekly_question();
        CREATE TRIGGER guard_completed_weekly_checkin
          BEFORE UPDATE OR DELETE ON app.weekly_checkins
          FOR EACH ROW EXECUTE FUNCTION app.guard_weekly_checkin();
        CREATE TRIGGER guard_weekly_checkin_response
          BEFORE UPDATE OR DELETE ON app.weekly_checkin_responses
          FOR EACH ROW EXECUTE FUNCTION app.guard_weekly_response();
        CREATE TRIGGER guard_referenced_weekly_question
          BEFORE UPDATE OR DELETE ON app.weekly_checkin_questions
          FOR EACH ROW EXECUTE FUNCTION app.guard_weekly_question();
        """
    )


def downgrade() -> None:
    _drop_weekly_checkin_invariants()
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM app.weekly_checkin_responses)
             OR EXISTS (SELECT 1 FROM app.weekly_checkins) THEN
            RAISE EXCEPTION 'cannot downgrade conversation/check-in schema after check-in facts exist'
              USING ERRCODE = '23514';
          END IF;
          DELETE FROM app.weekly_checkin_questions WHERE version = 'weekly-checkin.v1';
          IF EXISTS (SELECT 1 FROM app.weekly_checkin_questions) THEN
            RAISE EXCEPTION 'cannot downgrade while another weekly definition exists'
              USING ERRCODE = '23514';
          END IF;
        END $$;
        """
    )
    op.drop_constraint(
        op.f("ck_weekly_checkin_questions_ck_weekly_questions_answer_type"),
        "weekly_checkin_questions",
        schema="app",
    )
    op.drop_column("weekly_checkin_responses", "answered_at", schema="app")
    op.drop_column("weekly_checkin_questions", "required", schema="app")
    op.drop_column("weekly_checkin_questions", "answer_schema", schema="app")
    op.drop_column("weekly_checkin_questions", "answer_type", schema="app")
    op.drop_constraint(
        op.f("ck_weekly_checkins_ck_weekly_checkins_positive_revision"),
        "weekly_checkins",
        schema="app",
    )
    op.drop_constraint(
        "fk_weekly_checkins_conversation",
        "weekly_checkins",
        schema="app",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_weekly_checkins_conversation",
        "weekly_checkins",
        schema="app",
        type_="unique",
    )
    op.drop_column("weekly_checkins", "revision", schema="app")
    op.drop_column("weekly_checkins", "conversation_id", schema="app")
    op.drop_constraint(
        op.f("ck_conversations_ck_conversations_positive_revision"),
        "conversations",
        schema="app",
    )
    op.drop_column("conversations", "revision", schema="app")


def _drop_weekly_checkin_invariants() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS guard_referenced_weekly_question ON app.weekly_checkin_questions;
        DROP TRIGGER IF EXISTS guard_weekly_checkin_response ON app.weekly_checkin_responses;
        DROP TRIGGER IF EXISTS guard_completed_weekly_checkin ON app.weekly_checkins;
        DROP TRIGGER IF EXISTS ct_weekly_questions_valid ON app.weekly_checkin_questions;
        DROP TRIGGER IF EXISTS ct_weekly_responses_valid ON app.weekly_checkin_responses;
        DROP TRIGGER IF EXISTS ct_weekly_checkins_valid ON app.weekly_checkins;
        DROP FUNCTION IF EXISTS app.guard_weekly_question();
        DROP FUNCTION IF EXISTS app.guard_weekly_response();
        DROP FUNCTION IF EXISTS app.guard_weekly_checkin();
        DROP FUNCTION IF EXISTS app.check_weekly_question();
        DROP FUNCTION IF EXISTS app.check_weekly_response();
        DROP FUNCTION IF EXISTS app.check_weekly_checkin();
        DROP FUNCTION IF EXISTS app.assert_weekly_checkin(uuid);
        """
    )
