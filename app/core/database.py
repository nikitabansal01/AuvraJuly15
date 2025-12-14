from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ARRAY, Text, ForeignKey, Date, Index, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.pool import NullPool
from datetime import datetime, date
import uuid
import hashlib
import logging
from app.core.config import settings
from typing import List

logger = logging.getLogger(__name__)

# Database engine creation
# For Supabase Session Pooler, use NullPool (no local pooling)
# Supabase Session Pooler already manages connection pooling on their side
if settings.ENVIRONMENT == "production":
    database_url = settings.DATABASE_URL
    if "?" not in database_url:
        database_url += "?sslmode=require"
    
    # Use NullPool - Supabase Session Pooler handles all connection pooling
    # This prevents "MaxClientsInSessionMode" errors
    engine = create_engine(
        database_url,
        poolclass=NullPool,    # No local pooling - let Supabase handle it
        echo=False             # Disable SQL logging in production
    )
else:
    # Development environment - use minimal pooling
    engine = create_engine(
        settings.DATABASE_URL,
        pool_size=2,              # Minimal pool size
        max_overflow=3,           # Small overflow
        pool_pre_ping=True,       # Connection status check
        pool_recycle=1800,        # Recreate connections every 30 minutes
        pool_timeout=30,          # Connection wait time
        echo=False                # Disable SQL query logging
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Create database session"""
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
        # Disable connection pool status logging (performance improvement)
        pass

def generate_session_id():
    """Generate session ID"""
    return f"session_{uuid.uuid4().hex[:12]}"

# Model definitions
class QuestionSession(Base):
    __tablename__ = "question_sessions"
    
    session_id = Column(String(255), primary_key=True, index=True)
    device_id = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)  # Session expiration time (24 hours)
    status = Column(String(50), default="active")  # active, completed, expired
    
    # Temporary survey data (excluding personal identification information)
    age = Column(Integer, nullable=True)  # Store as number
    period_description = Column(String(100), nullable=True)
    birth_control = Column(ARRAY(String), nullable=True)
    last_period_date_utc = Column(DateTime, nullable=True)  # Last period start date (UTC)
    cycle_length = Column(String(50), nullable=True)
    period_concerns = Column(JSONB, nullable=True)
    body_concerns = Column(JSONB, nullable=True)
    skin_hair_concerns = Column(JSONB, nullable=True)
    mental_health_concerns = Column(JSONB, nullable=True)
    other_concerns = Column(JSONB, nullable=True)
    top_concern = Column(String(255), nullable=True)
    diagnosed_conditions = Column(ARRAY(String), nullable=True)
    family_history = Column(ARRAY(String), nullable=True)
    workout_intensity = Column(String(50), nullable=True)
    sleep_duration = Column(String(50), nullable=True)
    stress_level = Column(String(50), nullable=True)
    survey_timezone = Column(String(50), nullable=True, default="Asia/Seoul")  # Timezone at survey input time
    
    # Root cause analysis results
    primary_hormone = Column(String(50), nullable=True)  # Primary hormone imbalance (e.g., "progesterone")
    secondary_hormones = Column(ARRAY(String), nullable=True)  # Secondary hormone imbalances (e.g., ["testosterone"])

class SessionProcessingStatus(Base):
    """Track AI recommendation generation progress by session"""
    __tablename__ = "session_processing_status"
    
    session_id = Column(String(255), primary_key=True, index=True)
    processing_status = Column(String(50), default="queued")  # queued, in_progress, completed, failed, canceled, stalled
    phase = Column(String(100), nullable=True)  # Human-readable phase name
    progress = Column(Integer, default=0)  # 0-100
    message = Column(Text, nullable=True)  # Short status message for UI
    
    # Processing status by category
    food_status = Column(String(50), default="pending")  # pending, processing, completed, failed
    movement_status = Column(String(50), default="pending")
    mindfulness_status = Column(String(50), default="pending")
    
    # Request/result data
    request_payload = Column(JSONB, nullable=True)  # UserProfile data
    result = Column(JSONB, nullable=True)  # Generated recommendation summary
    error = Column(JSONB, nullable=True)  # Error information
    
    # Time information
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)  # Last update time
    
    # Retry information
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index('idx_processing_status', 'processing_status'),
        Index('idx_heartbeat_at', 'heartbeat_at'),
    )

class UserProfile(Base):
    __tablename__ = "user_profiles"
    
    uid = Column(String(255), primary_key=True, index=True)  # Firebase UID
    name = Column(String(255), nullable=True)  # Retrieved from Firebase Auth
    email = Column(String(255), nullable=True)  # Retrieved from Firebase Auth
    current_timezone = Column(String(50), default="Asia/Seoul")  # Current user timezone
    lifestyle_focus = Column(ARRAY(String), nullable=True)  # User's preferred focus areas: ["eat", "move", "pause"]
    chatbot_memory = Column(JSONB, nullable=True)  # Permanent memory for chatbot (preferences, facts)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserResponse(Base):
    __tablename__ = "user_responses"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), nullable=False, index=True)  # Firebase UID
    
    # Anonymized survey data (personal identification information removed)
    age = Column(Integer, nullable=True)  # Actual age (number)
    period_description = Column(String(100), nullable=True)
    birth_control = Column(ARRAY(String), nullable=True)
    last_period_date_utc = Column(DateTime, nullable=True)  # Last period start date (UTC)
    cycle_length = Column(String(50), nullable=True)
    period_concerns = Column(JSONB, nullable=True)
    body_concerns = Column(JSONB, nullable=True)
    skin_hair_concerns = Column(JSONB, nullable=True)
    mental_health_concerns = Column(JSONB, nullable=True)
    other_concerns = Column(JSONB, nullable=True)
    top_concern = Column(String(255), nullable=True)
    diagnosed_conditions = Column(ARRAY(String), nullable=True)
    family_history = Column(ARRAY(String), nullable=True)
    workout_intensity = Column(String(50), nullable=True)
    sleep_duration = Column(String(50), nullable=True)
    stress_level = Column(String(50), nullable=True)
    survey_timezone = Column(String(50), nullable=True)  # Timezone at survey input time (reference)
    
    # Root cause analysis results
    primary_hormone = Column(String(50), nullable=True)  # Primary hormone imbalance (e.g., "progesterone")
    secondary_hormones = Column(ARRAY(String), nullable=True)  # Secondary hormone imbalances (e.g., ["testosterone"])
    
    # Lifestyle preference from onboarding (Eat/Move/Pause)
    lifestyle_focus = Column(ARRAY(String), nullable=True)  # User's preferred focus areas: ["eat", "move", "pause"]
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class RecommendationRecord(Base):
    __tablename__ = "recommendation_records"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), nullable=True, index=True)  # Firebase UID (NULL for temporary sessions)
    session_id = Column(String(255), nullable=True, index=True)  # Session ID (for temporary storage)
    
    # Recommendation metadata
    recommendation_type = Column(String(50), nullable=False)  # "general" or "rag"
    category = Column(String(50), nullable=False)  # "food", "movement", "mindfulness"
    confidence = Column(Integer, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)
    
    # Recommendation card information
    title = Column(String(500), nullable=True)  # Concise method description (1-2 words)
    purpose = Column(String(500), nullable=True)  # Recommendation purpose/effect
    specific_action = Column(Text, nullable=True)
    priority = Column(String(50), nullable=True)
    contraindications = Column(JSONB, nullable=True)
    
    # Tag information
    conditions = Column(ARRAY(String), nullable=True)
    symptoms = Column(ARRAY(String), nullable=True)
    hormones = Column(ARRAY(String), nullable=True)
    
    # Array fields by category
    # Food related (arrays)
    food_amounts = Column(ARRAY(String), nullable=True)  # ["150g", "100g", "2 tablespoons"]
    food_items = Column(ARRAY(String), nullable=True)    # ["oats", "lentils", "flaxseed"]
    
    # Exercise related (arrays)
    exercise_durations = Column(ARRAY(String), nullable=True)  # ["30 minutes", "45 minutes"]
    exercise_types = Column(ARRAY(String), nullable=True)      # ["yoga", "walking"]
    exercise_intensities = Column(ARRAY(String), nullable=True) # ["moderate", "low"]
    
    # Mindfulness related (arrays)
    mindfulness_durations = Column(ARRAY(String), nullable=True)  # ["15 minutes", "20 minutes"]
    mindfulness_techniques = Column(ARRAY(String), nullable=True) # ["meditation", "deep breathing"]
    
    # Common fields
    frequency_detail = Column(String(100), nullable=True)  # Structured format (e.g., "daily:1", "weekly:3")
    duration_weeks = Column(Integer, nullable=True)  # Numbers only (e.g., 12, 8, 16)
    optimal_times = Column(ARRAY(String), nullable=True)  # ["morning", "afternoon", "night"]
    
    # Research basis
    research_summary = Column(Text, nullable=True)
    research_studies = Column(JSONB, nullable=True)
    
    # User profile snapshot
    user_profile_snapshot = Column(JSONB, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship settings
    schedules = relationship("RecommendationSchedule", back_populates="recommendation")

class RecommendationAdvice(Base):
    __tablename__ = "recommendation_advices"
    
    id = Column(Integer, primary_key=True, index=True)
    recommendation_id = Column(Integer, ForeignKey("recommendation_records.id", ondelete="CASCADE"), nullable=False)
    uid = Column(String(255), nullable=True, index=True)  # Firebase UID (NULL for temporary sessions)
    session_id = Column(String(255), nullable=True, index=True)  # Session ID (for temporary storage)
    
    # Advice information
    advice_type = Column(String(50), nullable=False)  # "easy", "tasty", "healthy" for food; "tip1", "tip2", "tip3" for movement/mindfulness
    category = Column(String(50), nullable=False)  # "food", "movement", "mindfulness"
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    recommendation_context = Column(JSONB, nullable=True)  # Recommendation context information
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserSchedule(Base):
    """User's daily schedule information"""
    __tablename__ = "user_schedules"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)  # Schedule date (YYYY-MM-DD)
    
    # Scheduled recommendations (stored as JSONB)
    scheduled_recommendations = Column(JSONB, nullable=False)  # {morning: [rec_ids], afternoon: [rec_ids], night: [rec_ids], anytime: [rec_ids]}
    
    # Completed recommendations
    completed_recommendations = Column(JSONB, nullable=False, default=list)  # [rec_ids]
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Composite index
    __table_args__ = (
        Index('idx_user_schedule_date', 'uid', 'date'),
    )

class RecommendationCompletion(Base):
    """Recommendation completion record"""
    __tablename__ = "recommendation_completions"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    recommendation_id = Column(Integer, ForeignKey("recommendation_records.id", ondelete="CASCADE"), nullable=False, index=True)
    completion_date = Column(Date, nullable=False, index=True)  # Completion date
    
    # Completion information
    completed_at = Column(DateTime, default=datetime.utcnow)  # Completion time
    notes = Column(Text, nullable=True)  # User notes
    
    # Composite indexes
    __table_args__ = (
        Index('idx_completion_user_date', 'uid', 'completion_date'),
        Index('idx_completion_rec_date', 'recommendation_id', 'completion_date'),
    )

class RecommendationRedistribution(Base):
    """Recommendation redistribution information"""
    __tablename__ = "recommendation_redistributions"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    recommendation_id = Column(Integer, ForeignKey("recommendation_records.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Redistribution information
    original_date = Column(Date, nullable=False, index=True)  # Originally scheduled date
    redistributed_dates = Column(JSONB, nullable=False)  # Redistributed dates [date1, date2, ...]
    redistribution_reason = Column(String(100), nullable=False, default="uncompleted")  # Redistribution reason
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Composite indexes
    __table_args__ = (
        Index('idx_redistribution_user_rec', 'uid', 'recommendation_id'),
        Index('idx_redistribution_date', 'original_date'),
    )

class RecommendationSchedule(Base):
    __tablename__ = "recommendation_schedules"
    
    id = Column(BigInteger, primary_key=True, index=True)
    uid = Column(String(255), nullable=False, index=True)  # Firebase UID
    recommendation_id = Column(BigInteger, ForeignKey("recommendation_records.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # UTC-based date/time
    start_date_utc = Column(DateTime, nullable=False)  # Start date (UTC)
    end_date_utc = Column(DateTime, nullable=True)     # End date (UTC)
    next_fire_at_utc = Column(DateTime, nullable=True) # Next execution time (UTC)
    
    # RRULE (Recurrence Rule)
    rrule = Column(String(500), nullable=False)  # RRULE string
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship settings
    recommendation = relationship("RecommendationRecord", back_populates="schedules")

class ScheduleRedistribution(Base):
    """Redistribution (exception/correction) - new scheduling system"""
    __tablename__ = "schedule_redistributions"
    
    id = Column(BigInteger, primary_key=True, index=True)
    schedule_id = Column(BigInteger, ForeignKey("recommendation_schedules.id", ondelete="CASCADE"), nullable=False, index=True)
    original_date = Column(Date, nullable=False)
    override_date = Column(Date, nullable=False)
    reason = Column(Text, nullable=True)
    source = Column(String(50), nullable=False, default="system")  # 'system' | 'user' | 'admin'
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Unique constraints
    __table_args__ = (
        Index('idx_redistribution_schedule', 'schedule_id'),
        Index('idx_redistribution_dates', 'original_date', 'override_date'),
    )

class DailyAssignment(Base):
    """Daily assignment/schedule - new scheduling system"""
    __tablename__ = "daily_assignments"
    
    id = Column(BigInteger, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    schedule_id = Column(BigInteger, ForeignKey("recommendation_schedules.id", ondelete="CASCADE"), nullable=False, index=True)
    recommendation_id = Column(Integer, ForeignKey("recommendation_records.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Daily schedule information
    assignment_date = Column(Date, nullable=False, index=True)  # User local date
    time_group = Column(String(50), nullable=False)  # morning, afternoon, night, anytime
    is_completed = Column(Boolean, default=False)
    
    # Completion information
    completed_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index('idx_assignment_user_date', 'uid', 'assignment_date'),
        Index('idx_assignment_schedule_date', 'schedule_id', 'assignment_date'),
    )

# ═══════════════════════════════════════════════════════════════════════════════
# CHATBOT MODELS - Doctor-like AI Assistant
# ═══════════════════════════════════════════════════════════════════════════════

class ChatSession(Base):
    """Chat conversation sessions"""
    __tablename__ = "chat_sessions"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    
    # Conversation context
    conversation_context = Column(String(50), nullable=False, default="general")  # care_plan_modal, symptom_checkin, personalise, know_body, general
    
    # State tracking
    status = Column(String(20), default="active")  # active, completed, archived
    current_step = Column(String(100), nullable=True)  # Track conversation flow position
    current_flow_data = Column(JSONB, default=dict)  # Store temporary flow state data
    
    # Timestamps
    started_at = Column(DateTime, default=datetime.utcnow)
    last_message_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    
    # Metadata (renamed from 'metadata' to avoid SQLAlchemy reserved name conflict)
    session_metadata = Column(JSONB, default=dict)
    
    # Indexes
    __table_args__ = (
        Index('idx_chat_sessions_user_status', 'user_id', 'status'),
        Index('idx_chat_sessions_last_msg', 'last_message_at'),
    )
    
    # Relationships
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    """Individual chat messages"""
    __tablename__ = "chat_messages"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Message content
    role = Column(String(20), nullable=False)  # user, assistant, system
    content = Column(Text, nullable=False)
    
    # Input metadata (for user messages)
    input_mode = Column(String(10), nullable=True)  # tap, yap, type
    selected_choice = Column(String(255), nullable=True)  # If tap mode, which button pressed
    slider_value = Column(Integer, nullable=True)  # If slider input (1-9)
    
    # Response metadata (for assistant messages)
    response_type = Column(String(20), nullable=True)  # text, choice_buttons, slider, confirmation
    choices = Column(JSONB, nullable=True)  # Array of choice options shown
    slider_config = Column(JSONB, nullable=True)  # {min, max, labels, step}
    actions = Column(JSONB, default=list)  # Frontend actions to trigger [{type, target, params}]
    
    # Tool execution
    tools_called = Column(JSONB, default=list)  # [{name, input, output, duration_ms}]
    
    # RAG context
    retrieval_context = Column(JSONB, nullable=True)  # Retrieved documents/papers used
    
    # Voice metadata
    audio_url = Column(String(500), nullable=True)
    transcription_confidence = Column(Integer, nullable=True)  # 0-100
    
    # LLM metadata
    model_used = Column(String(50), nullable=True)
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    
    # Evaluation scores (for quality monitoring)
    evaluation_scores = Column(JSONB, nullable=True)  # {faithfulness, relevancy, empathy, safety}
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index('idx_chat_messages_session_created', 'session_id', 'created_at'),
    )
    
    # Relationships
    session = relationship("ChatSession", back_populates="messages")


class SymptomLog(Base):
    """User symptom tracking logs (from chatbot interactions)"""
    __tablename__ = "symptom_logs"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    
    # Symptom data
    symptom_type = Column(String(50), nullable=False)  # bloating, pain, mood, energy, cramps, headache, etc.
    severity = Column(Integer, nullable=False)  # 1-9 scale
    notes = Column(Text, nullable=True)
    
    # Contextual factors reported with symptom
    factors = Column(JSONB, default=list)  # ["more_stress", "less_sleep", "ate_out", ...]
    
    # Cycle context (snapshot at time of logging)
    cycle_day = Column(Integer, nullable=True)
    phase = Column(String(30), nullable=True)  # Menses, Follicular, Ovulation, Luteal
    
    # Source tracking
    logged_via = Column(String(30), default="chatbot")  # chatbot, manual, weekly_checkin
    chat_message_id = Column(String(36), ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    
    # Timestamps
    logged_at = Column(DateTime, default=datetime.utcnow)
    logged_date = Column(Date, default=date.today)
    
    # Indexes
    __table_args__ = (
        Index('idx_symptom_logs_user_type_date', 'user_id', 'symptom_type', 'logged_date'),
        Index('idx_symptom_logs_user_date', 'user_id', 'logged_date'),
    )


class ConversationSummary(Base):
    """Summarized conversation insights for memory (Layer 2)"""
    __tablename__ = "conversation_summaries"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    
    # Summary period
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    summary_type = Column(String(20), nullable=False)  # weekly, monthly
    
    # Summary content
    summary_data = Column(JSONB, nullable=False)
    # Structure:
    # {
    #     "symptoms_reported": [{"type": "bloating", "avg_severity": 6.5, "count": 3, "trend": "improving"}],
    #     "common_factors": ["stress", "sleep"],
    #     "completions": {"total": 35, "rate": 0.82, "streak_days": 5},
    #     "concerns_mentioned": ["fatigue", "mood"],
    #     "preferences_changed": ["added dietary restriction"],
    #     "key_interactions": ["asked about progesterone", "skipped yoga 3x", "loved meditation"]
    # }
    
    # Timestamps
    generated_at = Column(DateTime, default=datetime.utcnow)
    
    # Unique constraint
    __table_args__ = (
        Index('idx_conv_summaries_user_period', 'user_id', 'summary_type', 'period_start', unique=True),
    )


class AssignmentSkipLog(Base):
    """Track when users skip assignments (for chatbot analytics)"""
    __tablename__ = "assignment_skip_logs"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    assignment_id = Column(BigInteger, ForeignKey("daily_assignments.id", ondelete="CASCADE"), nullable=False)
    recommendation_id = Column(Integer, ForeignKey("recommendation_records.id", ondelete="CASCADE"), nullable=False)
    
    # Skip details
    skip_reason = Column(String(100), nullable=True)  # no_time, not_feeling_well, dont_like, other
    reason_notes = Column(Text, nullable=True)
    
    # Alternative offered/taken
    alternative_offered = Column(Boolean, default=False)
    alternative_taken_id = Column(Integer, nullable=True)  # If they chose an alternative
    
    # Context
    cycle_day = Column(Integer, nullable=True)
    phase = Column(String(30), nullable=True)
    chat_session_id = Column(String(36), nullable=True)
    
    # Timestamps
    skipped_at = Column(DateTime, default=datetime.utcnow)
    skip_date = Column(Date, default=date.today)
    
    # Indexes
    __table_args__ = (
        Index('idx_skip_logs_user_date', 'user_id', 'skip_date'),
        Index('idx_skip_logs_recommendation', 'recommendation_id'),
    )


# Database table creation
def create_tables():
    """Create tables"""
    Base.metadata.create_all(bind=engine) 