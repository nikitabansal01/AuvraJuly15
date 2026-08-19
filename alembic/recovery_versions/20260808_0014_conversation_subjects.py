"""Let a conversation name the thing it is about.

Revision ID: 20260808_0014
Revises: 20260808_0013

A care-plan check-in is a conversation about one specific plan, but a
conversation had no way to say so. `weekly_checkins` owns its `conversation_id`,
which works because a check-in is mutable; plans are immutable revisions and
cannot hold a mutable back-reference.

The two obvious wrong answers are a `care_plan_conversations` join table, which
is a table per feature, and stuffing `plan_id` into
`conversation_messages.metadata_json`, which is the JSON-blob habit in
miniature. Instead the existing discriminated `conversations` table gains a
generic, typed subject reference, with a partial unique index so the database
guarantees one care-plan thread per plan.

`subject_type` is deliberately a closed vocabulary of one. Widening it later is
a migration and an explicit decision, not an accident.
"""

from alembic import op


revision = "20260808_0014"
down_revision = "20260808_0013"
branch_labels = depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE app.conversations
          ADD COLUMN subject_type varchar(32),
          ADD COLUMN subject_id   uuid
        """
    )
    op.execute(
        "ALTER TABLE app.conversations "
        "ADD CONSTRAINT ck_conversations_subject_pairing "
        "CHECK (num_nonnulls(subject_type, subject_id) <> 1)"
    )
    op.execute(
        "ALTER TABLE app.conversations "
        "ADD CONSTRAINT ck_conversations_valid_subject_type "
        "CHECK (subject_type IS NULL OR subject_type IN ('action_plan'))"
    )
    # One care-plan thread per plan, enforced by the database rather than by
    # the command that happens to create it.
    op.execute(
        "CREATE UNIQUE INDEX uq_conversations_subject "
        "ON app.conversations (user_id, subject_type, subject_id) "
        "WHERE subject_type IS NOT NULL"
    )
    _create_subject_scope_guard()


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ck_conversation_subject_scope ON app.conversations")
    op.execute("DROP FUNCTION IF EXISTS app.check_conversation_subject()")
    op.execute("DROP FUNCTION IF EXISTS app.assert_conversation_subject(uuid)")
    op.execute("DROP INDEX IF EXISTS app.uq_conversations_subject")
    op.execute(
        "ALTER TABLE app.conversations " "DROP CONSTRAINT ck_conversations_valid_subject_type"
    )
    op.execute("ALTER TABLE app.conversations " "DROP CONSTRAINT ck_conversations_subject_pairing")
    op.execute("ALTER TABLE app.conversations DROP COLUMN subject_id, DROP COLUMN subject_type")


def _create_subject_scope_guard() -> None:
    """A thread may only be about its own owner's plan.

    Without this a client could open a conversation citing another user's plan
    id; the row would look valid and the thread would be titled with a plan the
    caller may not read.
    """

    op.execute(
        """
        CREATE FUNCTION app.assert_conversation_subject(p_conversation_id uuid)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
          v_user_id uuid;
          v_subject_type text;
          v_subject_id uuid;
          v_plan_user_id uuid;
        BEGIN
          SELECT user_id, subject_type, subject_id
            INTO v_user_id, v_subject_type, v_subject_id
          FROM app.conversations WHERE id = p_conversation_id;
          IF NOT FOUND OR v_subject_type IS NULL THEN
            RETURN;
          END IF;
          IF v_subject_type = 'action_plan' THEN
            SELECT user_id INTO v_plan_user_id
            FROM app.action_plans WHERE id = v_subject_id;
            IF NOT FOUND OR v_plan_user_id IS DISTINCT FROM v_user_id THEN
              RAISE EXCEPTION
                'a conversation subject must belong to the same user'
                USING ERRCODE = '23514';
            END IF;
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION app.check_conversation_subject() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM app.assert_conversation_subject(NEW.id);
          RETURN NULL;
        END $$;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER ck_conversation_subject_scope "
        "AFTER INSERT OR UPDATE OF subject_type, subject_id ON app.conversations "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION app.check_conversation_subject()"
    )
