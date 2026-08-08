"""Name non-fungible reward assets and make freezes and balances enforceable.

Revision ID: 20260808_0012
Revises: 20260808_0011

Three things, all of which have to ship together because the first one is only
safe once the second and third exist.

1. `app.reward_ledger` gains `asset_key` and `catalog_version`, and the asset
   vocabulary gains `entitlement`. Without a key the ledger can record that a
   freeze moved but not *which* non-fungible thing moved, so a claim of one
   personalization reward is indistinguishable from a claim of another.

2. `app.assert_streak_day_scope` currently returns early for every freeze row:

       IF NOT FOUND OR v_evidence_type = 'freeze' THEN RETURN; END IF;

   Freeze rows are therefore completely unvalidated. A `frozen` streak day can
   be inserted with an `evidence_id` that points at nothing, at another user's
   ledger row, or at a `grant` rather than a `redeem` — silently extending a
   streak with no token spent. Shipping the freeze endpoint without this fix
   would ship the exploit with the feature.

3. Balances are computed from the ledger rather than stored, which is only
   sound if the ledger cannot go negative. A deferred constraint trigger makes
   overdraft structurally impossible instead of merely discouraged.

Also adds `ck_weekly_scale_answer_numeric`: the weekly-trends query in a later
slice casts `answer->>'value'` to numeric, and one non-numeric row in a scale
question would fail the whole endpoint. `validate_weekly_answer` enforces this
in the application layer today, which does not survive a bad backfill.
"""

from alembic import op


revision = "20260808_0012"
down_revision = "20260808_0011"
branch_labels = depends_on = None


def upgrade() -> None:
    _reject_unnameable_assets()

    op.execute("ALTER TABLE app.reward_ledger ADD COLUMN asset_key varchar(64)")
    op.execute(
        "ALTER TABLE app.reward_ledger ADD COLUMN catalog_version varchar(32) "
        "NOT NULL DEFAULT 'engagement.v1'"
    )
    op.execute(
        "UPDATE app.reward_ledger SET asset_key = 'streak_freeze' "
        "WHERE asset_type = 'freeze' AND asset_key IS NULL"
    )

    op.execute(
        "ALTER TABLE app.reward_ledger "
        "DROP CONSTRAINT ck_reward_ledger_ck_reward_ledger_asset"
    )
    op.execute(
        "ALTER TABLE app.reward_ledger "
        "ADD CONSTRAINT ck_reward_ledger_ck_reward_ledger_asset "
        "CHECK (asset_type IN ('points','freeze','entitlement'))"
    )
    # Fungible points carry no key; everything else must name what moved.
    op.execute(
        "ALTER TABLE app.reward_ledger ADD CONSTRAINT ck_reward_ledger_asset_key_presence "
        "CHECK ((asset_type = 'points' AND asset_key IS NULL) "
        "OR (asset_type <> 'points' AND asset_key IS NOT NULL "
        "AND btrim(asset_key) <> ''))"
    )
    # An entitlement is a one-time unlock: granted once, never spent.
    op.execute(
        "ALTER TABLE app.reward_ledger ADD CONSTRAINT ck_reward_ledger_entitlement_quantity "
        "CHECK (asset_type <> 'entitlement' "
        "OR (event_type = 'grant' AND quantity = 1))"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_reward_ledger_entitlement "
        "ON app.reward_ledger (user_id, asset_key) "
        "WHERE asset_type = 'entitlement'"
    )
    op.execute(
        "CREATE INDEX ix_reward_ledger_user_id_asset_type_created_at "
        "ON app.reward_ledger (user_id, asset_type, created_at)"
    )

    _create_balance_guard()
    _repair_freeze_scope_validation()
    _create_weekly_scale_answer_guard()


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS ck_weekly_scale_answer_numeric "
        "ON app.weekly_checkin_responses"
    )
    op.execute("DROP FUNCTION IF EXISTS app.check_weekly_scale_answer()")

    _restore_unvalidated_freeze_scope()

    op.execute("DROP TRIGGER IF EXISTS ck_reward_ledger_balance ON app.reward_ledger")
    op.execute("DROP FUNCTION IF EXISTS app.check_reward_balance()")
    op.execute("DROP FUNCTION IF EXISTS app.assert_reward_balance(uuid, text, text)")

    op.execute("DROP INDEX IF EXISTS app.ix_reward_ledger_user_id_asset_type_created_at")
    op.execute("DROP INDEX IF EXISTS app.uq_reward_ledger_entitlement")
    op.execute(
        "ALTER TABLE app.reward_ledger "
        "DROP CONSTRAINT ck_reward_ledger_entitlement_quantity"
    )
    op.execute(
        "ALTER TABLE app.reward_ledger "
        "DROP CONSTRAINT ck_reward_ledger_asset_key_presence"
    )
    op.execute(
        "ALTER TABLE app.reward_ledger "
        "DROP CONSTRAINT ck_reward_ledger_ck_reward_ledger_asset"
    )
    op.execute(
        "DELETE FROM app.reward_ledger WHERE asset_type = 'entitlement'"
    )
    op.execute(
        "ALTER TABLE app.reward_ledger "
        "ADD CONSTRAINT ck_reward_ledger_ck_reward_ledger_asset "
        "CHECK (asset_type IN ('points','freeze'))"
    )
    op.execute("ALTER TABLE app.reward_ledger DROP COLUMN catalog_version")
    op.execute("ALTER TABLE app.reward_ledger DROP COLUMN asset_key")


def _reject_unnameable_assets() -> None:
    """Refuse to migrate a ledger holding a non-points asset we cannot name."""

    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM app.reward_ledger
            WHERE asset_type NOT IN ('points','freeze')
          ) THEN
            RAISE EXCEPTION
              'reward_ledger holds an asset type this revision cannot name';
          END IF;
        END $$;
        """
    )


def _create_balance_guard() -> None:
    """Make an overdrawn balance impossible rather than merely unexpected."""

    op.execute(
        """
        CREATE FUNCTION app.assert_reward_balance(
          p_user uuid, p_asset text, p_key text
        ) RETURNS void LANGUAGE plpgsql AS $$
        DECLARE v_balance integer;
        BEGIN
          SELECT coalesce(sum(quantity), 0) INTO v_balance
          FROM app.reward_ledger
          WHERE user_id = p_user
            AND asset_type = p_asset
            AND coalesce(asset_key, '') = coalesce(p_key, '');
          IF v_balance < 0 THEN
            RAISE EXCEPTION
              'reward balance for % cannot go negative', coalesce(p_key, p_asset)
              USING ERRCODE = '23514';
          END IF;
        END $$;
        """
    )
    # DEFERRABLE INITIALLY DEFERRED so a redeem and its compensating grant may
    # be written in either order inside one transaction; the balance only has
    # to be non-negative at commit.
    op.execute(
        """
        CREATE FUNCTION app.check_reward_balance() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM app.assert_reward_balance(
            NEW.user_id, NEW.asset_type, NEW.asset_key
          );
          RETURN NULL;
        END $$;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER ck_reward_ledger_balance "
        "AFTER INSERT ON app.reward_ledger "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION app.check_reward_balance()"
    )


def _repair_freeze_scope_validation() -> None:
    """Replace the unconditional freeze bypass with real evidence checks."""

    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.assert_streak_day_scope(p_streak_id uuid)
        RETURNS void LANGUAGE plpgsql AS $$
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
          v_ledger_user_id uuid;
          v_ledger_event text;
          v_ledger_asset text;
          v_ledger_source_id uuid;
        BEGIN
          SELECT user_id, local_date, timezone, evidence_type, evidence_id
            INTO v_user_id, v_local_date, v_timezone, v_evidence_type, v_evidence_id
          FROM app.streak_days WHERE id = p_streak_id;
          IF NOT FOUND THEN
            RETURN;
          END IF;

          IF v_evidence_type = 'freeze' THEN
            SELECT user_id, event_type, asset_type, source_id
              INTO v_ledger_user_id, v_ledger_event, v_ledger_asset, v_ledger_source_id
            FROM app.reward_ledger WHERE id = v_evidence_id;
            IF NOT FOUND
               OR v_ledger_event <> 'redeem'
               OR v_ledger_asset <> 'freeze'
               OR v_user_id IS DISTINCT FROM v_ledger_user_id
               OR v_ledger_source_id IS DISTINCT FROM p_streak_id THEN
              RAISE EXCEPTION
                'a frozen streak day must cite its own redeemed freeze token'
                USING ERRCODE = '23514';
            END IF;
            RETURN;
          END IF;

          SELECT user_id, local_date, timezone, status
            INTO v_review_user_id, v_review_date, v_review_timezone, v_review_status
          FROM app.daily_reviews WHERE id = v_evidence_id;
          IF NOT FOUND OR v_review_status <> 'completed'
             OR v_user_id IS DISTINCT FROM v_review_user_id
             OR v_local_date IS DISTINCT FROM v_review_date
             OR v_timezone IS DISTINCT FROM v_review_timezone THEN
            RAISE EXCEPTION
              'streak day must match one completed Daily Review local decision'
              USING ERRCODE = '23514';
          END IF;
        END $$;
        """
    )


def _restore_unvalidated_freeze_scope() -> None:
    """Restore 0011's definition so downgrade is symmetric."""

    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.assert_streak_day_scope(p_streak_id uuid)
        RETURNS void LANGUAGE plpgsql AS $$
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
            RAISE EXCEPTION
              'streak day must match one completed Daily Review local decision'
              USING ERRCODE = '23514';
          END IF;
        END $$;
        """
    )


def _create_weekly_scale_answer_guard() -> None:
    """A scale answer must be numeric so aggregate reads cannot fail on a row."""

    op.execute(
        """
        CREATE FUNCTION app.check_weekly_scale_answer() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE v_answer_type text;
        BEGIN
          SELECT answer_type INTO v_answer_type
          FROM app.weekly_checkin_questions WHERE id = NEW.question_id;
          IF v_answer_type = 'scale'
             AND jsonb_typeof(NEW.answer -> 'value') IS DISTINCT FROM 'number' THEN
            RAISE EXCEPTION 'a scale answer must record a numeric value'
              USING ERRCODE = '23514';
          END IF;
          RETURN NULL;
        END $$;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER ck_weekly_scale_answer_numeric "
        "AFTER INSERT OR UPDATE OF answer, question_id "
        "ON app.weekly_checkin_responses "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION app.check_weekly_scale_answer()"
    )
