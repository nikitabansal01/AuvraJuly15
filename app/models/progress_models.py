from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from datetime import date

class PeriodInfo(BaseModel):
    """기간 정보"""
    week_start: Optional[str] = None
    week_end: Optional[str] = None
    month_start: Optional[str] = None
    month_end: Optional[str] = None
    current_date: str

class OverallProgress(BaseModel):
    """전체 진행상황"""
    total_recommendations: int
    completed_recommendations: int
    completion_rate: float
    active_recommendations: Optional[int] = None
    total_completions: Optional[int] = None

class StreakInfo(BaseModel):
    """연속성 정보"""
    current_streak: int
    longest_streak: int

class DailyCompletion(BaseModel):
    """일일 완료 통계"""
    date: str
    completions: int

class WeeklyStats(BaseModel):
    """주별 통계"""
    week_start: str
    week_end: str
    completions: int

class RecommendationProgress(BaseModel):
    """추천 진행상황"""
    id: int
    title: str
    category: str
    frequency_detail: Optional[str] = None
    duration_weeks: Optional[int] = None

class ProgressInfo(BaseModel):
    """진행 정보"""
    total_completions: int
    current_streak: int
    longest_streak: int

class RecentCompletion(BaseModel):
    """최근 완료 기록"""
    completion_date: str
    completed_at: Optional[str] = None
    notes: Optional[str] = None

class WeeklyProgressResponse(BaseModel):
    """주간 진행상황 응답"""
    period: PeriodInfo
    overall: OverallProgress
    streak: StreakInfo
    daily_completions: Dict[str, DailyCompletion]
    hormone_stats: Dict[str, int]

class MonthlyProgressResponse(BaseModel):
    """월간 진행상황 응답"""
    period: PeriodInfo
    overall: OverallProgress
    weekly_stats: List[WeeklyStats]
    hormone_stats: Dict[str, int]
    best_day: str

class RecommendationProgressResponse(BaseModel):
    """추천 진행상황 응답"""
    recommendation: RecommendationProgress
    progress: ProgressInfo
    recent_completions: List[RecentCompletion]

class OverallProgressResponse(BaseModel):
    """전체 진행상황 응답"""
    overall: OverallProgress
    hormone_stats: Dict[str, int]

