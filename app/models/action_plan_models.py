"""
AUVRA Action Plan API Models

Pydantic models for the new action plan system.
"""

from datetime import date, datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ============================================================================
# REQUEST MODELS
# ============================================================================

class ActionPlanFeedbackRequest(BaseModel):
    """Request model for submitting feedback on an action."""
    item_id: int = Field(..., description="ID of the action item")
    feedback_type: str = Field(..., description="'like' or 'dislike'")
    time_shown: datetime = Field(..., description="When the action was first shown to user")
    
class ActionReplacementRequest(BaseModel):
    """Request model for replacing a disliked action."""
    item_id: int = Field(..., description="ID of the action item to replace")
    reason: Optional[str] = Field(None, description="Why the user disliked this action")

class ActionCompletionRequest(BaseModel):
    """Request model for marking an action as completed."""
    item_id: int = Field(..., description="ID of the action item")
    notes: Optional[str] = Field(None, description="Optional completion notes")
    variant_used: Optional[str] = Field(None, description="Which variant was used if any")


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class VariantInfo(BaseModel):
    """Information about an action variant."""
    variant_type: str
    title: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    
    class Config:
        from_attributes = True

class ResearchStudy(BaseModel):
    """Research study citation."""
    title: str
    journal: str
    year: int
    participants: Optional[int] = None
    finding: Optional[str] = None

class ActionItemInfo(BaseModel):
    """Full information about an action item."""
    id: int
    slot: int
    time_slot: str = Field(..., description="'morning', 'afternoon', or 'evening'")
    category: str = Field(..., description="'food', 'movement', or 'mindfulness'")
    title: str
    specific_action: str
    purpose: Optional[str] = None
    target_hormone: str
    hormone_persona_intro: Optional[str] = None
    hero_image_url: Optional[str] = None
    research_studies: List[Dict[str, Any]] = []
    is_completed: bool = False
    is_replaced: bool = False
    variants: List[VariantInfo] = []
    
    # Category-specific fields
    food_items: Optional[List[str]] = None
    food_amounts: Optional[List[str]] = None
    exercise_types: Optional[List[str]] = None
    exercise_durations: Optional[List[str]] = None
    exercise_intensities: Optional[List[str]] = None
    mindfulness_techniques: Optional[List[str]] = None
    mindfulness_durations: Optional[List[str]] = None
    
    class Config:
        from_attributes = True

class ActionPlanResponse(BaseModel):
    """Response model for today's action plan."""
    success: bool
    plan_id: Optional[int] = None
    plan_date: Optional[str] = None
    primary_hormone: Optional[str] = None
    secondary_hormones: Optional[List[str]] = None
    cycle_day: Optional[int] = None
    cycle_phase: Optional[str] = None
    actions: List[ActionItemInfo] = []
    generation_cost: Optional[str] = None
    generation_time_ms: Optional[int] = None
    
    # For compatibility with old system
    total_actions: int = 0
    completed_actions: int = 0
    
    # Error handling
    error: Optional[str] = None

class FeedbackResponse(BaseModel):
    """Response model for feedback submission."""
    success: bool
    feedback_id: Optional[int] = None
    time_to_feedback_seconds: Optional[int] = None
    can_replace: bool = False  # True if 30 seconds have passed
    error: Optional[str] = None

class ReplacementResponse(BaseModel):
    """Response model for action replacement."""
    success: bool
    original_id: Optional[int] = None
    replacement_id: Optional[int] = None
    replacement_action: Optional[ActionItemInfo] = None
    error: Optional[str] = None

class CompletionResponse(BaseModel):
    """Response model for action completion."""
    success: bool
    item_id: Optional[int] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


# ============================================================================
# LEGACY COMPATIBILITY MODELS
# ============================================================================

class LegacyAssignmentInfo(BaseModel):
    """
    Legacy assignment format for backward compatibility.
    Maps new action plan items to old assignment format.
    """
    id: int
    recommendation_id: int  # Same as item_id in new system
    title: str
    purpose: Optional[str] = None
    specific_action: Optional[str] = None
    category: str
    conditions: List[str] = []
    symptoms: List[str] = []
    hormones: List[str] = []  # Will contain [target_hormone]
    research_summary: Optional[str] = None
    research_studies: Optional[List[Dict[str, Any]]] = []
    is_completed: bool = False
    completed_at: Optional[str] = None
    advices: List[Dict[str, str]] = []
    
    # Hero image (new field)
    hero_image_url: Optional[str] = None
    hormone_persona_intro: Optional[str] = None
    variants: List[VariantInfo] = []
    
    # Category-specific
    food_amounts: Optional[List[str]] = None
    food_items: Optional[List[str]] = None
    exercise_durations: Optional[List[str]] = None
    exercise_types: Optional[List[str]] = None
    exercise_intensities: Optional[List[str]] = None
    mindfulness_durations: Optional[List[str]] = None
    mindfulness_techniques: Optional[List[str]] = None

class LegacyAssignmentResponse(BaseModel):
    """
    Legacy response format for backward compatibility.
    Maps new action plan to old assignment response format.
    """
    date: str
    assignments: Dict[str, List[LegacyAssignmentInfo]]  # morning/afternoon/evening/completed
    total_assignments: int
    completed_assignments: int
    completion_rate: float
    hormone_stats: Dict[str, Dict[str, int]]
    
    # New fields for enhanced functionality
    plan_id: Optional[int] = None
    primary_hormone: Optional[str] = None
    cycle_phase: Optional[str] = None
    generation_source: str = "action_plan"  # Indicates this is from new system
