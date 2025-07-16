#!/usr/bin/env python3
"""
Firebase Authentication Test Script
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
        """Health check test"""
        try:
            response = self.session.get(f"{self.base_url}/health")
            if response.status_code == 200:
                print("✅ Health check successful")
                return True
            else:
                print(f"❌ Health check failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Health check error: {e}")
            return False
    
    def test_auth_providers(self) -> bool:
        """Authentication providers list test"""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/auth/providers")
            if response.status_code == 200:
                print("✅ Authentication providers list retrieved successfully")
                providers = response.json()
                print(f"   Number of providers: {len(providers.get('providers', []))}")
                return True
            else:
                print(f"❌ Authentication providers list retrieval failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Authentication providers list retrieval error: {e}")
            return False
    
    def test_token_verification_invalid(self) -> bool:
        """Invalid token verification test"""
        try:
            invalid_token_data = {
                "id_token": "invalid_token_123"
            }
            response = self.session.post(
                f"{self.base_url}/api/v1/auth/verify",
                json=invalid_token_data
            )
            if response.status_code == 401:
                print("✅ Invalid token verification successful (expected failure)")
                return True
            else:
                print(f"❌ Invalid token verification failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Invalid token verification error: {e}")
            return False
    
    def test_token_verification_empty(self) -> bool:
        """Empty token verification test"""
        try:
            empty_token_data = {
                "id_token": ""
            }
            response = self.session.post(
                f"{self.base_url}/api/v1/auth/verify",
                json=empty_token_data
            )
            if response.status_code == 401:
                print("✅ Empty token verification successful (expected failure)")
                return True
            else:
                print(f"❌ Empty token verification failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Empty token verification error: {e}")
            return False
    
    def test_me_endpoint_without_token(self) -> bool:
        """Test /me endpoint without token"""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/auth/me")
            if response.status_code == 401:
                print("✅ Access to /me without token successful (expected failure)")
                return True
            else:
                print(f"❌ Access to /me without token failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Access to /me without token error: {e}")
            return False
    
    def test_logout(self) -> bool:
        """Logout test"""
        try:
            response = self.session.post(f"{self.base_url}/api/v1/auth/logout")
            if response.status_code == 200:
                print("✅ Logout successful")
                return True
            else:
                print(f"❌ Logout failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Logout error: {e}")
            return False
    
    def test_with_real_token(self, token: str) -> bool:
        """Test with real Firebase token"""
        try:
            # Token verification
            verify_data = {"id_token": token}
            response = self.session.post(
                f"{self.base_url}/api/v1/auth/verify",
                json=verify_data
            )
            
            if response.status_code == 200:
                print("✅ Real token verification successful")
                user_info = response.json()
                print(f"   User UID: {user_info.get('uid', 'N/A')}")
                print(f"   Email: {user_info.get('email', 'N/A')}")
                return True
            else:
                print(f"❌ Real token verification failed: {response.status_code}")
                print(f"   Response: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Real token verification error: {e}")
            return False
    
    def run_basic_tests(self) -> Dict[str, bool]:
        """Run basic tests"""
        print("🚀 Starting Firebase authentication basic tests...")
        print("=" * 50)
        
        tests = {
            "Health Check": self.test_health_check,
            "Auth Providers List": self.test_auth_providers,
            "Invalid Token Verification": self.test_token_verification_invalid,
            "Empty Token Verification": self.test_token_verification_empty,
            "Access /me without Token": self.test_me_endpoint_without_token,
            "Logout": self.test_logout,
        }
        
        results = {}
        for test_name, test_func in tests.items():
            print(f"\n📋 Running {test_name} test...")
            results[test_name] = test_func()
        
        print("\n" + "=" * 50)
        print("📊 Basic test results summary:")
        
        passed = sum(results.values())
        total = len(results)
        
        for test_name, result in results.items():
            status = "✅ Passed" if result else "❌ Failed"
            print(f"   {test_name}: {status}")
        
        print(f"\nTotal {total} tests, {passed} passed ({passed/total*100:.1f}%)")
        
        return results
    
    def run_real_token_test(self, token: str) -> bool:
        """Real token test"""
        print(f"\n🔐 Real Firebase token test...")
        return self.test_with_real_token(token)


def main():
    """Main function"""
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    else:
        base_url = "http://localhost:8000"
    
    tester = FirebaseAuthTester(base_url)
    
    # Run basic tests
    results = tester.run_basic_tests()
    
    # Test with real token if provided
    if len(sys.argv) > 2:
        real_token = sys.argv[2]
        print(f"\n🔐 Additional test with real token...")
        real_token_result = tester.run_real_token_test(real_token)
        results["Real Token Verification"] = real_token_result
    
    # Check if all tests passed
    if all(results.values()):
        print("\n🎉 All Firebase authentication tests succeeded!")
        sys.exit(0)
    else:
        print("\n⚠️  Some tests failed.")
        sys.exit(1)


if __name__ == "__main__":
    main() 