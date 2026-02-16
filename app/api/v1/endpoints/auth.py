from fastapi import APIRouter, HTTPException, status, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
import firebase_admin
from firebase_admin import auth

from app.core.rate_limiter import limiter, AUTH_LIMIT

router = APIRouter()
security = HTTPBearer()


class FirebaseTokenRequest(BaseModel):
    id_token: str


class UserInfo(BaseModel):
    uid: str
    email: Optional[str] = None
    email_verified: bool = False
    display_name: Optional[str] = None
    photo_url: Optional[str] = None
    provider: str = "password"


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Get current user information from Firebase ID token."""
    try:
        decoded_token = auth.verify_id_token(credentials.credentials)
        return decoded_token
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Firebase token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_active_user(current_user: dict = Depends(get_current_user)) -> dict:
    """Get current active user information."""
    if not current_user.get("email_verified", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email not verified."
        )
    return current_user


@router.post("/verify", response_model=UserInfo)
@limiter.limit(AUTH_LIMIT)
async def verify_token(request: Request, body: FirebaseTokenRequest):
    """Verify Firebase ID token."""
    try:
        decoded_token = auth.verify_id_token(body.id_token)
        
        return UserInfo(
            uid=decoded_token.get("uid"),
            email=decoded_token.get("email"),
            email_verified=decoded_token.get("email_verified", False),
            display_name=decoded_token.get("name"),
            photo_url=decoded_token.get("picture"),
            provider=decoded_token.get("firebase", {}).get("sign_in_provider", "password")
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification failed: {str(e)}"
        )


@router.get("/me", response_model=UserInfo)
@limiter.limit(AUTH_LIMIT)
async def get_current_user_info(request: Request, current_user: dict = Depends(get_current_active_user)):
    """Get current user information."""
    return UserInfo(
        uid=current_user.get("uid"),
        email=current_user.get("email"),
        email_verified=current_user.get("email_verified", False),
        display_name=current_user.get("name"),
        photo_url=current_user.get("picture"),
        provider=current_user.get("firebase", {}).get("sign_in_provider", "password")
    )


@router.post("/logout")
@limiter.limit(AUTH_LIMIT)
async def logout(request: Request):
    """User logout (handled by client)."""
    return {"message": "Logged out. Handle Firebase logout on client side."}


@router.get("/providers")
@limiter.limit(AUTH_LIMIT)
async def get_auth_providers(request: Request):
    """Get available authentication providers."""
    return {
        "providers": [
            {
                "name": "password",
                "display_name": "Email/Password",
                "enabled": True
            },
            {
                "name": "google.com",
                "display_name": "Google",
                "enabled": True
            },
            {
                "name": "facebook.com",
                "display_name": "Facebook",
                "enabled": True
            },
            {
                "name": "github.com",
                "display_name": "GitHub",
                "enabled": True
            },
            {
                "name": "phone",
                "display_name": "Phone",
                "enabled": True
            }
        ],
        "note": "Enable/disable authentication providers in Firebase Console."
    } 