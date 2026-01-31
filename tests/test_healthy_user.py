"""
Test script for Healthy User Edge Case
Tests that users with no symptoms get is_healthy=True response
"""
import sys
sys.path.insert(0, '/Users/mohanganesh/AUVRA/AuvraJuly15')

from app.services.root_cause_engine import RootCauseEngine

# Test Case 1: Healthy user (no symptoms)
print("\n" + "="*60)
print("TEST CASE 1: Healthy User (No Symptoms)")
print("="*60)

healthy_user_data = {
    "period_description": "Regular",  # Not irregular
    "cycle_length": "21-30 days",      # Normal range
    "period_concerns": [],              # No concerns
    "body_concerns": [],                # No concerns
    "skin_hair_concerns": [],           # No concerns
    "mental_health_concerns": [],       # No concerns
    "other_concerns": ["None of these"], # Explicitly none
    "diagnosed_conditions": [],          # No diagnosis
    "family_history": [],                # No family history
    "workout_intensity": None,           # Skipped
    "sleep_duration": None,              # Skipped
    "stress_level": None                 # Skipped
}

result = RootCauseEngine.analyze_hormone_imbalance(healthy_user_data)
print(f"Result: {result}")
print(f"is_healthy: {result.get('is_healthy', False)}")
print(f"total_score: {result.get('total_score', 'N/A')}")
print(f"Expected: is_healthy=True, total_score < 2")

assert result.get("is_healthy") == True, "FAIL: Healthy user should have is_healthy=True"
print("✅ PASSED: Healthy user detected correctly!")


# Test Case 2: User with mild symptoms (should NOT be marked healthy)
print("\n" + "="*60)
print("TEST CASE 2: User with Mild Symptoms")
print("="*60)

mild_symptom_user = {
    "period_description": "Irregular",   # +2 androgens_high, +1 thyroid_low
    "cycle_length": "21-30 days",
    "period_concerns": [],
    "body_concerns": [],
    "skin_hair_concerns": [],
    "mental_health_concerns": [],
    "other_concerns": [],
    "diagnosed_conditions": [],
    "family_history": [],
    "workout_intensity": None,
    "sleep_duration": None,
    "stress_level": None
}

result2 = RootCauseEngine.analyze_hormone_imbalance(mild_symptom_user)
print(f"Result: {result2}")
print(f"is_healthy: {result2.get('is_healthy', False)}")
print(f"primary_imbalance: {result2.get('primary_imbalance')}")
print(f"Expected: is_healthy=False (or not present), has primary_imbalance")

assert result2.get("is_healthy", False) == False, "FAIL: User with irregular periods should not be marked healthy"
assert result2.get("primary_imbalance") is not None, "FAIL: Should have a primary imbalance"
print("✅ PASSED: User with symptoms correctly processed!")


# Test Case 3: User with PCOS diagnosis (definitely not healthy)
print("\n" + "="*60)
print("TEST CASE 3: User with PCOS Diagnosis")
print("="*60)

pcos_user = {
    "period_description": "Regular",
    "cycle_length": "21-30 days",
    "period_concerns": [],
    "body_concerns": [],
    "skin_hair_concerns": [],
    "mental_health_concerns": [],
    "other_concerns": [],
    "diagnosed_conditions": ["PCOS"],  # +5 androgens_high, +5 insulin_high
    "family_history": [],
    "workout_intensity": None,
    "sleep_duration": None,
    "stress_level": None
}

result3 = RootCauseEngine.analyze_hormone_imbalance(pcos_user)
print(f"Result: {result3}")
print(f"is_healthy: {result3.get('is_healthy', False)}")
print(f"primary_imbalance: {result3.get('primary_imbalance')}")
print(f"Expected: is_healthy=False, primary=androgens or insulin")

assert result3.get("is_healthy", False) == False, "FAIL: PCOS user should not be marked healthy"
assert result3.get("primary_imbalance") in ["androgens", "insulin"], "FAIL: PCOS should show androgens or insulin"
print("✅ PASSED: PCOS user correctly processed!")


print("\n" + "="*60)
print("ALL TESTS PASSED! ✅")
print("="*60)
