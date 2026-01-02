#!/usr/bin/env python3
"""
AUVRA Flow Verification - Code Structure Test
==============================================

This verifies that all the required code components exist for the 
complete user flow from login to review modal.

No server required - this is a static code analysis.
"""

import os
import sys
import importlib.util

# Add the project to path
sys.path.insert(0, '/Users/mohanganesh/AUVRA/AuvraJuly15')

test_results = {"passed": 0, "failed": 0, "details": []}

def log_test(name: str, passed: bool, message: str = ""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status}: {name}")
    if message:
        print(f"       {message}")
    if passed:
        test_results["passed"] += 1
    else:
        test_results["failed"] += 1
    test_results["details"].append({"name": name, "passed": passed, "message": message})

def check_module_exists(module_path: str, module_name: str) -> bool:
    """Check if a Python module exists and can be imported"""
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        return spec is not None
    except:
        return False

def check_file_contains(filepath: str, patterns: list) -> dict:
    """Check if a file contains certain patterns"""
    results = {}
    try:
        with open(filepath, 'r') as f:
            content = f.read()
            for pattern in patterns:
                results[pattern] = pattern in content
    except:
        for pattern in patterns:
            results[pattern] = False
    return results

print("""
╔══════════════════════════════════════════════════════════════╗
║     AUVRA COMPLETE FLOW VERIFICATION - CODE STRUCTURE        ║
║                                                              ║
║  Verifying: Login → Profile → Action Plan → Review → Rewards ║
╚══════════════════════════════════════════════════════════════╝
""")

# ============================================================================
# SECTION 1: API ENDPOINTS EXIST
# ============================================================================

print("\n" + "=" * 60)
print("SECTION 1: API ENDPOINT FILES")
print("=" * 60)

endpoint_files = [
    ("/Users/mohanganesh/AUVRA/AuvraJuly15/app/api/v1/endpoints/auth.py", "Authentication"),
    ("/Users/mohanganesh/AUVRA/AuvraJuly15/app/api/v1/endpoints/users.py", "User Management"),
    ("/Users/mohanganesh/AUVRA/AuvraJuly15/app/api/v1/endpoints/action_plan.py", "Action Plan"),
    ("/Users/mohanganesh/AUVRA/AuvraJuly15/app/api/v1/endpoints/cycle.py", "Menstrual Cycle"),
    ("/Users/mohanganesh/AUVRA/AuvraJuly15/app/api/v1/endpoints/progress.py", "Progress Tracking"),
    ("/Users/mohanganesh/AUVRA/AuvraJuly15/app/api/v1/endpoints/rewards.py", "Rewards/Streaks"),
    ("/Users/mohanganesh/AUVRA/AuvraJuly15/app/api/v1/endpoints/weekly_checkin.py", "Weekly Check-in"),
    ("/Users/mohanganesh/AUVRA/AuvraJuly15/app/api/v1/endpoints/timezone.py", "Timezone"),
    ("/Users/mohanganesh/AUVRA/AuvraJuly15/app/api/v1/endpoints/chat.py", "AI Chat"),
]

for filepath, name in endpoint_files:
    exists = os.path.exists(filepath)
    log_test(f"Endpoint: {name}", exists, filepath.split('/')[-1])

# ============================================================================
# SECTION 2: ACTION PLAN ENDPOINTS (DAILY REVIEW)
# ============================================================================

print("\n" + "=" * 60)
print("SECTION 2: ACTION PLAN / DAILY REVIEW ENDPOINTS")
print("=" * 60)

action_plan_file = "/Users/mohanganesh/AUVRA/AuvraJuly15/app/api/v1/endpoints/action_plan.py"

action_plan_endpoints = [
    "@router.get(\"/assignments/today\"",        # Today's plan
    "@router.get(\"/pending-review\"",           # Check pending review
    "@router.post(\"/submit-daily-review\"",     # Submit daily review
    "/complete\", response_model",               # Complete single action
    "@router.post(\"/batch-replace\"",           # Replace actions
    "@router.post(\"/replace\"",                 # Single replacement
    "DailyReviewRequest",                        # Request model (imported)
    "DailyReviewResponse",                       # Response model (imported)
    "PendingReviewResponse",                     # Pending review model (imported)
]

results = check_file_contains(action_plan_file, action_plan_endpoints)
for pattern, found in results.items():
    clean_name = pattern.replace("@router.", "").replace("class ", "Model: ")
    log_test(f"Action Plan: {clean_name[:40]}", found)

# ============================================================================
# SECTION 3: AUTHENTICATION FLOW
# ============================================================================

print("\n" + "=" * 60)
print("SECTION 3: AUTHENTICATION FLOW")
print("=" * 60)

security_file = "/Users/mohanganesh/AUVRA/AuvraJuly15/app/core/security.py"
firebase_file = "/Users/mohanganesh/AUVRA/AuvraJuly15/app/core/firebase.py"

auth_patterns = [
    "verify_firebase_token",
    "get_current_user",
    "HTTPBearer",
    "firebase_admin",
    "auth.verify_id_token",
]

if os.path.exists(security_file):
    results = check_file_contains(security_file, auth_patterns)
    for pattern, found in results.items():
        log_test(f"Security: {pattern}", found)
else:
    log_test("Security file exists", False, "security.py not found")

# ============================================================================
# SECTION 4: DATABASE MODELS
# ============================================================================

print("\n" + "=" * 60)
print("SECTION 4: DATABASE MODELS")
print("=" * 60)

database_file = "/Users/mohanganesh/AUVRA/AuvraJuly15/app/core/database.py"

db_models = [
    "class UserProfile",
    "class ActionPlan",
    "class ActionPlanItem",
    "class ActionPlanDailyReview",
    "class UserStreakData",
    "class ActionPlanFeedback",
]

results = check_file_contains(database_file, db_models)
for pattern, found in results.items():
    log_test(f"DB Model: {pattern.replace('class ', '')}", found)

# ============================================================================
# SECTION 5: STREAK & FREEZE SYSTEM
# ============================================================================

print("\n" + "=" * 60)
print("SECTION 5: STREAK & FREEZE SYSTEM")
print("=" * 60)

streak_patterns = [
    "current_streak",
    "freeze_count",
    "freeze_used_date",
    "streak_maintained",
    "freezes_available",
]

results = check_file_contains(action_plan_file, streak_patterns)
for pattern, found in results.items():
    log_test(f"Streak System: {pattern}", found)

# ============================================================================
# SECTION 6: TIMEZONE HANDLING
# ============================================================================

print("\n" + "=" * 60)
print("SECTION 6: TIMEZONE HANDLING")
print("=" * 60)

timezone_utils_file = "/Users/mohanganesh/AUVRA/AuvraJuly15/app/utils/timezone_utils.py"

tz_patterns = [
    "get_user_current_date",
    "get_user_timezone",
    "zoneinfo",
]

if os.path.exists(timezone_utils_file):
    results = check_file_contains(timezone_utils_file, tz_patterns)
    for pattern, found in results.items():
        log_test(f"Timezone: {pattern}", found)
else:
    log_test("Timezone utils file exists", False)

# ============================================================================
# SECTION 7: AI/GPT INTEGRATION
# ============================================================================

print("\n" + "=" * 60)
print("SECTION 7: AI/GPT INTEGRATION")
print("=" * 60)

services_dir = "/Users/mohanganesh/AUVRA/AuvraJuly15/app/services"
ai_services = [
    "action_plan_generator.py",
    "evaluation_service.py",
]

for service in ai_services:
    service_path = os.path.join(services_dir, service)
    exists = os.path.exists(service_path)
    log_test(f"AI Service: {service}", exists)

# Check for Groq fallback
config_file = "/Users/mohanganesh/AUVRA/AuvraJuly15/app/core/config.py"
env_file = "/Users/mohanganesh/AUVRA/AuvraJuly15/.env"
if os.path.exists(config_file):
    results = check_file_contains(config_file, ["GROQ_API_KEY"])
    for pattern, found in results.items():
        log_test(f"Config: {pattern}", found)
    # Also check .env for GROQ_FALLBACK_MODEL
    if os.path.exists(env_file):
        env_results = check_file_contains(env_file, ["GROQ_FALLBACK_MODEL"])
        for pattern, found in env_results.items():
            log_test(f"Env: {pattern}", found)

# ============================================================================
# SECTION 8: MOBILE FRONTEND COMPONENTS (Basic Check)
# ============================================================================

print("\n" + "=" * 60)
print("SECTION 8: MOBILE FRONTEND COMPONENTS")
print("=" * 60)

mobile_components = [
    ("/Users/mohanganesh/AUVRA/mobileFEKD/app/screens/LoginScreen.tsx", "LoginScreen"),
    ("/Users/mohanganesh/AUVRA/mobileFEKD/app/screens/HomeScreen.tsx", "HomeScreen"),
    ("/Users/mohanganesh/AUVRA/mobileFEKD/app/screens/SplashScreen.tsx", "SplashScreen"),
    ("/Users/mohanganesh/AUVRA/mobileFEKD/components/DailyReviewModal.tsx", "DailyReviewModal"),
    ("/Users/mohanganesh/AUVRA/mobileFEKD/services/authService.ts", "AuthService"),
    ("/Users/mohanganesh/AUVRA/mobileFEKD/services/homeService.ts", "HomeService"),
    ("/Users/mohanganesh/AUVRA/mobileFEKD/services/sessionService.ts", "SessionService"),
    ("/Users/mohanganesh/AUVRA/mobileFEKD/config/firebase.ts", "Firebase Config"),
]

for filepath, name in mobile_components:
    exists = os.path.exists(filepath)
    log_test(f"Frontend: {name}", exists)

# ============================================================================
# SECTION 9: FRONTEND API CALLS
# ============================================================================

print("\n" + "=" * 60)
print("SECTION 9: FRONTEND API INTEGRATION")
print("=" * 60)

home_service_file = "/Users/mohanganesh/AUVRA/mobileFEKD/services/homeService.ts"

frontend_api_patterns = [
    "getPendingReview",
    "submitDailyReview",
    "getTodayAssignments",
    "/assignments/",       # Complete action pattern
    "/api/v1/new-scheduling",
]

if os.path.exists(home_service_file):
    results = check_file_contains(home_service_file, frontend_api_patterns)
    for pattern, found in results.items():
        log_test(f"API Call: {pattern}", found)

# ============================================================================
# SECTION 10: REVIEW MODAL FLOW
# ============================================================================

print("\n" + "=" * 60)
print("SECTION 10: DAILY REVIEW MODAL FLOW")
print("=" * 60)

review_modal_file = "/Users/mohanganesh/AUVRA/mobileFEKD/components/DailyReviewModal.tsx"

review_patterns = [
    "handleStatusSelect",
    "handleSubmitReview",
    "forgot_to_mark",
    "replaced",
    "skipped",
    "was_completed",
    "streak_maintained",
    "useFreeze",
]

if os.path.exists(review_modal_file):
    results = check_file_contains(review_modal_file, review_patterns)
    for pattern, found in results.items():
        log_test(f"Review Modal: {pattern}", found)

# ============================================================================
# SUMMARY
# ============================================================================

print("\n" + "=" * 60)
print("TEST SUMMARY")
print("=" * 60)

total = test_results["passed"] + test_results["failed"]
pass_rate = (test_results["passed"] / max(1, total)) * 100

print(f"""
┌─────────────────────────────────────────────────┐
│  Total Checks:    {total:3d}                          │
│  ✅ Passed:       {test_results['passed']:3d}                          │
│  ❌ Failed:       {test_results['failed']:3d}                          │
│                                                 │
│  Pass Rate:       {pass_rate:.1f}%                        │
└─────────────────────────────────────────────────┘
""")

if test_results["failed"] > 0:
    print("\n❌ Failed Checks:")
    for detail in test_results["details"]:
        if not detail["passed"]:
            print(f"    - {detail['name']}")

if pass_rate >= 90:
    print("\n🎉 FLOW VERIFICATION PASSED!")
    print("   All core components for the user flow are in place.")
elif pass_rate >= 70:
    print("\n⚠️  FLOW MOSTLY COMPLETE")
    print("   Some components may need attention.")
else:
    print("\n❌ FLOW INCOMPLETE")
    print("   Several components are missing.")

print("""
╔══════════════════════════════════════════════════════════════╗
║                    USER FLOW SUMMARY                         ║
╠══════════════════════════════════════════════════════════════╣
║  1. LOGIN                                                    ║
║     └─ LoginScreen.tsx → Firebase Auth → Backend verify      ║
║                                                              ║
║  2. SESSION LINK                                             ║
║     └─ sessionService.ts → /questions/link-session           ║
║                                                              ║
║  3. HOME SCREEN                                              ║
║     └─ HomeScreen.tsx → /new-scheduling/today                ║
║                                                              ║
║  4. ACTION PLAN                                              ║
║     └─ AI generates plan → 4 daily actions                   ║
║                                                              ║
║  5. COMPLETE ACTIONS                                         ║
║     └─ /new-scheduling/complete-action                       ║
║                                                              ║
║  6. NEXT DAY - PENDING REVIEW CHECK                          ║
║     └─ HomeScreen → /new-scheduling/pending-review           ║
║                                                              ║
║  7. DAILY REVIEW MODAL                                       ║
║     └─ DailyReviewModal.tsx shows incomplete items           ║
║     └─ Options: completed, forgot, replaced, skipped         ║
║                                                              ║
║  8. SUBMIT REVIEW                                            ║
║     └─ /new-scheduling/submit-daily-review                   ║
║     └─ Updates streak, may use freeze token                  ║
║                                                              ║
║  9. STREAK & REWARDS                                         ║
║     └─ Streak maintained/broken feedback                     ║
║     └─ /rewards/streak-info for stats                        ║
╚══════════════════════════════════════════════════════════════╝
""")

sys.exit(0 if test_results["failed"] == 0 else 1)
