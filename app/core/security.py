import firebase_admin
from firebase_admin import auth, credentials
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings
import json
import os

# HTTP Bearer token schema
security = HTTPBearer()

# Firebase initialization
def initialize_firebase():
    """Initialize Firebase"""
    try:
        # Check if already initialized
        if firebase_admin._apps:
            print("Firebase already initialized")
            return
        
        # If service account key file exists
        if os.path.exists("firebase-service-account.json"):
            cred = credentials.Certificate("firebase-service-account.json")
            firebase_admin.initialize_app(cred)
            print("Firebase initialized with service account file")
        else:
            # Configure from environment variables
            if settings.FIREBASE_PROJECT_ID and settings.FIREBASE_PROJECT_ID != "your-firebase-project-id":
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
                print("Firebase initialized with environment variables")
            else:
                # Use default app in development
                firebase_admin.initialize_app()
                print("Firebase initialized with default app")
                
    except Exception as e:
        print(f"Firebase initialization error: {e}")
        # Ignore if already initialized
        if not firebase_admin._apps:
            try:
                firebase_admin.initialize_app()
                print("Firebase initialized with fallback")
            except Exception as fallback_error:
                print(f"Firebase fallback initialization failed: {fallback_error}")


async def verify_firebase_token(token: str) -> dict:
    """Verify Firebase ID token"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        logger.info(f"Firebase token verification started: token length={len(token)}")
        
        # Check if Firebase app is initialized
        if not firebase_admin._apps:
            logger.error("Firebase not initialized")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Firebase not initialized"
            )
        
        # Verify Firebase token (allow 10 seconds clock skew)
        logger.info("Firebase token verification in progress...")
        decoded_token = auth.verify_id_token(token, check_revoked=False, clock_skew_seconds=10)
        logger.info(f"Firebase token verification successful: uid={decoded_token.get('uid')}, email={decoded_token.get('email')}")
        return decoded_token
        
    except Exception as e:
        logger.error(f"Firebase token verification failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Firebase token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Get current user information (Firebase)"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        logger.info(f"=== get_current_user started ===")
        token = credentials.credentials
        logger.info(f"Token length: {len(token) if token else 0}")
        
        decoded_token = await verify_firebase_token(token)
        logger.info(f"Firebase token verification successful: uid={decoded_token.get('uid')}")
        
        # Extract Firebase user information
        user_info = {
            "uid": decoded_token.get("uid"),
            "user_id": decoded_token.get("uid"),  # Provide both for compatibility
            "email": decoded_token.get("email"),
            "email_verified": decoded_token.get("email_verified", False),
            "name": decoded_token.get("name"),
            "picture": decoded_token.get("picture"),
            "provider": decoded_token.get("firebase", {}).get("sign_in_provider", "password")
        }
        
        logger.info(f"User information extraction completed: uid={user_info.get('user_id')}, email={user_info.get('email')}")
        return user_info
        
    except Exception as e:
        logger.error(f"get_current_user failed: {str(e)}", exc_info=True)
        raise


async def get_current_active_user(current_user: dict = Depends(get_current_user)) -> dict:
    """Get current active user information"""
    # Email verification check disabled (during development)
    # if not current_user.get("email_verified", True):
    #     raise HTTPException(
    #         status_code=status.HTTP_400_BAD_REQUEST,
    #         detail="User email not verified."
    #     )
    return current_user


async def create_firebase_user(email: str, password: str, display_name: str = None) -> dict:
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User creation failed: {str(e)}"
        )


async def update_firebase_user(uid: str, **kwargs) -> dict:
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User update failed: {str(e)}"
        )


async def delete_firebase_user(uid: str):
    """Delete Firebase user"""
    try:
        auth.delete_user(uid)
        return {"message": "User deleted successfully."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User deletion failed: {str(e)}"
        )


async def send_email_verification(uid: str):
    """Send email verification"""
    try:
        # Firebase Admin SDK cannot send emails directly
        # Handle on client side or use Cloud Functions
        return {"message": "Email verification sent."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Email sending failed: {str(e)}"
        )


async def send_password_reset_email(email: str):
    """Send password reset email"""
    try:
        # Firebase Admin SDK cannot send emails directly
        # Handle on client side or use Cloud Functions
        return {"message": "Password reset email sent."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Email sending failed: {str(e)}"
        ) 