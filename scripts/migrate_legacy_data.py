"""Copy the reviewed 2025 legacy application data into a blank baseline DB.

This deliberately does *not* restore a Supabase cluster dump.  The old backup
must first be restored into an isolated PostgreSQL 17 database.  This script
then copies only the explicitly reviewed public application columns into a
separate database that has already been bootstrapped at revision
``20260723_0001``.

The command is validation-only unless ``--apply`` is supplied.  It never logs
row contents or connection URLs.

STATUS (2026-08-08): superseded by the schema actually shipped. ``20260723_0001``
was an early v2 baseline draft that reused legacy public-schema table and
column names; it was archived (see ``alembic/legacy_evidence/``) and replaced
by the ``app``/``ops`` canonical schema starting at ``20260801_0002``, whose
table shapes (UUID-revisioned plans, generalized ``user_observations``,
JSON-versioned ``onboarding_assessments`` validated against a strict
documented-enum schema) do not match ``LEGACY_COLUMNS`` below. Running this
script against the current chain will correctly refuse via
``_validate_contract`` rather than silently miscopy.

A live production audit on this date found 29 legacy Firebase users, almost
all inactive for ~11-12 months, with free-text onboarding answers that do not
validate against the current strict ``MobileQuestionnaireV1`` schema. Forcing
them through would mean guessing at a mapping the data does not unambiguously
support — the plan's own governing rule is that ambiguous rows stay archived,
not guessed into the canonical schema. ``app.users``/``app.user_profiles`` also
do not need pre-population: the v2 onboarding-claim flow
(``app/v2/application/services.py::claim_onboarding_session``) creates both
automatically, identically for a new or returning user, the moment anyone
completes onboarding again. A real legacy migration remains possible later —
the legacy tables are untouched in ``public.*`` — but it is an owner-approved
ETL design task against the current schema, not a rerun of this script.
"""

from __future__ import annotations

import argparse
import os
from collections import OrderedDict
from typing import Dict, Mapping, Sequence, Tuple

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool


BASELINE_REVISION = "20260723_0001"

# Snapshot contract reconstructed from backend commit d7495ab (2025-08-30),
# the final application schema before the 2025-09-05 backup.  Table order is
# dependency order and is therefore also the insertion order.
LEGACY_COLUMNS: "OrderedDict[str, Tuple[str, ...]]" = OrderedDict(
    (
        (
            "question_sessions",
            (
                "session_id",
                "device_id",
                "created_at",
                "expires_at",
                "status",
                "age",
                "period_description",
                "birth_control",
                "last_period_date_utc",
                "cycle_length",
                "period_concerns",
                "body_concerns",
                "skin_hair_concerns",
                "mental_health_concerns",
                "other_concerns",
                "top_concern",
                "diagnosed_conditions",
                "family_history",
                "workout_intensity",
                "sleep_duration",
                "stress_level",
                "survey_timezone",
                "primary_hormone",
                "secondary_hormones",
            ),
        ),
        (
            "session_processing_status",
            (
                "session_id",
                "processing_status",
                "phase",
                "progress",
                "message",
                "food_status",
                "movement_status",
                "mindfulness_status",
                "request_payload",
                "result",
                "error",
                "started_at",
                "finished_at",
                "heartbeat_at",
                "retry_count",
                "max_retries",
                "created_at",
                "updated_at",
            ),
        ),
        (
            "user_profiles",
            ("uid", "name", "email", "current_timezone", "created_at", "updated_at"),
        ),
        (
            "user_responses",
            (
                "id",
                "uid",
                "age",
                "period_description",
                "birth_control",
                "last_period_date_utc",
                "cycle_length",
                "period_concerns",
                "body_concerns",
                "skin_hair_concerns",
                "mental_health_concerns",
                "other_concerns",
                "top_concern",
                "diagnosed_conditions",
                "family_history",
                "workout_intensity",
                "sleep_duration",
                "stress_level",
                "survey_timezone",
                "primary_hormone",
                "secondary_hormones",
                "created_at",
                "updated_at",
            ),
        ),
        (
            "recommendation_records",
            (
                "id",
                "uid",
                "session_id",
                "recommendation_type",
                "category",
                "confidence",
                "generated_at",
                "title",
                "purpose",
                "specific_action",
                "priority",
                "contraindications",
                "conditions",
                "symptoms",
                "hormones",
                "food_amounts",
                "food_items",
                "exercise_durations",
                "exercise_types",
                "exercise_intensities",
                "mindfulness_durations",
                "mindfulness_techniques",
                "frequency_detail",
                "duration_weeks",
                "optimal_times",
                "research_summary",
                "research_studies",
                "user_profile_snapshot",
                "created_at",
                "updated_at",
            ),
        ),
        (
            "recommendation_advices",
            (
                "id",
                "recommendation_id",
                "uid",
                "session_id",
                "advice_type",
                "category",
                "title",
                "description",
                "recommendation_context",
                "created_at",
                "updated_at",
            ),
        ),
        (
            "user_schedules",
            (
                "id",
                "uid",
                "date",
                "scheduled_recommendations",
                "completed_recommendations",
                "created_at",
                "updated_at",
            ),
        ),
        (
            "recommendation_completions",
            (
                "id",
                "uid",
                "recommendation_id",
                "completion_date",
                "completed_at",
                "notes",
            ),
        ),
        (
            "recommendation_redistributions",
            (
                "id",
                "uid",
                "recommendation_id",
                "original_date",
                "redistributed_dates",
                "redistribution_reason",
                "created_at",
                "updated_at",
            ),
        ),
        (
            "recommendation_schedules",
            (
                "id",
                "uid",
                "recommendation_id",
                "start_date_utc",
                "end_date_utc",
                "next_fire_at_utc",
                "rrule",
                "created_at",
                "updated_at",
            ),
        ),
        (
            "schedule_redistributions",
            (
                "id",
                "schedule_id",
                "original_date",
                "override_date",
                "reason",
                "source",
                "created_at",
            ),
        ),
        (
            "daily_assignments",
            (
                "id",
                "uid",
                "schedule_id",
                "recommendation_id",
                "assignment_date",
                "time_group",
                "is_completed",
                "completed_at",
                "notes",
                "created_at",
                "updated_at",
            ),
        ),
    )
)

# New non-null columns that did not exist in the legacy snapshot.
DESTINATION_DEFAULTS: Mapping[str, Mapping[str, object]] = {
    "user_profiles": {"feedback_last_count": 0},
}


class MigrationContractError(RuntimeError):
    """Raised before writes when either database violates the reviewed contract."""


def _column_names_match(
    actual_columns: Sequence[str], expected_columns: Sequence[str]
) -> bool:
    """Return true only when both sides contain the same unique names.

    Physical PostgreSQL column order is deliberately ignored because every
    copied value is selected and inserted by its explicit reviewed name.
    """
    return len(actual_columns) == len(expected_columns) and set(actual_columns) == set(
        expected_columns
    )


def _engine(url: str) -> Engine:
    # Database exceptions can otherwise include the failed parameter payload,
    # which may contain health/profile data from the legacy snapshot.
    return sa.create_engine(
        url,
        poolclass=NullPool,
        future=True,
        hide_parameters=True,
    )


def _table(connection: Connection, schema: str, name: str) -> sa.Table:
    return sa.Table(
        name,
        sa.MetaData(),
        schema=schema,
        autoload_with=connection,
    )


def _count(connection: Connection, table: sa.Table) -> int:
    return int(
        connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
    )


def _validate_contract(
    source: Connection,
    destination: Connection,
    source_schema: str,
    destination_schema: str,
    expected_total_rows: int,
) -> Tuple[Dict[str, sa.Table], Dict[str, sa.Table], Dict[str, int]]:
    version_table = _table(destination, destination_schema, "alembic_version")
    destination_revision = destination.execute(
        sa.select(version_table.c.version_num)
    ).scalar_one_or_none()
    if destination_revision != BASELINE_REVISION:
        raise MigrationContractError(
            "Destination is not at the canonical baseline revision."
        )

    source_tables: Dict[str, sa.Table] = {}
    destination_tables: Dict[str, sa.Table] = {}
    source_counts: Dict[str, int] = {}

    for name, expected_columns in LEGACY_COLUMNS.items():
        source_table = _table(source, source_schema, name)
        destination_table = _table(destination, destination_schema, name)

        # PostgreSQL preserves physical column order, but that order is not part
        # of the data-migration contract.  The reviewed backup and historical
        # ORM declare several otherwise-identical tables in different orders.
        # Require the exact reviewed column-name set so no source data is
        # silently ignored, then select every value explicitly by name below.
        actual_source_columns = tuple(column.name for column in source_table.columns)
        if not _column_names_match(actual_source_columns, expected_columns):
            raise MigrationContractError(
                f"Legacy schema mismatch for {name}; manual mapping review is required."
            )

        missing_destination_columns = set(expected_columns) - {
            column.name for column in destination_table.columns
        }
        if missing_destination_columns:
            raise MigrationContractError(
                f"Destination schema is missing reviewed columns for {name}."
            )

        if _count(destination, destination_table) != 0:
            raise MigrationContractError(
                f"Destination table {name} is not empty; refusing to merge data."
            )

        source_tables[name] = source_table
        destination_tables[name] = destination_table
        source_counts[name] = _count(source, source_table)

    actual_total_rows = sum(source_counts.values())
    if actual_total_rows != expected_total_rows:
        raise MigrationContractError(
            "Legacy row total differs from the operator-supplied expectation."
        )

    return source_tables, destination_tables, source_counts


def _copy_table(
    source: Connection,
    destination: Connection,
    source_table: sa.Table,
    destination_table: sa.Table,
    columns: Sequence[str],
    extra_values: Mapping[str, object],
    batch_size: int,
) -> None:
    primary_key_columns = [column.name for column in source_table.primary_key.columns]
    query = sa.select(*(source_table.c[name] for name in columns))
    if primary_key_columns:
        query = query.order_by(*(source_table.c[name] for name in primary_key_columns))

    result = source.execution_options(stream_results=True).execute(query)
    try:
        while True:
            rows = result.fetchmany(batch_size)
            if not rows:
                break
            payload = []
            for row in rows:
                values = dict(row._mapping)
                values.update(extra_values)
                payload.append(values)
            destination.execute(sa.insert(destination_table), payload)
    finally:
        result.close()


def _verify_table(
    source: Connection,
    destination: Connection,
    source_table: sa.Table,
    destination_table: sa.Table,
    columns: Sequence[str],
) -> None:
    primary_key_columns = [column.name for column in source_table.primary_key.columns]
    source_query = sa.select(*(source_table.c[name] for name in columns))
    destination_query = sa.select(*(destination_table.c[name] for name in columns))
    if primary_key_columns:
        source_query = source_query.order_by(
            *(source_table.c[name] for name in primary_key_columns)
        )
        destination_query = destination_query.order_by(
            *(destination_table.c[name] for name in primary_key_columns)
        )

    source_rows = source.execute(source_query).all()
    destination_rows = destination.execute(destination_query).all()
    if source_rows != destination_rows:
        raise MigrationContractError(
            f"Post-copy verification failed for {source_table.name}."
        )


def _reset_owned_sequence(
    destination: Connection,
    destination_schema: str,
    table: sa.Table,
) -> None:
    primary_key_columns = list(table.primary_key.columns)
    if len(primary_key_columns) != 1:
        return
    primary_key = primary_key_columns[0]
    if not isinstance(primary_key.type, (sa.Integer, sa.BigInteger)):
        return

    qualified_table = f"{destination_schema}.{table.name}"
    sequence_name = destination.execute(
        sa.text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
        {"table_name": qualified_table, "column_name": primary_key.name},
    ).scalar_one_or_none()
    if not sequence_name:
        return

    maximum = destination.execute(sa.select(sa.func.max(primary_key))).scalar_one()
    if maximum is None:
        destination.execute(
            sa.text("SELECT setval(CAST(:sequence_name AS regclass), 1, false)"),
            {"sequence_name": sequence_name},
        )
    else:
        destination.execute(
            sa.text("SELECT setval(CAST(:sequence_name AS regclass), :value, true)"),
            {"sequence_name": sequence_name, "value": int(maximum)},
        )


def migrate(
    source_url: str,
    destination_url: str,
    source_schema: str,
    destination_schema: str,
    expected_total_rows: int,
    batch_size: int,
    apply: bool,
) -> Dict[str, int]:
    source_engine = _engine(source_url)
    destination_engine = _engine(destination_url)
    if source_engine.url.render_as_string(hide_password=True) == (
        destination_engine.url.render_as_string(hide_password=True)
    ):
        raise MigrationContractError(
            "Source and destination must be different databases."
        )

    try:
        with source_engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as source:
            source_transaction = source.begin()
            try:
                source.execute(sa.text("SET TRANSACTION READ ONLY"))
                with destination_engine.begin() as destination:
                    source_tables, destination_tables, counts = _validate_contract(
                        source,
                        destination,
                        source_schema,
                        destination_schema,
                        expected_total_rows,
                    )
                    if not apply:
                        return counts

                    for name, columns in LEGACY_COLUMNS.items():
                        _copy_table(
                            source,
                            destination,
                            source_tables[name],
                            destination_tables[name],
                            columns,
                            DESTINATION_DEFAULTS.get(name, {}),
                            batch_size,
                        )
                        _verify_table(
                            source,
                            destination,
                            source_tables[name],
                            destination_tables[name],
                            columns,
                        )
                        _reset_owned_sequence(
                            destination, destination_schema, destination_tables[name]
                        )

                    profile_table = destination_tables["user_profiles"]
                    bad_profile_defaults = destination.execute(
                        sa.select(sa.func.count())
                        .select_from(profile_table)
                        .where(profile_table.c.feedback_last_count != 0)
                    ).scalar_one()
                    if bad_profile_defaults:
                        raise MigrationContractError(
                            "Destination defaults failed validation for user_profiles."
                        )
                    return counts
            finally:
                source_transaction.rollback()
    finally:
        source_engine.dispose()
        destination_engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-schema", default="public")
    parser.add_argument("--destination-schema", default="public")
    parser.add_argument("--expected-total-rows", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Copy data. Without this flag the command only validates contracts.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    source_url = os.environ.get("LEGACY_DATABASE_URL")
    destination_url = os.environ.get("DATABASE_URL")
    if not source_url or not destination_url:
        raise SystemExit(
            "LEGACY_DATABASE_URL and DATABASE_URL must both be set in the environment."
        )
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1.")

    try:
        counts = migrate(
            source_url=source_url,
            destination_url=destination_url,
            source_schema=args.source_schema,
            destination_schema=args.destination_schema,
            expected_total_rows=args.expected_total_rows,
            batch_size=args.batch_size,
            apply=args.apply,
        )
    except MigrationContractError as exc:
        raise SystemExit(f"Migration refused: {exc}") from exc
    except sa.exc.SQLAlchemyError as exc:
        # Keep the detailed exception chained for local debugging without
        # rendering its statement parameters in normal CLI output.
        raise SystemExit(
            "Migration failed because a database operation was rejected; "
            "no destination changes were committed."
        ) from exc

    mode = "copied and verified" if args.apply else "validated (no writes)"
    print(
        f"Legacy data {mode}: {sum(counts.values())} rows across {len(counts)} tables."
    )
    for table_name, row_count in counts.items():
        print(f"  {table_name}: {row_count}")


if __name__ == "__main__":
    main()
