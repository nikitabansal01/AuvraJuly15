import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from app.core.database import (
    RecommendationSchedule, ScheduleRedistribution, DailyAssignment,
    RecommendationRecord, RecommendationAdvice, UserProfile
)
from app.utils.timezone_utils import (
    compute_next_fire_at_utc, get_local_date, should_emit_for_date,
    convert_frequency_detail_to_rrule
)
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

class NewSchedulingService:
    """새로운 시간대 기반 스케줄링 서비스"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_schedule_from_recommendation(self, recommendation, tzid="Asia/Seoul"):
        """
        추천으로부터 스케줄 생성
        
        Args:
            recommendation: RecommendationRecord 객체
            tzid: 시간대 ID (기본값: Asia/Seoul, UserProfile에서 우선 조회)
        
        Returns:
            생성된 RecommendationSchedule 객체
        """
        try:
            logger.info(f"스케줄 생성 시작: recommendation_id={recommendation.id}, timezone={tzid}")
            
            # UserProfile에서 current_timezone 우선 조회
            user_profile = self.db.query(UserProfile).filter(UserProfile.uid == recommendation.uid).first()
            if user_profile and user_profile.current_timezone:
                tzid = user_profile.current_timezone
                logger.info(f"UserProfile에서 시간대 사용: {tzid}")
            else:
                logger.info(f"UserProfile 시간대 없음, 기본값 사용: {tzid}")
            
            # UTC 기준으로 시작/종료 날짜 설정
            start_date_utc = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
            end_date_utc = start_date_utc + timedelta(weeks=recommendation.duration_weeks)
            
            # RRULE 생성
            rrule_str = self._create_rrule_from_frequency(recommendation.frequency_detail, tzid)
            
            # 다음 실행 시각을 UTC로 계산
            next_fire_at_utc = compute_next_fire_at_utc(tzid, 0, 0)
            
            # 스케줄 생성
            schedule = RecommendationSchedule(
                uid=recommendation.uid,
                recommendation_id=recommendation.id,
                start_date_utc=start_date_utc,
                end_date_utc=end_date_utc,
                next_fire_at_utc=next_fire_at_utc,
                rrule=rrule_str,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            self.db.add(schedule)
            self.db.flush()  # ID 생성을 위해 flush만 수행
            
            logger.info(f"스케줄 생성 완료: schedule_id={schedule.id}, timezone={tzid}")
            return schedule
            
        except Exception as e:
            logger.error(f"스케줄 생성 실패: {str(e)}")
            raise Exception(f"스케줄 생성 실패: {str(e)}")
    
    def get_due_schedules(self, limit: int = 500) -> List[RecommendationSchedule]:
        """
        실행 예정인 스케줄들 조회 (배치 처리용)
        
        Args:
            limit: 조회할 최대 개수
        
        Returns:
            실행 예정인 스케줄들
        """
        try:
            now_utc = datetime.utcnow()
            
            schedules = self.db.query(RecommendationSchedule).filter(
                RecommendationSchedule.next_fire_at_utc <= now_utc
            ).order_by(RecommendationSchedule.next_fire_at_utc).limit(limit).all()
            
            return schedules
            
        except Exception as e:
            logger.error(f"실행 예정 스케줄 조회 실패: {str(e)}")
            return []
    
    def process_schedule(self, schedule: RecommendationSchedule) -> bool:
        """
        개별 스케줄 처리 (배치 워커용)
        
        Args:
            schedule: 처리할 스케줄
        
        Returns:
            처리 성공 여부
        """
        try:
            # UserProfile에서 시간대 정보 가져오기
            user_profile = self.db.query(UserProfile).filter(UserProfile.uid == schedule.uid).first()
            tzid = user_profile.current_timezone if user_profile and user_profile.current_timezone else "Asia/Seoul"
            
            # 오늘 로컬 날짜 계산
            today_local = get_local_date(tzid)
            
            # 멱등성 체크: 이미 오늘 발행했는지 확인 (DailyAssignment 존재 여부로 체크)
            existing_assignment = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.schedule_id == schedule.id,
                    DailyAssignment.assignment_date == today_local
                )
            ).first()
            
            if existing_assignment:
                logger.info(f"이미 오늘 발행됨: schedule_id={schedule.id}, date={today_local}")
                # 다음 실행 시각만 업데이트
                self._update_next_fire_time(schedule)
                return True
            
            # 오늘 발행해야 하는지 확인
            should_emit = should_emit_for_date(
                schedule.id, today_local, schedule.rrule, 
                schedule.start_date_utc.date(), schedule.end_date_utc.date() if schedule.end_date_utc else None, self.db
            )
            
            if not should_emit:
                logger.info(f"오늘 발행 대상 아님: schedule_id={schedule.id}, date={today_local}")
                # 다음 실행 시각만 업데이트
                self._update_next_fire_time(schedule)
                return True
            
            # 일일 과제 생성
            self._create_daily_assignments(schedule, today_local)
            
            # 다음 실행 시각 업데이트
            self._update_next_fire_time(schedule)
            
            self.db.commit()
            logger.info(f"스케줄 처리 완료: schedule_id={schedule.id}, date={today_local}")
            return True
            
        except Exception as e:
            logger.error(f"스케줄 처리 실패: {str(e)}")
            self.db.rollback()
            return False
    
    def _create_daily_assignments(self, schedule: RecommendationSchedule, assignment_date: date):
        """
        일일 과제 생성
        
        Args:
            schedule: 스케줄
            assignment_date: 과제 날짜
        """
        try:
            # 추천 정보 가져오기
            recommendation = self.db.query(RecommendationRecord).filter(
                RecommendationRecord.id == schedule.recommendation_id
            ).first()
            
            if not recommendation:
                logger.error(f"추천 정보 없음: recommendation_id={schedule.recommendation_id}")
                return
            
            # 이미 존재하는지 확인 (하나의 추천당 하나의 과제만)
            existing = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.schedule_id == schedule.id,
                    DailyAssignment.assignment_date == assignment_date
                )
            ).first()
            
            if existing:
                logger.info(f"이미 과제 존재: schedule_id={schedule.id}, date={assignment_date}")
                return
            
            # optimal_times에서 첫 번째 시간대만 사용 (하나의 과제로 통합)
            optimal_times = recommendation.optimal_times or ["anytime"]
            time_group = optimal_times[0] if optimal_times else "anytime"
            
            # 새 과제 생성 (하나의 추천당 하나의 과제)
            assignment = DailyAssignment(
                uid=schedule.uid,
                schedule_id=schedule.id,
                recommendation_id=recommendation.id,
                assignment_date=assignment_date,
                time_group=time_group,
                is_completed=False
            )
            
            self.db.add(assignment)
            logger.info(f"일일 과제 생성 완료: schedule_id={schedule.id}, date={assignment_date}, time_group={time_group}")
            
        except Exception as e:
            logger.error(f"일일 과제 생성 실패: {str(e)}")
            raise
    
    def _update_next_fire_time(self, schedule: RecommendationSchedule):
        """
        다음 실행 시각 업데이트
        
        Args:
            schedule: 업데이트할 스케줄
        """
        try:
            # tzid 필드가 제거되어 UserProfile에서 시간대 정보를 가져와야 함
            user_profile = self.db.query(UserProfile).filter(UserProfile.uid == schedule.uid).first()
            tzid = user_profile.current_timezone if user_profile and user_profile.current_timezone else "Asia/Seoul"
            
            schedule.next_fire_at_utc = compute_next_fire_at_utc(tzid, 0, 0)
            schedule.updated_at = datetime.utcnow()
            
        except Exception as e:
            logger.error(f"다음 실행 시각 업데이트 실패: {str(e)}")
            raise
    
    def get_user_assignments_for_date(self, uid: str, target_date: date, 
                                    tzid: str = "Asia/Seoul") -> Dict[str, Any]:
        """
        사용자의 특정 날짜 과제 조회 (API용)
        
        Args:
            uid: 사용자 ID
            target_date: 조회할 날짜
            tzid: 사용자 시간대
        
        Returns:
            과제 정보
        """
        try:
            # 1. 활성 스케줄들 확인 및 보정
            self._ensure_schedules_emitted_for_date(uid, target_date, tzid)
            
            # 2. 기존 과제 정리 및 새로운 선별 로직 적용
            self._cleanup_and_reselect_assignments(uid, target_date, tzid)
            
            # 3. 해당 날짜의 과제들 조회
            assignments = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == uid,
                    DailyAssignment.assignment_date == target_date
                )
            ).all()
            
            # 3. 시간대별로 그룹화
            time_groups = {
                "morning": [],
                "afternoon": [],
                "night": [],
                "anytime": []
            }
            
            completed_count = 0
            
            for assignment in assignments:
                # 추천 정보 가져오기
                recommendation = self.db.query(RecommendationRecord).filter(
                    RecommendationRecord.id == assignment.recommendation_id
                ).first()
                
                if not recommendation:
                    continue
                
                # 디버깅: 추천 데이터 확인
                logger.debug(f"추천 데이터 확인: id={recommendation.id}, specific_action={recommendation.specific_action}, research_summary={recommendation.research_summary}")
                
                # 조언 정보 가져오기
                advices = self.db.query(RecommendationAdvice).filter(
                    RecommendationAdvice.recommendation_id == recommendation.id
                ).all()
                
                # 과제 정보 구성
                assignment_info = {
                    "id": assignment.id,
                    "recommendation_id": recommendation.id,
                    "title": recommendation.title,
                    "purpose": recommendation.purpose,
                    "specific_action": recommendation.specific_action or "",
                    "category": recommendation.category,
                    "conditions": recommendation.conditions or [],
                    "symptoms": recommendation.symptoms or [],
                    "hormones": recommendation.hormones or [],
                    "research_summary": recommendation.research_summary or "",
                    "research_studies": recommendation.research_studies or [],
                    "is_completed": assignment.is_completed,
                    "completed_at": assignment.completed_at.isoformat() if assignment.completed_at else None,
                    "advices": [
                        {
                            "type": advice.advice_type,
                            "category": advice.category,
                            "title": advice.title,
                            "description": advice.description
                        }
                        for advice in advices
                    ]
                }
                
                # 완료 상태 로깅 (디버깅용)
                if assignment.is_completed:
                    logger.debug(f"완료된 과제 조회: assignment_id={assignment.id}, completed_at={assignment.completed_at}")
                
                # 카테고리별 세부 정보 추가
                if recommendation.category == "food":
                    assignment_info.update({
                        "food_amounts": recommendation.food_amounts or [],
                        "food_items": recommendation.food_items or []
                    })
                elif recommendation.category == "movement":
                    assignment_info.update({
                        "exercise_durations": recommendation.exercise_durations or [],
                        "exercise_types": recommendation.exercise_types or [],
                        "exercise_intensities": recommendation.exercise_intensities or []
                    })
                elif recommendation.category == "mindfulness":
                    assignment_info.update({
                        "mindfulness_durations": recommendation.mindfulness_durations or [],
                        "mindfulness_techniques": recommendation.mindfulness_techniques or []
                    })
                
                time_groups[assignment.time_group].append(assignment_info)
                
                if assignment.is_completed:
                    completed_count += 1
            
            # 4. 완료된 과제들을 맨 앞으로 정렬
            for time_group in time_groups.values():
                time_group.sort(key=lambda x: (not x["is_completed"], x["id"]))
            
            # 5. 호르몬별 통계 계산
            hormone_stats = self._calculate_hormone_stats(assignments)
            
            return {
                "date": target_date.isoformat(),
                "assignments": time_groups,
                "total_assignments": len(assignments),
                "completed_assignments": completed_count,
                "completion_rate": (completed_count / len(assignments) * 100) if assignments else 0,
                "hormone_stats": hormone_stats
            }
            
        except Exception as e:
            logger.error(f"사용자 과제 조회 실패: {str(e)}")
            return {
                "date": target_date.isoformat(),
                "assignments": {"morning": [], "afternoon": [], "night": [], "anytime": []},
                "total_assignments": 0,
                "completed_assignments": 0,
                "completion_rate": 0,
                "hormone_stats": {}
            }
    
    def _ensure_schedules_emitted_for_date(self, uid: str, target_date: date, tzid: str):
        """
        특정 날짜에 대한 스케줄 발행 보장 (API 보정용)
        하루에 3-4개의 과제만 선별하여 생성
        
        Args:
            uid: 사용자 ID
            target_date: 대상 날짜
            tzid: 사용자 시간대
        """
        try:
            # 활성 스케줄들 조회
            schedules = self.db.query(RecommendationSchedule).filter(
                RecommendationSchedule.uid == uid
            ).all()
            
            # 해당 날짜에 발행해야 하는 스케줄들 필터링
            eligible_schedules = []
            for schedule in schedules:
                should_emit = should_emit_for_date(
                    schedule.id, target_date, schedule.rrule,
                    schedule.start_date_utc.date(), schedule.end_date_utc.date() if schedule.end_date_utc else None, self.db
                )
                
                if should_emit:
                    # 이미 과제가 존재하는지 확인
                    existing_assignment = self.db.query(DailyAssignment).filter(
                        and_(
                            DailyAssignment.schedule_id == schedule.id,
                            DailyAssignment.assignment_date == target_date
                        )
                    ).first()
                    
                    if not existing_assignment:
                        eligible_schedules.append(schedule)
            
            # 3-4개만 선별하여 과제 생성
            if eligible_schedules:
                # 우선순위에 따라 정렬 (priority, created_at 등)
                selected_schedules = self._select_daily_assignments(eligible_schedules, target_date)
                
                for schedule in selected_schedules:
                    self._create_daily_assignments(schedule, target_date)
                    schedule.updated_at = datetime.utcnow()
                    logger.info(f"선별된 과제 생성: schedule_id={schedule.id}, date={target_date}")
            
            self.db.commit()
            
        except Exception as e:
            logger.error(f"스케줄 발행 보장 실패: {str(e)}")
            self.db.rollback()
    
    def _cleanup_duplicate_assignments(self, uid: str, target_date: date):
        """
        중복 과제 정리 (하나의 스케줄당 하나의 과제만 유지)
        
        Args:
            uid: 사용자 ID
            target_date: 대상 날짜
        """
        try:
            # 해당 날짜의 모든 과제 조회
            assignments = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == uid,
                    DailyAssignment.assignment_date == target_date
                )
            ).all()
            
            # 스케줄별로 그룹화
            schedule_assignments = {}
            for assignment in assignments:
                if assignment.schedule_id not in schedule_assignments:
                    schedule_assignments[assignment.schedule_id] = []
                schedule_assignments[assignment.schedule_id].append(assignment)
            
            # 각 스케줄에서 첫 번째 과제만 유지하고 나머지 삭제
            for schedule_id, assignment_list in schedule_assignments.items():
                if len(assignment_list) > 1:
                    # 첫 번째 과제 유지, 나머지 삭제
                    for assignment in assignment_list[1:]:
                        self.db.delete(assignment)
                        logger.info(f"중복 과제 삭제: assignment_id={assignment.id}, schedule_id={schedule_id}")
            
            self.db.commit()
            
        except Exception as e:
            logger.error(f"중복 과제 정리 실패: {str(e)}")
            self.db.rollback()
    
    def _cleanup_and_reselect_assignments(self, uid: str, target_date: date, tzid: str):
        """
        기존 과제 정리 및 새로운 선별 로직 적용
        
        Args:
            uid: 사용자 ID
            target_date: 대상 날짜
            tzid: 사용자 시간대
        """
        try:
            # 1. 기존 과제들 조회
            existing_assignments = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == uid,
                    DailyAssignment.assignment_date == target_date
                )
            ).all()
            
            # 2. 과제가 4개 이상이면 기존 과제들 삭제
            if len(existing_assignments) > 4:
                logger.info(f"기존 과제 {len(existing_assignments)}개 삭제 후 재선별")
                for assignment in existing_assignments:
                    self.db.delete(assignment)
                self.db.commit()
                
                            # 3. 새로운 선별 로직으로 과제 재생성 (재귀 방지)
            self._create_selected_assignments(uid, target_date, tzid)
            
            # 4. 중복 과제 정리 (하나의 스케줄당 하나의 과제만)
            self._cleanup_duplicate_assignments(uid, target_date)
            
        except Exception as e:
            logger.error(f"과제 정리 및 재선별 실패: {str(e)}")
            self.db.rollback()
    
    def _select_daily_assignments(self, eligible_schedules: List[RecommendationSchedule], target_date: date) -> List[RecommendationSchedule]:
        """
        하루에 표시할 과제들을 선별 (3-4개)
        
        Args:
            eligible_schedules: 선별 대상 스케줄들
            target_date: 대상 날짜
        
        Returns:
            선별된 스케줄들 (3-4개)
        """
        try:
            if not eligible_schedules:
                return []
            
            # 현재 과제 수 확인
            current_assignments = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == eligible_schedules[0].uid,
                    DailyAssignment.assignment_date == target_date
                )
            ).count()
            
            # 이미 4개 이상이면 추가 생성하지 않음
            if current_assignments >= 4:
                logger.info(f"이미 충분한 과제 존재: {current_assignments}개")
                return []
            
            # 추가로 생성할 수 있는 과제 수
            max_new_assignments = 4 - current_assignments
            
            # 우선순위에 따라 정렬
            sorted_schedules = self._prioritize_schedules(eligible_schedules, target_date)
            
            # 상위 3-4개만 선택
            selected_schedules = sorted_schedules[:max_new_assignments]
            
            logger.info(f"과제 선별 완료: {len(selected_schedules)}개 선택 (기존 {current_assignments}개 + 신규 {len(selected_schedules)}개)")
            return selected_schedules
            
        except Exception as e:
            logger.error(f"과제 선별 실패: {str(e)}")
            return eligible_schedules[:3]  # 에러 시 기본값
    
    def _prioritize_schedules(self, schedules: List[RecommendationSchedule], target_date: date) -> List[RecommendationSchedule]:
        """
        스케줄 우선순위 정렬
        
        Args:
            schedules: 정렬할 스케줄들
            target_date: 대상 날짜
        
        Returns:
            우선순위별로 정렬된 스케줄들
        """
        try:
            # 추천 정보와 함께 스케줄 정보 구성
            schedule_info = []
            for schedule in schedules:
                recommendation = self.db.query(RecommendationRecord).filter(
                    RecommendationRecord.id == schedule.recommendation_id
                ).first()
                
                if recommendation:
                    # 우선순위 점수 계산
                    priority_score = self._calculate_priority_score(recommendation, schedule, target_date)
                    schedule_info.append((schedule, priority_score))
            
            # 우선순위 점수로 정렬 (높은 점수 우선)
            schedule_info.sort(key=lambda x: x[1], reverse=True)
            
            return [schedule for schedule, score in schedule_info]
            
        except Exception as e:
            logger.error(f"스케줄 우선순위 정렬 실패: {str(e)}")
            return schedules
    
    def _calculate_priority_score(self, recommendation: RecommendationRecord, schedule: RecommendationSchedule, target_date: date) -> float:
        """
        우선순위 점수 계산
        
        Args:
            recommendation: 추천 정보
            schedule: 스케줄 정보
            target_date: 대상 날짜
        
        Returns:
            우선순위 점수 (높을수록 우선)
        """
        try:
            score = 0.0
            
            # 1. 우선순위 (high > medium > low)
            priority_map = {"high": 100, "medium": 50, "low": 10}
            score += priority_map.get(recommendation.priority, 25)
            
            # 2. 최근 생성된 추천 우선 (신선도)
            days_since_creation = (target_date - recommendation.created_at.date()).days
            freshness_score = max(0, 30 - days_since_creation)  # 최대 30점
            score += freshness_score
            
            # 3. 카테고리 다양성 (food, movement, mindfulness 균형)
            # 이는 이미 생성된 과제들과 비교하여 계산
            
            # 4. 호르몬 중요도 (insulin, cortisol 등이 더 중요)
            hormone_importance = {
                "insulin": 20, "cortisol": 20, "estrogen": 15, 
                "progesterone": 15, "androgens": 10, "thyroid": 10
            }
            
            if recommendation.hormones:
                for hormone in recommendation.hormones:
                    score += hormone_importance.get(hormone.lower(), 5)
            
            return score
            
        except Exception as e:
            logger.error(f"우선순위 점수 계산 실패: {str(e)}")
            return 25.0  # 기본값
    
    def _create_selected_assignments(self, uid: str, target_date: date, tzid: str):
        """
        선별된 과제들만 생성 (재귀 방지용)
        
        Args:
            uid: 사용자 ID
            target_date: 대상 날짜
            tzid: 사용자 시간대
        """
        try:
            # 활성 스케줄들 조회
            schedules = self.db.query(RecommendationSchedule).filter(
                RecommendationSchedule.uid == uid
            ).all()
            
            # 해당 날짜에 발행해야 하는 스케줄들 필터링
            eligible_schedules = []
            for schedule in schedules:
                should_emit = should_emit_for_date(
                    schedule.id, target_date, schedule.rrule,
                    schedule.start_date_utc.date(), schedule.end_date_utc.date() if schedule.end_date_utc else None, self.db
                )
                
                if should_emit:
                    eligible_schedules.append(schedule)
            
            # 3-4개만 선별하여 과제 생성
            if eligible_schedules:
                selected_schedules = self._select_daily_assignments(eligible_schedules, target_date)
                
                for schedule in selected_schedules:
                    self._create_daily_assignments(schedule, target_date)
                    schedule.updated_at = datetime.utcnow()
                    logger.info(f"재선별된 과제 생성: schedule_id={schedule.id}, date={target_date}")
            
            self.db.commit()
            
        except Exception as e:
            logger.error(f"선별된 과제 생성 실패: {str(e)}")
            self.db.rollback()
    
    def _calculate_hormone_stats(self, assignments: List[DailyAssignment]) -> Dict[str, Any]:
        """
        호르몬별 통계 계산
        
        Args:
            assignments: 과제 목록
        
        Returns:
            호르몬별 통계
        """
        try:
            hormone_stats = {}
            
            for assignment in assignments:
                recommendation = self.db.query(RecommendationRecord).filter(
                    RecommendationRecord.id == assignment.recommendation_id
                ).first()
                
                if not recommendation or not recommendation.hormones:
                    continue
                
                for hormone in recommendation.hormones:
                    if hormone not in hormone_stats:
                        hormone_stats[hormone] = {"total": 0, "completed": 0}
                    
                    hormone_stats[hormone]["total"] += 1
                    if assignment.is_completed:
                        hormone_stats[hormone]["completed"] += 1
            
            return hormone_stats
            
        except Exception as e:
            logger.error(f"호르몬 통계 계산 실패: {str(e)}")
            return {}
    
    def mark_assignment_completed(self, assignment_id: int, uid: str, 
                                notes: Optional[str] = None) -> bool:
        """
        과제 완료 표시
        
        Args:
            assignment_id: 과제 ID
            uid: 사용자 ID
            notes: 메모
        
        Returns:
            성공 여부
        """
        try:
            assignment = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.id == assignment_id,
                    DailyAssignment.uid == uid
                )
            ).first()
            
            if not assignment:
                logger.error(f"과제 없음: assignment_id={assignment_id}, uid={uid}")
                return False
            
            assignment.is_completed = True
            assignment.completed_at = datetime.utcnow()
            assignment.notes = notes
            assignment.updated_at = datetime.utcnow()
            
            self.db.commit()
            logger.info(f"과제 완료 표시: assignment_id={assignment_id}, uid={uid}, is_completed={assignment.is_completed}")
            return True
            
        except Exception as e:
            logger.error(f"과제 완료 표시 실패: {str(e)}")
            self.db.rollback()
            return False
    
    def create_redistribution(self, schedule_id: int, original_date: date, 
                            override_date: date, reason: str, 
                            source: str = "system") -> bool:
        """
        재배치 정보 생성
        
        Args:
            schedule_id: 스케줄 ID
            original_date: 원래 날짜
            override_date: 재배치 날짜
            reason: 재배치 이유
            source: 재배치 소스
        
        Returns:
            성공 여부
        """
        try:
            redistribution = ScheduleRedistribution(
                schedule_id=schedule_id,
                original_date=original_date,
                override_date=override_date,
                reason=reason,
                source=source
            )
            
            self.db.add(redistribution)
            self.db.commit()
            
            logger.info(f"재배치 정보 생성: schedule_id={schedule_id}, original={original_date}, override={override_date}")
            return True
            
        except Exception as e:
            logger.error(f"재배치 정보 생성 실패: {str(e)}")
            self.db.rollback()
            return False

    def _create_rrule_from_frequency(self, frequency_detail: str, tzid: str) -> str:
        """
        frequency_detail을 RRULE로 변환
        
        Args:
            frequency_detail: 빈도 상세 정보 (예: "daily:1", "weekly:3")
            tzid: 시간대 ID
        
        Returns:
            RRULE 문자열
        """
        try:
            # frequency_detail 파싱
            if ":" not in frequency_detail:
                logger.warning(f"잘못된 frequency_detail 형식: {frequency_detail}")
                return "FREQ=DAILY;INTERVAL=1"
            
            freq_type, times = frequency_detail.split(":")
            times = int(times)
            
            # RRULE 생성
            if freq_type.lower() == "daily":
                return f"FREQ=DAILY;INTERVAL={max(1, 7 // times)}"
            elif freq_type.lower() == "weekly":
                return f"FREQ=WEEKLY;INTERVAL={max(1, 4 // times)}"
            elif freq_type.lower() == "monthly":
                return f"FREQ=MONTHLY;INTERVAL={max(1, 12 // times)}"
            else:
                logger.warning(f"알 수 없는 빈도 타입: {freq_type}")
                return "FREQ=DAILY;INTERVAL=1"
                
        except Exception as e:
            logger.error(f"RRULE 생성 실패: {str(e)}")
            return "FREQ=DAILY;INTERVAL=1"

