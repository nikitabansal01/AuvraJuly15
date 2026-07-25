"""Show recent and pending action-plan reviews for one selected user.

Usage:
    DATABASE_URL=... DEBUG_UID=... python check_review_state.py
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
                WHERE uid = %s AND review_completed = false
                ORDER BY plan_date DESC
                """,
                (uid,),
            )
            pending = cursor.fetchall()

    print("Recent plans:")
    for row in recent:
        print(row)
    print("Pending reviews:")
    for row in pending:
        print(row)


if __name__ == "__main__":
    main()
