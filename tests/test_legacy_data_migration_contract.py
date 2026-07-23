from app.core.database import Base
from scripts.migrate_legacy_data import (
    DESTINATION_DEFAULTS,
    LEGACY_COLUMNS,
    _column_names_match,
)


# Physical order recorded in the supplied 2025 PostgreSQL cluster dump for
# tables whose order differs from the final pre-backup ORM.  The migration is
# name-addressed, so these must be accepted without weakening the exact-name
# contract.
BACKUP_COLUMN_ORDER = {
    "question_sessions": (
        "session_id", "device_id", "created_at", "status", "expires_at",
        "age", "period_description", "birth_control", "cycle_length",
        "period_concerns", "body_concerns", "skin_hair_concerns",
        "mental_health_concerns", "other_concerns", "top_concern",
        "diagnosed_conditions", "family_history", "workout_intensity",
        "sleep_duration", "stress_level", "survey_timezone",
        "last_period_date_utc", "primary_hormone", "secondary_hormones",
    ),
    "user_profiles": (
        "uid", "name", "email", "created_at", "updated_at", "current_timezone",
    ),
    "user_responses": (
        "id", "uid", "period_description", "birth_control", "cycle_length",
        "period_concerns", "body_concerns", "skin_hair_concerns",
        "mental_health_concerns", "other_concerns", "top_concern",
        "diagnosed_conditions", "created_at", "updated_at", "family_history",
        "workout_intensity", "sleep_duration", "stress_level", "age",
        "last_period_date_utc", "survey_timezone", "primary_hormone",
        "secondary_hormones",
    ),
    "recommendation_records": (
        "id", "uid", "recommendation_type", "category", "confidence",
        "generated_at", "title", "specific_action", "priority",
        "contraindications", "conditions", "symptoms", "hormones",
        "frequency_detail", "duration_weeks", "research_summary",
        "research_studies", "user_profile_snapshot", "created_at", "updated_at",
        "food_amounts", "food_items", "exercise_durations", "exercise_types",
        "exercise_intensities", "mindfulness_durations",
        "mindfulness_techniques", "optimal_times", "session_id", "purpose",
    ),
    "recommendation_advices": (
        "id", "recommendation_id", "uid", "advice_type", "category", "title",
        "description", "recommendation_context", "created_at", "updated_at",
        "session_id",
    ),
    "recommendation_schedules": (
        "id", "uid", "rrule", "next_fire_at_utc", "recommendation_id",
        "created_at", "updated_at", "start_date_utc", "end_date_utc",
    ),
}


def test_legacy_copy_contract_fits_current_orm() -> None:
    assert len(LEGACY_COLUMNS) == 12

    for table_name, legacy_columns in LEGACY_COLUMNS.items():
        assert len(legacy_columns) == len(set(legacy_columns))
        assert table_name in Base.metadata.tables

        destination_table = Base.metadata.tables[table_name]
        destination_columns = {column.name for column in destination_table.columns}
        assert set(legacy_columns) <= destination_columns

        supplied_columns = set(legacy_columns) | set(
            DESTINATION_DEFAULTS.get(table_name, {})
        )
        required_new_columns = {
            column.name
            for column in destination_table.columns
            if not column.nullable
            and not column.primary_key
            and column.server_default is None
            and column.name not in legacy_columns
        }
        assert required_new_columns <= supplied_columns


def test_destination_defaults_only_fill_new_columns() -> None:
    for table_name, defaults in DESTINATION_DEFAULTS.items():
        assert table_name in LEGACY_COLUMNS
        destination_columns = Base.metadata.tables[table_name].columns

        for column_name in defaults:
            assert column_name in destination_columns
            assert column_name not in LEGACY_COLUMNS[table_name]


def test_actual_backup_column_order_matches_by_name() -> None:
    for table_name, backup_columns in BACKUP_COLUMN_ORDER.items():
        canonical_columns = LEGACY_COLUMNS[table_name]
        assert backup_columns != canonical_columns
        assert _column_names_match(backup_columns, canonical_columns)

        assert not _column_names_match(backup_columns[:-1], canonical_columns)
        assert not _column_names_match(
            backup_columns[:-1] + ("unexpected_column",), canonical_columns
        )
