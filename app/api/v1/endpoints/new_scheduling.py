import logging
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import get_current_user
from app.services.new_scheduling_service import NewSchedulingService
from app.models.scheduling_models import AssignmentResponse, AssignmentCompletionRequest
from sqlalchemy import and_

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/assignments/today", response_model=AssignmentResponse)
async def get_today_assignments(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    오늘의 과제 조회 (시간대 기반)
    
    - UserProfile의 current_timezone 기준으로 오늘 날짜 계산
    - 필요한 경우 즉시 스케줄 발행 보장
    - 시간대별로 그룹화된 과제 목록 반환
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="사용자 ID 없음")
        
        # UserProfile에서 current_timezone 가져오기
        from app.core.database import UserProfile
        user_profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
        user_timezone = user_profile.current_timezone if user_profile else "Asia/Seoul"
        
        service = NewSchedulingService(db)
        
        # 오늘 날짜 (사용자 시간대 기준)
        today = date.today()
        
        # 과제 조회 (보정 포함)
        result = service.get_user_assignments_for_date(uid, today, user_timezone)
        
        logger.info(f"오늘 과제 조회: uid={uid}, timezone={user_timezone}, count={result['total_assignments']}")
        return result
        
    except Exception as e:
        logger.error(f"오늘 과제 조회 실패: {str(e)}")
        raise HTTPException(status_code=500, detail="과제 조회 실패")

@router.get("/assignments/{target_date}", response_model=AssignmentResponse)
async def get_assignments_for_date(
    target_date: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    특정 날짜의 과제 조회
    
    - target_date: YYYY-MM-DD 형식
    - UserProfile의 current_timezone 기준으로 보정
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="사용자 ID 없음")
        
        # UserProfile에서 current_timezone 가져오기
        from app.core.database import UserProfile
        user_profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
        user_timezone = user_profile.current_timezone if user_profile else "Asia/Seoul"
        
        # 날짜 파싱
        try:
            parsed_date = date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="잘못된 날짜 형식")
        
        service = NewSchedulingService(db)
        
        # 과제 조회
        result = service.get_user_assignments_for_date(uid, parsed_date, user_timezone)
        
        logger.info(f"특정 날짜 과제 조회: uid={uid}, date={target_date}, timezone={user_timezone}, count={result['total_assignments']}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"특정 날짜 과제 조회 실패: {str(e)}")
        raise HTTPException(status_code=500, detail="과제 조회 실패")

@router.post("/assignments/{assignment_id}/complete")
async def complete_assignment(
    assignment_id: int,
    request: AssignmentCompletionRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    과제 완료 표시
    
    - assignment_id: 완료할 과제 ID
    - notes: 완료 메모 (선택사항)
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="사용자 ID 없음")
        
        service = NewSchedulingService(db)
        
        # 과제 완료 표시
        success = service.mark_assignment_completed(
            assignment_id=assignment_id,
            uid=uid,
            notes=request.notes
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="과제를 찾을 수 없음")
        
        logger.info(f"과제 완료: assignment_id={assignment_id}, uid={uid}")
        return {"message": "과제가 완료되었습니다", "assignment_id": assignment_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"과제 완료 실패: {str(e)}")
        raise HTTPException(status_code=500, detail="과제 완료 실패")

@router.post("/schedules/{recommendation_id}/create")
async def create_schedule_from_recommendation(
    recommendation_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    기존 추천을 새로운 스케줄로 변환
    
    - recommendation_id: 변환할 추천 ID
    - UserProfile의 current_timezone 사용
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="사용자 ID 없음")
        
        # UserProfile에서 current_timezone 가져오기
        from app.core.database import UserProfile
        user_profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
        user_timezone = user_profile.current_timezone if user_profile else "Asia/Seoul"
        
        # 추천 확인
        from app.core.database import RecommendationRecord
        recommendation = db.query(RecommendationRecord).filter(
            and_(
                RecommendationRecord.id == recommendation_id,
                RecommendationRecord.uid == uid
            )
        ).first()
        
        if not recommendation:
            raise HTTPException(status_code=404, detail="추천을 찾을 수 없음")
        
        service = NewSchedulingService(db)
        
        # 스케줄 생성
        schedule = service.create_schedule_from_recommendation(recommendation, user_timezone)
        
        logger.info(f"스케줄 생성: recommendation_id={recommendation_id}, schedule_id={schedule.id}, timezone={user_timezone}")
        return {
            "message": "스케줄이 생성되었습니다",
            "schedule_id": schedule.id,
            "recommendation_id": recommendation_id,
            "timezone": user_timezone
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"스케줄 생성 실패: {str(e)}")
        raise HTTPException(status_code=500, detail="스케줄 생성 실패")

@router.get("/schedules/active")
async def get_active_schedules(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    활성 스케줄 목록 조회
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="사용자 ID 없음")
        
        from app.core.database import RecommendationSchedule, RecommendationRecord
        
        # 활성 스케줄 조회
        active_schedules = db.query(RecommendationSchedule).filter(
            RecommendationSchedule.uid == uid
        ).all()
        
        result = []
        for schedule in active_schedules:
            # 추천 정보 가져오기
            recommendation = db.query(RecommendationRecord).filter(
                RecommendationRecord.id == schedule.recommendation_id
            ).first()
            
            if recommendation:
                result.append({
                    "schedule_id": schedule.id,
                    "recommendation_id": schedule.recommendation_id,
                    "title": recommendation.title,
                    "category": recommendation.category,
                    "rrule": schedule.rrule,
                    "start_date": schedule.start_date_utc.isoformat(),
                    "end_date": schedule.end_date_utc.isoformat() if schedule.end_date_utc else None,
                    "next_fire_at_utc": schedule.next_fire_at_utc.isoformat() if schedule.next_fire_at_utc else None
                })
        
        logger.info(f"활성 스케줄 조회: uid={uid}, count={len(result)}")
        return {"schedules": result}
        
    except Exception as e:
        logger.error(f"활성 스케줄 조회 실패: {str(e)}")
        raise HTTPException(status_code=500, detail="스케줄 조회 실패")

@router.delete("/schedules/{schedule_id}")
async def deactivate_schedule(
    schedule_id: int,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    스케줄 비활성화
    """
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="사용자 ID 없음")
        
        from app.core.database import RecommendationSchedule
        
        # 스케줄 확인 및 비활성화
        schedule = db.query(RecommendationSchedule).filter(
            and_(
                RecommendationSchedule.id == schedule_id,
                RecommendationSchedule.uid == uid
            )
        ).first()
        
        if not schedule:
            raise HTTPException(status_code=404, detail="스케줄을 찾을 수 없음")
        
        # 스케줄 삭제 (is_active 대신 실제 삭제)
        db.delete(schedule)
        db.commit()
        
        logger.info(f"스케줄 삭제: schedule_id={schedule_id}, uid={uid}")
        return {"message": "스케줄이 삭제되었습니다", "schedule_id": schedule_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"스케줄 비활성화 실패: {str(e)}")
        raise HTTPException(status_code=500, detail="스케줄 비활성화 실패")

