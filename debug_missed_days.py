"""Trace missed-day and streak-freeze logic for one explicitly selected user.

Usage:
    DATABASE_URL=... DEBUG_UID=... python debug_missed_days.py
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func

from app.core.database import (
    ActionPlan,
    ActionPlanItem,
    SessionLocal,
    UserProfile,
    UserStreakData,
)


def main() -> None:
    uid = os.environ["DEBUG_UID"]

    with SessionLocal() as db:
        streak_data = (
            db.query(UserStreakData).filter(UserStreakData.uid == uid).first()
        )
        profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
        timezone_name = (
            profile.current_timezone if profile and profile.current_timezone else "UTC"
        )
        today = datetime.now(ZoneInfo(timezone_name)).date()

        frozen_dates = []
        if streak_data and streak_data.freeze_used_dates:
            frozen_dates = [
                datetime.fromisoformat(value).date()
                for value in streak_data.freeze_used_dates
                if value
            ]

        missed_days = []
        check_date = today - timedelta(days=1)
        for _ in range(7):
            plan = (
                db.query(ActionPlan)
                .filter(
                    and_(
                        ActionPlan.uid == uid,
                        ActionPlan.plan_date == check_date,
                    )
                )
                .first()
            )
            completed_count = 0
            if plan:
                completed_count = (
                    db.query(func.count(ActionPlanItem.id))
                    .filter(
                        and_(
                            ActionPlanItem.plan_id == plan.id,
                            ActionPlanItem.is_completed.is_(True),
                            ActionPlanItem.is_replaced.isnot(True),
                        )
                    )
                    .scalar()
                    or 0
                )

            if completed_count:
                break
            if check_date not in frozen_dates:
                missed_days.append(check_date)
            check_date -= timedelta(days=1)

        freeze_count = streak_data.freeze_count if streak_data else 0
        print(f"timezone={timezone_name}")
        print(f"missed_days={missed_days}")
        print(f"freeze_count={freeze_count}")
        print(f"can_freeze={bool(missed_days) and freeze_count >= len(missed_days)}")


if __name__ == "__main__":
    main()
