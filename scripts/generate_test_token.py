#!/usr/bin/env python3
"""
Firebase test token generation script
"""

import firebase_admin
from firebase_admin import auth, credentials
import json
import sys


def initialize_firebase():
    """Initialize Firebase"""
    try:
        # Skip if already initialized
        if firebase_admin._apps:
            return True
        
        # Check service account key file
        key_file = "auvra-adf59-firebase-adminsdk-fbsvc-f60acd9df3.json"
        if not firebase_admin._apps:
            cred = credentials.Certificate(key_file)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase initialization successful")
            return True
    except Exception as e:
        print(f"❌ Firebase initialization failed: {e}")
        return False


def create_test_user():
    """Create test user"""
    try:
        # Create user with test email
        email = "test@auvra.com"
        password = "testpassword123"
        
        # Check for existing user
        try:
            user = auth.get_user_by_email(email)
            print(f"✅ Existing test user found: {user.uid}")
            return user.uid
        except:
            # Create new user
            user = auth.create_user(
                email=email,
                password=password,
                display_name="Test User"
            )
            print(f"✅ New test user created: {user.uid}")
            return user.uid
    except Exception as e:
        print(f"❌ Test user creation failed: {e}")
        return None


def generate_custom_token(uid):
    """Generate custom token"""
    try:
        custom_token = auth.create_custom_token(uid)
        print("✅ Custom token generated successfully")
        return custom_token.decode()
    except Exception as e:
        print(f"❌ Custom token generation failed: {e}")
        return None


def main():
    """Main function"""
    print("🔐 Firebase Test Token Generator")
    print("=" * 50)
    
    # Initialize Firebase
    if not initialize_firebase():
        print("❌ Firebase initialization failed. Please check the service account key file.")
        sys.exit(1)
    
    # Create test user
    uid = create_test_user()
    if not uid:
        print("❌ Test user creation failed")
        sys.exit(1)
    
    # Generate custom token
    custom_token = generate_custom_token(uid)
    if not custom_token:
        print("❌ Custom token generation failed")
        sys.exit(1)
    
    print("\n" + "=" * 50)
    print("📋 Test Information:")
    print(f"   User UID: {uid}")
    print(f"   Email: test@auvra.com")
    print(f"   Password: testpassword123")
    print(f"   Custom Token: {custom_token}")
    
    print("\n🔧 Usage:")
    print("1. Log in with test@auvra.com on Firebase Console")
    print("2. Or use the custom token for testing")
    print("3. python scripts/test_firebase_auth.py http://localhost:8000 [ACTUAL_TOKEN]")
    
    print("\n⚠️  Note: This token is for testing purposes only. Do not use in production.")


if __name__ == "__main__":
    main() 