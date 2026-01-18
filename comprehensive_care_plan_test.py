#!/usr/bin/env python3
"""
Comprehensive Care Plan Check-in Test Suite
Tests all edge cases, irregular user behaviors, intent switches, and worst-case scenarios.
Records all tap options, UI blocks, and bot responses.
"""
import requests
import json
import time
from datetime import datetime

BASE_URL = 'https://auvrajuly15.onrender.com'
FIREBASE_API_KEY = 'AIzaSyDr1bLIcQrd4HtzOb1sVGNKGirA6XeFdPo'

# Global log
TEST_LOG = []

def log(category: str, data: dict):
    """Log test data with timestamp."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "category": category,
        **data
    }
    TEST_LOG.append(entry)
    print(f"[{category}] {json.dumps(data, indent=2)[:500]}")

def get_firebase_token():
    """Authenticate with Firebase."""
    url = f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}'
    resp = requests.post(url, json={'email': 'a18@gmail.com', 'password': 'qwerty', 'returnSecureToken': True})
    auth = resp.json()
    if 'idToken' not in auth:
        log("AUTH_ERROR", {"error": auth})
        return None, None
    return auth['idToken'], auth['localId']

def start_care_plan(headers: dict):
    """Start a new care plan check-in session."""
    resp = requests.post(
        f'{BASE_URL}/api/v1/care-plan-checkin/start',
        headers=headers,
        params={'force_new': 'true'},
        timeout=60
    )
    return resp

def send_message(headers: dict, thread_id: str, message: str):
    """Send a message to the care plan check-in."""
    resp = requests.post(
        f'{BASE_URL}/api/v1/care-plan-checkin/respond',
        headers=headers,
        json={'thread_id': thread_id, 'message_text': message},
        timeout=120
    )
    return resp

def send_event(headers: dict, thread_id: str, block_id: str, action_id: str = None, event_type: str = "action"):
    """Send a UI event (tap action)."""
    payload = {
        'thread_id': thread_id,
        'block_id': block_id,
        'event_type': event_type,
    }
    if action_id:
        payload['action_id'] = action_id
    
    resp = requests.post(
        f'{BASE_URL}/api/v1/care-plan-checkin/event',
        headers=headers,
        json=payload,
        timeout=120
    )
    return resp

def analyze_response(resp, test_name: str, input_type: str, input_value: str):
    """Analyze and log the response."""
    result = {
        "test_name": test_name,
        "input_type": input_type,
        "input_value": input_value,
        "status_code": resp.status_code,
        "success": resp.status_code == 200,
    }
    
    if resp.status_code == 200:
        data = resp.json()
        result["thread_id"] = data.get("thread_id")
        result["local_date"] = data.get("local_date")
        
        # Extract bot messages
        history = data.get("history", [])
        bot_messages = [m for m in history if m.get("isBot")]
        result["bot_message_count"] = len(bot_messages)
        if bot_messages:
            result["last_bot_message"] = bot_messages[-1].get("text", "")[:300]
        
        # Extract tap options
        tap_options = data.get("tap_options", [])
        result["tap_options"] = tap_options
        result["tap_option_count"] = len(tap_options)
        
        # Extract UI blocks
        ui_blocks = data.get("ui_blocks", [])
        result["ui_blocks"] = ui_blocks
        result["ui_block_count"] = len(ui_blocks)
        
        # Extract actionable insights
        insights = data.get("actionable_insights", {})
        result["actionable_insights"] = insights
        
    else:
        result["error"] = resp.text[:500]
        # Check for specific errors
        if "route_by_intent" in resp.text:
            result["error_type"] = "ROUTING_BUG_route_by_intent"
        elif "route_after_change" in resp.text:
            result["error_type"] = "ROUTING_BUG_route_after_change"
        elif "KeyError" in resp.text:
            result["error_type"] = "KEY_ERROR"
        elif "NameError" in resp.text:
            result["error_type"] = "NAME_ERROR"
        else:
            result["error_type"] = "OTHER"
    
    log("RESPONSE", result)
    return result

def run_test_scenario(headers: dict, scenario_name: str, messages: list):
    """Run a test scenario with a sequence of messages/actions."""
    print(f"\n{'='*60}")
    print(f"SCENARIO: {scenario_name}")
    print(f"{'='*60}")
    
    # Start fresh session
    resp = start_care_plan(headers)
    start_result = analyze_response(resp, f"{scenario_name}_START", "start", "force_new=true")
    
    if not start_result["success"]:
        log("SCENARIO_FAILED", {"scenario": scenario_name, "reason": "Could not start session"})
        return
    
    thread_id = start_result["thread_id"]
    
    # Execute each step
    for i, step in enumerate(messages):
        step_name = f"{scenario_name}_STEP_{i+1}"
        
        if step["type"] == "message":
            print(f"\n--- Step {i+1}: Sending message: '{step['value']}' ---")
            resp = send_message(headers, thread_id, step["value"])
            result = analyze_response(resp, step_name, "message", step["value"])
            
        elif step["type"] == "tap":
            print(f"\n--- Step {i+1}: Tapping: block='{step.get('block_id')}', action='{step.get('action_id')}' ---")
            resp = send_event(headers, thread_id, step.get("block_id", ""), step.get("action_id"))
            result = analyze_response(resp, step_name, "tap", f"block={step.get('block_id')},action={step.get('action_id')}")
        
        elif step["type"] == "wait":
            print(f"\n--- Step {i+1}: Waiting {step['value']} seconds ---")
            time.sleep(step["value"])
            continue
        
        # Check for errors
        if not result.get("success"):
            log("STEP_FAILED", {"scenario": scenario_name, "step": i+1, "error": result.get("error_type")})
            # Continue anyway to see how system handles errors
        
        time.sleep(0.5)  # Small delay between steps

def main():
    print("="*60)
    print("COMPREHENSIVE CARE PLAN CHECK-IN TEST SUITE")
    print("="*60)
    
    # Authenticate
    token, uid = get_firebase_token()
    if not token:
        print("Authentication failed!")
        return
    
    log("AUTH_SUCCESS", {"uid": uid})
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    
    # ============================================================
    # TEST SCENARIOS
    # ============================================================
    
    # 1. Basic intents - one at a time
    basic_intents = [
        {"name": "MARK_DONE_BASIC", "messages": [
            {"type": "message", "value": "I did my morning action"},
        ]},
        {"name": "MARK_DONE_SPECIFIC", "messages": [
            {"type": "message", "value": "I completed the breakfast recommendation"},
        ]},
        {"name": "SKIP_BASIC", "messages": [
            {"type": "message", "value": "skip"},
        ]},
        {"name": "SKIP_SPECIFIC", "messages": [
            {"type": "message", "value": "I want to skip the walking exercise"},
        ]},
        {"name": "ASK_WHY", "messages": [
            {"type": "message", "value": "Why is this recommended for me?"},
        ]},
        {"name": "ASK_WHY_SPECIFIC", "messages": [
            {"type": "message", "value": "Why should I do yoga?"},
        ]},
    ]
    
    # 2. Change intent variations
    change_intents = [
        {"name": "CHANGE_X_TO_Y", "messages": [
            {"type": "message", "value": "Can I do yoga instead of walking?"},
        ]},
        {"name": "CHANGE_X_ONLY", "messages": [
            {"type": "message", "value": "I want to change the breakfast recommendation"},
        ]},
        {"name": "SUGGEST_ALTERNATES_FOR_X", "messages": [
            {"type": "message", "value": "Suggest alternates for the morning exercise"},
        ]},
        {"name": "DONT_LIKE_PLAN", "messages": [
            {"type": "message", "value": "I don't like this plan"},
        ]},
        {"name": "GIVE_SUGGESTIONS_FOR_X", "messages": [
            {"type": "message", "value": "Give me suggestions for the mindfulness activity"},
        ]},
        {"name": "SHOW_ME_OPTIONS", "messages": [
            {"type": "message", "value": "Show me other options"},
        ]},
        {"name": "CHANGE_EVERYTHING", "messages": [
            {"type": "message", "value": "Change everything, I hate all of it"},
        ]},
        {"name": "REPLACE_WITH_SPECIFIC", "messages": [
            {"type": "message", "value": "Replace oatmeal with eggs"},
        ]},
    ]
    
    # 3. Mid-conversation intent switches (WORST CASE)
    intent_switches = [
        {"name": "SWITCH_SKIP_TO_DONE", "messages": [
            {"type": "message", "value": "skip the morning action"},
            {"type": "message", "value": "actually wait, I did it"},
        ]},
        {"name": "SWITCH_CHANGE_TO_WHY", "messages": [
            {"type": "message", "value": "I want to change the breakfast"},
            {"type": "message", "value": "actually why is it recommended?"},
        ]},
        {"name": "SWITCH_WHY_TO_SKIP", "messages": [
            {"type": "message", "value": "Why should I do this exercise?"},
            {"type": "message", "value": "nah skip it"},
        ]},
        {"name": "SWITCH_DONE_TO_CHANGE", "messages": [
            {"type": "message", "value": "I completed the meal"},
            {"type": "message", "value": "wait no, can I change it?"},
        ]},
        {"name": "RAPID_INTENT_SWITCH", "messages": [
            {"type": "message", "value": "done"},
            {"type": "message", "value": "skip"},
            {"type": "message", "value": "change it"},
            {"type": "message", "value": "why?"},
        ]},
    ]
    
    # 4. Ambiguous inputs
    ambiguous_inputs = [
        {"name": "VAGUE_OK", "messages": [
            {"type": "message", "value": "ok"},
        ]},
        {"name": "VAGUE_SURE", "messages": [
            {"type": "message", "value": "sure"},
        ]},
        {"name": "VAGUE_WHATEVER", "messages": [
            {"type": "message", "value": "whatever"},
        ]},
        {"name": "VAGUE_IDK", "messages": [
            {"type": "message", "value": "I don't know"},
        ]},
        {"name": "EMPTY_LIKE", "messages": [
            {"type": "message", "value": "..."},
        ]},
        {"name": "RANDOM_GIBBERISH", "messages": [
            {"type": "message", "value": "asdfghjkl"},
        ]},
        {"name": "QUESTION_MARK_ONLY", "messages": [
            {"type": "message", "value": "?"},
        ]},
        {"name": "EMOJI_ONLY", "messages": [
            {"type": "message", "value": "🤷‍♀️"},
        ]},
    ]
    
    # 5. Conflicting intents in one message
    conflicting_intents = [
        {"name": "DONE_AND_SKIP", "messages": [
            {"type": "message", "value": "I did the breakfast but skip the exercise"},
        ]},
        {"name": "CHANGE_AND_WHY", "messages": [
            {"type": "message", "value": "Change it and tell me why it was chosen"},
        ]},
        {"name": "SKIP_ALL_DONE_ONE", "messages": [
            {"type": "message", "value": "Skip everything except breakfast which I already did"},
        ]},
        {"name": "MULTIPLE_CHANGES", "messages": [
            {"type": "message", "value": "Change breakfast to eggs and change walking to yoga"},
        ]},
    ]
    
    # 6. Cancel flow tests
    cancel_tests = [
        {"name": "CANCEL_BASIC", "messages": [
            {"type": "message", "value": "cancel"},
        ]},
        {"name": "CANCEL_AFTER_START", "messages": [
            {"type": "message", "value": "I want to change breakfast"},
            {"type": "message", "value": "cancel that"},
        ]},
        {"name": "NEVERMIND", "messages": [
            {"type": "message", "value": "show me alternatives"},
            {"type": "message", "value": "never mind"},
        ]},
        {"name": "GO_BACK", "messages": [
            {"type": "message", "value": "skip the morning action"},
            {"type": "message", "value": "go back"},
        ]},
    ]
    
    # 7. Edge case phrasings
    edge_phrasings = [
        {"name": "DONE_PAST_TENSE", "messages": [
            {"type": "message", "value": "I already ate my breakfast"},
        ]},
        {"name": "DONE_FUTURE", "messages": [
            {"type": "message", "value": "I will do the exercise later"},
        ]},
        {"name": "CONDITIONAL_DONE", "messages": [
            {"type": "message", "value": "If I do it now, will it count?"},
        ]},
        {"name": "NEGATIVE_CHANGE", "messages": [
            {"type": "message", "value": "I can't do walking because of my injury"},
        ]},
        {"name": "PREFERENCE_STATEMENT", "messages": [
            {"type": "message", "value": "I prefer meditation over breathing exercises"},
        ]},
        {"name": "COMPLAINT", "messages": [
            {"type": "message", "value": "This is too hard for me"},
        ]},
        {"name": "QUESTION_ABOUT_ACTION", "messages": [
            {"type": "message", "value": "What exactly is brisk walking?"},
        ]},
    ]
    
    # 8. Tap option tests (using common tap IDs)
    tap_tests = [
        {"name": "TAP_WANT_TO_CHANGE", "messages": [
            {"type": "tap", "block_id": "want-to-change", "action_id": "want-to-change"},
        ]},
        {"name": "TAP_ALTERNATE_SUGGESTIONS", "messages": [
            {"type": "tap", "block_id": "alternate-suggestions", "action_id": "alternate-suggestions"},
        ]},
        {"name": "TAP_MANAGE_PLAN", "messages": [
            {"type": "tap", "block_id": "manage_plan", "action_id": "manage_plan"},
        ]},
        {"name": "TAP_THEN_MESSAGE", "messages": [
            {"type": "tap", "block_id": "want-to-change", "action_id": "want-to-change"},
            {"type": "message", "value": "I want to do yoga instead"},
        ]},
        {"name": "MESSAGE_THEN_TAP", "messages": [
            {"type": "message", "value": "hmm let me think"},
            {"type": "tap", "block_id": "alternate-suggestions", "action_id": "alternate-suggestions"},
        ]},
    ]
    
    # 9. Worst case: User trying to break the system
    worst_cases = [
        {"name": "VERY_LONG_MESSAGE", "messages": [
            {"type": "message", "value": "I want to " + "change " * 100 + "everything"},
        ]},
        {"name": "SPECIAL_CHARACTERS", "messages": [
            {"type": "message", "value": "Change <script>alert('xss')</script> to yoga"},
        ]},
        {"name": "SQL_INJECTION_ATTEMPT", "messages": [
            {"type": "message", "value": "'; DROP TABLE actions; --"},
        ]},
        {"name": "UNICODE_CHAOS", "messages": [
            {"type": "message", "value": "Ĉḫầñgé thḯs ṱö ẏógā 🧘‍♀️"},
        ]},
        {"name": "NULL_BYTE", "messages": [
            {"type": "message", "value": "change\x00this"},
        ]},
        {"name": "EXTREMELY_RAPID_MESSAGES", "messages": [
            {"type": "message", "value": "done"},
            {"type": "message", "value": "skip"},
            {"type": "message", "value": "change"},
            {"type": "message", "value": "why"},
            {"type": "message", "value": "cancel"},
            {"type": "message", "value": "done again"},
        ]},
    ]
    
    # ============================================================
    # RUN ALL TESTS
    # ============================================================
    
    all_scenarios = (
        basic_intents + 
        change_intents + 
        intent_switches + 
        ambiguous_inputs + 
        conflicting_intents + 
        cancel_tests + 
        edge_phrasings +
        tap_tests +
        worst_cases
    )
    
    for scenario in all_scenarios:
        try:
            run_test_scenario(headers, scenario["name"], scenario["messages"])
        except Exception as e:
            log("SCENARIO_EXCEPTION", {"scenario": scenario["name"], "error": str(e)})
        time.sleep(1)  # Delay between scenarios to avoid rate limiting
    
    # ============================================================
    # SAVE RESULTS
    # ============================================================
    
    print("\n" + "="*60)
    print("TEST COMPLETE - SAVING RESULTS")
    print("="*60)
    
    # Save full log
    with open("/Users/mohanganesh/AUVRA/AuvraJuly15/care_plan_test_results.json", "w") as f:
        json.dump(TEST_LOG, f, indent=2, default=str)
    
    # Generate summary
    summary = {
        "total_tests": len(TEST_LOG),
        "successes": len([t for t in TEST_LOG if t.get("success") == True]),
        "failures": len([t for t in TEST_LOG if t.get("success") == False]),
        "errors_by_type": {},
        "tap_options_seen": set(),
        "ui_blocks_seen": set(),
        "irregularities": []
    }
    
    for entry in TEST_LOG:
        if entry.get("category") == "RESPONSE":
            # Count error types
            if entry.get("error_type"):
                error_type = entry.get("error_type")
                summary["errors_by_type"][error_type] = summary["errors_by_type"].get(error_type, 0) + 1
            
            # Collect tap options
            for tap in entry.get("tap_options", []):
                summary["tap_options_seen"].add(json.dumps(tap))
            
            # Collect UI blocks
            for block in entry.get("ui_blocks", []):
                summary["ui_blocks_seen"].add(block.get("type", "unknown"))
            
            # Check for irregularities
            if entry.get("tap_option_count", 0) == 0 and entry.get("ui_block_count", 0) == 0 and entry.get("success"):
                summary["irregularities"].append({
                    "test": entry.get("test_name"),
                    "issue": "No tap options or UI blocks returned",
                    "message": entry.get("last_bot_message", "")[:100]
                })
            
            if entry.get("success") and not entry.get("last_bot_message"):
                summary["irregularities"].append({
                    "test": entry.get("test_name"),
                    "issue": "No bot message returned",
                })
    
    summary["tap_options_seen"] = list(summary["tap_options_seen"])
    summary["ui_blocks_seen"] = list(summary["ui_blocks_seen"])
    
    # Save summary
    with open("/Users/mohanganesh/AUVRA/AuvraJuly15/care_plan_test_summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    
    print(f"\nTotal tests: {summary['total_tests']}")
    print(f"Successes: {summary['successes']}")
    print(f"Failures: {summary['failures']}")
    print(f"Errors by type: {summary['errors_by_type']}")
    print(f"Irregularities found: {len(summary['irregularities'])}")
    
    if summary["irregularities"]:
        print("\n--- IRREGULARITIES ---")
        for irr in summary["irregularities"][:10]:  # Show first 10
            print(f"  {irr}")
    
    print(f"\nResults saved to:")
    print(f"  - care_plan_test_results.json (full log)")
    print(f"  - care_plan_test_summary.json (summary)")

if __name__ == "__main__":
    main()
