"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA CHATBOT - Pydantic Models
═══════════════════════════════════════════════════════════════════════════════
Request/Response models for the doctor-like AI chatbot.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime, date
from enum import Enum


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════

class InputMode(str, Enum):
    TAP = "tap"
    YAP = "yap"
    TYPE = "type"


class ConversationContext(str, Enum):
    CARE_PLAN_MODAL = "care_plan_modal"
    SYMPTOM_CHECKIN = "symptom_checkin"
    PERSONALISE = "personalise"
    KNOW_BODY = "know_body"
    GENERAL = "general"


class ResponseType(str, Enum):
    TEXT = "text"
    CHOICE_BUTTONS = "choice_buttons"
    SLIDER = "slider"
    CONFIRMATION = "confirmation"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ChatMessageRequest(BaseModel):
    """Request to send a chat message"""
    user_id: str = Field(..., description="User's ID")
    message: str = Field(..., description="User's message text")
    session_id: Optional[str] = Field(None, description="Existing session ID, or None to create new")
    input_mode: InputMode = Field(default=InputMode.TYPE, description="How user input the message")
    conversation_context: ConversationContext = Field(
        default=ConversationContext.GENERAL, 
        description="Conversation context for routing"
    )
    
    # For tap mode (choice buttons)
    selected_choice: Optional[str] = Field(None, description="Selected choice button text")
    
    # For slider input
    slider_value: Optional[int] = Field(None, ge=1, le=9, description="Slider value (1-9)")
    
    # Additional metadata
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class VoiceMessageRequest(BaseModel):
    """Request for voice messages with base64 encoded audio"""
    user_id: str = Field(..., description="User's ID")
    audio_base64: str = Field(..., description="Base64 encoded audio data")
    audio_format: str = Field(default="m4a", description="Audio format (m4a, mp3, wav, etc.)")
    language: str = Field(default="en", description="Language code")
    session_id: Optional[str] = None
    conversation_context: ConversationContext = ConversationContext.GENERAL


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class SliderConfig(BaseModel):
    """Configuration for slider UI"""
    min: int = 1
    max: int = 9
    step: int = 1
    labels: List[str] = ["None", "Mild", "Moderate", "Strong", "Extreme"]
    default_value: Optional[int] = None


class ChatAction(BaseModel):
    """Action for frontend to execute"""
    type: str  # navigate, refresh, show_modal, complete_assignment, skip_assignment
    target: Optional[str] = None  # Screen name or target ID
    params: Optional[Dict[str, Any]] = None  # Additional parameters


class ChatMessageResponse(BaseModel):
    """Response from chat message"""
    session_id: str
    message_id: str = Field(default="")  # Optional, generated if not provided
    
    # Response content
    response_type: ResponseType = ResponseType.TEXT
    content: str = Field(default="", description="Response text content")
    
    # Alias for content (some services use 'message')
    @property
    def message(self) -> str:
        return self.content
    
    # UI elements (optional based on response_type)
    choices: Optional[List[str]] = None
    slider_config: Optional[SliderConfig] = None
    
    # Actions for frontend
    actions: Optional[List[ChatAction]] = None
    
    # Voice-specific (if input was voice)
    transcription: Optional[str] = None
    transcription_confidence: Optional[int] = None
    
    # Metadata
    confidence: float = 1.0
    sources: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    timestamp: Optional[datetime] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "session_id": "abc123",
                "message_id": "msg456",
                "response_type": "choice_buttons",
                "content": "How does your action plan look today?",
                "choices": ["👍 It works for me", "👎 I want to change it"],
                "actions": [],
                "confidence": 0.95
            }
        }


class ChatSessionSummary(BaseModel):
    """Summary of a chat session"""
    session_id: str
    conversation_context: ConversationContext
    status: SessionStatus
    started_at: datetime
    last_message_at: datetime
    message_count: int
    preview: Optional[str] = None  # First/last message preview


class ChatSessionHistory(BaseModel):
    """Full session history"""
    session_id: str
    conversation_context: ConversationContext
    status: SessionStatus
    started_at: datetime
    messages: List[Dict[str, Any]]


# ═══════════════════════════════════════════════════════════════════════════════
# USER CONTEXT MODELS (for LangGraph state)
# ═══════════════════════════════════════════════════════════════════════════════

class PatientProfile(BaseModel):
    """Complete patient profile for chatbot context"""
    # Basic info
    user_id: str
    name: Optional[str] = None
    age: Optional[int] = None
    
    # Cycle info
    cycle_day: Optional[int] = None
    phase: Optional[str] = None
    phase_description: Optional[str] = None
    last_period_date: Optional[date] = None
    cycle_length: Optional[str] = None
    
    # Health concerns
    period_description: Optional[str] = None
    period_concerns: List[str] = []
    body_concerns: List[str] = []
    skin_hair_concerns: List[str] = []
    mental_health_concerns: List[str] = []
    other_concerns: List[str] = []
    top_concern: Optional[str] = None
    
    # Medical history
    diagnosed_conditions: List[str] = []
    family_history: List[str] = []
    birth_control: List[str] = []
    
    # Lifestyle
    workout_intensity: Optional[str] = None
    sleep_duration: Optional[str] = None
    stress_level: Optional[str] = None
    lifestyle_focus: List[str] = []  # ["eat", "move", "pause"]
    
    # Hormone analysis
    primary_hormone: Optional[str] = None
    secondary_hormones: List[str] = []


class TodaysPlan(BaseModel):
    """Today's action plan summary"""
    date: date
    total_assignments: int
    completed_assignments: int
    completion_rate: float
    
    # Assignments by time slot
    morning: List[Dict[str, Any]] = []
    afternoon: List[Dict[str, Any]] = []
    evening: List[Dict[str, Any]] = []
    anytime: List[Dict[str, Any]] = []
    
    # Hormone progress
    hormone_stats: Dict[str, Dict[str, int]] = {}


class RecentSummary(BaseModel):
    """Recent activity summary (last 7 days) for memory Layer 2"""
    symptoms_reported: List[Dict[str, Any]] = []
    common_factors: List[str] = []
    completions: Dict[str, Any] = {}
    concerns_mentioned: List[str] = []
    recent_skips: List[Dict[str, Any]] = []
    conversation_themes: List[str] = []


# ═══════════════════════════════════════════════════════════════════════════════
# SYMPTOM TRACKING MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class SymptomLogRequest(BaseModel):
    """Request to log a symptom"""
    symptom_type: str
    severity: int = Field(..., ge=1, le=9)
    notes: Optional[str] = None
    factors: List[str] = []


class SymptomLogResponse(BaseModel):
    """Response after logging symptom"""
    log_id: str
    symptom_type: str
    severity: int
    cycle_day: Optional[int]
    phase: Optional[str]
    
    # Analysis
    comparison_to_average: Optional[str] = None  # "higher", "lower", "similar"
    average_severity: Optional[float] = None
    trend: Optional[str] = None  # "improving", "worsening", "stable"


class SymptomHistory(BaseModel):
    """Symptom history for analysis"""
    symptom_type: str
    data_points: List[Dict[str, Any]]
    average: float
    trend: str
    phase_correlation: Optional[Dict[str, float]] = None


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ToolCall(BaseModel):
    """Record of a tool being called"""
    name: str
    input: Dict[str, Any]
    output: Any
    duration_ms: int
    success: bool
    error: Optional[str] = None


class AssignmentAction(BaseModel):
    """Action on an assignment"""
    assignment_id: int
    action: str  # complete, skip, swap, reschedule
    reason: Optional[str] = None
    new_time_slot: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# SAFETY MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class SafetyCheck(BaseModel):
    """Result of safety check"""
    is_safe: bool = True
    is_emergency: bool = False
    safety_type: str = "safe"  # safe, emergency, off_limits, needs_disclaimer
    topic: Optional[str] = None
    override_response: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATION MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class EvaluationScores(BaseModel):
    """Evaluation scores for a response"""
    faithfulness: Optional[float] = None
    relevancy: Optional[float] = None
    empathy: Optional[float] = None
    safety: Optional[float] = None
    overall: Optional[float] = None
