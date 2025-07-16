#!/usr/bin/env python3
"""
API 테스트 스크립트
"""

import requests
import json
import sys
from typing import Dict, Any


class APITester:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.session = requests.Session()
    
    def test_health_check(self) -> bool:
        """헬스체크 테스트"""
        try:
            response = self.session.get(f"{self.base_url}/health")
            if response.status_code == 200:
                print("✅ 헬스체크 성공")
                print(f"   응답: {response.json()}")
                return True
            else:
                print(f"❌ 헬스체크 실패: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ 헬스체크 오류: {e}")
            return False
    
    def test_detailed_health_check(self) -> bool:
        """상세 헬스체크 테스트"""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/health/detailed")
            if response.status_code == 200:
                print("✅ 상세 헬스체크 성공")
                print(f"   응답: {response.json()}")
                return True
            else:
                print(f"❌ 상세 헬스체크 실패: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ 상세 헬스체크 오류: {e}")
            return False
    
    def test_auth_login(self) -> bool:
        """로그인 테스트"""
        try:
            login_data = {
                "username": "admin",
                "password": "password"
            }
            response = self.session.post(
                f"{self.base_url}/api/v1/auth/login",
                json=login_data
            )
            if response.status_code == 200:
                print("✅ 로그인 성공")
                token_data = response.json()
                print(f"   토큰: {token_data.get('access_token', 'N/A')}")
                return True
            else:
                print(f"❌ 로그인 실패: {response.status_code}")
                print(f"   응답: {response.text}")
                return False
        except Exception as e:
            print(f"❌ 로그인 오류: {e}")
            return False
    
    def test_users_endpoint(self) -> bool:
        """사용자 엔드포인트 테스트"""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/users/")
            if response.status_code == 200:
                print("✅ 사용자 목록 조회 성공")
                users = response.json()
                print(f"   사용자 수: {len(users)}")
                return True
            else:
                print(f"❌ 사용자 목록 조회 실패: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ 사용자 목록 조회 오류: {e}")
            return False
    
    def test_docs_endpoint(self) -> bool:
        """API 문서 엔드포인트 테스트"""
        try:
            response = self.session.get(f"{self.base_url}/docs")
            if response.status_code == 200:
                print("✅ API 문서 접근 성공")
                return True
            else:
                print(f"❌ API 문서 접근 실패: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ API 문서 접근 오류: {e}")
            return False
    
    def run_all_tests(self) -> Dict[str, bool]:
        """모든 테스트 실행"""
        print("🚀 API 테스트 시작...")
        print("=" * 50)
        
        tests = {
            "헬스체크": self.test_health_check,
            "상세 헬스체크": self.test_detailed_health_check,
            "로그인": self.test_auth_login,
            "사용자 목록": self.test_users_endpoint,
            "API 문서": self.test_docs_endpoint,
        }
        
        results = {}
        for test_name, test_func in tests.items():
            print(f"\n📋 {test_name} 테스트 중...")
            results[test_name] = test_func()
        
        print("\n" + "=" * 50)
        print("📊 테스트 결과 요약:")
        
        passed = sum(results.values())
        total = len(results)
        
        for test_name, result in results.items():
            status = "✅ 통과" if result else "❌ 실패"
            print(f"   {test_name}: {status}")
        
        print(f"\n총 {total}개 테스트 중 {passed}개 통과 ({passed/total*100:.1f}%)")
        
        return results


def main():
    """메인 함수"""
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    else:
        base_url = "http://localhost:8000"
    
    tester = APITester(base_url)
    results = tester.run_all_tests()
    
    # 모든 테스트가 통과했는지 확인
    if all(results.values()):
        print("\n🎉 모든 테스트가 성공했습니다!")
        sys.exit(0)
    else:
        print("\n⚠️  일부 테스트가 실패했습니다.")
        sys.exit(1)


if __name__ == "__main__":
    main() 