#!/usr/bin/env python3
"""Summarize pending action-plan reviews for one selected user.

Usage:
    DATABASE_URL=... DEBUG_UID=... python check_pending_review.py
"""

from __future__ import annotations

import os

import psycopg2


def main() -> None:
    uid = os.environ["DEBUG_UID"]
    with psycopg2.connect(os.environ["DATABASE_URL"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, plan_date, review_completed, created_at
                FROM action_plans
                WHERE uid = %s
                ORDER BY created_at DESC
                LIMIT 10
                """,
                (uid,),
            )
            recent = cursor.fetchall()
            cursor.execute(
                """
                SELECT id, plan_date, review_completed
                FROM action_plans
                WHERE uid = %s
                  AND plan_date < CURRENT_DATE
                  AND review_completed = false
                ORDER BY plan_date DESC
                """,
                (uid,),
            )
            pending = cursor.fetchall()
            cursor.execute(
                "SELECT current_timezone FROM user_profiles WHERE uid = %s",
                (uid,),
            )
            timezone_row = cursor.fetchone()

    print(f"recent_plan_count={len(recent)}")
    print(f"pending_review_count={len(pending)}")
    print(f"timezone={timezone_row[0] if timezone_row else 'unknown'}")
    for row in pending:
        print(row)


if __name__ == "__main__":
    main()
