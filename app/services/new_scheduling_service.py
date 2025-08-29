import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from app.core.database import (
    RecommendationSchedule, ScheduleRedistribution, DailyAssignment,
    RecommendationRecord, RecommendationAdvice, UserProfile, UserResponse
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
            # 1. 어제 미완료 과제 재배치 처리
            self._handle_uncompleted_assignments(uid, target_date)
            
            # 2. 활성 스케줄들 확인 및 보정
            self._ensure_schedules_emitted_for_date(uid, target_date, tzid)
            
            # 3. 기존 과제 정리 및 새로운 선별 로직 적용
            self._cleanup_and_reselect_assignments(uid, target_date, tzid)
            
            # 3. 해당 날짜의 과제들 조회
            assignments = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == uid,
                    DailyAssignment.assignment_date == target_date
                )
            ).all()
            
            # 3. 시간대별로 그룹화 및 완료된 과제 분리
            time_groups = {
                "morning": [],
                "afternoon": [],
                "night": [],
                "anytime": []
            }
            
            completed_group = []  # 완료된 과제들을 별도로 저장
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
            
            # 4. 완료된 과제를 별도 섹션으로 분리
            completed_group, reorganized_time_groups = self._reorganize_assignments_with_completed_group(time_groups)
            
            # 5. 호르몬별 통계 계산
            hormone_stats = self._calculate_hormone_stats(assignments)
            
            # completed 섹션을 assignments 내에서 최상단에 배치
            assignments_with_completed = {
                "completed": completed_group,
                **reorganized_time_groups
            }
            
            return {
                "date": target_date.isoformat(),
                "assignments": assignments_with_completed,
                "total_assignments": len(assignments),
                "completed_assignments": completed_count,
                "completion_rate": (completed_count / len(assignments) * 100) if assignments else 0,
                "hormone_stats": hormone_stats
            }
            
        except Exception as e:
            logger.error(f"사용자 과제 조회 실패: {str(e)}")
            return {
                "date": target_date.isoformat(),
                "assignments": {
                    "completed": [],
                    "morning": [],
                    "afternoon": [],
                    "night": [],
                    "anytime": []
                },
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
            
            # 해당 날짜에 발행해야 하는 스케줄들 필터링 (재배치 고려)
            eligible_schedules = []
            for schedule in schedules:
                should_emit = self._should_emit_for_date_with_redistribution(
                    schedule, target_date
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
        Primary/Secondary hormone 기반 균등 선택, 홀수일 때 primary 우선
        
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
            
            # UserResponse에서 primary/secondary hormone 정보 가져오기
            uid = eligible_schedules[0].uid
            user_response = self.db.query(UserResponse).filter(UserResponse.uid == uid).first()
            
            if user_response and user_response.primary_hormone:
                # Primary/Secondary hormone 기반 균등 선택
                selected_schedules = self._select_balanced_hormone_assignments(
                    eligible_schedules, target_date, max_new_assignments, 
                    user_response.primary_hormone, user_response.secondary_hormones
                )
            else:
                # 기존 우선순위 방식 fallback
                sorted_schedules = self._prioritize_schedules(eligible_schedules, target_date)
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
        우선순위 점수 계산 (Primary/Secondary hormone 기반)
        
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
            
            # 4. 호르몬 중요도 (UserResponse의 primary/secondary hormone 기반)
            user_response = self.db.query(UserResponse).filter(
                UserResponse.uid == schedule.uid
            ).first()
            
            if user_response and recommendation.hormones:
                for hormone in recommendation.hormones:
                    hormone_lower = hormone.lower()
                    # Primary hormone은 최고 점수 (50점)
                    if user_response.primary_hormone and hormone_lower == user_response.primary_hormone.lower():
                        score += 50
                    # Secondary hormone은 중간 점수 (30점)
                    elif user_response.secondary_hormones and hormone_lower in [h.lower() for h in user_response.secondary_hormones]:
                        score += 30
                    # 기타 호르몬은 기본 점수 (5점)
                    else:
                        score += 5
            elif recommendation.hormones:
                # UserResponse가 없으면 기존 하드코딩 방식 fallback
                hormone_importance = {
                    "insulin": 20, "cortisol": 20, "estrogen": 15, 
                    "progesterone": 15, "androgens": 10, "thyroid": 10
                }
                for hormone in recommendation.hormones:
                    score += hormone_importance.get(hormone.lower(), 5)
            
            return score
            
        except Exception as e:
            logger.error(f"우선순위 점수 계산 실패: {str(e)}")
            return 25.0  # 기본값
    
    def _select_balanced_hormone_assignments(self, schedules: List[RecommendationSchedule], target_date: date, 
                                           max_count: int, primary_hormone: str, secondary_hormones: List[str]) -> List[RecommendationSchedule]:
        """
        Primary/Secondary hormone 기반 균등 선택
        홀수 개수일 때는 primary hormone 우선
        
        Args:
            schedules: 선별 대상 스케줄들
            target_date: 대상 날짜  
            max_count: 최대 선택 개수
            primary_hormone: 주요 호르몬
            secondary_hormones: 보조 호르몬들
        
        Returns:
            선별된 스케줄들
        """
        try:
            # 호르몬별로 추천 분류
            primary_schedules = []
            secondary_schedules = []
            other_schedules = []
            
            for schedule in schedules:
                recommendation = self.db.query(RecommendationRecord).filter(
                    RecommendationRecord.id == schedule.recommendation_id
                ).first()
                
                if not recommendation or not recommendation.hormones:
                    other_schedules.append(schedule)
                    continue
                
                # 추천의 호르몬들을 소문자로 변환하여 비교
                rec_hormones = [h.lower() for h in recommendation.hormones]
                
                # Primary hormone 관련 추천인지 확인
                if primary_hormone.lower() in rec_hormones:
                    primary_schedules.append(schedule)
                # Secondary hormone 관련 추천인지 확인
                elif secondary_hormones and any(sh.lower() in rec_hormones for sh in secondary_hormones):
                    secondary_schedules.append(schedule)
                else:
                    other_schedules.append(schedule)
            
            # 각 카테고리별로 우선순위 정렬
            primary_schedules = self._prioritize_schedules(primary_schedules, target_date)
            secondary_schedules = self._prioritize_schedules(secondary_schedules, target_date)
            other_schedules = self._prioritize_schedules(other_schedules, target_date)
            
            # 균등 선택 로직
            selected = []
            
            if max_count <= 0:
                return selected
            
            # Primary와 Secondary 호르몬 관련 추천이 각각 최소 1개씩 포함되도록 보장
            if primary_schedules and len(selected) < max_count:
                selected.append(primary_schedules[0])
                primary_schedules = primary_schedules[1:]
            
            if secondary_schedules and len(selected) < max_count:
                selected.append(secondary_schedules[0])
                secondary_schedules = secondary_schedules[1:]
            
            # 나머지 자리는 균등하게 배분
            # 홀수 개가 남으면 primary 우선
            remaining_count = max_count - len(selected)
            
            while remaining_count > 0:
                added_in_round = False
                
                # Primary 추가 (홀수일 때 우선)
                if primary_schedules and remaining_count > 0:
                    selected.append(primary_schedules[0])
                    primary_schedules = primary_schedules[1:]
                    remaining_count -= 1
                    added_in_round = True
                
                # Secondary 추가
                if secondary_schedules and remaining_count > 0:
                    selected.append(secondary_schedules[0])
                    secondary_schedules = secondary_schedules[1:]
                    remaining_count -= 1
                    added_in_round = True
                
                # 기타 추가
                if other_schedules and remaining_count > 0:
                    selected.append(other_schedules[0])
                    other_schedules = other_schedules[1:]
                    remaining_count -= 1
                    added_in_round = True
                
                # 더 이상 추가할 추천이 없으면 종료
                if not added_in_round:
                    break
            
            logger.info(f"균등 선택 완료: primary={len([s for s in selected if self._is_primary_hormone_schedule(s, primary_hormone)])}, " +
                       f"secondary={len([s for s in selected if self._is_secondary_hormone_schedule(s, secondary_hormones)])}, " +
                       f"total={len(selected)}")
            
            return selected
            
        except Exception as e:
            logger.error(f"균등 선택 실패: {str(e)}")
            # 에러 시 기본 우선순위 방식으로 fallback
            sorted_schedules = self._prioritize_schedules(schedules, target_date)
            return sorted_schedules[:max_count]
    
    def _is_primary_hormone_schedule(self, schedule: RecommendationSchedule, primary_hormone: str) -> bool:
        """스케줄이 primary hormone 관련인지 확인"""
        try:
            recommendation = self.db.query(RecommendationRecord).filter(
                RecommendationRecord.id == schedule.recommendation_id
            ).first()
            
            if not recommendation or not recommendation.hormones:
                return False
                
            return primary_hormone.lower() in [h.lower() for h in recommendation.hormones]
        except:
            return False
    
    def _is_secondary_hormone_schedule(self, schedule: RecommendationSchedule, secondary_hormones: List[str]) -> bool:
        """스케줄이 secondary hormone 관련인지 확인"""
        try:
            if not secondary_hormones:
                return False
                
            recommendation = self.db.query(RecommendationRecord).filter(
                RecommendationRecord.id == schedule.recommendation_id
            ).first()
            
            if not recommendation or not recommendation.hormones:
                return False
                
            rec_hormones = [h.lower() for h in recommendation.hormones]
            return any(sh.lower() in rec_hormones for sh in secondary_hormones)
        except:
            return False
    
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
    
    def _reorganize_assignments_with_completed_group(self, time_groups: Dict[str, List[Dict[str, Any]]]) -> tuple:
        """
        과제를 재구성하여 완료된 과제들을 별도 그룹으로 분리
        - 시간대 내에서 완료된 과제들은 맨 앞에 유지
        - 이전 시간대가 미완료인데 다음 시간대가 완료된 과제들만 completed 섹션으로 이동
        
        Args:
            time_groups: 시간대별 과제 그룹
            
        Returns:
            (completed_group, reorganized_time_groups)
        """
        try:
            completed_group = []
            reorganized_time_groups = {
                "morning": [],
                "afternoon": [], 
                "night": [],
                "anytime": []
            }
            
            # 시간대 순서 정의
            time_order = ['morning', 'afternoon', 'night', 'anytime']
            
            # 각 시간대별로 미완료 과제가 있는지 확인
            has_incomplete_before = {}  # 이전 시간대에 미완료 과제가 있는지
            current_has_incomplete = False
            
            for time_group in time_order:
                items = time_groups[time_group]
                has_incomplete_before[time_group] = current_has_incomplete
                
                # 현재 시간대에 미완료 과제가 있는지 확인
                has_incomplete_in_current = any(not item["is_completed"] for item in items)
                current_has_incomplete = current_has_incomplete or has_incomplete_in_current
            
            # 과제 재구성
            for time_group in time_order:
                items = time_groups[time_group]
                
                for item in items:
                    if item["is_completed"]:
                        # 이전 시간대에 미완료 과제가 있고, 현재 시간대가 완료된 경우만 completed 섹션으로 이동
                        if has_incomplete_before[time_group]:
                            completed_group.append(item)
                        else:
                            # 그 외의 경우는 원래 시간대에 유지 (맨 앞에 정렬됨)
                            reorganized_time_groups[time_group].append(item)
                    else:
                        # 미완료 과제는 원래 시간대에 추가
                        reorganized_time_groups[time_group].append(item)
            
            return completed_group, reorganized_time_groups
            
        except Exception as e:
            logger.error(f"과제 재구성 실패: {str(e)}")
            return [], time_groups
    
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
    
    def _handle_uncompleted_assignments(self, uid: str, target_date: date):
        """
        어제 미완료된 과제들을 재배치
        
        Args:
            uid: 사용자 ID
            target_date: 대상 날짜 (오늘)
        """
        try:
            # 어제 날짜 계산
            yesterday = target_date - timedelta(days=1)
            
            # 어제 미완료된 과제들 찾기
            yesterday_assignments = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == uid,
                    DailyAssignment.assignment_date == yesterday,
                    DailyAssignment.is_completed == False
                )
            ).all()
            
            if not yesterday_assignments:
                return  # 어제 미완료 과제가 없음
            
            logger.info(f"어제 미완료 과제 {len(yesterday_assignments)}개 발견: uid={uid}, date={yesterday}")
            
            # 각 미완료 과제에 대해 재배치 로직 적용
            for assignment in yesterday_assignments:
                self._redistribute_uncompleted_assignment(assignment, target_date)
                
        except Exception as e:
            logger.error(f"미완료 과제 재배치 실패: {str(e)}")
    
    def _redistribute_uncompleted_assignment(self, assignment: DailyAssignment, target_date: date):
        """
        특정 미완료 과제를 재배치
        
        Args:
            assignment: 미완료 과제
            target_date: 재배치 대상 날짜
        """
        try:
            # 추천 정보 가져오기
            recommendation = self.db.query(RecommendationRecord).filter(
                RecommendationRecord.id == assignment.recommendation_id
            ).first()
            
            if not recommendation:
                logger.warning(f"추천 정보 없음: recommendation_id={assignment.recommendation_id}")
                return
            
            # frequency_detail 파싱
            frequency_info = self._parse_frequency_detail(recommendation.frequency_detail)
            if not frequency_info:
                logger.warning(f"frequency_detail 파싱 실패: {recommendation.frequency_detail}")
                return
            
            # daily 추천은 재배치하지 않음 (매일 나타나므로)
            if frequency_info.get('type') == 'daily':
                logger.info(f"Daily 과제는 재배치하지 않음: assignment_id={assignment.id}")
                return
            
            # 남은 기간 계산
            remaining_days = self._calculate_remaining_days(recommendation, target_date)
            if remaining_days <= 0:
                logger.info(f"기간 만료된 과제: assignment_id={assignment.id}")
                return
            
            # 재배치된 날짜들 계산
            redistributed_dates = self._calculate_redistributed_dates(
                recommendation, frequency_info, target_date, remaining_days
            )
            
            if not redistributed_dates:
                logger.warning(f"재배치 날짜 계산 실패: assignment_id={assignment.id}")
                return
            
            # 재배치 정보 저장
            self._save_redistribution_info(assignment, redistributed_dates)
            
            logger.info(f"과제 재배치 완료: assignment_id={assignment.id}, dates={[d.isoformat() for d in redistributed_dates]}")
            
        except Exception as e:
            logger.error(f"과제 재배치 실패: assignment_id={assignment.id}, error={str(e)}")
    
    def _parse_frequency_detail(self, frequency_detail: str) -> Optional[Dict[str, Any]]:
        """
        frequency_detail을 파싱
        
        Args:
            frequency_detail: 빈도 상세 정보 (예: "daily:1", "weekly:3")
            
        Returns:
            파싱된 빈도 정보
        """
        if not frequency_detail or ':' not in frequency_detail:
            return None
        
        try:
            freq_type, times_str = frequency_detail.split(':', 1)
            times = int(times_str)
            
            return {
                'type': freq_type.lower(),
                'times': times,
                'description': frequency_detail
            }
        except (ValueError, AttributeError):
            return None
    
    def _calculate_remaining_days(self, recommendation: RecommendationRecord, target_date: date) -> int:
        """
        추천의 남은 기간을 계산
        
        Args:
            recommendation: 추천 정보
            target_date: 기준 날짜
            
        Returns:
            남은 일수
        """
        try:
            if not recommendation.duration_weeks:
                return 365  # duration이 없으면 1년으로 가정
                
            created_date = recommendation.created_at.date()
            end_date = created_date + timedelta(weeks=recommendation.duration_weeks)
            
            # target_date가 end_date를 넘어가면 0 반환
            if target_date >= end_date:
                return 0
            
            return (end_date - target_date).days
            
        except Exception as e:
            logger.error(f"남은 기간 계산 실패: {str(e)}")
            return 0
    
    def _calculate_redistributed_dates(self, recommendation: RecommendationRecord, frequency_info: Dict[str, Any], 
                                     target_date: date, remaining_days: int) -> List[date]:
        """
        재배치된 날짜들을 계산
        
        Args:
            recommendation: 추천 정보
            frequency_info: 빈도 정보
            target_date: 시작 날짜
            remaining_days: 남은 일수
            
        Returns:
            재배치 날짜 리스트
        """
        try:
            freq_type = frequency_info['type']
            times = frequency_info['times']
            
            if freq_type == 'weekly':
                # 주 단위: 남은 기간을 times개로 나누어 균등 분배
                if remaining_days < times:
                    # 남은 일수가 times보다 적으면 매일
                    return [target_date + timedelta(days=i) for i in range(remaining_days)]
                else:
                    # 균등 분배
                    interval = remaining_days // times
                    dates = []
                    for i in range(times):
                        day_offset = i * interval
                        if day_offset < remaining_days:
                            dates.append(target_date + timedelta(days=day_offset))
                    return dates
                    
            elif freq_type == 'monthly':
                # 월 단위: 남은 기간을 times개로 나누어 균등 분배
                if remaining_days < times:
                    # 남은 일수가 times보다 적으면 매일
                    return [target_date + timedelta(days=i) for i in range(remaining_days)]
                else:
                    # 균등 분배
                    interval = remaining_days // times
                    dates = []
                    for i in range(times):
                        day_offset = i * interval
                        if day_offset < remaining_days:
                            dates.append(target_date + timedelta(days=day_offset))
                    return dates
            
            return []
            
        except Exception as e:
            logger.error(f"재배치 날짜 계산 실패: {str(e)}")
            return []
    
    def _save_redistribution_info(self, assignment: DailyAssignment, redistributed_dates: List[date]):
        """
        재배치 정보를 저장
        
        Args:
            assignment: 원본 과제
            redistributed_dates: 재배치 날짜들
        """
        try:
            # 각 재배치 날짜마다 ScheduleRedistribution 레코드 생성
            for redistributed_date in redistributed_dates:
                redistribution = ScheduleRedistribution(
                    schedule_id=assignment.schedule_id,
                    original_date=assignment.assignment_date,
                    override_date=redistributed_date,
                    reason="uncompleted",
                    source="system"
                )
                
                self.db.add(redistribution)
            
            self.db.commit()
            logger.info(f"재배치 정보 저장 완료: schedule_id={assignment.schedule_id}, dates={len(redistributed_dates)}개")
            
        except Exception as e:
            logger.error(f"재배치 정보 저장 실패: {str(e)}")
            self.db.rollback()
    
    def _should_emit_for_date_with_redistribution(self, schedule: RecommendationSchedule, target_date: date) -> bool:
        """
        재배치 정보를 고려하여 해당 날짜에 스케줄을 실행해야 하는지 확인
        
        Args:
            schedule: 스케줄 정보
            target_date: 확인할 날짜
            
        Returns:
            실행 여부
        """
        try:
            # 1. 기본 RRULE 확인
            should_emit_original = should_emit_for_date(
                schedule.id, target_date, schedule.rrule,
                schedule.start_date_utc.date(), 
                schedule.end_date_utc.date() if schedule.end_date_utc else None, 
                self.db
            )
            
            # 2. 재배치 정보 확인
            redistribution = self.db.query(ScheduleRedistribution).filter(
                and_(
                    ScheduleRedistribution.schedule_id == schedule.id,
                    ScheduleRedistribution.override_date == target_date
                )
            ).first()
            
            # 재배치 정보가 있으면 해당 날짜에 실행
            if redistribution:
                logger.info(f"재배치된 과제 발견: schedule_id={schedule.id}, date={target_date}, original_date={redistribution.original_date}")
                return True
            
            # 원래 날짜에서 재배치된 경우 원래 날짜에서는 실행하지 않음
            if should_emit_original:
                redistributed_from_today = self.db.query(ScheduleRedistribution).filter(
                    and_(
                        ScheduleRedistribution.schedule_id == schedule.id,
                        ScheduleRedistribution.original_date == target_date
                    )
                ).first()
                
                if redistributed_from_today:
                    logger.info(f"오늘 날짜에서 재배치된 과제: schedule_id={schedule.id}, date={target_date}")
                    return False
            
            return should_emit_original
            
        except Exception as e:
            logger.error(f"재배치 고려 실행 여부 확인 실패: {str(e)}")
            # 에러 시 기본 RRULE로 fallback
            return should_emit_for_date(
                schedule.id, target_date, schedule.rrule,
                schedule.start_date_utc.date(), 
                schedule.end_date_utc.date() if schedule.end_date_utc else None, 
                self.db
            )

