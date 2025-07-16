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

router = APIRouter()

@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    session_data: SessionCreate,
    db: Session = Depends(get_db)
):
    """새로운 질문 세션 생성 (로그인 없이 가능)"""
    try:
        service = QuestionService(db)
        # 로그인하지 않은 사용자도 세션 생성 가능
        session_id = service.create_session(session_data.device_id, None)
        
        # 생성된 세션 정보 반환
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
            detail=f"세션 생성 실패: {str(e)}"
        )

@router.post("/sessions/{session_id}/responses", response_model=UserResponseFull)
async def save_responses(
    session_id: str,
    response_data: UserResponseCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """사용자 응답 저장"""
    try:
        service = QuestionService(db)
        uid = current_user.get("uid") if current_user else None
        
        # 세션 존재 확인
        session = service.get_session(session_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="세션을 찾을 수 없습니다"
            )
        
        # 응답 저장
        saved_response = service.save_user_responses(
            session_id, 
            response_data.responses, 
            uid
        )
        
        return UserResponseFull.from_orm(saved_response)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"응답 저장 실패: {str(e)}"
        )

@router.post("/sessions/{session_id}/link")
async def link_session_to_user(
    session_id: str,
    link_data: SessionLinkRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """세션을 사용자와 연결"""
    try:
        service = QuestionService(db)
        
        # 본인만 연결 가능하도록 체크
        if current_user.get("uid") != link_data.uid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="본인의 세션만 연결할 수 있습니다"
            )
        
        success = service.link_session_to_user(session_id, link_data.uid)
        
        if success:
            return {"message": "세션이 성공적으로 연결되었습니다"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="세션 연결에 실패했습니다"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"세션 연결 실패: {str(e)}"
        )

@router.get("/users/{uid}/responses", response_model=List[UserResponseFull])
async def get_user_responses(
    uid: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """사용자의 모든 응답 조회"""
    try:
        # 본인만 조회 가능하도록 체크
        if current_user.get("uid") != uid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="본인의 응답만 조회할 수 있습니다"
            )
        
        service = QuestionService(db)
        responses = service.get_user_responses(uid)
        
        return [UserResponseFull.from_orm(response) for response in responses]
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"응답 조회 실패: {str(e)}"
        )

@router.get("/sessions/{session_id}/responses", response_model=UserResponseFull)
async def get_session_responses(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """세션의 응답 조회"""
    try:
        service = QuestionService(db)
        response = service.get_session_responses(session_id)
        
        if not response:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="응답을 찾을 수 없습니다"
            )
        
        return UserResponseFull.from_orm(response)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"응답 조회 실패: {str(e)}"
        )

@router.post("/users/{uid}/merge-sessions")
async def merge_user_sessions(
    uid: str,
    session_ids: List[str],
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """여러 세션을 하나의 사용자로 병합"""
    try:
        # 본인만 병합 가능하도록 체크
        if current_user.get("uid") != uid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="본인의 세션만 병합할 수 있습니다"
            )
        
        service = QuestionService(db)
        success = service.merge_user_sessions(uid, session_ids)
        
        if success:
            return {"message": "세션이 성공적으로 병합되었습니다"}
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="세션 병합에 실패했습니다"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"세션 병합 실패: {str(e)}"
        )

@router.get("/analytics", response_model=AnalyticsResponse)
async def get_analytics(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """분석 데이터 조회 (관리자만)"""
    try:
        # TODO: 관리자 권한 체크 로직 추가
        service = QuestionService(db)
        analytics_data = service.get_analytics()
        
        return AnalyticsResponse(**analytics_data)
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"분석 데이터 조회 실패: {str(e)}"
        )

@router.post("/init-database")
async def initialize_database():
    """데이터베이스 테이블 생성 (개발용)"""
    try:
        create_tables()
        return {"message": "데이터베이스 테이블이 성공적으로 생성되었습니다"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"데이터베이스 초기화 실패: {str(e)}"
        ) 