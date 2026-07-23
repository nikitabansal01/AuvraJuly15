"""Repair the two non-unique check-in thread indexes.

Usage:
    DATABASE_URL=... python scripts/fix_db_index.py
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, text


def fix_indexes() -> None:
    engine = create_engine(os.environ["DATABASE_URL"], hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("DROP INDEX IF EXISTS idx_care_plan_thread_user_date")
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_care_plan_thread_user_date "
                    "ON care_plan_checkin_threads (uid, local_date)"
                )
            )
            connection.execute(
                text("DROP INDEX IF EXISTS idx_symptom_thread_user_date")
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_symptom_thread_user_date "
                    "ON symptom_checkin_threads (uid, local_date)"
                )
            )
    finally:
        engine.dispose()

    print("Indexes updated successfully.")


if __name__ == "__main__":
    fix_indexes()
