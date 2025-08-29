from datetime import date
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

class CompletionRequest(BaseModel):
    """추천 완료 요청 모델"""
    recommendation_id: int
    completion_date: Optional[date] = None  # 기본값: 오늘
    notes: Optional[str] = None

class StreakInfo(BaseModel):
    """연속성 정보"""
    current_streak: int
    longest_streak: int

class ScheduleResponse(BaseModel):
    """스케줄 응답 모델"""
    date: str
    completed: List[Dict[str, Any]]
    morning: List[Dict[str, Any]]
    afternoon: List[Dict[str, Any]]
    night: List[Dict[str, Any]]
    anytime: List[Dict[str, Any]]
    hormone_completion_stats: Dict[str, Any]
    streak_info: StreakInfo

class ScheduleStats(BaseModel):
    """스케줄 통계 모델"""
    date: str
    total_recommendations: int
    completed_recommendations: int
    completion_rate: float
    hormone_completion_stats: Dict[str, Any]

# 새로운 스케줄링 시스템 모델들
class AssignmentInfo(BaseModel):
    """과제 정보"""
    id: int
    recommendation_id: int
    title: str
    purpose: Optional[str] = None
    specific_action: Optional[str] = None
    category: str
    conditions: List[str] = []
    symptoms: List[str] = []
    hormones: List[str] = []
    research_summary: Optional[str] = None
    research_studies: Optional[List[Dict[str, Any]]] = []
    is_completed: bool
    completed_at: Optional[str] = None
    advices: List[Dict[str, str]] = []
    
    # 카테고리별 세부 정보
    food_amounts: Optional[List[str]] = None
    food_items: Optional[List[str]] = None
    exercise_durations: Optional[List[str]] = None
    exercise_types: Optional[List[str]] = None
    exercise_intensities: Optional[List[str]] = None
    mindfulness_durations: Optional[List[str]] = None
    mindfulness_techniques: Optional[List[str]] = None

class AssignmentResponse(BaseModel):
    """과제 응답 모델 (새로운 스케줄링 시스템)"""
    date: str
    assignments: Dict[str, List[AssignmentInfo]]  # completed + time_group별 과제들
    total_assignments: int
    completed_assignments: int
    completion_rate: float
    hormone_stats: Dict[str, Dict[str, int]]

class AssignmentCompletionRequest(BaseModel):
    """과제 완료 요청 모델"""
    notes: Optional[str] = None
