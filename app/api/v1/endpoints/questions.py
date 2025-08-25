from fastapi import APIRouter, HTTPException, status, Depends
from sqlalchemy.orm import Session
from typing import List
from app.core.database import get_db
from app.services.question_service import QuestionService
from app.services.recommendation_service import RecommendationService
from app.models.question_models import (
    SessionCreate, SessionResponse, SessionDataCreate, SessionData,
    UserResponseFull, SessionLinkRequest, AnalyticsResponse
)
from app.core.security import get_current_active_user, get_current_user
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.database import create_tables
import logging
from app.models.question_models import TimezoneUpdateRequest, TimezoneUpdateResponse

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

@router.get("/sessions/{session_id}/recommendations", response_model=dict)
async def get_session_recommendations(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get session recommendations (available without login)"""
    try:
        from app.core.database import RecommendationRecord, RecommendationAdvice
        
        # 세션 연결된 추천들 조회
        recommendations = db.query(RecommendationRecord).filter(
            RecommendationRecord.session_id == session_id
        ).all()
        
        result = []
        for rec in recommendations:
            # 추천에 연결된 조언들 조회
            advices = db.query(RecommendationAdvice).filter(
                RecommendationAdvice.recommendation_id == rec.id
            ).all()
            
            recommendation_data = {
                "id": rec.id,
                "category": rec.category,
                "title": rec.title,
                "purpose": rec.purpose,
                "specific_action": rec.specific_action,
                "priority": rec.priority,
                "contraindications": rec.contraindications,
                "conditions": rec.conditions,
                "symptoms": rec.symptoms,
                "hormones": rec.hormones,
                "food_amounts": rec.food_amounts,
                "food_items": rec.food_items,
                "exercise_durations": rec.exercise_durations,
                "exercise_types": rec.exercise_types,
                "exercise_intensities": rec.exercise_intensities,
                "mindfulness_durations": rec.mindfulness_durations,
                "mindfulness_techniques": rec.mindfulness_techniques,
                "frequency_detail": rec.frequency_detail,
                "duration_weeks": rec.duration_weeks,
                "optimal_times": rec.optimal_times,
                "research_summary": rec.research_summary,
                "research_studies": rec.research_studies,
                "advices": [
                    {
                        "id": advice.id,
                        "advice_type": advice.advice_type,
                        "category": advice.category,
                        "title": advice.title,
                        "description": advice.description
                    }
                    for advice in advices
                ]
            }
            result.append(recommendation_data)
        
        return {
            "session_id": session_id,
            "recommendations": result
        }
        
    except Exception as e:
        logger.error(f"세션 추천 조회 실패: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session recommendations retrieval failed: {str(e)}"
        )

@router.post("/sessions/{session_id}/generate-recommendations", response_model=dict)
async def start_session_recommendations_generation(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Start generating recommendations for session (available without login)"""
    try:
        logger.info(f"세션 추천 생성 시작 요청: session_id={session_id}")
        
        service = QuestionService(db)
        
        # Check if session exists and has data
        session = service.get_session(session_id)
        if not session:
            logger.error(f"세션을 찾을 수 없음: {session_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found or expired"
            )
        
        # Check if session has data
        if session.age is None and session.period_description is None:
            logger.error(f"세션에 데이터가 없음: {session_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session has no data to generate recommendations"
            )
        
        # Check if recommendations already exist
        from app.core.database import RecommendationRecord
        existing_recommendations = db.query(RecommendationRecord).filter(
            RecommendationRecord.session_id == session_id
        ).count()
        
        if existing_recommendations > 0:
            logger.info(f"이미 추천이 존재함: {session_id}, count={existing_recommendations}")
            return {
                "message": "Recommendations already exist",
                "status": "completed",
                "recommendations_count": existing_recommendations
            }
        
        # Start recommendation generation in background
        import asyncio
        asyncio.create_task(_generate_recommendations_background(session_id, service, db))
        
        logger.info(f"세션 추천 생성 시작됨: {session_id}")
        return {
            "message": "Recommendation generation started",
            "status": "started",
            "session_id": session_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"세션 추천 생성 시작 실패: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start recommendation generation: {str(e)}"
        )

async def _generate_recommendations_background(session_id: str, service, db) -> None:
    """
    백그라운드에서 세션 추천 생성
    """
    try:
        logger.info(f"백그라운드 추천 생성 시작: {session_id}")
        
        recommendation_service = RecommendationService(db)
        
        # 세션 데이터로 UserProfile 생성 (임시)
        session_data = service.get_session_data(session_id)
        if session_data:
            # 임시 UserProfile 생성 (uid는 None)
            temp_user_profile = {
                "age": session_data.age,
                "period_description": session_data.period_description,
                "birth_control": session_data.birth_control,
                "cycle_length": session_data.cycle_length,
                "period_concerns": session_data.period_concerns,
                "body_concerns": session_data.body_concerns,
                "skin_hair_concerns": session_data.skin_hair_concerns,
                "mental_health_concerns": session_data.mental_health_concerns,
                "other_concerns": session_data.other_concerns,
                "top_concern": session_data.top_concern,
                "diagnosed_conditions": session_data.diagnosed_conditions,
                "family_history": session_data.family_history,
                "workout_intensity": session_data.workout_intensity,
                "sleep_duration": session_data.sleep_duration,
                "stress_level": session_data.stress_level
            }
            
            # 각 카테고리별 추천 생성 (에러 핸들링 개선)
            categories = ["food", "movement", "mindfulness"]
            successful_categories = []
            failed_categories = []
            
            for category in categories:
                try:
                    success = await recommendation_service.generate_and_save_session_recommendations(
                        session_id=session_id,
                        user_profile=temp_user_profile,
                        category=category
                    )
                    if success:
                        successful_categories.append(category)
                        logger.info(f"카테고리 추천 생성 성공: {session_id}, {category}")
                    else:
                        failed_categories.append(category)
                        logger.error(f"카테고리 추천 생성 실패: {session_id}, {category}")
                except Exception as e:
                    failed_categories.append(category)
                    logger.error(f"카테고리 추천 생성 중 예외: {session_id}, {category}, error={str(e)}")
            
            logger.info(f"백그라운드 세션 추천 생성 완료: {session_id}")
            logger.info(f"성공한 카테고리: {successful_categories}")
            logger.info(f"실패한 카테고리: {failed_categories}")
            
        else:
            logger.warning(f"세션 데이터를 찾을 수 없음: {session_id}")
            
    except Exception as e:
        logger.error(f"백그라운드 세션 추천 생성 실패: {str(e)}", exc_info=True)
        # 백그라운드 실패는 사용자에게 영향을 주지 않음

@router.post("/sessions/{session_id}/link")
async def link_session_to_user(
    session_id: str,
    link_data: SessionLinkRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """Link session to user and delete session"""
    import asyncio
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"=== 엔드포인트 진입 성공 ===")
    logger.info(f"session_id: {session_id}")
    logger.info(f"link_data: {link_data}")
    logger.info(f"current_user: {current_user}")
    logger.info(f"db 객체: {type(db)}")
    
    try:
        logger.info(f"=== _link_session_to_user_internal 호출 시작 ===")
        # 타임아웃 설정 (30초)
        result = await asyncio.wait_for(
            _link_session_to_user_internal(session_id, link_data, db, current_user),
            timeout=30.0
        )
        logger.info(f"=== _link_session_to_user_internal 완료 ===")
        return result
    except asyncio.TimeoutError:
        logger.error(f"세션 연결 타임아웃: session_id={session_id}")
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail="Session linking timeout"
        )

async def _link_session_to_user_internal(
    session_id: str,
    link_data: SessionLinkRequest,
    db: Session,
    current_user: dict
):
    """Link session to user and delete session"""
    try:
        logger.info(f"=== 세션 연결 시작 ===")
        logger.info(f"세션 연결 시도: session_id={session_id}")
        logger.info(f"현재 사용자 정보: uid={current_user.get('uid')}, email={current_user.get('email')}")
        logger.info(f"요청 데이터: name={link_data.user_profile.name}, email={link_data.user_profile.email}")
        
        service = QuestionService(db)
        
        # Check that only the user can link their own session
        # Firebase UID와 이메일이 일치하는지 확인
        logger.info(f"이메일 일치 확인: current={current_user.get('email')}, request={link_data.user_profile.email}")
        if current_user.get("email") != link_data.user_profile.email:
            logger.warning(f"이메일 불일치: current_user_email={current_user.get('email')}, request_email={link_data.user_profile.email}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only link your own sessions"
            )
        
        logger.info(f"세션 연결 서비스 호출: uid={current_user.get('uid')}, name={link_data.user_profile.name}, current_timezone={link_data.current_timezone}")
        success = service.link_session_to_user(
            session_id, 
            current_user.get("uid"),
            link_data.user_profile.name,
            link_data.user_profile.email,
            link_data.current_timezone
        )
        
        if success:
            logger.info(f"세션 연결 성공: session_id={session_id}")
            return {"message": "Session linked successfully and deleted"}
        else:
            logger.error(f"세션 연결 실패: session_id={session_id}, success=False")
            logger.error(f"=== 400 오류 발생: Session linking failed ===")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session linking failed"
            )
            
    except HTTPException as he:
        logger.error(f"HTTPException 발생: session_id={session_id}, status_code={he.status_code}, detail={he.detail}")
        raise
    except Exception as e:
        logger.error(f"세션 연결 중 예외 발생: session_id={session_id}, error={str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session linking failed: {str(e)}"
        )

@router.post("/test-auth")
async def test_auth(
    current_user: dict = Depends(get_current_active_user)
):
    """테스트용 인증 엔드포인트"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"=== 테스트 인증 성공 ===")
    logger.info(f"current_user: {current_user}")
    
    return {"message": "Authentication successful", "user": current_user}

@router.post("/test-db")
async def test_db(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """테스트용 DB 연결 엔드포인트"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"=== DB 연결 테스트 시작 ===")
    logger.info(f"db 객체: {type(db)}")
    logger.info(f"current_user: {current_user}")
    
    try:
        # 간단한 DB 쿼리 테스트
        from app.core.database import QuestionSession
        session_count = db.query(QuestionSession).count()
        logger.info(f"세션 개수: {session_count}")
        
        logger.info(f"=== DB 연결 테스트 성공 ===")
        return {"message": "Database connection successful", "session_count": session_count}
    except Exception as e:
        logger.error(f"DB 연결 테스트 실패: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database test failed: {str(e)}")

@router.post("/test-pydantic")
async def test_pydantic(
    current_user: dict = Depends(get_current_active_user)
):
    """테스트용 Pydantic 모델 엔드포인트"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"=== Pydantic 테스트 시작 ===")
    logger.info(f"current_user: {current_user}")
    
    try:
        # 간단한 Pydantic 모델 테스트
        from app.models.question_models import UserProfileCreate
        
        test_data = {"name": "Test", "email": "test@test.com"}
        logger.info(f"테스트 데이터: {test_data}")
        
        profile = UserProfileCreate(**test_data)
        logger.info(f"Pydantic 모델 생성 성공: {profile}")
        
        logger.info(f"=== Pydantic 테스트 성공 ===")
        return {"message": "Pydantic test successful", "profile": profile.dict()}
    except Exception as e:
        logger.error(f"Pydantic 테스트 실패: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Pydantic test failed: {str(e)}")

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

@router.get("/sessions/{session_id}/recommendations/status", response_model=dict)
async def get_session_recommendations_status(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get session recommendations generation status"""
    try:
        from app.core.database import RecommendationRecord
        
        # 세션 존재 여부 확인
        service = QuestionService(db)
        session = service.get_session(session_id)
        
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found or expired"
            )
        
        # 카테고리별 추천 개수 확인
        categories = ["food", "movement", "mindfulness"]
        category_counts = {}
        total_recommendations = 0
        
        for category in categories:
            count = db.query(RecommendationRecord).filter(
                RecommendationRecord.session_id == session_id,
                RecommendationRecord.category == category
            ).count()
            category_counts[category] = count
            total_recommendations += count
        
        # 더 정교한 상태 판단
        completed_categories = [cat for cat, count in category_counts.items() if count > 0]
        
        if len(completed_categories) == 3:  # 모든 카테고리 완료
            # 추가로 최소 추천 수 확인 (각 카테고리당 최소 1개)
            if all(count > 0 for count in category_counts.values()):
                status = "completed"
            else:
                status = "in_progress"
        elif len(completed_categories) > 0:  # 일부 카테고리 완료
            status = "in_progress"
        else:  # 아직 시작 안됨
            status = "pending"
        
        return {
            "session_id": session_id,
            "status": status,
            "recommendations_count": total_recommendations,
            "expected_count": 3,
            "category_breakdown": category_counts,
            "completed_categories": completed_categories
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"세션 추천 상태 조회 실패: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session recommendations status retrieval failed: {str(e)}"
        ) 

@router.put("/users/timezone")
async def update_user_timezone(
    timezone_update: TimezoneUpdateRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """사용자 시간대 업데이트"""
    try:
        logger.info(f"시간대 변경 요청: uid={current_user.get('uid')}, new_timezone={timezone_update.new_timezone}")
        
        service = QuestionService(db)
        success = service.update_user_timezone(current_user.get("uid"), timezone_update.new_timezone)
        
        if success:
            return TimezoneUpdateResponse(
                success=True,
                message="시간대가 성공적으로 업데이트되었습니다",
                new_timezone=timezone_update.new_timezone
            )
        else:
            return TimezoneUpdateResponse(
                success=False,
                message="시간대 업데이트에 실패했습니다"
            )
            
    except Exception as e:
        logger.error(f"시간대 변경 실패: {str(e)}")
        return TimezoneUpdateResponse(
            success=False,
            message=f"시간대 변경 중 오류가 발생했습니다: {str(e)}"
        ) 