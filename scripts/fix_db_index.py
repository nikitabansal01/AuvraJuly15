
import os
import sys
import sqlalchemy
from sqlalchemy import create_engine, text

# Hardcoded from .env check
DATABASE_URL = "postgresql://postgres.dculqiokbqnwuhqpdret:HlsJUbre21mItNrw@aws-0-us-east-2.pooler.supabase.com:5432/postgres"

def fix_indexes():
    print(f"Connecting to DB...", file=sys.stderr)
    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as conn:
            print("Dropping UNIQUE constraints...", file=sys.stderr)
            
            # 1. Care Plan Threads
            print("Fixing care_plan_checkin_threads...", file=sys.stderr)
            conn.execute(text("DROP INDEX IF EXISTS idx_care_plan_thread_user_date"))
            # Ensure we don't error if it doesn't exist? No, create should be fine.
            # But if we want to be safe about recreating:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_care_plan_thread_user_date ON care_plan_checkin_threads (uid, local_date)"))
            
            # 2. Symptom Check-in Threads
            print("Fixing symptom_checkin_threads...", file=sys.stderr)
            conn.execute(text("DROP INDEX IF EXISTS idx_symptom_thread_user_date"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_symptom_thread_user_date ON symptom_checkin_threads (uid, local_date)"))
            
            conn.commit()
            print("✅ Successfully updated indexes!", file=sys.stderr)
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    fix_indexes()
