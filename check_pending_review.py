#!/usr/bin/env python3
"""Check the current state of action plans and pending reviews for the test user."""
import psycopg2
from datetime import datetime, date

conn = psycopg2.connect(
    host="aws-0-us-east-1.pooler.supabase.com",
    port=6543,
    database="postgres",
    user="postgres.lxlonwjksmxdtsomtxrd",
    password="Agk3112003@#"
)

cur = conn.cursor()

print("=" * 70)
print("DATABASE STATE CHECK FOR TEST USER")
print("=" * 70)
print()

# 1. Get the test user's UID
cur.execute("SELECT uid FROM user_profiles WHERE uid LIKE 'ITX%' LIMIT 1")
row = cur.fetchone()
if not row:
    print("No test user found!")
    exit(1)
uid = row[0]
print(f"Test User UID: {uid[:20]}...")
print()

# 2. Show recent plans
print("RECENT ACTION PLANS:")
print("-" * 70)
cur.execute("""
    SELECT id, plan_date, review_completed, created_at 
    FROM action_plans 
    WHERE uid = %s 
    ORDER BY created_at DESC 
    LIMIT 10
""", (uid,))

for row in cur.fetchall():
    plan_id, plan_date, review_completed, created_at = row
    status = "✅ Reviewed" if review_completed else "⏳ PENDING REVIEW"
    print(f"  Plan {plan_id}: {plan_date} | {status} | Created: {created_at}")
print()

# 3. Check plans needing review (plan_date < today AND review_completed = false)
print("PLANS NEEDING REVIEW (plan_date < today AND review_completed=false):")
print("-" * 70)
cur.execute("""
    SELECT id, plan_date, review_completed 
    FROM action_plans 
    WHERE uid = %s 
      AND plan_date < CURRENT_DATE 
      AND review_completed = false
    ORDER BY plan_date DESC
""", (uid,))

rows = cur.fetchall()
if not rows:
    print("  NONE - No pending reviews!")
else:
    for row in rows:
        print(f"  Plan {row[0]}: {row[1]} | review_completed: {row[2]}")
print()

# 4. Check total plan count (for new user protection)
cur.execute("SELECT COUNT(*) FROM action_plans WHERE uid = %s", (uid,))
total_plans = cur.fetchone()[0]
print(f"TOTAL PLANS FOR USER: {total_plans}")
if total_plans <= 1:
    print("  ⚠️  User has <= 1 plan - review modal will be SKIPPED (new user protection)")
print()

# 5. Database current date
cur.execute("SELECT CURRENT_DATE, CURRENT_TIMESTAMP AT TIME ZONE 'UTC'")
row = cur.fetchone()
print(f"DATABASE CURRENT_DATE: {row[0]}")
print(f"DATABASE CURRENT_TIME: {row[1]}")

# 6. Check user timezone
cur.execute("SELECT current_timezone FROM user_profiles WHERE uid = %s", (uid,))
row = cur.fetchone()
user_tz = row[0] if row else "Unknown"
print(f"USER TIMEZONE: {user_tz}")

conn.close()
print()
print("=" * 70)
