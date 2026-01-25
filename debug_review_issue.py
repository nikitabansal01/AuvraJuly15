#!/usr/bin/env python3
"""Check why review modal might not be appearing after 5+ day absence."""
import psycopg2
from datetime import datetime, date, timedelta

conn = psycopg2.connect(
    host="aws-0-us-east-1.pooler.supabase.com",
    port=6543,
    database="postgres",
    user="postgres.lxlonwjksmxdtsomtxrd",
    password="Agk3112003@#"
)

cur = conn.cursor()

# Get test user UID
cur.execute("SELECT uid FROM user_profiles WHERE uid LIKE 'ITX%' LIMIT 1")
row = cur.fetchone()
if not row:
    print("No test user found!")
    exit(1)
uid = row[0]
print(f"User UID: {uid}")
print()

# Get database current date
cur.execute("SELECT CURRENT_DATE, CURRENT_TIMESTAMP AT TIME ZONE 'UTC'")
row = cur.fetchone()
db_date = row[0]
print(f"Database CURRENT_DATE: {db_date}")
print()

# Get ALL plans for this user
print("=" * 70)
print("ALL ACTION PLANS FOR THIS USER:")
print("=" * 70)
cur.execute("""
    SELECT id, plan_date, review_completed, created_at 
    FROM action_plans 
    WHERE uid = %s 
    ORDER BY plan_date DESC
""", (uid,))

plans = cur.fetchall()
print(f"Total plans: {len(plans)}")
print()

for plan_id, plan_date, review_completed, created_at in plans:
    status = "✅ REVIEWED" if review_completed else "❌ NOT REVIEWED"
    is_before_today = plan_date < db_date
    needs_review = is_before_today and not review_completed
    marker = ">>> NEEDS REVIEW <<<" if needs_review else ""
    print(f"  Plan {plan_id}: {plan_date} | {status} | Created: {created_at} {marker}")

print()

# Specifically check the pending review query
print("=" * 70)
print("PENDING REVIEW QUERY (plan_date < today AND review_completed = false):")
print("=" * 70)
cur.execute("""
    SELECT id, plan_date, review_completed 
    FROM action_plans 
    WHERE uid = %s AND plan_date < %s AND review_completed = false
    ORDER BY plan_date DESC
    LIMIT 1
""", (uid, db_date))

pending = cur.fetchone()
if pending:
    print(f"FOUND: Plan {pending[0]}, date: {pending[1]}, review_completed: {pending[2]}")
else:
    print("NO PENDING PLANS FOUND!")
    print()
    print("Possible reasons:")
    print("  1. All plans have review_completed = true")
    print("  2. The only plan is from TODAY (plan_date = today)")
    print("  3. User has no plans at all")

print()

# Check if there's a plan from today
cur.execute("""
    SELECT id, plan_date, review_completed 
    FROM action_plans 
    WHERE uid = %s AND plan_date = %s
""", (uid, db_date))
today_plan = cur.fetchone()
if today_plan:
    print(f"TODAY'S PLAN: Plan {today_plan[0]}, date: {today_plan[1]}")
else:
    print("NO PLAN FOR TODAY YET")

conn.close()
