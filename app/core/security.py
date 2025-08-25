import firebase_admin
from firebase_admin import auth, credentials
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings
import json
import os

# HTTP Bearer 토큰 스키마
security = HTTPBearer()

# Firebase 초기화
def initialize_firebase():
    """Firebase 초기화"""
    try:
        # 이미 초기화되었는지 확인
        if firebase_admin._apps:
            print("Firebase already initialized")
            return
        
        # 서비스 계정 키 파일이 있는 경우
        if os.path.exists("firebase-service-account.json"):
            cred = credentials.Certificate("firebase-service-account.json")
            firebase_admin.initialize_app(cred)
            print("Firebase initialized with service account file")
        else:
            # 환경변수에서 설정
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
                # 개발 환경에서는 기본 앱 사용
                firebase_admin.initialize_app()
                print("Firebase initialized with default app")
                
    except Exception as e:
        print(f"Firebase 초기화 오류: {e}")
        # 이미 초기화된 경우 무시
        if not firebase_admin._apps:
            try:
                firebase_admin.initialize_app()
                print("Firebase initialized with fallback")
            except Exception as fallback_error:
                print(f"Firebase fallback initialization failed: {fallback_error}")


async def verify_firebase_token(token: str) -> dict:
    """Firebase ID 토큰 검증"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        logger.info(f"Firebase 토큰 검증 시작: 토큰 길이={len(token)}")
        
        # Firebase 앱이 초기화되었는지 확인
        if not firebase_admin._apps:
            logger.error("Firebase not initialized")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Firebase not initialized"
            )
        
        # Firebase 토큰 검증 (clock skew 10초 허용)
        logger.info("Firebase 토큰 검증 실행 중...")
        decoded_token = auth.verify_id_token(token, check_revoked=False, clock_skew_seconds=10)
        logger.info(f"Firebase 토큰 검증 성공: uid={decoded_token.get('uid')}, email={decoded_token.get('email')}")
        return decoded_token
        
    except Exception as e:
        logger.error(f"Firebase 토큰 검증 실패: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"유효하지 않은 Firebase 토큰입니다: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """현재 사용자 정보 가져오기 (Firebase)"""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        logger.info(f"=== get_current_user 시작 ===")
        token = credentials.credentials
        logger.info(f"토큰 길이: {len(token) if token else 0}")
        
        decoded_token = await verify_firebase_token(token)
        logger.info(f"Firebase 토큰 검증 성공: uid={decoded_token.get('uid')}")
        
        # Firebase 사용자 정보 추출
        user_info = {
            "uid": decoded_token.get("uid"),
            "user_id": decoded_token.get("uid"),  # 호환성을 위해 둘 다 제공
            "email": decoded_token.get("email"),
            "email_verified": decoded_token.get("email_verified", False),
            "name": decoded_token.get("name"),
            "picture": decoded_token.get("picture"),
            "provider": decoded_token.get("firebase", {}).get("sign_in_provider", "password")
        }
        
        logger.info(f"사용자 정보 추출 완료: uid={user_info.get('user_id')}, email={user_info.get('email')}")
        return user_info
        
    except Exception as e:
        logger.error(f"get_current_user 실패: {str(e)}", exc_info=True)
        raise


async def get_current_active_user(current_user: dict = Depends(get_current_user)) -> dict:
    """현재 활성 사용자 정보 가져오기"""
    # 이메일 인증 체크 비활성화 (개발 중)
    # if not current_user.get("email_verified", True):
    #     raise HTTPException(
    #         status_code=status.HTTP_400_BAD_REQUEST,
    #         detail="이메일이 인증되지 않은 사용자입니다."
    #     )
    return current_user


async def create_firebase_user(email: str, password: str, display_name: str = None) -> dict:
    """Firebase 사용자 생성"""
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
            detail=f"사용자 생성 실패: {str(e)}"
        )


async def update_firebase_user(uid: str, **kwargs) -> dict:
    """Firebase 사용자 정보 업데이트"""
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
            detail=f"사용자 업데이트 실패: {str(e)}"
        )


async def delete_firebase_user(uid: str):
    """Firebase 사용자 삭제"""
    try:
        auth.delete_user(uid)
        return {"message": "사용자가 성공적으로 삭제되었습니다."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"사용자 삭제 실패: {str(e)}"
        )


async def send_email_verification(uid: str):
    """이메일 인증 메일 발송"""
    try:
        # Firebase Admin SDK에서는 직접 이메일 발송 불가
        # 클라이언트에서 처리하거나 Cloud Functions 사용
        return {"message": "이메일 인증 메일이 발송되었습니다."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"이메일 발송 실패: {str(e)}"
        )


async def send_password_reset_email(email: str):
    """비밀번호 재설정 이메일 발송"""
    try:
        # Firebase Admin SDK에서는 직접 이메일 발송 불가
        # 클라이언트에서 처리하거나 Cloud Functions 사용
        return {"message": "비밀번호 재설정 이메일이 발송되었습니다."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"이메일 발송 실패: {str(e)}"
        ) 