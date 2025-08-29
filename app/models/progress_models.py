from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from datetime import date

class PeriodInfo(BaseModel):
    """Period information"""
    week_start: Optional[str] = None
    week_end: Optional[str] = None
    month_start: Optional[str] = None
    month_end: Optional[str] = None
    current_date: str

class OverallProgress(BaseModel):
    """Overall progress"""
    total_recommendations: int
    completed_recommendations: int
    completion_rate: float
    active_recommendations: Optional[int] = None
    total_completions: Optional[int] = None

class StreakInfo(BaseModel):
    """Streak information"""
    current_streak: int
    longest_streak: int

class DailyCompletion(BaseModel):
    """Daily completion statistics"""
    date: str
    completions: int

class WeeklyStats(BaseModel):
    """Weekly statistics"""
    week_start: str
    week_end: str
    completions: int

class RecommendationProgress(BaseModel):
    """Recommendation progress"""
    id: int
    title: str
    category: str
    frequency_detail: Optional[str] = None
    duration_weeks: Optional[int] = None

class ProgressInfo(BaseModel):
    """Progress information"""
    total_completions: int
    current_streak: int
    longest_streak: int

class RecentCompletion(BaseModel):
    """Recent completion record"""
    completion_date: str
    completed_at: Optional[str] = None
    notes: Optional[str] = None

class WeeklyProgressResponse(BaseModel):
    """Weekly progress response"""
    period: PeriodInfo
    overall: OverallProgress
    streak: StreakInfo
    daily_completions: Dict[str, DailyCompletion]
    hormone_stats: Dict[str, int]

class MonthlyProgressResponse(BaseModel):
    """Monthly progress response"""
    period: PeriodInfo
    overall: OverallProgress
    weekly_stats: List[WeeklyStats]
    hormone_stats: Dict[str, int]
    best_day: str

class RecommendationProgressResponse(BaseModel):
    """Recommendation progress response"""
    recommendation: RecommendationProgress
    progress: ProgressInfo
    recent_completions: List[RecentCompletion]

class OverallProgressResponse(BaseModel):
    """Overall progress response"""
    overall: OverallProgress
    hormone_stats: Dict[str, int]

