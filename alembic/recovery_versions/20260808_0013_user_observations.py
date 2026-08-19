"""Generalize symptom observations into one corrigible user-assertion table.

Revision ID: 20260808_0013
Revises: 20260808_0012

Preferences, body metrics, symptoms and period dates were four subsystems in
v1: three tables plus a free-form `user_profiles.chatbot_memory` JSON blob.
They share one row grain — the user asserted that a named observable held a
value at an instant — so they become one table rather than four. Net new
tables in this revision: zero. `app.symptom_observations` is renamed and
widened rather than joined by siblings.

Three design points the constraints enforce:

* No JSONB. A `value JSONB` column would be `chatbot_memory` under a new name.
  Three typed columns with `num_nonnulls(...) = 1` keep values queryable and
  make a malformed row impossible.

* Append-only with `supersedes_id`. Correcting a period date means amending an
  assertion about the past, so latest-by-`observed_at` is not enough. A
  correction is a new row citing the one it replaces, and the unique index on
  `supersedes_id` stops a correction chain from forking.

* `recorded_at` is distinct from `observed_at`. That is what makes as-of
  replay possible, which Slice B needs to reconcile `action_plans.cycle_snapshot`
  against the observations that existed when the plan was published.
"""

from alembic import op


revision = "20260808_0013"
down_revision = "20260808_0012"
branch_labels = depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.symptom_observations RENAME TO user_observations")

    op.execute(
        """
        ALTER TABLE app.user_observations
          ADD COLUMN observation_type      varchar(24),
          ADD COLUMN code                  varchar(64),
          ADD COLUMN catalog_version       varchar(32) NOT NULL
                                           DEFAULT 'observations.v1',
          ADD COLUMN observed_local_date   date,
          ADD COLUMN observed_timezone     varchar(64),
          ADD COLUMN value_numeric         numeric(10,3),
          ADD COLUMN value_unit            varchar(16),
          ADD COLUMN value_codes           text[],
          ADD COLUMN value_text            text,
          ADD COLUMN source                varchar(24) NOT NULL DEFAULT 'user',
          ADD COLUMN source_id             uuid,
          ADD COLUMN supersedes_id         uuid
                                           REFERENCES app.user_observations(id)
                                           ON DELETE RESTRICT,
          ADD COLUMN client_observation_id uuid,
          ADD COLUMN recorded_at           timestamptz NOT NULL DEFAULT now()
        """
    )

    # Backfill is deterministic and order-independent. Existing rows are all
    # symptoms; severity becomes a numeric value on v1's 0-10 scale, and a row
    # with no severity becomes a bare occurrence.
    op.execute(
        """
        UPDATE app.user_observations o SET
          observation_type      = 'symptom',
          code                  = o.symptom_code,
          client_observation_id = o.id,
          observed_timezone     = coalesce(p.timezone, 'UTC'),
          observed_local_date   = (o.observed_at AT TIME ZONE
                                   coalesce(p.timezone, 'UTC'))::date,
          value_numeric         = o.severity,
          value_unit            = CASE WHEN o.severity IS NOT NULL
                                       THEN 'score_0_10' END,
          value_codes           = CASE WHEN o.severity IS NULL
                                       THEN ARRAY['present'] END
        FROM app.user_profiles p
        WHERE p.user_id = o.user_id
        """
    )
    # Users with no profile row still need a coherent observation.
    op.execute(
        """
        UPDATE app.user_observations SET
          observation_type      = 'symptom',
          code                  = symptom_code,
          client_observation_id = id,
          observed_timezone     = 'UTC',
          observed_local_date   = (observed_at AT TIME ZONE 'UTC')::date,
          value_numeric         = severity,
          value_unit            = CASE WHEN severity IS NOT NULL
                                       THEN 'score_0_10' END,
          value_codes           = CASE WHEN severity IS NULL
                                       THEN ARRAY['present'] END
        WHERE observation_type IS NULL
        """
    )

    # 0002 created this via op.execute with an already-prefixed name, so the
    # naming convention produced the doubled form.
    op.execute(
        "ALTER TABLE app.user_observations "
        "DROP CONSTRAINT ck_symptom_observations_ck_symptom_observations_severity"
    )
    # PostgreSQL keeps constraint names across a table rename, so realign them
    # with the naming convention the ORM metadata expects.
    op.execute(
        "ALTER TABLE app.user_observations "
        "RENAME CONSTRAINT pk_symptom_observations TO pk_user_observations"
    )
    op.execute(
        "ALTER TABLE app.user_observations RENAME CONSTRAINT "
        "fk_symptom_observations_user_id_users TO fk_user_observations_user_id_users"
    )
    op.execute(
        """
        ALTER TABLE app.user_observations
          DROP COLUMN symptom_code,
          DROP COLUMN severity,
          ALTER COLUMN observation_type      SET NOT NULL,
          ALTER COLUMN code                  SET NOT NULL,
          ALTER COLUMN observed_local_date    SET NOT NULL,
          ALTER COLUMN observed_timezone      SET NOT NULL,
          ALTER COLUMN client_observation_id  SET NOT NULL,
          ALTER COLUMN observation_type       DROP DEFAULT
        """
    )

    _create_code_set_helper()

    op.execute(
        """
        ALTER TABLE app.user_observations
          ADD CONSTRAINT uq_user_observations_user_id_client_observation_id
              UNIQUE (user_id, client_observation_id),
          ADD CONSTRAINT uq_user_observations_supersedes_id
              UNIQUE (supersedes_id),
          ADD CONSTRAINT ck_user_observations_valid_type
              CHECK (observation_type IN
                     ('symptom','body_metric','preference','cycle_event')),
          ADD CONSTRAINT ck_user_observations_valid_source
              CHECK (source IN ('user','onboarding_assessment','weekly_checkin',
                                'conversation','import')),
          ADD CONSTRAINT ck_user_observations_source_id_presence
              CHECK ((source = 'user') = (source_id IS NULL)),
          ADD CONSTRAINT ck_user_observations_exactly_one_value
              CHECK (num_nonnulls(value_numeric, value_codes, value_text) = 1),
          ADD CONSTRAINT ck_user_observations_unit_iff_numeric
              CHECK ((value_numeric IS NULL) = (value_unit IS NULL)),
          ADD CONSTRAINT ck_user_observations_normalized_codes
              CHECK (value_codes IS NULL
                     OR app.is_normalized_code_set(value_codes)),
          ADD CONSTRAINT ck_user_observations_timezone_nonempty
              CHECK (btrim(observed_timezone) <> ''),
          ADD CONSTRAINT ck_user_observations_code_nonempty
              CHECK (btrim(code) <> '')
        """
    )

    op.execute("DROP INDEX IF EXISTS app.ix_symptom_observations_user_time")
    op.execute(
        "CREATE INDEX ix_user_observations_user_code_time "
        "ON app.user_observations (user_id, code, observed_at)"
    )
    op.execute(
        "CREATE INDEX ix_user_observations_type_day "
        "ON app.user_observations "
        "(user_id, observation_type, observed_local_date)"
    )

    _create_correction_scope_guard()
    _create_immutability_guard()
    _create_live_view()


def downgrade() -> None:
    _reject_lossy_downgrade()

    op.execute("DROP VIEW IF EXISTS app.user_observations_live")
    op.execute("DROP TRIGGER IF EXISTS guard_user_observation_updates " "ON app.user_observations")
    op.execute("DROP FUNCTION IF EXISTS app.guard_user_observation_update()")
    op.execute(
        "DROP TRIGGER IF EXISTS ck_user_observation_correction_scope " "ON app.user_observations"
    )
    op.execute("DROP FUNCTION IF EXISTS app.check_user_observation_correction()")
    op.execute("DROP FUNCTION IF EXISTS app.assert_user_observation_correction(uuid)")

    op.execute("DROP INDEX IF EXISTS app.ix_user_observations_type_day")
    op.execute("DROP INDEX IF EXISTS app.ix_user_observations_user_code_time")

    for constraint in (
        "ck_user_observations_code_nonempty",
        "ck_user_observations_timezone_nonempty",
        "ck_user_observations_normalized_codes",
        "ck_user_observations_unit_iff_numeric",
        "ck_user_observations_exactly_one_value",
        "ck_user_observations_source_id_presence",
        "ck_user_observations_valid_source",
        "ck_user_observations_valid_type",
        "uq_user_observations_supersedes_id",
        "uq_user_observations_user_id_client_observation_id",
    ):
        op.execute(f"ALTER TABLE app.user_observations DROP CONSTRAINT {constraint}")

    op.execute("DROP FUNCTION IF EXISTS app.is_normalized_code_set(text[])")

    op.execute(
        """
        ALTER TABLE app.user_observations
          ADD COLUMN symptom_code varchar(64),
          ADD COLUMN severity     integer
        """
    )
    op.execute(
        "UPDATE app.user_observations SET symptom_code = code, " "severity = value_numeric::integer"
    )
    op.execute("ALTER TABLE app.user_observations ALTER COLUMN symptom_code SET NOT NULL")
    op.execute(
        """
        ALTER TABLE app.user_observations
          DROP COLUMN recorded_at,
          DROP COLUMN client_observation_id,
          DROP COLUMN supersedes_id,
          DROP COLUMN source_id,
          DROP COLUMN source,
          DROP COLUMN value_text,
          DROP COLUMN value_codes,
          DROP COLUMN value_unit,
          DROP COLUMN value_numeric,
          DROP COLUMN observed_timezone,
          DROP COLUMN observed_local_date,
          DROP COLUMN catalog_version,
          DROP COLUMN code,
          DROP COLUMN observation_type
        """
    )
    op.execute(
        "ALTER TABLE app.user_observations ADD CONSTRAINT "
        "ck_symptom_observations_ck_symptom_observations_severity "
        "CHECK (severity IS NULL OR severity BETWEEN 0 AND 10)"
    )
    op.execute(
        "ALTER TABLE app.user_observations RENAME CONSTRAINT "
        "fk_user_observations_user_id_users TO fk_symptom_observations_user_id_users"
    )
    op.execute(
        "ALTER TABLE app.user_observations "
        "RENAME CONSTRAINT pk_user_observations TO pk_symptom_observations"
    )
    op.execute("ALTER TABLE app.user_observations RENAME TO symptom_observations")
    op.execute(
        "CREATE INDEX ix_symptom_observations_user_time "
        "ON app.symptom_observations (user_id, observed_at)"
    )


def _reject_lossy_downgrade() -> None:
    """The legacy shape holds symptoms only, so refuse to discard the rest."""

    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM app.user_observations
            WHERE observation_type <> 'symptom'
          ) THEN
            RAISE EXCEPTION
              'cannot downgrade while non-symptom observations exist';
          END IF;
        END $$;
        """
    )


def _create_code_set_helper() -> None:
    op.execute(
        """
        CREATE FUNCTION app.is_normalized_code_set(p_codes text[])
        RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
          SELECT cardinality(p_codes) BETWEEN 1 AND 24
             AND array_position(p_codes, NULL) IS NULL
             AND NOT EXISTS (
                   SELECT 1 FROM unnest(p_codes) AS c(v)
                   WHERE btrim(c.v) = ''
                 )
             AND p_codes = (
                   SELECT coalesce(array_agg(DISTINCT v ORDER BY v), '{}')
                   FROM unnest(p_codes) AS c(v)
                 );
        $$;
        """
    )


def _create_correction_scope_guard() -> None:
    """A correction must replace the same user's assertion about the same code."""

    op.execute(
        """
        CREATE FUNCTION app.assert_user_observation_correction(p_id uuid)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
          v_user_id uuid;
          v_type text;
          v_code text;
          v_supersedes uuid;
          v_prior_user uuid;
          v_prior_type text;
          v_prior_code text;
        BEGIN
          SELECT user_id, observation_type, code, supersedes_id
            INTO v_user_id, v_type, v_code, v_supersedes
          FROM app.user_observations WHERE id = p_id;
          IF NOT FOUND OR v_supersedes IS NULL THEN
            RETURN;
          END IF;
          SELECT user_id, observation_type, code
            INTO v_prior_user, v_prior_type, v_prior_code
          FROM app.user_observations WHERE id = v_supersedes;
          IF NOT FOUND
             OR v_user_id IS DISTINCT FROM v_prior_user
             OR v_type    IS DISTINCT FROM v_prior_type
             OR v_code    IS DISTINCT FROM v_prior_code THEN
            RAISE EXCEPTION
              'a correction must replace the same user''s observation of the '
              'same code'
              USING ERRCODE = '23514';
          END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION app.check_user_observation_correction() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM app.assert_user_observation_correction(NEW.id);
          RETURN NULL;
        END $$;
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER ck_user_observation_correction_scope "
        "AFTER INSERT OR UPDATE OF supersedes_id ON app.user_observations "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION app.check_user_observation_correction()"
    )


def _create_immutability_guard() -> None:
    """An assertion is a fact. Corrections supersede; they never rewrite."""

    op.execute(
        """
        CREATE FUNCTION app.guard_user_observation_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION
            'user observations are immutable; record a superseding observation'
            USING ERRCODE = '55000';
        END $$;
        """
    )
    op.execute(
        "CREATE TRIGGER guard_user_observation_updates "
        "BEFORE UPDATE OF user_id, observation_type, code, observed_at, "
        "value_numeric, value_unit, value_codes, value_text, source, source_id "
        "ON app.user_observations "
        "FOR EACH ROW EXECUTE FUNCTION app.guard_user_observation_update()"
    )


def _create_live_view() -> None:
    """One definition of 'current' every consumer shares.

    An observation is live when nothing supersedes it. The unique index on
    supersedes_id makes this an index-backed anti-join.
    """

    op.execute(
        """
        CREATE VIEW app.user_observations_live AS
        SELECT o.*
        FROM app.user_observations o
        WHERE NOT EXISTS (
          SELECT 1 FROM app.user_observations c WHERE c.supersedes_id = o.id
        );
        """
    )
