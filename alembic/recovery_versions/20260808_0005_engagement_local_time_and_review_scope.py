"""Store engagement decisions at their immutable user-local grain.

Revision ID: 20260808_0005
Revises: 20260808_0004
"""

from alembic import op
import sqlalchemy as sa


revision = "20260808_0005"
down_revision = "20260808_0004"
branch_labels = depends_on = None


def upgrade() -> None:
    op.add_column(
        "action_item_events",
        sa.Column("decision_local_date", sa.Date(), nullable=True),
        schema="app",
    )
    op.add_column(
        "action_item_events",
        sa.Column("decision_timezone", sa.String(64), nullable=True),
        schema="app",
    )
    op.create_index(
        "ix_action_item_events_user_decision_day",
        "action_item_events",
        ["user_id", "decision_local_date"],
        schema="app",
    )
    op.add_column(
        "plan_refreshes",
        sa.Column("local_date", sa.Date(), nullable=True),
        schema="app",
    )
    op.add_column(
        "plan_refreshes",
        sa.Column("timezone", sa.String(64), nullable=True),
        schema="app",
    )
    op.create_index(
        "ix_plan_refreshes_accepted_day",
        "plan_refreshes",
        ["user_id", "local_date"],
        schema="app",
    )
    _backfill_local_decisions()
    _require_complete_backfill()
    _set_local_decisions_not_null()
    _protect_action_event_facts()
    _replace_review_assertion()


def _backfill_local_decisions() -> None:
    op.execute(
        """
        UPDATE app.action_item_events event
        SET decision_local_date = plan.local_date,
            decision_timezone = plan.timezone
        FROM app.action_plan_items item
        JOIN app.action_plans plan ON plan.id = item.plan_id
        WHERE event.plan_item_id = item.id
          AND (event.decision_local_date IS NULL OR event.decision_timezone IS NULL);

        UPDATE app.plan_refreshes refresh
        SET local_date = COALESCE(
              (SELECT plan.local_date FROM app.action_plans plan WHERE plan.id = refresh.completed_plan_id),
              (SELECT plan.local_date FROM app.action_plan_items item JOIN app.action_plans plan ON plan.id = item.plan_id WHERE item.id = refresh.old_item_id),
              (SELECT plan.local_date FROM app.action_plan_items item JOIN app.action_plans plan ON plan.id = item.plan_id WHERE item.id = refresh.new_item_id)
            ),
            timezone = COALESCE(
              (SELECT plan.timezone FROM app.action_plans plan WHERE plan.id = refresh.completed_plan_id),
              (SELECT plan.timezone FROM app.action_plan_items item JOIN app.action_plans plan ON plan.id = item.plan_id WHERE item.id = refresh.old_item_id),
              (SELECT plan.timezone FROM app.action_plan_items item JOIN app.action_plans plan ON plan.id = item.plan_id WHERE item.id = refresh.new_item_id)
            )
        WHERE refresh.local_date IS NULL OR refresh.timezone IS NULL;
        """
    )


def _require_complete_backfill() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM app.action_item_events
            WHERE decision_local_date IS NULL OR decision_timezone IS NULL
          ) THEN
            RAISE EXCEPTION 'cannot migrate action events without a canonical plan local decision'
              USING ERRCODE = '23514';
          END IF;
          IF EXISTS (
            SELECT 1 FROM app.plan_refreshes
            WHERE local_date IS NULL OR timezone IS NULL
          ) THEN
            RAISE EXCEPTION 'cannot migrate plan refresh without a canonical plan local decision'
              USING ERRCODE = '23514';
          END IF;
        END;
        $$;
        """
    )


def _set_local_decisions_not_null() -> None:
    op.alter_column(
        "action_item_events", "decision_local_date", nullable=False, schema="app"
    )
    op.alter_column(
        "action_item_events", "decision_timezone", nullable=False, schema="app"
    )
    op.alter_column("plan_refreshes", "local_date", nullable=False, schema="app")
    op.alter_column("plan_refreshes", "timezone", nullable=False, schema="app")


def _protect_action_event_facts() -> None:
    """Make event ownership/local-day decisions true below the API layer."""
    op.execute(
        """
        ALTER TABLE app.action_item_events
          ADD CONSTRAINT ck_action_item_events_timezone_nonempty
          CHECK (btrim(decision_timezone) <> '');
        ALTER TABLE app.plan_refreshes
          ADD CONSTRAINT ck_plan_refreshes_timezone_nonempty
          CHECK (btrim(timezone) <> '');

        CREATE FUNCTION app.assert_action_event_scope(p_event_id uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
          v_event_user_id uuid;
          v_event_date date;
          v_event_timezone text;
          v_occurred_at timestamptz;
          v_plan_user_id uuid;
          v_plan_date date;
          v_plan_timezone text;
        BEGIN
          SELECT event.user_id,
                 event.decision_local_date,
                 event.decision_timezone,
                 event.occurred_at,
                 plan.user_id,
                 plan.local_date,
                 plan.timezone
            INTO v_event_user_id,
                 v_event_date,
                 v_event_timezone,
                 v_occurred_at,
                 v_plan_user_id,
                 v_plan_date,
                 v_plan_timezone
          FROM app.action_item_events event
          JOIN app.action_plan_items item ON item.id = event.plan_item_id
          JOIN app.action_plans plan ON plan.id = item.plan_id
          WHERE event.id = p_event_id;
          IF NOT FOUND
             OR v_event_user_id IS DISTINCT FROM v_plan_user_id
             OR v_event_date IS DISTINCT FROM v_plan_date
             OR v_event_timezone IS DISTINCT FROM v_plan_timezone
             OR (v_occurred_at AT TIME ZONE v_event_timezone)::date
                IS DISTINCT FROM v_event_date THEN
            RAISE EXCEPTION 'action event must match its plan owner and immutable local decision'
              USING ERRCODE = '23514';
          END IF;
        END;
        $$;

        CREATE FUNCTION app.check_action_event_scope() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM app.assert_action_event_scope(NEW.id);
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION app.guard_action_event_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'recorded action events are immutable'
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER ck_action_event_scope
        AFTER INSERT OR UPDATE ON app.action_item_events
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION app.check_action_event_scope();

        CREATE TRIGGER guard_action_event_updates
        BEFORE UPDATE ON app.action_item_events
        FOR EACH ROW EXECUTE FUNCTION app.guard_action_event_update();
        """
    )


def _replace_review_assertion() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.assert_completed_review(p_review_id uuid) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
          v_status text;
          v_plan_id uuid;
          v_user_id uuid;
          v_local_date date;
          v_timezone text;
          v_plan_count integer;
          v_review_count integer;
          v_valid_count integer;
        BEGIN
          SELECT status, plan_id, user_id, local_date, timezone
            INTO v_status, v_plan_id, v_user_id, v_local_date, v_timezone
          FROM app.daily_reviews WHERE id = p_review_id;
          IF NOT FOUND THEN
            RETURN;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM app.action_plans
            WHERE id = v_plan_id
              AND user_id = v_user_id
              AND local_date = v_local_date
              AND timezone = v_timezone
          ) THEN
            RAISE EXCEPTION 'daily review must match its plan owner and local decision' USING ERRCODE = '23514';
          END IF;
          IF v_status <> 'completed' THEN
            RETURN;
          END IF;
          SELECT count(*) INTO v_plan_count
          FROM app.action_plan_items WHERE plan_id = v_plan_id AND status = 'active';
          SELECT count(*) INTO v_review_count
          FROM app.daily_review_items WHERE daily_review_id = p_review_id;
          SELECT count(*) INTO v_valid_count
          FROM app.daily_review_items review_item
          JOIN app.action_plan_items plan_item ON plan_item.id = review_item.plan_item_id
          WHERE review_item.daily_review_id = p_review_id
            AND plan_item.plan_id = v_plan_id
            AND plan_item.status = 'active'
            AND review_item.outcome IS NOT NULL
            AND review_item.answered_at IS NOT NULL;
          IF v_plan_count = 0 OR v_review_count <> v_plan_count OR v_valid_count <> v_plan_count THEN
            RAISE EXCEPTION 'completed review must answer exactly its active plan items' USING ERRCODE = '23514';
          END IF;
        END;
        $$;
        """
    )


def downgrade() -> None:
    _restore_prior_review_assertion()
    op.execute(
        "DROP TRIGGER IF EXISTS guard_action_event_updates " "ON app.action_item_events"
    )
    op.execute("DROP TRIGGER IF EXISTS ck_action_event_scope ON app.action_item_events")
    op.execute("DROP FUNCTION IF EXISTS app.guard_action_event_update()")
    op.execute("DROP FUNCTION IF EXISTS app.check_action_event_scope()")
    op.execute("DROP FUNCTION IF EXISTS app.assert_action_event_scope(uuid)")
    op.execute(
        "ALTER TABLE app.plan_refreshes DROP CONSTRAINT IF EXISTS "
        "ck_plan_refreshes_timezone_nonempty"
    )
    op.execute(
        "ALTER TABLE app.action_item_events DROP CONSTRAINT IF EXISTS "
        "ck_action_item_events_timezone_nonempty"
    )
    op.drop_index(
        "ix_plan_refreshes_accepted_day", table_name="plan_refreshes", schema="app"
    )
    op.drop_column("plan_refreshes", "timezone", schema="app")
    op.drop_column("plan_refreshes", "local_date", schema="app")
    op.drop_index(
        "ix_action_item_events_user_decision_day",
        table_name="action_item_events",
        schema="app",
    )
    op.drop_column("action_item_events", "decision_timezone", schema="app")
    op.drop_column("action_item_events", "decision_local_date", schema="app")


def _restore_prior_review_assertion() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.assert_completed_review(p_review_id uuid) RETURNS void
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
        """
    )
