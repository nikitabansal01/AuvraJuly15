from datetime import date
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

class CompletionRequest(BaseModel):
    """Recommendation completion request model"""
    recommendation_id: int
    completion_date: Optional[date] = None  # Default: today
    notes: Optional[str] = None

class StreakInfo(BaseModel):
    """Streak information"""
    current_streak: int
    longest_streak: int

class ScheduleResponse(BaseModel):
    """Schedule response model"""
    date: str
    completed: List[Dict[str, Any]]
    morning: List[Dict[str, Any]]
    afternoon: List[Dict[str, Any]]
    night: List[Dict[str, Any]]
    anytime: List[Dict[str, Any]]
    hormone_completion_stats: Dict[str, Any]
    streak_info: StreakInfo

class ScheduleStats(BaseModel):
    """Schedule statistics model"""
    date: str
    total_recommendations: int
    completed_recommendations: int
    completion_rate: float
    hormone_completion_stats: Dict[str, Any]

# New scheduling system models
class AssignmentInfo(BaseModel):
    """Assignment information"""
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
    
    # Category-specific details
    food_amounts: Optional[List[str]] = None
    food_items: Optional[List[str]] = None
    exercise_durations: Optional[List[str]] = None
    exercise_types: Optional[List[str]] = None
    exercise_intensities: Optional[List[str]] = None
    mindfulness_durations: Optional[List[str]] = None
    mindfulness_techniques: Optional[List[str]] = None

class AssignmentResponse(BaseModel):
    """Assignment response model (new scheduling system)"""
    date: str
    assignments: Dict[str, List[AssignmentInfo]]  # completed + time_group assignments
    total_assignments: int
    completed_assignments: int
    completion_rate: float
    hormone_stats: Dict[str, Dict[str, int]]

class AssignmentCompletionRequest(BaseModel):
    """Assignment completion request model"""
    notes: Optional[str] = None
