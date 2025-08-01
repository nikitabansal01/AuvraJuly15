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
    title: Optional[str]
    specificAction: Optional[str]
    researchBacking: Optional[ResearchBacking] = None
    contraindications: Optional[List[Any]] = []
    frequency: Optional[str] = 'Daily'
    expectedTimeline: Optional[str] = '4-6 weeks'
    priority: Optional[str] = 'medium'

class RecommendationResult(BaseModel):
    food: List[RecommendationCard] = []
    movement: List[RecommendationCard] = []
    mindfulness: List[RecommendationCard] = []
    userProfile: Optional[UserProfile] = None
    generatedAt: Optional[str] = None
    confidence: Optional[int] = None
    rawLLMResponse: Optional[str] = None 