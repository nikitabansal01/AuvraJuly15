from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class HormoneScores(BaseModel):
    androgens: float = 0
    progesterone: float = 0
    estrogen: float = 0
    thyroid: float = 0
    cortisol: float = 0
    insulin: float = 0

class UserProfile(BaseModel):
    hormoneScores: Optional[HormoneScores] = None
    primaryImbalance: Optional[str] = ''
    secondaryImbalances: Optional[List[str]] = []
    conditions: Optional[List[str]] = []
    symptoms: Optional[List[str]] = []
    cyclePhase: Optional[str] = 'unknown'
    birthControlStatus: Optional[str] = 'No'
    age: Optional[int] = None
    ethnicity: Optional[str] = None
    cravings: Optional[List[str]] = []
    confidence: Optional[str] = 'low'

class ResearchBacking(BaseModel):
    summary: str
    studies: Optional[List[Dict[str, Any]]] = []

class RecommendationCard(BaseModel):
    title: Optional[str]  # 간결한 방법 설명 (1-2단어)
    purpose: Optional[str]  # 추천 목적/효과
    specificAction: Optional[str]  # 기존 필드 유지 (호환성)
    researchBacking: Optional[ResearchBacking] = None
    contraindications: Optional[List[Any]] = []
    frequency: Optional[str] = 'Daily'
    expectedTimeline: Optional[str] = '4-6 weeks'
    priority: Optional[str] = 'medium'
    # 태그 필드들 추가
    conditions: Optional[List[str]] = []  # 관련 질병 (예: ["PCOS", "endometriosis"])
    symptoms: Optional[List[str]] = []    # 관련 증상 (예: ["irregular periods", "weight gain"])
    hormones: Optional[List[str]] = []    # 관련 호르몬 (예: ["insulin", "androgens"])
    
    # 카테고리별 배열 필드들
    # 음식 관련 (배열)
    food_amounts: Optional[List[str]] = []      # 정확한 양 배열 (예: ["150g", "100g", "2 tablespoons"])
    food_items: Optional[List[str]] = []        # 음식/영양소 배열 (예: ["oats", "lentils", "flaxseed"])
    
    # 운동 관련 (배열)
    exercise_durations: Optional[List[str]] = []  # 정확한 시간 배열 (예: ["30 minutes", "45 minutes"])
    exercise_types: Optional[List[str]] = []      # 운동 종류 배열 (예: ["yoga", "walking", "strength training"])
    exercise_intensities: Optional[List[str]] = [] # 운동 강도 배열 (예: ["moderate", "low", "high"])
    
    # 마음챙김 관련 (배열)
    mindfulness_durations: Optional[List[str]] = []  # 정확한 시간 배열 (예: ["15 minutes", "20 minutes"])
    mindfulness_techniques: Optional[List[str]] = [] # 기법 배열 (예: ["meditation", "deep breathing", "yoga"])
    
    # 공통 필드
    frequency_detail: Optional[str] = None  # 상세 빈도 (예: "daily:1", "weekly:3", "monthly:1")
    duration_weeks: Optional[int] = None    # 기간 (숫자만, 예: 12, 8, 16)
    optimal_times: Optional[List[str]] = [] # 최적 시간대 (예: ["morning", "afternoon", "night"])

class RecommendationResult(BaseModel):
    food: List[RecommendationCard] = []
    movement: List[RecommendationCard] = []
    mindfulness: List[RecommendationCard] = []
    userProfile: Optional[UserProfile] = None
    generatedAt: Optional[str] = None
    confidence: Optional[int] = None
    rawLLMResponse: Optional[str] = None 