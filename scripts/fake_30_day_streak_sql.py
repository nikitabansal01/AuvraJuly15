#!/usr/bin/env python3
"""
Fake a 30-day streak using raw SQL (no dependencies needed on conda).
Run this on the server using: DATABASE_URL=<url> python fake_30_day_streak_sql.py
"""
import os
import psycopg2
from datetime import date, timedelta

# Connection from DATABASE_URL
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print("❌ Set DATABASE_URL environment variable")
    exit(1)

# Convert postgres:// to postgresql:// if needed
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

USER_UID = "QEAS0DwsVDfi4VvdulM6yvHWp3C3"

def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    try:
        # Get user's streak data
        cur.execute("SELECT freeze_count, current_streak FROM user_streak_data WHERE uid = %s", (USER_UID,))
        row = cur.fetchone()
        if not row:
            print(f"❌ No streak data for {USER_UID}")
            return
        
        print(f"Before: freeze_count={row[0]}, current_streak={row[1]}")
        
        today = date(2025, 12, 29)
        created_count = 0
        updated_count = 0
        
        for i in range(1, 31):  # 30 days back
            plan_date = today - timedelta(days=i)
            
            # Check if plan exists
            cur.execute("SELECT id FROM action_plans WHERE uid = %s AND plan_date = %s", (USER_UID, plan_date))
            existing = cur.fetchone()
            
            if existing:
                plan_id = existing[0]
                # Mark all items as completed
                cur.execute(
                    "UPDATE action_plan_items SET is_completed = true WHERE plan_id = %s",
                    (plan_id,)
                )
                updated_count += 1
                print(f"  ✅ Marked items completed for {plan_date} (plan_id={plan_id})")
            else:
                # Create a new plan
                cur.execute("""
                    INSERT INTO action_plans (uid, plan_date, cycle_phase, primary_hormone, created_at, updated_at)
                    VALUES (%s, %s, 'follicular', 'estrogen', NOW(), NOW())
                    RETURNING id
                """, (USER_UID, plan_date))
                plan_id = cur.fetchone()[0]
                
                # Add 4 completed items
                for slot in range(1, 5):
                    time_slot = ['morning', 'afternoon', 'evening', 'anytime'][slot - 1]
                    cur.execute("""
                        INSERT INTO action_plan_items 
                        (plan_id, uid, slot, time_slot, category, title, specific_action, target_hormone, is_completed, created_at)
                        VALUES (%s, %s, %s, %s, 'food', %s, 'Fake action for testing', 'estrogen', true, NOW())
                    """, (plan_id, USER_UID, slot, time_slot, f'Fake Item {slot}'))
                
                created_count += 1
                print(f"  ✅ Created plan with 4 completed items for {plan_date}")
        
        # Clear frozen dates
        cur.execute(
            "UPDATE user_streak_data SET freeze_used_dates = '[]' WHERE uid = %s",
            (USER_UID,)
        )
        
        conn.commit()
        
        # Verify
        cur.execute("SELECT freeze_count, current_streak FROM user_streak_data WHERE uid = %s", (USER_UID,))
        row = cur.fetchone()
        print(f"\n✅ Done! Created {created_count} plans, updated {updated_count} existing")
        print(f"After: freeze_count={row[0]}")
        print(f"\nRefresh the app to see streak = 30")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
