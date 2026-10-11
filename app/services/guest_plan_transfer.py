"""Adopt a signup's guest plan without replacing a real assigned daily plan."""
from sqlalchemy import text

async def transfer_guest_plan(db, plan_id, uid, plan_date):
    result = await db.execute(text("""
        UPDATE action_plans AS candidate
        SET uid = :uid, session_id = NULL, plan_date = :plan_date
        WHERE candidate.id = :plan_id
          AND (candidate.uid IS NULL OR candidate.uid = :uid)
          AND NOT EXISTS (
              SELECT 1 FROM action_plans AS assigned
              WHERE assigned.uid = :uid AND assigned.plan_date = :plan_date
                AND assigned.id <> candidate.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM action_plan_items AS item
              WHERE item.plan_id = candidate.id
                AND item.uid IS NOT NULL AND item.uid <> :uid
          )
        RETURNING candidate.id
    """), {"uid": uid, "plan_id": plan_id, "plan_date": plan_date})
    if result.scalar_one_or_none() is None:
        return False
    await db.execute(text("""
        UPDATE action_plan_items SET uid = :uid, session_id = NULL
        WHERE plan_id = :plan_id AND (uid IS NULL OR uid = :uid)
    """), {"uid": uid, "plan_id": plan_id})
    return True
