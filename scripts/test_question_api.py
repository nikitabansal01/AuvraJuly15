#!/usr/bin/env python3
"""
Question API Test Script
"""

import requests
import json
from datetime import datetime

# API base URL
BASE_URL = "http://localhost:8000/api/v1"

def test_question_api():
    """Question API Test"""
    
    print("🧪 Starting Question API Test")
    print("=" * 50)
    
    # 1. Session creation test
    print("\n1. Session Creation Test")
    session_data = {
        "device_id": "test_device_123"
    }
    
    response = requests.post(f"{BASE_URL}/questions/sessions", json=session_data)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        session_info = response.json()
        session_id = session_info["session_id"]
        print(f"Created session ID: {session_id}")
    else:
        print(f"Session creation failed: {response.text}")
        return
    
    # 2. Response storage test
    print("\n2. Response Storage Test")
    response_data = {
        "session_id": session_id,
        "responses": {
            "name": "Test User",
            "age": 25,
            "period_description": "Regular",
            "birth_control": ["Hormonal Birth Control Pills"],
            "last_period_date": "01/15/2024",
            "cycle_length": "26-30 days",
            "period_concerns": ["Painful Periods", "Irregular Periods"],
            "body_concerns": ["Bloating", "Recent weight gain"],
            "skin_hair_concerns": ["Adult Acne"],
            "mental_health_concerns": ["Mood swings"],
            "other_concerns": ["Special health concerns"],
            "top_concern": "Painful Periods",
            "diagnosed_conditions": ["PCOS"]
        }
    }
    
    response = requests.post(f"{BASE_URL}/questions/sessions/{session_id}/responses", json=response_data)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        saved_response = response.json()
        print(f"Response saved successfully: ID {saved_response['id']}")
    else:
        print(f"Response save failed: {response.text}")
    
    # 3. Session response retrieval test
    print("\n3. Session Response Retrieval Test")
    response = requests.get(f"{BASE_URL}/questions/sessions/{session_id}/responses")
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        session_responses = response.json()
        print(f"Retrieved response: {session_responses['name']}, Age: {session_responses['age']}")
    else:
        print(f"Response retrieval failed: {response.text}")
    
    # 4. Analytics data retrieval test
    print("\n4. Analytics Data Retrieval Test")
    response = requests.get(f"{BASE_URL}/questions/analytics")
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        analytics = response.json()
        print(f"Total users: {analytics['total_users']}")
        print(f"Age distribution: {analytics['age_distribution']}")
    else:
        print(f"Analytics data retrieval failed: {response.text}")
    
    print("\n" + "=" * 50)
    print("✅ Test completed")

def test_validation():
    """Validation Test"""
    print("\n🧪 Validation Test")
    print("=" * 50)
    
    # 1. Valid data test
    print("\n1. Valid Data Test")
    valid_data = {
        "session_id": "test_session",
        "responses": {
            "name": "Test User",
            "age": 25,
            "period_description": "Regular",  # ✅ Valid option
            "cycle_length": "26-30 days",    # ✅ Valid option
            "top_concern": "Painful Periods" # ✅ Valid option
        }
    }
    
    response = requests.post(f"{BASE_URL}/questions/sessions/test_session/responses", json=valid_data)
    print(f"Valid data Status: {response.status_code}")
    
    # 2. Invalid data test
    print("\n2. Invalid Data Test")
    invalid_data = {
        "session_id": "test_session",
        "responses": {
            "name": "Test User",
            "age": 25,
            "period_description": "Invalid Option",  # ❌ Invalid option
            "cycle_length": "999 days",             # ❌ Invalid option
            "top_concern": "Hacked Data"            # ❌ Invalid option
        }
    }
    
    response = requests.post(f"{BASE_URL}/questions/sessions/test_session/responses", json=invalid_data)
    print(f"Invalid data Status: {response.status_code}")
    if response.status_code == 422:  # Validation Error
        print("✅ Validation is working correctly!")
    else:
        print("❌ Validation is not working.")

def test_firebase_integration():
    """Firebase Integration Test"""
    
    print("\n🔥 Firebase Integration Test")
    print("=" * 50)
    
    # Firebase token verification test
    print("\n1. Firebase Token Verification Test")
    # Commented out because actual Firebase token is required
    # token_data = {"id_token": "your_firebase_token_here"}
    # response = requests.post(f"{BASE_URL}/auth/verify", json=token_data)
    # print(f"Status: {response.status_code}")
    # print(f"Response: {response.json()}")
    
    print("Actual Firebase token required, skipping real test")

if __name__ == "__main__":
    try:
        test_question_api()
        test_validation()
        test_firebase_integration()
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to server. Please check if the server is running.")
    except Exception as e:
        print(f"❌ Error occurred during test: {str(e)}") 