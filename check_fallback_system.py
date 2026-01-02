#!/usr/bin/env python3
"""
Check if the fallback system components are all in place and working.
"""
import os
import sys
from dotenv import load_dotenv
load_dotenv()

print("=== CHECKING FALLBACK SYSTEM COMPONENTS ===")
print()

# 1. Check API keys are set
openai_key = os.getenv("OPENAI_API_KEY")
groq_key = os.getenv("GROQ_API_KEY")
fallback_model = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")

print("1. API KEYS:")
if openai_key:
    print(f"   OPENAI_API_KEY: Set ({openai_key[:10]}...)")
else:
    print("   OPENAI_API_KEY: NOT SET")
    
if groq_key:
    print(f"   GROQ_API_KEY: Set ({groq_key[:10]}...)")
else:
    print("   GROQ_API_KEY: NOT SET")
    
print(f"   GROQ_FALLBACK_MODEL: {fallback_model}")
print()

# 2. Check imports work
print("2. IMPORTS:")
try:
    from app.services.evaluation_service import get_action_plan_evaluator, ActionPlanEvaluator
    print("   evaluation_service imports OK")
except Exception as e:
    print(f"   evaluation_service import FAILED: {e}")

try:
    from app.services.action_plan_generator import ActionPlanGenerator
    print("   action_plan_generator imports OK")
except Exception as e:
    print(f"   action_plan_generator import FAILED: {e}")

try:
    from app.core.database import AIModelUsageLog, ActionPlanEvaluation
    print("   database models imports OK")
except Exception as e:
    print(f"   database models import FAILED: {e}")
print()

# 3. Check database tables exist
print("3. DATABASE TABLES:")
from sqlalchemy import create_engine, text
engine = create_engine(os.getenv("DATABASE_URL"))
with engine.connect() as conn:
    # Check ai_model_usage_logs
    try:
        result = conn.execute(text("SELECT COUNT(*) FROM ai_model_usage_logs"))
        count = result.scalar()
        print(f"   ai_model_usage_logs exists ({count} records)")
    except Exception as e:
        print(f"   ai_model_usage_logs: {e}")
    
    # Check action_plan_evaluations
    try:
        result = conn.execute(text("SELECT COUNT(*) FROM action_plan_evaluations"))
        count = result.scalar()
        print(f"   action_plan_evaluations exists ({count} records)")
    except Exception as e:
        print(f"   action_plan_evaluations: {e}")
print()

# 4. Check if there are any fallback events recorded
print("4. FALLBACK EVENTS:")
with engine.connect() as conn:
    try:
        result = conn.execute(text("""
            SELECT COUNT(*) FROM ai_model_usage_logs 
            WHERE fallback_model IS NOT NULL
        """))
        fallback_count = result.scalar()
        print(f"   Total fallback events: {fallback_count}")
        
        if fallback_count > 0:
            result = conn.execute(text("""
                SELECT plan_id, primary_model, fallback_model, switch_reason, created_at
                FROM ai_model_usage_logs 
                WHERE fallback_model IS NOT NULL
                ORDER BY created_at DESC LIMIT 3
            """))
            rows = result.fetchall()
            for row in rows:
                print(f"   - Plan {row[0]}: {row[1]} -> {row[2]}")
                reason = row[3] or "N/A"
                if len(reason) > 80:
                    reason = reason[:80] + "..."
                print(f"     Reason: {reason}")
    except Exception as e:
        print(f"   Error: {e}")
print()

# 5. Check low-score evaluations
print("5. LOW SCORE EVALUATIONS (condition_appropriateness < 70):")
with engine.connect() as conn:
    try:
        result = conn.execute(text("""
            SELECT plan_id, condition_appropriateness, personalization_score, overall_quality_score
            FROM action_plan_evaluations 
            WHERE condition_appropriateness < 70
            ORDER BY created_at DESC LIMIT 5
        """))
        rows = result.fetchall()
        if rows:
            for row in rows:
                print(f"   Plan {row[0]}: condition={row[1]}, personalization={row[2]}, overall={row[3]}")
        else:
            print("   No low-score evaluations found (all plans scored >= 70)")
    except Exception as e:
        print(f"   Error: {e}")
print()

# 6. Check all evaluations summary
print("6. EVALUATION SUMMARY:")
with engine.connect() as conn:
    try:
        result = conn.execute(text("""
            SELECT 
                COUNT(*) as total,
                AVG(condition_appropriateness) as avg_condition,
                AVG(personalization_score) as avg_personalization,
                AVG(overall_quality_score) as avg_overall,
                MIN(condition_appropriateness) as min_condition,
                MAX(condition_appropriateness) as max_condition
            FROM action_plan_evaluations
        """))
        row = result.fetchone()
        if row and row[0] > 0:
            print(f"   Total evaluations: {row[0]}")
            print(f"   Avg condition_appropriateness: {row[1]:.1f}" if row[1] else "   Avg condition: N/A")
            print(f"   Avg personalization_score: {row[2]:.1f}" if row[2] else "   Avg personalization: N/A")
            print(f"   Avg overall_quality_score: {row[3]:.1f}" if row[3] else "   Avg overall: N/A")
            print(f"   Min/Max condition: {row[4]} / {row[5]}")
        else:
            print("   No evaluations recorded yet")
    except Exception as e:
        print(f"   Error: {e}")

print()
print("=== CHECK COMPLETE ===")
