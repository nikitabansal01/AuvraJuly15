#!/usr/bin/env python3
"""
질문 API 테스트 스크립트
"""

import requests
import json
from datetime import datetime

# API 기본 URL
BASE_URL = "http://localhost:8000/api/v1"

def test_question_api():
    """질문 API 테스트"""
    
    print("🧪 질문 API 테스트 시작")
    print("=" * 50)
    
    # 1. 세션 생성 테스트
    print("\n1. 세션 생성 테스트")
    session_data = {
        "device_id": "test_device_123"
    }
    
    response = requests.post(f"{BASE_URL}/questions/sessions", json=session_data)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        session_info = response.json()
        session_id = session_info["session_id"]
        print(f"생성된 세션 ID: {session_id}")
    else:
        print(f"세션 생성 실패: {response.text}")
        return
    
    # 2. 응답 저장 테스트
    print("\n2. 응답 저장 테스트")
    response_data = {
        "session_id": session_id,
        "responses": {
            "name": "테스트 사용자",
            "age": 25,
            "period_description": "Regular",
            "birth_control": ["Hormonal Birth Control Pills"],
            "last_period_date": "01/15/2024",
            "cycle_length": "26-30 days",
            "period_concerns": ["Painful Periods", "Irregular Periods"],
            "body_concerns": ["Bloating", "Recent weight gain"],
            "skin_hair_concerns": ["Adult Acne"],
            "mental_health_concerns": ["Mood swings"],
            "other_concerns": ["특별한 건강 문제가 있습니다"],
            "top_concern": "Painful Periods",
            "diagnosed_conditions": ["PCOS"]
        }
    }
    
    response = requests.post(f"{BASE_URL}/questions/sessions/{session_id}/responses", json=response_data)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        saved_response = response.json()
        print(f"응답 저장 성공: ID {saved_response['id']}")
    else:
        print(f"응답 저장 실패: {response.text}")
    
    # 3. 세션 응답 조회 테스트
    print("\n3. 세션 응답 조회 테스트")
    response = requests.get(f"{BASE_URL}/questions/sessions/{session_id}/responses")
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        session_responses = response.json()
        print(f"조회된 응답: {session_responses['name']}, 나이: {session_responses['age']}")
    else:
        print(f"응답 조회 실패: {response.text}")
    
    # 4. 분석 데이터 조회 테스트
    print("\n4. 분석 데이터 조회 테스트")
    response = requests.get(f"{BASE_URL}/questions/analytics")
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        analytics = response.json()
        print(f"총 사용자 수: {analytics['total_users']}")
        print(f"나이 분포: {analytics['age_distribution']}")
    else:
        print(f"분석 데이터 조회 실패: {response.text}")
    
    print("\n" + "=" * 50)
    print("✅ 테스트 완료")

def test_validation():
    """검증 테스트"""
    print("\n🧪 검증 테스트")
    print("=" * 50)
    
    # 1. 유효한 데이터 테스트
    print("\n1. 유효한 데이터 테스트")
    valid_data = {
        "session_id": "test_session",
        "responses": {
            "name": "테스트 사용자",
            "age": 25,
            "period_description": "Regular",  # ✅ 유효한 옵션
            "cycle_length": "26-30 days",    # ✅ 유효한 옵션
            "top_concern": "Painful Periods" # ✅ 유효한 옵션
        }
    }
    
    response = requests.post(f"{BASE_URL}/questions/sessions/test_session/responses", json=valid_data)
    print(f"유효한 데이터 Status: {response.status_code}")
    
    # 2. 잘못된 데이터 테스트
    print("\n2. 잘못된 데이터 테스트")
    invalid_data = {
        "session_id": "test_session",
        "responses": {
            "name": "테스트 사용자",
            "age": 25,
            "period_description": "Invalid Option",  # ❌ 잘못된 옵션
            "cycle_length": "999 days",             # ❌ 잘못된 옵션
            "top_concern": "Hacked Data"            # ❌ 잘못된 옵션
        }
    }
    
    response = requests.post(f"{BASE_URL}/questions/sessions/test_session/responses", json=invalid_data)
    print(f"잘못된 데이터 Status: {response.status_code}")
    if response.status_code == 422:  # Validation Error
        print("✅ 검증이 정상적으로 작동합니다!")
    else:
        print("❌ 검증이 작동하지 않습니다.")

def test_firebase_integration():
    """Firebase 통합 테스트"""
    
    print("\n🔥 Firebase 통합 테스트")
    print("=" * 50)
    
    # Firebase 토큰 검증 테스트
    print("\n1. Firebase 토큰 검증 테스트")
    # 실제 Firebase 토큰이 필요하므로 주석 처리
    # token_data = {"id_token": "your_firebase_token_here"}
    # response = requests.post(f"{BASE_URL}/auth/verify", json=token_data)
    # print(f"Status: {response.status_code}")
    # print(f"Response: {response.json()}")
    
    print("Firebase 토큰이 필요하므로 실제 테스트는 생략")

if __name__ == "__main__":
    try:
        test_question_api()
        test_validation()
        test_firebase_integration()
    except requests.exceptions.ConnectionError:
        print("❌ 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.")
    except Exception as e:
        print(f"❌ 테스트 중 오류 발생: {str(e)}") 