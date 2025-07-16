#!/usr/bin/env python3
"""
Firebase 인증 테스트 스크립트
"""

import requests
import json
import sys
from typing import Dict, Any


class FirebaseAuthTester:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session = requests.Session()
    
    def test_health_check(self) -> bool:
        """헬스체크 테스트"""
        try:
            response = self.session.get(f"{self.base_url}/health")
            if response.status_code == 200:
                print("✅ 헬스체크 성공")
                return True
            else:
                print(f"❌ 헬스체크 실패: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ 헬스체크 오류: {e}")
            return False
    
    def test_auth_providers(self) -> bool:
        """인증 제공자 목록 테스트"""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/auth/providers")
            if response.status_code == 200:
                print("✅ 인증 제공자 목록 조회 성공")
                providers = response.json()
                print(f"   제공자 수: {len(providers.get('providers', []))}")
                return True
            else:
                print(f"❌ 인증 제공자 목록 조회 실패: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ 인증 제공자 목록 조회 오류: {e}")
            return False
    
    def test_token_verification_invalid(self) -> bool:
        """잘못된 토큰으로 검증 테스트"""
        try:
            invalid_token_data = {
                "id_token": "invalid_token_123"
            }
            response = self.session.post(
                f"{self.base_url}/api/v1/auth/verify",
                json=invalid_token_data
            )
            if response.status_code == 401:
                print("✅ 잘못된 토큰 검증 성공 (예상된 실패)")
                return True
            else:
                print(f"❌ 잘못된 토큰 검증 실패: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ 잘못된 토큰 검증 오류: {e}")
            return False
    
    def test_token_verification_empty(self) -> bool:
        """빈 토큰으로 검증 테스트"""
        try:
            empty_token_data = {
                "id_token": ""
            }
            response = self.session.post(
                f"{self.base_url}/api/v1/auth/verify",
                json=empty_token_data
            )
            if response.status_code == 401:
                print("✅ 빈 토큰 검증 성공 (예상된 실패)")
                return True
            else:
                print(f"❌ 빈 토큰 검증 실패: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ 빈 토큰 검증 오류: {e}")
            return False
    
    def test_me_endpoint_without_token(self) -> bool:
        """토큰 없이 /me 엔드포인트 테스트"""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/auth/me")
            if response.status_code == 401:
                print("✅ 토큰 없이 /me 접근 성공 (예상된 실패)")
                return True
            else:
                print(f"❌ 토큰 없이 /me 접근 실패: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ 토큰 없이 /me 접근 오류: {e}")
            return False
    
    def test_logout(self) -> bool:
        """로그아웃 테스트"""
        try:
            response = self.session.post(f"{self.base_url}/api/v1/auth/logout")
            if response.status_code == 200:
                print("✅ 로그아웃 성공")
                return True
            else:
                print(f"❌ 로그아웃 실패: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ 로그아웃 오류: {e}")
            return False
    
    def test_with_real_token(self, token: str) -> bool:
        """실제 Firebase 토큰으로 테스트"""
        try:
            # 토큰 검증
            verify_data = {"id_token": token}
            response = self.session.post(
                f"{self.base_url}/api/v1/auth/verify",
                json=verify_data
            )
            
            if response.status_code == 200:
                print("✅ 실제 토큰 검증 성공")
                user_info = response.json()
                print(f"   사용자 UID: {user_info.get('uid', 'N/A')}")
                print(f"   이메일: {user_info.get('email', 'N/A')}")
                return True
            else:
                print(f"❌ 실제 토큰 검증 실패: {response.status_code}")
                print(f"   응답: {response.text}")
                return False
        except Exception as e:
            print(f"❌ 실제 토큰 검증 오류: {e}")
            return False
    
    def run_basic_tests(self) -> Dict[str, bool]:
        """기본 테스트 실행"""
        print("🚀 Firebase 인증 기본 테스트 시작...")
        print("=" * 50)
        
        tests = {
            "헬스체크": self.test_health_check,
            "인증 제공자 목록": self.test_auth_providers,
            "잘못된 토큰 검증": self.test_token_verification_invalid,
            "빈 토큰 검증": self.test_token_verification_empty,
            "토큰 없이 /me 접근": self.test_me_endpoint_without_token,
            "로그아웃": self.test_logout,
        }
        
        results = {}
        for test_name, test_func in tests.items():
            print(f"\n📋 {test_name} 테스트 중...")
            results[test_name] = test_func()
        
        print("\n" + "=" * 50)
        print("📊 기본 테스트 결과 요약:")
        
        passed = sum(results.values())
        total = len(results)
        
        for test_name, result in results.items():
            status = "✅ 통과" if result else "❌ 실패"
            print(f"   {test_name}: {status}")
        
        print(f"\n총 {total}개 테스트 중 {passed}개 통과 ({passed/total*100:.1f}%)")
        
        return results
    
    def run_real_token_test(self, token: str) -> bool:
        """실제 토큰 테스트"""
        print(f"\n🔐 실제 Firebase 토큰 테스트...")
        return self.test_with_real_token(token)


def main():
    """메인 함수"""
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    else:
        base_url = "http://localhost:8000"
    
    tester = FirebaseAuthTester(base_url)
    
    # 기본 테스트 실행
    results = tester.run_basic_tests()
    
    # 실제 토큰이 제공된 경우 테스트
    if len(sys.argv) > 2:
        real_token = sys.argv[2]
        print(f"\n🔐 실제 토큰으로 추가 테스트...")
        real_token_result = tester.run_real_token_test(real_token)
        results["실제 토큰 검증"] = real_token_result
    
    # 모든 테스트가 통과했는지 확인
    if all(results.values()):
        print("\n🎉 모든 Firebase 인증 테스트가 성공했습니다!")
        sys.exit(0)
    else:
        print("\n⚠️  일부 테스트가 실패했습니다.")
        sys.exit(1)


if __name__ == "__main__":
    main() 