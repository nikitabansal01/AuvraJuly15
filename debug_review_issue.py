#!/usr/bin/env python3
"""Explain why a selected user's review modal may not be appearing.

Usage:
    DATABASE_URL=... DEBUG_UID=... python debug_review_issue.py
"""

from __future__ import annotations

import os

import psycopg2


def main() -> None:
    uid = os.environ["DEBUG_UID"]
    with psycopg2.connect(os.environ["DATABASE_URL"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT CURRENT_DATE")
            current_date = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT id, plan_date, review_completed, created_at
                FROM action_plans
                WHERE uid = %s
                ORDER BY plan_date DESC
                """,
                (uid,),
            )
            plans = cursor.fetchall()

    print(f"database_date={current_date}")
    print(f"plan_count={len(plans)}")
    for plan in plans:
        needs_review = plan[1] < current_date and not plan[2]
        print(f"plan={plan} needs_review={needs_review}")


if __name__ == "__main__":
    main()
