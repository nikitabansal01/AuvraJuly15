import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, func

from app.core.database import RecommendationRecord, UserSchedule, RecommendationCompletion, UserProfile, RecommendationAdvice, RecommendationRedistribution
from app.services.progress_service import ProgressService

logger = logging.getLogger(__name__)

class SchedulingService:
    def __init__(self, db: Session):
        self.db = db
    
    def generate_user_schedule(self, uid: str, target_date: date) -> Dict[str, Any]:
        """
        사용자의 특정 날짜 스케줄을 생성합니다.
        frequency_detail과 duration_weeks에 따라 스케줄링하고 optimal_times에 따라 분류합니다.
        """
        try:
            # 1. 사용자의 활성 추천들을 가져옴
            active_recommendations = self._get_active_recommendations(uid, target_date)
            
            if not active_recommendations:
                return self._create_empty_schedule()
            
            # 2. 미완료 추천 재배치 처리
            self._handle_uncompleted_recommendations(uid, target_date, active_recommendations)
            
            # 3. 스케줄링 로직 적용
            scheduled_recommendations = self._schedule_recommendations(active_recommendations, target_date)
            
            # 4. 시간대별로 분류 (morning, afternoon, night, anytime)
            time_grouped_schedule = self._group_by_optimal_times(scheduled_recommendations)
            
            # 5. 최대 4개로 제한
            limited_schedule = self._limit_recommendations(time_grouped_schedule, max_total=4)
            
            # 6. 완료 상태 확인
            completed_recs = self._get_completed_recommendations(uid, target_date)
            
            # 7. 스케줄 저장 또는 업데이트
            self._save_or_update_schedule(uid, target_date, limited_schedule, completed_recs)
            
            # 8. 응답 형식으로 변환
            return self._format_schedule_response(limited_schedule, completed_recs, uid)
            
        except Exception as e:
            logger.error(f"스케줄 생성 실패: {str(e)}")
            return self._create_empty_schedule()
    
    def _get_active_recommendations(self, uid: str, target_date: date) -> List[RecommendationRecord]:
        """사용자의 활성 추천들을 가져옵니다."""
        # duration_weeks 내의 추천들만 가져옴
        start_date = target_date - timedelta(days=365)  # 최대 1년 전부터
        
        recommendations = self.db.query(RecommendationRecord).filter(
            and_(
                RecommendationRecord.uid == uid,
                RecommendationRecord.created_at >= start_date,
                RecommendationRecord.duration_weeks.isnot(None)
            )
        ).all()
        
        return recommendations
    
    def _schedule_recommendations(self, recommendations: List[RecommendationRecord], target_date: date) -> List[Dict[str, Any]]:
        """추천들을 스케줄링합니다."""
        scheduled = []
        
        for rec in recommendations:
            # frequency_detail 파싱
            frequency_info = self._parse_frequency_detail(rec.frequency_detail)
            if not frequency_info:
                continue
            
            # 해당 날짜에 실행해야 하는지 확인 (재배치 고려)
            if self._should_execute_today_with_redistribution(rec, frequency_info, target_date):
                scheduled.append({
                    'recommendation': rec,
                    'frequency_info': frequency_info
                })
        
        return scheduled
    
    def _parse_frequency_detail(self, frequency_detail: str) -> Optional[Dict[str, Any]]:
        """frequency_detail을 파싱합니다."""
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
    
    def _should_execute_today(self, rec: RecommendationRecord, frequency_info: Dict[str, Any], target_date: date) -> bool:
        """해당 날짜에 추천을 실행해야 하는지 확인합니다. 일정한 간격으로 분산됩니다."""
        created_date = rec.created_at.date()
        days_since_creation = (target_date - created_date).days
        
        if days_since_creation < 0:
            return False
        
        freq_type = frequency_info['type']
        times = frequency_info['times']
        
        if freq_type == 'daily':
            return True
        elif freq_type == 'weekly':
            # 주 단위: 7일을 times개 구간으로 나누어 고르게 분산
            if times >= 7:
                return True  # 매일 실행
            elif times == 1:
                # 주 1회: 주중 하루
                week_day = days_since_creation % 7
                return week_day == 3  # 수요일 (주중)
            else:
                # 주 2-6회: 일정한 간격으로 분산
                week_day = days_since_creation % 7
                interval = 7 // times
                return week_day % interval == 0 and week_day < times * interval
        elif freq_type == 'monthly':
            # 월 단위: 30일을 times개 구간으로 나누어 고르게 분산
            if times >= 30:
                return True  # 매일 실행
            elif times == 1:
                # 월 1회: 월중 하루
                month_day = days_since_creation % 30
                return month_day == 14  # 월중 (15일)
            else:
                # 월 2-29회: 일정한 간격으로 분산
                month_day = days_since_creation % 30
                interval = 30 // times
                return month_day % interval == 0 and month_day < times * interval
        
        return False
    
    def _group_by_optimal_times(self, scheduled_recommendations: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """optimal_times에 따라 추천들을 분류합니다."""
        time_groups = {
            'morning': [],
            'afternoon': [],
            'night': [],
            'anytime': []
        }
        
        for item in scheduled_recommendations:
            rec = item['recommendation']
            optimal_times = rec.optimal_times or []
            
            if not optimal_times:
                time_groups['anytime'].append(item)
            else:
                # 첫 번째 optimal_time을 사용
                optimal_time = optimal_times[0].lower()
                if optimal_time in ['morning', '아침']:
                    time_groups['morning'].append(item)
                elif optimal_time in ['afternoon', '오후']:
                    time_groups['afternoon'].append(item)
                elif optimal_time in ['night', '밤', 'evening']:
                    time_groups['night'].append(item)
                else:
                    time_groups['anytime'].append(item)
        
        return time_groups
    
    def _limit_recommendations(self, time_grouped_schedule: Dict[str, List[Dict[str, Any]]], max_total: int = 4) -> Dict[str, List[Dict[str, Any]]]:
        """추천 개수를 제한합니다."""
        # 각 시간대별로 우선순위에 따라 정렬
        for time_group in time_grouped_schedule.values():
            time_group.sort(key=lambda x: x['recommendation'].priority or 'medium', reverse=True)
        
        # 전체 추천 개수 계산
        total_count = sum(len(items) for items in time_grouped_schedule.values())
        
        if total_count <= max_total:
            return time_grouped_schedule
        
        # 제한이 필요한 경우, 우선순위가 높은 것부터 선택
        all_items = []
        for time_group, items in time_grouped_schedule.items():
            for item in items:
                all_items.append((time_group, item))
        
        # 우선순위로 정렬
        all_items.sort(key=lambda x: x[1]['recommendation'].priority or 'medium', reverse=True)
        
        # 상위 max_total개만 선택
        limited_items = all_items[:max_total]
        
        # 다시 시간대별로 분류
        result = {
            'morning': [],
            'afternoon': [],
            'night': [],
            'anytime': []
        }
        
        for time_group, item in limited_items:
            result[time_group].append(item)
        
        return result
    
    def _get_completed_recommendations(self, uid: str, target_date: date) -> List[int]:
        """해당 날짜에 완료된 추천 ID들을 가져옵니다."""
        completions = self.db.query(RecommendationCompletion).filter(
            and_(
                RecommendationCompletion.uid == uid,
                RecommendationCompletion.completion_date == target_date
            )
        ).all()
        
        return [comp.recommendation_id for comp in completions]
    
    def _save_or_update_schedule(self, uid: str, target_date: date, schedule: Dict[str, List[Dict[str, Any]]], completed_recs: List[int]):
        """스케줄을 저장하거나 업데이트합니다."""
        # 스케줄된 추천 ID들 추출
        scheduled_rec_ids = []
        for items in schedule.values():
            for item in items:
                scheduled_rec_ids.append(item['recommendation'].id)
        
        # 기존 스케줄 확인
        existing_schedule = self.db.query(UserSchedule).filter(
            and_(
                UserSchedule.uid == uid,
                UserSchedule.date == target_date
            )
        ).first()
        
        if existing_schedule:
            # 업데이트
            existing_schedule.scheduled_recommendations = {
                'morning': [item['recommendation'].id for item in schedule['morning']],
                'afternoon': [item['recommendation'].id for item in schedule['afternoon']],
                'night': [item['recommendation'].id for item in schedule['night']],
                'anytime': [item['recommendation'].id for item in schedule['anytime']]
            }
            existing_schedule.completed_recommendations = completed_recs
            existing_schedule.updated_at = datetime.utcnow()
        else:
            # 새로 생성
            new_schedule = UserSchedule(
                uid=uid,
                date=target_date,
                scheduled_recommendations={
                    'morning': [item['recommendation'].id for item in schedule['morning']],
                    'afternoon': [item['recommendation'].id for item in schedule['afternoon']],
                    'night': [item['recommendation'].id for item in schedule['night']],
                    'anytime': [item['recommendation'].id for item in schedule['anytime']]
                },
                completed_recommendations=completed_recs
            )
            self.db.add(new_schedule)
        
        self.db.commit()
    
    def _format_schedule_response(self, schedule: Dict[str, List[Dict[str, Any]]], completed_recs: List[int], uid: str) -> Dict[str, Any]:
        """스케줄 응답을 포맷합니다."""
        # 호르몬별 상세 통계 계산
        hormone_stats = self._calculate_detailed_hormone_stats(schedule, completed_recs)
        
        # 완료된 추천들을 별도 그룹으로 분리
        completed_group, reorganized_schedule = self._reorganize_schedule_with_completed_group(schedule, completed_recs)
        
        # 연속성 정보 추가
        progress_service = ProgressService(self.db)
        current_streak = progress_service._calculate_streak_days(uid, date.today())
        longest_streak = progress_service._get_longest_streak(uid)
        
        response = {
            'date': datetime.now().date().isoformat(),
            'completed': completed_group,
            'morning': self._format_time_group(reorganized_schedule['morning'], completed_recs),
            'afternoon': self._format_time_group(reorganized_schedule['afternoon'], completed_recs),
            'night': self._format_time_group(reorganized_schedule['night'], completed_recs),
            'anytime': self._format_time_group(reorganized_schedule['anytime'], completed_recs),
            'hormone_completion_stats': hormone_stats,
            'streak_info': {
                'current_streak': current_streak,
                'longest_streak': longest_streak
            }
        }
        
        return response
    
    def _format_time_group(self, items: List[Dict[str, Any]], completed_recs: List[int]) -> List[Dict[str, Any]]:
        """시간대별 추천들을 포맷합니다. 완료된 추천을 첫 번째로 표시합니다."""
        formatted_items = []
        
        # 완료된 추천과 미완료 추천을 분리
        completed_items = []
        incomplete_items = []
        
        for item in items:
            rec = item['recommendation']
            is_completed = rec.id in completed_recs
            
            formatted_item = {
                'id': rec.id,
                'title': rec.title,
                'purpose': rec.purpose or "",
                'conditions': rec.conditions or [],
                'symptoms': rec.symptoms or [],
                'hormones': rec.hormones or [],
                'is_completed': is_completed
            }
            
            # 카테고리별 특정 필드 추가
            if rec.category == 'food':
                formatted_item['food_amounts'] = rec.food_amounts or []
            elif rec.category == 'mindfulness':
                formatted_item['mindfulness_durations'] = rec.mindfulness_durations or []
            elif rec.category == 'movement':
                formatted_item['exercise_durations'] = rec.exercise_durations or []
            
            # 추천별 advice 정보 추가
            advice_info = self._get_recommendation_advice(rec.id)
            formatted_item['advice'] = advice_info
            
            if is_completed:
                completed_items.append(formatted_item)
            else:
                incomplete_items.append(formatted_item)
        
        # 완료된 추천을 먼저, 그 다음 미완료 추천을 추가
        formatted_items.extend(completed_items)
        formatted_items.extend(incomplete_items)
        
        return formatted_items
    
    def _reorganize_schedule_with_completed_group(self, schedule: Dict[str, List[Dict[str, Any]]], completed_recs: List[int]) -> tuple:
        """스케줄을 재구성하여 완료된 추천들을 별도 그룹으로 분리합니다."""
        completed_group = []
        reorganized_schedule = {
            'morning': [],
            'afternoon': [],
            'night': [],
            'anytime': []
        }
        
        # 시간대 순서 정의
        time_order = ['morning', 'afternoon', 'night', 'anytime']
        
        for time_group in time_order:
            items = schedule[time_group]
            
            for item in items:
                rec = item['recommendation']
                is_completed = rec.id in completed_recs
                
                if is_completed:
                    # 완료된 추천을 completed 그룹에 추가
                    completed_group.append(item)
                else:
                    # 미완료 추천을 원래 시간대에 추가
                    reorganized_schedule[time_group].append(item)
        
        return completed_group, reorganized_schedule
    
    def _calculate_detailed_hormone_stats(self, schedule: Dict[str, List[Dict[str, Any]]], completed_recs: List[int]) -> Dict[str, Any]:
        """호르몬별 상세 통계를 계산합니다 (progress bar용)."""
        # 모든 추천에서 호르몬 정보 수집
        all_hormones = {}
        completed_hormones = {}
        
        for time_group in schedule.values():
            for item in time_group:
                rec = item['recommendation']
                hormones = rec.hormones or []
                is_completed = rec.id in completed_recs
                
                for hormone in hormones:
                    # 전체 호르몬별 추천 수
                    all_hormones[hormone] = all_hormones.get(hormone, 0) + 1
                    
                    # 완료된 호르몬별 추천 수
                    if is_completed:
                        completed_hormones[hormone] = completed_hormones.get(hormone, 0) + 1
        
        # 통계 계산
        hormone_stats = {}
        total_recommendations = sum(all_hormones.values())
        total_completed = sum(completed_hormones.values())
        
        for hormone in all_hormones:
            total_count = all_hormones[hormone]
            completed_count = completed_hormones.get(hormone, 0)
            
            hormone_stats[hormone] = {
                'total_recommendations': total_count,
                'completed_recommendations': completed_count,
                'completion_rate': round((completed_count / total_count * 100), 1) if total_count > 0 else 0
            }
        
        # 전체 통계
        overall_stats = {
            'total_recommendations': total_recommendations,
            'completed_recommendations': total_completed,
            'completion_rate': round((total_completed / total_recommendations * 100), 1) if total_recommendations > 0 else 0
        }
        
        return {
            'by_hormone': hormone_stats,
            'overall': overall_stats
        }
    
    def _get_recommendation_advice(self, recommendation_id: int) -> List[Dict[str, Any]]:
        """추천별 advice 정보를 가져옵니다."""
        try:
            advices = self.db.query(RecommendationAdvice).filter(
                RecommendationAdvice.recommendation_id == recommendation_id
            ).all()
            
            advice_list = []
            for advice in advices:
                advice_list.append({
                    'type': advice.advice_type or "",
                    'category': advice.category or "",
                    'title': advice.title or "",
                    'description': advice.description or ""
                })
            
            return advice_list
            
        except Exception as e:
            logger.error(f"Advice 조회 실패: {str(e)}")
            return []
    
    def _calculate_hormone_completion_stats(self, uid: str, completed_recs: List[int]) -> Dict[str, int]:
        """호르몬별 완료 통계를 계산합니다."""
        if not completed_recs:
            return {}
        
        # 완료된 추천들의 호르몬 정보 가져오기
        completed_recommendations = self.db.query(RecommendationRecord).filter(
            RecommendationRecord.id.in_(completed_recs)
        ).all()
        
        hormone_stats = {}
        for rec in completed_recommendations:
            hormones = rec.hormones or []
            for hormone in hormones:
                hormone_stats[hormone] = hormone_stats.get(hormone, 0) + 1
        
        return hormone_stats
    
    def _create_empty_schedule(self) -> Dict[str, Any]:
        """빈 스케줄을 생성합니다."""
        return {
            'date': datetime.now().date().isoformat(),
            'completed': [],
            'morning': [],
            'afternoon': [],
            'night': [],
            'anytime': [],
            'hormone_completion_stats': {
                'by_hormone': {},
                'overall': {
                    'total_recommendations': 0,
                    'completed_recommendations': 0,
                    'completion_rate': 0
                }
            }
        }
    
    def mark_recommendation_completed(self, uid: str, recommendation_id: int, completion_date: date, notes: str = None) -> bool:
        """추천을 완료로 표시합니다."""
        try:
            # 이미 완료되었는지 확인
            existing_completion = self.db.query(RecommendationCompletion).filter(
                and_(
                    RecommendationCompletion.uid == uid,
                    RecommendationCompletion.recommendation_id == recommendation_id,
                    RecommendationCompletion.completion_date == completion_date
                )
            ).first()
            
            if existing_completion:
                return True  # 이미 완료됨
            
            # 새 완료 기록 생성
            completion = RecommendationCompletion(
                uid=uid,
                recommendation_id=recommendation_id,
                completion_date=completion_date,
                notes=notes
            )
            
            self.db.add(completion)
            self.db.commit()
            
            logger.info(f"추천 완료 표시: uid={uid}, rec_id={recommendation_id}, date={completion_date}")
            return True
            
        except Exception as e:
            logger.error(f"추천 완료 표시 실패: {str(e)}")
            self.db.rollback()
            return False
    
    def _handle_uncompleted_recommendations(self, uid: str, target_date: date, active_recommendations: List[RecommendationRecord]):
        """미완료된 추천들을 재배치합니다."""
        try:
            # 어제 날짜 계산
            yesterday = target_date - timedelta(days=1)
            
            # 어제 스케줄에서 미완료된 추천들 찾기
            yesterday_schedule = self.db.query(UserSchedule).filter(
                and_(
                    UserSchedule.uid == uid,
                    UserSchedule.date == yesterday
                )
            ).first()
            
            if not yesterday_schedule:
                return  # 어제 스케줄이 없으면 재배치할 필요 없음
            
            # 어제 완료된 추천들
            yesterday_completed = set(yesterday_schedule.completed_recommendations)
            
            # 어제 스케줄된 추천들 중 미완료된 것들
            yesterday_scheduled = set()
            for time_group in yesterday_schedule.scheduled_recommendations.values():
                yesterday_scheduled.update(time_group)
            
            uncompleted_recs = yesterday_scheduled - yesterday_completed
            
            if not uncompleted_recs:
                return  # 미완료된 추천이 없음
            
            # 각 미완료 추천에 대해 재배치 로직 적용
            for rec_id in uncompleted_recs:
                self._redistribute_uncompleted_recommendation(uid, rec_id, target_date)
                
        except Exception as e:
            logger.error(f"미완료 추천 재배치 실패: {str(e)}")
    
    def _redistribute_uncompleted_recommendation(self, uid: str, recommendation_id: int, target_date: date):
        """특정 미완료 추천을 재배치합니다."""
        try:
            # 추천 정보 가져오기
            recommendation = self.db.query(RecommendationRecord).filter(
                and_(
                    RecommendationRecord.id == recommendation_id,
                    RecommendationRecord.uid == uid
                )
            ).first()
            
            if not recommendation:
                return
            
            # frequency_detail 파싱
            frequency_info = self._parse_frequency_detail(recommendation.frequency_detail)
            if not frequency_info:
                return
            
            # daily 추천은 재배치하지 않음
            if frequency_info['type'] == 'daily':
                return
            
            # 남은 기간 계산
            remaining_days = self._calculate_remaining_days(recommendation, target_date)
            if remaining_days <= 0:
                return
            
            # 재배치된 날짜들 계산
            redistributed_dates = self._calculate_redistributed_dates(
                recommendation, frequency_info, target_date, remaining_days
            )
            
            # 재배치 정보 저장
            self._save_redistribution_info(uid, recommendation_id, redistributed_dates)
            
            logger.info(f"추천 재배치 완료: rec_id={recommendation_id}, dates={redistributed_dates}")
            
        except Exception as e:
            logger.error(f"추천 재배치 실패: {str(e)}")
    
    def _calculate_remaining_days(self, recommendation: RecommendationRecord, target_date: date) -> int:
        """추천의 남은 기간을 계산합니다."""
        try:
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
        """재배치된 날짜들을 계산합니다."""
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
    
    def _save_redistribution_info(self, uid: str, recommendation_id: int, redistributed_dates: List[date]):
        """재배치 정보를 저장합니다."""
        try:
            # 기존 재배치 정보가 있는지 확인
            existing_redistribution = self.db.query(RecommendationRedistribution).filter(
                and_(
                    RecommendationRedistribution.uid == uid,
                    RecommendationRedistribution.recommendation_id == recommendation_id
                )
            ).first()
            
            # 날짜를 ISO 형식 문자열로 변환
            redistributed_dates_str = [d.isoformat() for d in redistributed_dates]
            
            if existing_redistribution:
                # 기존 정보 업데이트
                existing_redistribution.redistributed_dates = redistributed_dates_str
                existing_redistribution.updated_at = datetime.utcnow()
            else:
                # 새 재배치 정보 생성
                redistribution = RecommendationRedistribution(
                    uid=uid,
                    recommendation_id=recommendation_id,
                    original_date=date.today() - timedelta(days=1),  # 어제 날짜
                    redistributed_dates=redistributed_dates_str,
                    redistribution_reason="uncompleted"
                )
                self.db.add(redistribution)
            
            self.db.commit()
            logger.info(f"재배치 정보 저장 완료: uid={uid}, rec_id={recommendation_id}, dates={redistributed_dates_str}")
            
        except Exception as e:
            logger.error(f"재배치 정보 저장 실패: {str(e)}")
            self.db.rollback()
    
    def _should_execute_today_with_redistribution(self, rec: RecommendationRecord, frequency_info: Dict[str, Any], 
                                                 target_date: date) -> bool:
        """재배치를 고려하여 해당 날짜에 추천을 실행해야 하는지 확인합니다."""
        try:
            # 기본 스케줄링 로직 확인
            if self._should_execute_today(rec, frequency_info, target_date):
                return True
            
            # 재배치된 날짜인지 확인
            redistribution = self.db.query(RecommendationRedistribution).filter(
                and_(
                    RecommendationRedistribution.uid == rec.uid,
                    RecommendationRedistribution.recommendation_id == rec.id
                )
            ).first()
            
            if redistribution and redistribution.redistributed_dates:
                # 재배치된 날짜들 중에 target_date가 있는지 확인
                target_date_str = target_date.isoformat()
                if target_date_str in redistribution.redistributed_dates:
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"재배치 고려 실행 확인 실패: {str(e)}")
            return False
