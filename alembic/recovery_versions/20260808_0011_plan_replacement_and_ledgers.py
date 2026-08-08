"""Finalize selected-variant replacements and immutable engagement ledgers.

Revision ID: 20260808_0011
Revises: 20260808_0010
"""

from alembic import op


revision = "20260808_0011"
down_revision = "20260808_0010"
branch_labels = depends_on = None


def upgrade() -> None:
    _reject_unmappable_streak_facts()
    _drop_predecessor_streak_constraint("evidence")
    _drop_predecessor_streak_constraint("state")
    op.execute(
        "UPDATE app.streak_days SET evidence_type = 'daily_review' "
        "WHERE evidence_type = 'review'"
    )
    op.execute(
        "ALTER TABLE app.streak_days ADD CONSTRAINT ck_streak_days_ck_streak_days_evidence "
        "CHECK (evidence_type IN ('daily_review','freeze'))"
    )
    op.execute(
        "ALTER TABLE app.streak_days ADD CONSTRAINT ck_streak_days_ck_streak_days_state "
        "CHECK (adjudication_state IN ('earned','frozen','missed'))"
    )
    op.execute(
        "ALTER TABLE app.streak_days ADD CONSTRAINT ck_streak_days_state_evidence "
        "CHECK ((adjudication_state IN ('earned','missed') AND evidence_type = 'daily_review') "
        "OR (adjudication_state = 'frozen' AND evidence_type = 'freeze'))"
    )
    op.execute(
        "ALTER TABLE app.streak_days ADD CONSTRAINT ck_streak_days_timezone_nonempty "
        "CHECK (btrim(timezone) <> '')"
    )
    op.create_unique_constraint(
        "uq_streak_days_evidence_type_evidence_id",
        "streak_days",
        ["evidence_type", "evidence_id"],
        schema="app",
    )
    op.create_unique_constraint(
        "uq_plan_refreshes_old_item_id", "plan_refreshes", ["old_item_id"], schema="app"
    )
    op.create_unique_constraint(
        "uq_plan_refreshes_new_item_id", "plan_refreshes", ["new_item_id"], schema="app"
    )
    op.execute(
        "ALTER TABLE app.plan_refreshes ADD CONSTRAINT ck_plan_refreshes_nonempty_reason "
        "CHECK (btrim(reason) <> '')"
    )
    _create_ledger_guards()


def _reject_unmappable_streak_facts() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM app.streak_days
            WHERE evidence_type NOT IN ('review', 'freeze')
               OR adjudication_state NOT IN ('earned', 'frozen')
          ) THEN
            RAISE EXCEPTION 'cannot migrate ambiguous streak facts to immutable daily-review policy'
              USING ERRCODE = '23514';
          END IF;
        END $$;
        """
    )


def _drop_predecessor_streak_constraint(suffix: str) -> None:
    """Accept the exact 0010 predecessor or the canonical re-upgrade name."""

    op.execute(
        f"""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'app.streak_days'::regclass
              AND conname = 'ck_streak_days_ck_streak_days_{suffix}'
          ) THEN
            ALTER TABLE app.streak_days
              DROP CONSTRAINT ck_streak_days_ck_streak_days_{suffix};
          ELSIF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'app.streak_days'::regclass
              AND conname = 'ck_streak_days_{suffix}'
          ) THEN
            ALTER TABLE app.streak_days DROP CONSTRAINT ck_streak_days_{suffix};
          ELSE
            RAISE EXCEPTION 'streak % constraint is missing before 0011 upgrade', '{suffix}'
              USING ERRCODE = '42704';
          END IF;
        END $$;
        """
    )


def _create_ledger_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION app.assert_streak_day_scope(p_streak_id uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
          v_user_id uuid;
          v_local_date date;
          v_timezone text;
          v_evidence_type text;
          v_evidence_id uuid;
          v_review_user_id uuid;
          v_review_date date;
          v_review_timezone text;
          v_review_status text;
        BEGIN
          SELECT user_id, local_date, timezone, evidence_type, evidence_id
            INTO v_user_id, v_local_date, v_timezone, v_evidence_type, v_evidence_id
          FROM app.streak_days WHERE id = p_streak_id;
          IF NOT FOUND OR v_evidence_type = 'freeze' THEN
            RETURN;
          END IF;
          SELECT user_id, local_date, timezone, status
            INTO v_review_user_id, v_review_date, v_review_timezone, v_review_status
          FROM app.daily_reviews WHERE id = v_evidence_id;
          IF NOT FOUND OR v_review_status <> 'completed'
             OR v_user_id IS DISTINCT FROM v_review_user_id
             OR v_local_date IS DISTINCT FROM v_review_date
             OR v_timezone IS DISTINCT FROM v_review_timezone THEN
            RAISE EXCEPTION 'streak day must match one completed Daily Review local decision'
              USING ERRCODE = '23514';
          END IF;
        END;
        $$;

        CREATE FUNCTION app.check_streak_day_scope() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM app.assert_streak_day_scope(NEW.id);
          RETURN NEW;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER ck_streak_day_scope
        AFTER INSERT OR UPDATE ON app.streak_days
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION app.check_streak_day_scope();

        CREATE FUNCTION app.guard_streak_day_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'streak ledger facts are immutable' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER guard_streak_day_updates
        BEFORE UPDATE ON app.streak_days
        FOR EACH ROW EXECUTE FUNCTION app.guard_streak_day_update();

        CREATE FUNCTION app.guard_reward_ledger_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'reward ledger facts are immutable' USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER guard_reward_ledger_updates
        BEFORE UPDATE ON app.reward_ledger
        FOR EACH ROW EXECUTE FUNCTION app.guard_reward_ledger_update();

        CREATE FUNCTION app.assert_plan_refresh_scope(p_refresh_id uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
          v_user_id uuid;
          v_date date;
          v_timezone text;
          v_old_plan_id uuid;
          v_new_plan_id uuid;
          v_old_user_id uuid;
          v_new_user_id uuid;
          v_old_date date;
          v_new_date date;
          v_old_timezone text;
          v_new_timezone text;
        BEGIN
          SELECT refresh.user_id, refresh.local_date, refresh.timezone,
                 old_item.plan_id, new_item.plan_id
            INTO v_user_id, v_date, v_timezone, v_old_plan_id, v_new_plan_id
          FROM app.plan_refreshes refresh
          JOIN app.action_plan_items old_item ON old_item.id = refresh.old_item_id
          JOIN app.action_plan_items new_item ON new_item.id = refresh.new_item_id
          WHERE refresh.id = p_refresh_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'accepted plan refresh must link old and new items'
              USING ERRCODE = '23514';
          END IF;
          SELECT user_id, local_date, timezone INTO v_old_user_id, v_old_date, v_old_timezone
          FROM app.action_plans WHERE id = v_old_plan_id;
          SELECT user_id, local_date, timezone INTO v_new_user_id, v_new_date, v_new_timezone
          FROM app.action_plans WHERE id = v_new_plan_id;
          IF v_user_id IS DISTINCT FROM v_old_user_id
             OR v_user_id IS DISTINCT FROM v_new_user_id
             OR v_date IS DISTINCT FROM v_old_date
             OR v_date IS DISTINCT FROM v_new_date
             OR v_timezone IS DISTINCT FROM v_old_timezone
             OR v_timezone IS DISTINCT FROM v_new_timezone
             OR v_old_plan_id = v_new_plan_id THEN
            RAISE EXCEPTION 'plan refresh items must share owner and immutable local plan lineage'
              USING ERRCODE = '23514';
          END IF;
        END;
        $$;

        CREATE FUNCTION app.check_plan_refresh_scope() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.accepted_at IS NOT NULL THEN
            PERFORM app.assert_plan_refresh_scope(NEW.id);
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER ck_plan_refresh_scope
        AFTER INSERT OR UPDATE ON app.plan_refreshes
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION app.check_plan_refresh_scope();
        """
    )


def downgrade() -> None:
    _reject_lossy_downgrade()
    op.execute("DROP TRIGGER IF EXISTS ck_plan_refresh_scope ON app.plan_refreshes")
    op.execute("DROP FUNCTION IF EXISTS app.check_plan_refresh_scope()")
    op.execute("DROP FUNCTION IF EXISTS app.assert_plan_refresh_scope(uuid)")
    op.execute(
        "DROP TRIGGER IF EXISTS guard_reward_ledger_updates ON app.reward_ledger"
    )
    op.execute("DROP FUNCTION IF EXISTS app.guard_reward_ledger_update()")
    op.execute("DROP TRIGGER IF EXISTS guard_streak_day_updates ON app.streak_days")
    op.execute("DROP FUNCTION IF EXISTS app.guard_streak_day_update()")
    op.execute("DROP TRIGGER IF EXISTS ck_streak_day_scope ON app.streak_days")
    op.execute("DROP FUNCTION IF EXISTS app.check_streak_day_scope()")
    op.execute("DROP FUNCTION IF EXISTS app.assert_streak_day_scope(uuid)")
    op.execute(
        "ALTER TABLE app.plan_refreshes DROP CONSTRAINT ck_plan_refreshes_nonempty_reason"
    )
    op.drop_constraint("uq_plan_refreshes_new_item_id", "plan_refreshes", schema="app")
    op.drop_constraint("uq_plan_refreshes_old_item_id", "plan_refreshes", schema="app")
    op.drop_constraint(
        "uq_streak_days_evidence_type_evidence_id", "streak_days", schema="app"
    )
    op.execute(
        "ALTER TABLE app.streak_days DROP CONSTRAINT ck_streak_days_timezone_nonempty"
    )
    op.execute(
        "ALTER TABLE app.streak_days DROP CONSTRAINT ck_streak_days_state_evidence"
    )
    op.execute(
        "ALTER TABLE app.streak_days DROP CONSTRAINT ck_streak_days_ck_streak_days_state"
    )
    op.execute(
        "ALTER TABLE app.streak_days DROP CONSTRAINT ck_streak_days_ck_streak_days_evidence"
    )
    op.execute("UPDATE app.streak_days SET evidence_type = 'review'")
    op.execute(
        "ALTER TABLE app.streak_days ADD CONSTRAINT ck_streak_days_ck_streak_days_evidence "
        "CHECK (evidence_type IN ('event','review','freeze'))"
    )
    op.execute(
        "ALTER TABLE app.streak_days ADD CONSTRAINT ck_streak_days_ck_streak_days_state "
        "CHECK (adjudication_state IN ('earned','frozen','revoked'))"
    )


def _reject_lossy_downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM app.streak_days WHERE adjudication_state = 'missed') THEN
            RAISE EXCEPTION 'cannot downgrade after missed streak facts exist' USING ERRCODE = '23514';
          END IF;
          IF EXISTS (SELECT 1 FROM app.plan_refreshes WHERE old_item_id IS NOT NULL) THEN
            RAISE EXCEPTION 'cannot downgrade after accepted replacement lineage exists' USING ERRCODE = '23514';
          END IF;
        END $$;
        """
    )
