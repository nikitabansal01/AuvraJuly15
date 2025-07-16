#!/usr/bin/env python3
"""
API Test Script
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
        """Health check test"""
        try:
            response = self.session.get(f"{self.base_url}/health")
            if response.status_code == 200:
                print("✅ Health check successful")
                print(f"   Response: {response.json()}")
                return True
            else:
                print(f"❌ Health check failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Health check error: {e}")
            return False
    
    def test_detailed_health_check(self) -> bool:
        """Detailed health check test"""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/health/detailed")
            if response.status_code == 200:
                print("✅ Detailed health check successful")
                print(f"   Response: {response.json()}")
                return True
            else:
                print(f"❌ Detailed health check failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Detailed health check error: {e}")
            return False
    
    def test_auth_login(self) -> bool:
        """Login test"""
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
                print("✅ Login successful")
                token_data = response.json()
                print(f"   Token: {token_data.get('access_token', 'N/A')}")
                return True
            else:
                print(f"❌ Login failed: {response.status_code}")
                print(f"   Response: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Login error: {e}")
            return False
    
    def test_users_endpoint(self) -> bool:
        """Users endpoint test"""
        try:
            response = self.session.get(f"{self.base_url}/api/v1/users/")
            if response.status_code == 200:
                print("✅ Users list retrieval successful")
                users = response.json()
                print(f"   Number of users: {len(users)}")
                return True
            else:
                print(f"❌ Users list retrieval failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Users list retrieval error: {e}")
            return False
    
    def test_docs_endpoint(self) -> bool:
        """API documentation endpoint test"""
        try:
            response = self.session.get(f"{self.base_url}/docs")
            if response.status_code == 200:
                print("✅ API documentation access successful")
                return True
            else:
                print(f"❌ API documentation access failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ API documentation access error: {e}")
            return False
    
    def run_all_tests(self) -> Dict[str, bool]:
        """Run all tests"""
        print("🚀 Starting API tests...")
        print("=" * 50)
        
        tests = {
            "Health Check": self.test_health_check,
            "Detailed Health Check": self.test_detailed_health_check,
            "Login": self.test_auth_login,
            "Users List": self.test_users_endpoint,
            "API Documentation": self.test_docs_endpoint,
        }
        
        results = {}
        for test_name, test_func in tests.items():
            print(f"\n📋 Running {test_name} test...")
            results[test_name] = test_func()
        
        print("\n" + "=" * 50)
        print("📊 Test results summary:")
        
        passed = sum(results.values())
        total = len(results)
        
        for test_name, result in results.items():
            status = "✅ Passed" if result else "❌ Failed"
            print(f"   {test_name}: {status}")
        
        print(f"\nTotal {total} tests, {passed} passed ({passed/total*100:.1f}%)")
        
        return results


def main():
    """Main function"""
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    else:
        base_url = "http://localhost:8000"
    
    tester = APITester(base_url)
    results = tester.run_all_tests()
    
    # Check if all tests passed
    if all(results.values()):
        print("\n🎉 All tests succeeded!")
        sys.exit(0)
    else:
        print("\n⚠️  Some tests failed.")
        sys.exit(1)


if __name__ == "__main__":
    main() 