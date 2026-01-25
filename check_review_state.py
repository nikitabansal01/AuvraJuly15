import psycopg2
import os

conn = psycopg2.connect(
    host="aws-0-us-east-1.pooler.supabase.com",
    port=6543,
    database="postgres",
    user="postgres.lxlonwjksmxdtsomtxrd",
    password=os.environ.get("SUPABASE_DB_PASSWORD", "Agk3112003@#")
)

cur = conn.cursor()

# Get recent plans for test user
cur.execute("""
    SELECT id, uid, plan_date, review_completed, created_at
    FROM action_plans
    WHERE uid LIKE 'ITX%'
    ORDER BY created_at DESC
    LIMIT 5
""")

print("Recent plans for test user:")
for row in cur.fetchall():
    print(f"  ID: {row[0]}, date: {row[2]}, review_completed: {row[3]}, created: {row[4]}")

# Check if there are any plans needing review
cur.execute("""
    SELECT id, plan_date, review_completed
    FROM action_plans
    WHERE uid LIKE 'ITX%' AND review_completed = false
    ORDER BY plan_date DESC
""")

print("\nPlans needing review (review_completed=false):")
rows = cur.fetchall()
if not rows:
    print("  NONE - all plans have review_completed=true")
else:
    for row in rows:
        print(f"  ID: {row[0]}, date: {row[1]}, review_completed: {row[2]}")

cur.close()
conn.close()
