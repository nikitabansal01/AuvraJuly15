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
    title: Optional[str]  # Concise method description (1-2 words)
    purpose: Optional[str]  # Recommendation purpose/effect
    specificAction: Optional[str]  # Keep existing field (compatibility)
    researchBacking: Optional[ResearchBacking] = None
    contraindications: Optional[List[Any]] = []
    frequency: Optional[str] = 'Daily'
    expectedTimeline: Optional[str] = '4-6 weeks'
    priority: Optional[str] = 'medium'
    # Tag fields added
    conditions: Optional[List[str]] = []  # Related diseases (e.g., ["PCOS", "endometriosis"])
    symptoms: Optional[List[str]] = []    # Related symptoms (e.g., ["irregular periods", "weight gain"])
    hormones: Optional[List[str]] = []    # Related hormones (e.g., ["insulin", "androgens"])
    
    # Array fields by category
    # Food related (arrays)
    food_amounts: Optional[List[str]] = []      # Exact amounts array (e.g., ["150g", "100g", "2 tablespoons"])
    food_items: Optional[List[str]] = []        # Food/nutrient array (e.g., ["oats", "lentils", "flaxseed"])
    
    # Exercise related (arrays)
    exercise_durations: Optional[List[str]] = []  # Exact time array (e.g., ["30 minutes", "45 minutes"])
    exercise_types: Optional[List[str]] = []      # Exercise type array (e.g., ["yoga", "walking", "strength training"])
    exercise_intensities: Optional[List[str]] = [] # Exercise intensity array (e.g., ["moderate", "low", "high"])
    
    # Mindfulness related (arrays)
    mindfulness_durations: Optional[List[str]] = []  # Exact time array (e.g., ["15 minutes", "20 minutes"])
    mindfulness_techniques: Optional[List[str]] = [] # Technique array (e.g., ["meditation", "deep breathing", "yoga"])
    
    # Common fields
    frequency_detail: Optional[str] = None  # Detailed frequency (e.g., "daily:1", "weekly:3", "monthly:1")
    duration_weeks: Optional[int] = None    # Duration (numbers only, e.g., 12, 8, 16)
    optimal_times: Optional[List[str]] = [] # Optimal time periods (e.g., ["morning", "afternoon", "night"])

class RecommendationResult(BaseModel):
    food: List[RecommendationCard] = []
    movement: List[RecommendationCard] = []
    mindfulness: List[RecommendationCard] = []
    userProfile: Optional[UserProfile] = None
    generatedAt: Optional[str] = None
    confidence: Optional[int] = None
    rawLLMResponse: Optional[str] = None 