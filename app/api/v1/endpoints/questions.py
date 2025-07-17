from fastapi import APIRouter, HTTPException, status, Depends
from sqlalchemy.orm import Session
from typing import List
from app.core.database import get_db
from app.services.question_service import QuestionService
from app.models.question_models import (
    SessionCreate, SessionResponse, UserResponseCreate, 
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
        # Non-logged in users can also create sessions
        session_id = service.create_session(session_data.device_id, None)
        
        # Return created session information
        session = service.get_session(session_id)
        return SessionResponse(
            session_id=session.session_id,
            device_id=session.device_id,
            created_at=session.created_at,
            status=session.status
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session creation failed: {str(e)}"
        )

@router.post("/sessions/{session_id}/responses", response_model=UserResponseFull)
async def save_responses(
    session_id: str,
    response_data: UserResponseCreate,
    db: Session = Depends(get_db)
):
    """Save user responses (available without login)"""
    try:
        logger.info(f"답변 저장 요청 받음: session_id={session_id}")
        logger.info(f"요청 데이터: {response_data}")
        
        service = QuestionService(db)
        uid = None  # 로그인 없이도 답변 저장 가능
        
        # Check if session exists
        session = service.get_session(session_id)
        if not session:
            logger.error(f"세션을 찾을 수 없음: {session_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found"
            )
        
        logger.info(f"세션 확인됨: {session.session_id}")
        
        # Save responses
        saved_response = service.save_user_responses(
            session_id, 
            response_data.responses, 
            uid
        )
        
        logger.info(f"답변 저장 성공: {saved_response.id}")
        return UserResponseFull.from_orm(saved_response)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"답변 저장 중 예외 발생: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Response save failed: {str(e)}"
        )

@router.post("/sessions/{session_id}/link")
async def link_session_to_user(
    session_id: str,
    link_data: SessionLinkRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """Link session to user"""
    try:
        service = QuestionService(db)
        
        # Check that only the user can link their own session
        if current_user.get("uid") != link_data.uid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only link your own sessions"
            )
        
        success = service.link_session_to_user(session_id, link_data.uid)
        
        if success:
            return {"message": "Session linked successfully"}
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
    """Get all user responses"""
    try:
        # Check that only the user can view their own responses
        if current_user.get("uid") != uid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view your own responses"
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

@router.get("/sessions/{session_id}/responses", response_model=UserResponseFull)
async def get_session_responses(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get session responses (available without login)"""
    try:
        service = QuestionService(db)
        response = service.get_session_responses(session_id)
        
        if not response:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Response not found"
            )
        
        return UserResponseFull.from_orm(response)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Response retrieval failed: {str(e)}"
        )

@router.post("/users/{uid}/merge-sessions")
async def merge_user_sessions(
    uid: str,
    session_ids: List[str],
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """Merge multiple sessions into one user"""
    try:
        # Check that only the user can merge their own sessions
        if current_user.get("uid") != uid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only merge your own sessions"
            )
        
        service = QuestionService(db)
        success = service.merge_user_sessions(uid, session_ids)
        
        if success:
            return {"message": "Sessions merged successfully"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session merge failed"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session merge failed: {str(e)}"
        )

@router.get("/analytics", response_model=AnalyticsResponse)
async def get_analytics(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """Get analytics data (admin only)"""
    try:
        # TODO: Add admin permission check logic
        service = QuestionService(db)
        analytics_data = service.get_analytics()
        
        return AnalyticsResponse(**analytics_data)
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analytics data retrieval failed: {str(e)}"
        )

@router.post("/init-database")
async def initialize_database():
    """데이터베이스 테이블 생성 (개발용)"""
    try:
        # 직접 테이블 생성 (더 안전한 방법)
        create_tables()
        return {"message": "데이터베이스 테이블이 성공적으로 생성되었습니다"}
            
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"데이터베이스 초기화 실패: {str(e)}"
        ) 