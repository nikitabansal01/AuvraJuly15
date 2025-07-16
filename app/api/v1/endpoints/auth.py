from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional
import firebase_admin
from firebase_admin import auth

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
    """Firebase ID 토큰으로 현재 사용자 정보 가져오기"""
    try:
        decoded_token = auth.verify_id_token(credentials.credentials)
        return decoded_token
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"유효하지 않은 Firebase 토큰입니다: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_active_user(current_user: dict = Depends(get_current_user)) -> dict:
    """현재 활성 사용자 정보 가져오기"""
    if not current_user.get("email_verified", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이메일이 인증되지 않은 사용자입니다."
        )
    return current_user


@router.post("/verify", response_model=UserInfo)
async def verify_token(request: FirebaseTokenRequest):
    """Firebase ID 토큰 검증"""
    try:
        decoded_token = auth.verify_id_token(request.id_token)
        
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
            detail=f"토큰 검증 실패: {str(e)}"
        )


@router.get("/me", response_model=UserInfo)
async def get_current_user_info(current_user: dict = Depends(get_current_active_user)):
    """현재 사용자 정보 조회"""
    return UserInfo(
        uid=current_user.get("uid"),
        email=current_user.get("email"),
        email_verified=current_user.get("email_verified", False),
        display_name=current_user.get("name"),
        photo_url=current_user.get("picture"),
        provider=current_user.get("firebase", {}).get("sign_in_provider", "password")
    )


@router.post("/logout")
async def logout():
    """사용자 로그아웃 (클라이언트에서 처리)"""
    return {"message": "로그아웃되었습니다. 클라이언트에서 Firebase 로그아웃을 처리하세요."}


@router.get("/providers")
async def get_auth_providers():
    """사용 가능한 인증 제공자 목록"""
    return {
        "providers": [
            {
                "name": "password",
                "display_name": "이메일/비밀번호",
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
                "display_name": "전화번호",
                "enabled": True
            }
        ],
        "note": "Firebase Console에서 인증 제공자를 활성화/비활성화할 수 있습니다."
    } 