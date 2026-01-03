#!/usr/bin/env python3
"""Calculate OpenAI API costs from Supabase database tables."""

import os
import sys
import re
from datetime import datetime, timedelta

# Load .env file manually
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value.strip('"\'')

from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in .env")
    sys.exit(1)

# Supabase needs sslmode
if '?' not in DATABASE_URL:
    DATABASE_URL += '?sslmode=require'

# OpenAI pricing per 1M tokens (current as of 2025)
# GPT-4o: $2.50 input / $10.00 output per 1M
# GPT-4o-mini: $0.15 input / $0.60 output per 1M
PRICING = {
    'gpt-4o': {'input': 2.50/1_000_000, 'output': 10.00/1_000_000},
    'gpt-4o-mini': {'input': 0.15/1_000_000, 'output': 0.60/1_000_000},
    'gpt-4': {'input': 30.00/1_000_000, 'output': 60.00/1_000_000},
    'gpt-4-turbo': {'input': 10.00/1_000_000, 'output': 30.00/1_000_000},
    'gpt-3.5-turbo': {'input': 0.50/1_000_000, 'output': 1.50/1_000_000},
}

def main():
    engine = create_engine(DATABASE_URL)
    
    print("=" * 60)
    print("OpenAI API Cost Analysis (last 30 days)")
    print("=" * 60)
    
    with engine.connect() as conn:
        # 1) ChatMessage tokens (chatbot / weekly check-in)
        chat_query = text("""
            SELECT 
                COALESCE(model_used, 'unknown') as model,
                SUM(COALESCE(tokens_input, 0)) as total_input,
                SUM(COALESCE(tokens_output, 0)) as total_output,
                COUNT(*) as message_count
            FROM chat_messages
            WHERE created_at >= NOW() - INTERVAL '30 days'
              AND (tokens_input IS NOT NULL OR tokens_output IS NOT NULL)
            GROUP BY model_used
            ORDER BY COALESCE(SUM(tokens_input), 0) + COALESCE(SUM(tokens_output), 0) DESC
        """)
        
        result = conn.execute(chat_query)
        rows = result.fetchall()
        
        print("\n📬 ChatMessage Token Usage (chatbot/weekly check-in):")
        print("-" * 50)
        
        chat_total_cost = 0.0
        if rows:
            for row in rows:
                model = row[0] or 'unknown'
                inp = row[1] or 0
                out = row[2] or 0
                count = row[3]
                
                # Get pricing (default to gpt-4o-mini if unknown)
                price = PRICING.get(model.lower(), PRICING['gpt-4o-mini'])
                cost = inp * price['input'] + out * price['output']
                chat_total_cost += cost
                
                print(f"  Model: {model}")
                print(f"    Messages: {count}")
                print(f"    Input tokens: {inp:,}")
                print(f"    Output tokens: {out:,}")
                print(f"    Cost: ${cost:.4f}")
                print()
        else:
            print("  No chat messages with token data in last 30 days")
        
        # 2) Check ActionPlan table for generation_cost
        plan_query = text("""
            SELECT 
                COALESCE(gpt_model_used, 'gpt-4o-mini') as model,
                COUNT(*) as plan_count,
                SUM(COALESCE(generation_time_ms, 0)) as total_time_ms,
                STRING_AGG(DISTINCT generation_cost, ', ') as cost_strings
            FROM action_plans
            WHERE created_at >= NOW() - INTERVAL '30 days'
            GROUP BY gpt_model_used
        """)
        
        result = conn.execute(plan_query)
        plan_rows = result.fetchall()
        
        print("\n📋 ActionPlan Generation (last 30 days):")
        print("-" * 50)
        
        plan_cost = 0.0
        total_plans = 0
        if plan_rows:
            for row in plan_rows:
                model = row[0] or 'gpt-4o-mini'
                count = row[1]
                total_plans += count
                time_ms = row[2] or 0
                cost_str = row[3]
                
                print(f"  Model: {model}")
                print(f"    Plans generated: {count}")
                print(f"    Total generation time: {time_ms/1000:.1f}s")
                if cost_str:
                    print(f"    Cost strings stored: {cost_str[:100]}...")
                    # Try to parse cost strings like "$0.0123"
                    costs = re.findall(r'\$?([\d.]+)', cost_str or '')
                    for c in costs:
                        try:
                            plan_cost += float(c)
                        except:
                            pass
                print()
        else:
            print("  No action plans in last 30 days")
        
        # 3) Weekly Check-in table check
        try:
            checkin_query = text("""
                SELECT COUNT(*) as count
                FROM weekly_checkins
                WHERE created_at >= NOW() - INTERVAL '30 days'
            """)
            result = conn.execute(checkin_query)
            checkin_count = result.scalar() or 0
            print(f"\n📝 Weekly Check-ins (last 30 days): {checkin_count}")
        except Exception as e:
            print(f"\n📝 Weekly Check-ins: Could not query ({e})")
        
        # 4) Count all records for context
        tables_query = text("""
            SELECT 
                (SELECT COUNT(*) FROM chat_sessions WHERE created_at >= NOW() - INTERVAL '30 days') as chat_sessions,
                (SELECT COUNT(*) FROM chat_messages WHERE created_at >= NOW() - INTERVAL '30 days') as chat_messages,
                (SELECT COUNT(*) FROM action_plans WHERE created_at >= NOW() - INTERVAL '30 days') as action_plans,
                (SELECT COUNT(*) FROM action_plan_items WHERE created_at >= NOW() - INTERVAL '30 days') as action_items,
                (SELECT COUNT(*) FROM action_plan_feedback WHERE created_at >= NOW() - INTERVAL '30 days') as feedback
        """)
        
        result = conn.execute(tables_query)
        counts = result.fetchone()
        
        print("\n📊 Record Counts (last 30 days):")
        print("-" * 50)
        print(f"  Chat sessions: {counts[0]}")
        print(f"  Chat messages: {counts[1]}")
        print(f"  Action plans: {counts[2]}")
        print(f"  Action items: {counts[3]}")
        print(f"  Feedback entries: {counts[4]}")
        
        # 5) Estimate action plan generation cost
        # Each action plan uses ~4000-8000 input tokens and ~2000-4000 output tokens with gpt-4o-mini
        # Estimate: ~6000 input + ~3000 output per plan
        action_plan_count = counts[2] or 0
        estimated_plan_input = action_plan_count * 6000
        estimated_plan_output = action_plan_count * 3000
        price = PRICING['gpt-4o-mini']
        estimated_plan_cost = estimated_plan_input * price['input'] + estimated_plan_output * price['output']
        
        print("\n💰 ESTIMATED OpenAI API COST (last 30 days):")
        print("=" * 60)
        print(f"  Chat/Weekly Check-in (from DB tokens): ${chat_total_cost:.4f}")
        print(f"  Action Plan generation (estimated):    ${estimated_plan_cost:.4f}")
        print(f"    ({action_plan_count} plans × ~6K input + ~3K output tokens each)")
        if plan_cost > 0:
            print(f"  Action Plan (from stored cost field):  ${plan_cost:.4f}")
        print("-" * 50)
        total = chat_total_cost + estimated_plan_cost
        print(f"  TOTAL ESTIMATED:                       ${total:.4f}")
        print()
        print("Note: This does NOT include image generation costs (DALL-E/FLUX).")
        print("Actual costs may vary - check OpenAI dashboard for exact billing.")

if __name__ == '__main__':
    main()
