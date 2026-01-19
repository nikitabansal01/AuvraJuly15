#!/usr/bin/env python3
"""Quick test to verify tap options and null byte fixes are deployed."""
import requests
import json

BASE_URL = "https://auvrajuly15.onrender.com"

# Firebase auth
def get_firebase_token():
    """Get Firebase ID token for test user."""
    resp = requests.post(
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword",
        params={"key": "AIzaSyDr1bLIcQrd4HtzOb1sVGNKGirA6XeFdPo"},
        json={"email": "a18@gmail.com", "password": "qwerty", "returnSecureToken": True}
    )
    if resp.status_code != 200:
        print(f"❌ Auth failed: {resp.text}")
        return None
    return resp.json()["idToken"]

def run_tests():
    print("=" * 60)
    print("VERIFICATION TESTS FOR CARE PLAN FIXES")
    print("=" * 60)
    
    token = get_firebase_token()
    if not token:
        return
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # Test 1: Start session
    print("\n[1] Starting new session...")
    resp = requests.post(f"{BASE_URL}/api/v1/care-plan-checkin/start?force_new=true", headers=headers)
    if resp.status_code != 200:
        print(f"❌ Start failed: {resp.status_code} - {resp.text[:200]}")
        return
    
    data = resp.json()
    thread_id = data["thread_id"]
    tap_options = data.get("tap_options", [])
    print(f"✅ Session started: {thread_id}")
    print(f"   Tap options: {[t['id'] for t in tap_options]}")
    
    # Test 2: TAP - want-to-change
    print("\n[2] Testing TAP: want-to-change...")
    resp = requests.post(f"{BASE_URL}/api/v1/care-plan-checkin/event", 
        headers=headers,
        json={"thread_id": thread_id, "block_id": "want-to-change", "action_id": "want-to-change"})
    
    if resp.status_code != 200:
        print(f"❌ TAP failed: {resp.status_code} - {resp.text[:200]}")
    else:
        data = resp.json()
        history = data.get("history", [])
        last_msg = history[-1]["text"] if history else "NO MESSAGE"
        new_taps = data.get("tap_options", [])
        ui_blocks = data.get("ui_blocks", [])
        
        if len(history) > 1 and history[-1]["isBot"]:
            print(f"✅ TAP want-to-change WORKS!")
            print(f"   Bot response: {last_msg[:100]}...")
            print(f"   Tap options: {len(new_taps)}, UI blocks: {len(ui_blocks)}")
        else:
            print(f"⚠️ TAP want-to-change returned but no new bot message")
            print(f"   History: {len(history)} messages")
    
    # Test 3: TAP - alternate-suggestions (new session)
    print("\n[3] Testing TAP: alternate-suggestions...")
    resp = requests.post(f"{BASE_URL}/api/v1/care-plan-checkin/start?force_new=true", headers=headers)
    thread_id = resp.json()["thread_id"]
    
    resp = requests.post(f"{BASE_URL}/api/v1/care-plan-checkin/event", 
        headers=headers,
        json={"thread_id": thread_id, "block_id": "alternate-suggestions", "action_id": "alternate-suggestions"})
    
    if resp.status_code != 200:
        print(f"❌ TAP failed: {resp.status_code}")
    else:
        data = resp.json()
        history = data.get("history", [])
        if len(history) > 1:
            print(f"✅ TAP alternate-suggestions WORKS!")
            print(f"   Bot response: {history[-1]['text'][:100]}...")
        else:
            print(f"⚠️ TAP alternate-suggestions - no new message")
    
    # Test 4: TAP - manage_plan (new session)
    print("\n[4] Testing TAP: manage_plan...")
    resp = requests.post(f"{BASE_URL}/api/v1/care-plan-checkin/start?force_new=true", headers=headers)
    thread_id = resp.json()["thread_id"]
    
    resp = requests.post(f"{BASE_URL}/api/v1/care-plan-checkin/event", 
        headers=headers,
        json={"thread_id": thread_id, "block_id": "manage_plan", "action_id": "manage_plan"})
    
    if resp.status_code != 200:
        print(f"❌ TAP failed: {resp.status_code}")
    else:
        data = resp.json()
        history = data.get("history", [])
        if len(history) > 1:
            print(f"✅ TAP manage_plan WORKS!")
            print(f"   Bot response: {history[-1]['text'][:100]}...")
        else:
            print(f"⚠️ TAP manage_plan - no new message")
    
    # Test 5: NULL BYTE sanitization
    print("\n[5] Testing NULL BYTE sanitization...")
    resp = requests.post(f"{BASE_URL}/api/v1/care-plan-checkin/start?force_new=true", headers=headers)
    thread_id = resp.json()["thread_id"]
    
    # Send message with null byte
    resp = requests.post(f"{BASE_URL}/api/v1/care-plan-checkin/respond",
        headers=headers,
        json={"thread_id": thread_id, "message_text": "change\x00this item please"})
    
    if resp.status_code == 200:
        print(f"✅ NULL BYTE sanitization WORKS!")
        data = resp.json()
        history = data.get("history", [])
        if history:
            print(f"   Bot response: {history[-1]['text'][:100]}...")
    else:
        print(f"❌ NULL BYTE test failed: {resp.status_code}")
        print(f"   Error: {resp.text[:300]}")
    
    print("\n" + "=" * 60)
    print("VERIFICATION COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
