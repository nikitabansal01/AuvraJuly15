import firebase_admin
from firebase_admin import credentials, auth
import os
import json
from app.core.config import settings


def initialize_firebase():
    """Initialize Firebase"""
    try:
        # Skip if already initialized
        if firebase_admin._apps:
            return
        
        # Service account key file exists
        if os.path.exists("auvra-adf59-firebase-adminsdk-fbsvc-f60acd9df3.json"):
                cred = credentials.Certificate("auvra-adf59-firebase-adminsdk-fbsvc-f60acd9df3.json")
                firebase_admin.initialize_app(cred)
                return
            
        
        # Environment variables configuration
        if settings.FIREBASE_PROJECT_ID:
            firebase_config = {
                "type": "service_account",
                "project_id": settings.FIREBASE_PROJECT_ID,
                "private_key_id": settings.FIREBASE_PRIVATE_KEY_ID,
                "private_key": settings.FIREBASE_PRIVATE_KEY.replace("\\n", "\n") if settings.FIREBASE_PRIVATE_KEY else "",
                "client_email": settings.FIREBASE_CLIENT_EMAIL,
                "client_id": settings.FIREBASE_CLIENT_ID,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{settings.FIREBASE_CLIENT_EMAIL}"
            }
            
            cred = credentials.Certificate(firebase_config)
            firebase_admin.initialize_app(cred)
        else:
            # Use default app in development
            firebase_admin.initialize_app()
            
    except Exception as e:
        print(f"Firebase initialization error: {e}")
        # Use default app in development
        try:
            firebase_admin.initialize_app()
        except:
            pass


def verify_firebase_token(token: str) -> dict:
    """Verify Firebase ID token"""
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        raise Exception(f"Invalid Firebase token: {str(e)}")


def get_user_by_uid(uid: str) -> dict:
    """Get user information by UID"""
    try:
        user_record = auth.get_user(uid)
        return {
            "uid": user_record.uid,
            "email": user_record.email,
            "email_verified": user_record.email_verified,
            "display_name": user_record.display_name,
            "photo_url": user_record.photo_url,
            "disabled": user_record.disabled
        }
    except Exception as e:
        raise Exception(f"Failed to get user information: {str(e)}")


def create_user(email: str, password: str, display_name: str = None) -> dict:
    """Create Firebase user"""
    try:
        user_properties = {
            "email": email,
            "password": password,
            "email_verified": False
        }
        
        if display_name:
            user_properties["display_name"] = display_name
        
        user_record = auth.create_user(**user_properties)
        
        return {
            "uid": user_record.uid,
            "email": user_record.email,
            "display_name": user_record.display_name,
            "email_verified": user_record.email_verified
        }
    except Exception as e:
        raise Exception(f"Failed to create user: {str(e)}")


def update_user(uid: str, **kwargs) -> dict:
    """Update Firebase user information"""
    try:
        user_record = auth.update_user(uid, **kwargs)
        return {
            "uid": user_record.uid,
            "email": user_record.email,
            "display_name": user_record.display_name,
            "email_verified": user_record.email_verified
        }
    except Exception as e:
        raise Exception(f"Failed to update user: {str(e)}")


def delete_user(uid: str):
    """Delete Firebase user"""
    try:
        auth.delete_user(uid)
        return {"message": "User deleted successfully."}
    except Exception as e:
        raise Exception(f"Failed to delete user: {str(e)}")


def list_users(max_results: int = 1000):
    """List users"""
    try:
        users = []
        page = auth.list_users(max_results=max_results)
        
        for user in page.users:
            users.append({
                "uid": user.uid,
                "email": user.email,
                "display_name": user.display_name,
                "email_verified": user.email_verified,
                "disabled": user.disabled
            })
        
        return users
    except Exception as e:
        raise Exception(f"Failed to list users: {str(e)}") 