#!/usr/bin/env python3
"""
Firebase 테스트 토큰 생성 스크립트
"""

import firebase_admin
from firebase_admin import auth, credentials
import json
import sys


def initialize_firebase():
    """Firebase 초기화"""
    try:
        # 이미 초기화된 경우 스킵
        if firebase_admin._apps:
            return True
        
        # 서비스 계정 키 파일 확인
        key_file = "auvra-adf59-firebase-adminsdk-fbsvc-f60acd9df3.json"
        if not firebase_admin._apps:
            cred = credentials.Certificate(key_file)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase 초기화 성공")
            return True
    except Exception as e:
        print(f"❌ Firebase 초기화 실패: {e}")
        return False


def create_test_user():
    """테스트 사용자 생성"""
    try:
        # 테스트 이메일로 사용자 생성
        email = "test@auvra.com"
        password = "testpassword123"
        
        # 기존 사용자 확인
        try:
            user = auth.get_user_by_email(email)
            print(f"✅ 기존 테스트 사용자 발견: {user.uid}")
            return user.uid
        except:
            # 새 사용자 생성
            user = auth.create_user(
                email=email,
                password=password,
                display_name="Test User"
            )
            print(f"✅ 새 테스트 사용자 생성: {user.uid}")
            return user.uid
    except Exception as e:
        print(f"❌ 테스트 사용자 생성 실패: {e}")
        return None


def generate_custom_token(uid):
    """커스텀 토큰 생성"""
    try:
        custom_token = auth.create_custom_token(uid)
        print("✅ 커스텀 토큰 생성 성공")
        return custom_token.decode()
    except Exception as e:
        print(f"❌ 커스텀 토큰 생성 실패: {e}")
        return None


def main():
    """메인 함수"""
    print("🔐 Firebase 테스트 토큰 생성기")
    print("=" * 50)
    
    # Firebase 초기화
    if not initialize_firebase():
        print("❌ Firebase 초기화 실패. 서비스 계정 키 파일을 확인하세요.")
        sys.exit(1)
    
    # 테스트 사용자 생성
    uid = create_test_user()
    if not uid:
        print("❌ 테스트 사용자 생성 실패")
        sys.exit(1)
    
    # 커스텀 토큰 생성
    custom_token = generate_custom_token(uid)
    if not custom_token:
        print("❌ 커스텀 토큰 생성 실패")
        sys.exit(1)
    
    print("\n" + "=" * 50)
    print("📋 테스트 정보:")
    print(f"   사용자 UID: {uid}")
    print(f"   이메일: test@auvra.com")
    print(f"   비밀번호: testpassword123")
    print(f"   커스텀 토큰: {custom_token}")
    
    print("\n🔧 사용 방법:")
    print("1. Firebase Console에서 test@auvra.com으로 로그인")
    print("2. 또는 커스텀 토큰을 사용하여 테스트")
    print("3. python scripts/test_firebase_auth.py http://localhost:8000 [실제_토큰]")
    
    print("\n⚠️  주의: 이 토큰은 테스트용입니다. 프로덕션에서는 사용하지 마세요.")


if __name__ == "__main__":
    main() 