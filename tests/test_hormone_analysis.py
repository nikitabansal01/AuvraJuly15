#!/usr/bin/env python3
"""Test script for the Root Cause Hormone Analysis Engine"""

from app.services.root_cause_engine import RootCauseEngine

# Test hormone analysis with sample user data
test_user_data = {
    'age': 25,
    'period_description': 'Irregular',
    'cycle_length': '35+ days',
    'period_concerns': ['Irregular Periods', 'Painful Periods'],
    'body_concerns': ['Recent weight gain', 'Difficulty losing weight / stubborn belly fat'],
    'skin_hair_concerns': ['Adult Acne', 'Hirsutism (hair growth on chin, nipples etc)'],
    'mental_health_concerns': ['Mood swings', 'Stress'],
    'diagnosed_conditions': ['PCOS'],
    'family_history': ['Diabetes'],
    'workout_intensity': 'Low',
    'sleep_duration': '<6 hours',
    'stress_level': 'High',
    'top_concern': 'Recent weight gain'
}

print("\n" + "=" * 60)
print("TESTING HORMONE ANALYSIS (100% LOCAL - NO API KEYS NEEDED!)")
print("=" * 60)

result = RootCauseEngine.analyze_hormone_imbalance(test_user_data)

print("\n" + "=" * 60)
print("HORMONE ANALYSIS RESULT")
print("=" * 60)
print(f"\n🎯 Primary Imbalance: {result['primary_imbalance'].upper()} ({result['primary_level']})")
print(f"\n📊 Secondary Imbalances:")
for i, (hormone, level) in enumerate(zip(result['secondary_imbalances'], result['secondary_levels'])):
    print(f"   {i+1}. {hormone} ({level})")

print(f"\n📈 All Hormone Scores (non-zero):")
for hormone, score in sorted(result['all_scores'].items(), key=lambda x: -x[1]):
    if score > 0:
        print(f"   {hormone}: {score}")

print("\n✅ Hormone analysis works without any API keys!")
print("=" * 60 + "\n")
