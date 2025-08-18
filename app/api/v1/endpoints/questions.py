from fastapi import APIRouter, HTTPException, status, Depends
from sqlalchemy.orm import Session
from typing import List
from app.core.database import get_db
from app.services.question_service import QuestionService
from app.models.question_models import (
    SessionCreate, SessionResponse, SessionDataCreate, SessionData,
    UserResponseFull, SessionLinkRequest, AnalyticsResponse
)
from app.api.v1.endpoints.auth import get_current_active_user, get_current_user
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.database import create_tables
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    session_data: SessionCreate,
    db: Session = Depends(get_db)
):
    """Create new question session (available without login)"""
    try:
        service = QuestionService(db)
        session_id = service.create_session(session_data.device_id)
        
        # Return created session information
        session = service.get_session(session_id)
        return SessionResponse(
            session_id=session.session_id,
            device_id=session.device_id,
            created_at=session.created_at,
            expires_at=session.expires_at,
            status=session.status
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session creation failed: {str(e)}"
        )

@router.post("/sessions/{session_id}/data", response_model=dict)
async def save_session_data(
    session_id: str,
    data_request: SessionDataCreate,
    db: Session = Depends(get_db)
):
    """Save survey data to session (available without login)"""
    try:
        logger.info(f"세션 데이터 저장 요청: session_id={session_id}")
        
        service = QuestionService(db)
        
        # Check if session exists
        session = service.get_session(session_id)
        if not session:
            logger.error(f"세션을 찾을 수 없음: {session_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found or expired"
            )
        
        # Save data to session
        success = service.save_session_data(session_id, data_request.data)
        
        if success:
            logger.info(f"세션 데이터 저장 성공: {session_id}")
            return {"message": "Session data saved successfully"}
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save session data"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"세션 데이터 저장 중 예외 발생: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session data save failed: {str(e)}"
        )

@router.get("/sessions/{session_id}/data", response_model=SessionData)
async def get_session_data(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get session data (available without login)"""
    try:
        service = QuestionService(db)
        data = service.get_session_data(session_id)
        
        if not data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session data not found"
            )
        
        return data
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session data retrieval failed: {str(e)}"
        )

@router.post("/sessions/{session_id}/link")
async def link_session_to_user(
    session_id: str,
    link_data: SessionLinkRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """Link session to user and delete session"""
    try:
        service = QuestionService(db)
        
        # Check that only the user can link their own session
        # Firebase UID와 이메일이 일치하는지 확인
        if current_user.get("email") != link_data.user_profile.email:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only link your own sessions"
            )
        
        success = service.link_session_to_user(
            session_id, 
            current_user.get("uid"),
            link_data.user_profile.name,
            link_data.user_profile.email
        )
        
        if success:
            return {"message": "Session linked successfully and deleted"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session linking failed"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session linking failed: {str(e)}"
        )

@router.get("/users/{uid}/responses", response_model=List[UserResponseFull])
async def get_user_responses(
    uid: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """Get user responses (requires authentication)"""
    try:
        # Check that user can only access their own data
        if current_user.get("uid") != uid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only access your own data"
            )
        
        service = QuestionService(db)
        responses = service.get_user_responses(uid)
        
        return [UserResponseFull.from_orm(response) for response in responses]
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Response retrieval failed: {str(e)}"
        )

@router.post("/cleanup/expired-sessions")
async def cleanup_expired_sessions(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """Clean up expired sessions (admin only)"""
    try:
        # TODO: Add admin check
        service = QuestionService(db)
        count = service.cleanup_expired_sessions()
        
        return {"message": f"Cleaned up {count} expired sessions"}
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cleanup failed: {str(e)}"
        ) 