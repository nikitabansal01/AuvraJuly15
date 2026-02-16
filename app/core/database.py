from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ARRAY, Text, ForeignKey, Date, Index, BigInteger, text
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

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

# Database engine creation
# For Supabase Session Pooler, use NullPool (no local pooling)
# Supabase Session Pooler already manages connection pooling on their side
if settings.ENVIRONMENT == "production":
    database_url = settings.DATABASE_URL
    if "?" not in database_url:
        database_url += "?sslmode=require"
    
    # Sync Engine
    # Use NullPool - Supabase Session Pooler handles all connection pooling
    # This prevents "MaxClientsInSessionMode" errors
    engine = create_engine(
        database_url,
        poolclass=NullPool,    # No local pooling - let Supabase handle it
        echo=False             # Disable SQL logging in production
    )

    # Async Engine
    # asyncpg uses 'ssl' parameter, not 'sslmode' - convert for compatibility
    async_database_url = database_url.replace("postgresql://", "postgresql+asyncpg://")
    async_database_url = async_database_url.replace("sslmode=", "ssl=")
    async_engine = create_async_engine(
        async_database_url,
        poolclass=NullPool,
        echo=False
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

    # Async Engine
    async_database_url = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
    async_engine = create_async_engine(
        async_database_url,
        pool_size=2,
        max_overflow=3,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=30,
        echo=False
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
AsyncSessionLocal = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

def get_async_session_maker():
    return AsyncSessionLocal

Base = declarative_base()

def get_db():
    """Create database session (for FastAPI dependency injection)"""
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


# ═══════════════════════════════════════════════════════════════════════════
# PRODUCTION FIX: Context managers for manual session creation (non-FastAPI)
# Usage: Used in LangGraph nodes which can't use FastAPI Depends()
# ═══════════════════════════════════════════════════════════════════════════
from contextlib import contextmanager, asynccontextmanager

@contextmanager
def get_db_session():
    """
    Synchronous context manager for database sessions.
    
    Usage:
        with get_db_session() as db:
            # ...use db...
            pass
        # db is automatically closed
    
    This prevents connection leaks from `db = next(get_db())` pattern.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()  # Auto-commit on success
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@asynccontextmanager
async def get_async_db_session():
    """
    Asynchronous context manager for database sessions.
    
    Usage:
        async with get_async_db_session() as db:
            # ...use db...
            pass
        # db is automatically closed
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
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
    
    # Lifestyle Focus: Eat/Move/Pause preference (personalization)
    lifestyle_focus = Column(ARRAY(String), nullable=True)  # ["eat", "move", "pause"]
    
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
    # NOTE: keep nullable=True for backwards compatibility with existing rows,
    # but default new rows to an empty dict so reads never see NULL unless legacy data.
    chatbot_memory = Column(JSONB, nullable=True, default=dict)  # Permanent memory for chatbot (preferences, facts)
    
    # Feedback summarization fields
    feedback_summary = Column(Text, nullable=True)  # GPT-generated summary of historical feedback
    feedback_summary_updated_at = Column(DateTime, nullable=True)  # When summary was last updated
    feedback_last_count = Column(Integer, default=0, nullable=False)  # Feedback count at last summarization
    
    # Weekly Check-in scheduling
    weekly_checkin_due_date = Column(Date, nullable=True)  # When next check-in is due
    last_weekly_checkin_id = Column(String(36), nullable=True)  # Most recent completed check-in
    
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
    # NOTE: Several services order/filter by created_at; keep it for compatibility
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    
    # Metadata (DB column name is "metadata"; attribute name avoids SQLAlchemy reserved name conflict)
    session_metadata = Column("metadata", JSONB, default=dict)

    # Optional session-level summary text
    summary = Column(Text, nullable=True)
    
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

    # Free-form metadata for messages (DB column exists in migrations)
    message_metadata = Column(JSONB, nullable=True)
    
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
    
    # Weekly Check-in link (if symptom was logged during a check-in)
    weekly_checkin_id = Column(String(36), ForeignKey("weekly_checkins.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Timestamps
    logged_at = Column(DateTime, default=datetime.utcnow)
    logged_date = Column(Date, default=date.today)
    
    # Indexes
    __table_args__ = (
        Index('idx_symptom_logs_user_type_date', 'user_id', 'symptom_type', 'logged_date'),
        Index('idx_symptom_logs_user_date', 'user_id', 'logged_date'),
        Index('idx_symptom_logs_checkin', 'weekly_checkin_id'),
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


# ═══════════════════════════════════════════════════════════════════════════════
# ACTION PLAN SYSTEM - Daily Personalized Recommendations
# ═══════════════════════════════════════════════════════════════════════════════

class ActionPlan(Base):
    """Daily action plan for a user - generated once per day"""
    __tablename__ = "action_plans"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=True, index=True)
    session_id = Column(String(255), nullable=True, index=True)  # NEW: For guest users
    
    # Plan date (user's local date when plan was generated)
    plan_date = Column(Date, nullable=False, index=True)
    
    # User context snapshot at generation time
    primary_hormone = Column(String(50), nullable=True)  # e.g., "progesterone"
    secondary_hormones = Column(ARRAY(String), nullable=True)  # e.g., ["testosterone", "insulin"]
    cycle_day = Column(Integer, nullable=True)
    cycle_phase = Column(String(50), nullable=True)  # Menses, Follicular, Ovulation, Luteal
    lifestyle_focus = Column(ARRAY(String), nullable=True)  # ["eat", "move", "pause"]
    
    # Generation metadata
    generation_cost = Column(String(50), nullable=True)  # Cost tracking
    generation_time_ms = Column(Integer, nullable=True)
    gpt_model_used = Column(String(50), default="gpt-4o-mini")
    
    # Status
    is_regenerated = Column(Boolean, default=False)  # If user requested regeneration
    feedback_collected = Column(Boolean, default=False)  # If 30-second feedback was given
    review_completed = Column(Boolean, default=False)  # If next-day review has been done
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # CRITICAL: Unique constraint to ensure ONE plan per user per day
    # This prevents duplicate plan generation from race conditions
    __table_args__ = (
        Index('idx_action_plan_user_date', 'uid', 'plan_date'),
        Index('idx_action_plan_session_date', 'session_id', 'plan_date'),
        # UNIQUE constraint - allows only one plan per user per day
        # Note: Uses partial index since uid can be NULL for guest plans
        Index('uq_action_plan_user_date', 'uid', 'plan_date', unique=True, postgresql_where=text('uid IS NOT NULL')),
        Index('uq_action_plan_session_date', 'session_id', 'plan_date', unique=True, postgresql_where=text('session_id IS NOT NULL')),
    )
    
    # Relationships
    items = relationship("ActionPlanItem", back_populates="plan", cascade="all, delete-orphan")


class ActionPlanItem(Base):
    """Individual action item within a daily plan"""
    __tablename__ = "action_plan_items"
    
    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    uid = Column(String(255), nullable=True, index=True)  # Denormalized for easy queries
    session_id = Column(String(255), nullable=True, index=True)  # NEW: For guest users
    
    # Item position and timing
    slot = Column(Integer, nullable=False)  # 1, 2, 3, 4
    time_slot = Column(String(20), nullable=False)  # morning, afternoon, evening, anytime
    
    # Core content
    category = Column(String(20), nullable=False)  # food, movement, mindfulness
    title = Column(String(255), nullable=False)  # e.g., "Pumpkin Seeds"
    specific_action = Column(Text, nullable=False)  # e.g., "Eat at least 1 tbsp of Pumpkin Seeds"
    purpose = Column(Text, nullable=True)  # Why this helps (hormone connection)
    
    # Hormone targeting (ONE hormone per action)
    target_hormone = Column(String(50), nullable=False)  # e.g., "progesterone"
    hormone_persona_intro = Column(Text, nullable=True)  # "Hi, I'm Progesterone! I help you..."
    
    # Category-specific details
    food_amounts = Column(ARRAY(String), nullable=True)  # ["1 tbsp", "2 tablespoons"]
    food_items = Column(ARRAY(String), nullable=True)  # ["pumpkin seeds", "flaxseeds"]
    exercise_durations = Column(ARRAY(String), nullable=True)  # ["15 min", "20 minutes"]
    exercise_types = Column(ARRAY(String), nullable=True)  # ["yoga", "walking"]
    exercise_intensities = Column(ARRAY(String), nullable=True)  # ["low", "moderate"]
    mindfulness_durations = Column(ARRAY(String), nullable=True)  # ["5 min", "10 minutes"]
    mindfulness_techniques = Column(ARRAY(String), nullable=True)  # ["deep breathing", "meditation"]
    
    # Tagging
    conditions = Column(ARRAY(String), nullable=True)  # ["PCOS", "endometriosis"]
    symptoms = Column(ARRAY(String), nullable=True)  # ["acne", "fatigue"]
    
    # Images (hero + 3 variants = 4 images per action)
    hero_image_url = Column(Text, nullable=True)  # Can be URL or base64 data URL
    hero_image_prompt = Column(Text, nullable=True)
    
    # Research/Citations
    research_studies = Column(JSONB, nullable=True)
    # Structure: [{"title": "...", "journal": "...", "year": 2023, "participants": "150 women", "results": "..."}]
    
    # Status
    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    is_replaced = Column(Boolean, default=False)  # If user replaced this action
    replaced_at = Column(DateTime, nullable=True)
    replacement_reason = Column(Text, nullable=True)
    
    # Carry forward tracking
    carried_forward_from = Column(Integer, nullable=True)  # Source item ID if carried from previous day
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index('idx_action_item_plan', 'plan_id'),
        Index('idx_action_item_user_date', 'uid', 'created_at'),
    )
    
    # Relationships
    plan = relationship("ActionPlan", back_populates="items")
    variants = relationship("ActionPlanItemVariant", back_populates="item", cascade="all, delete-orphan")


class ActionPlanItemVariant(Base):
    """Variant ways to do an action (Easy/Tasty/Healthy for food, etc.)"""
    __tablename__ = "action_plan_item_variants"
    
    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("action_plan_items.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Variant type
    variant_type = Column(String(20), nullable=False)
    # Food: "tasty", "healthiest", "easy"
    # Movement: "quick", "effective", "gentle"
    # Mindfulness: "calming", "energizing", "grounding"
    
    # Content
    title = Column(String(255), nullable=False)  # e.g., "Roasted Pumpkin Seeds"
    description = Column(Text, nullable=True)  # How to do this variant
    
    # Image
    image_url = Column(Text, nullable=True)  # Can be URL or base64 data URL
    image_prompt = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    item = relationship("ActionPlanItem", back_populates="variants")


class ActionPlanFeedback(Base):
    """User feedback on action plan items - stored for GPT context"""
    __tablename__ = "action_plan_feedback"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Integer, ForeignKey("action_plan_items.id", ondelete="CASCADE"), nullable=True)
    
    # Feedback type
    feedback_type = Column(String(20), nullable=False)  # "liked", "disliked", "completed", "skipped", "loved", "not_for_me"
    
    # Details
    action_title = Column(String(255), nullable=True)  # Denormalized for easy memory lookup
    action_category = Column(String(20), nullable=True)  # food, movement, mindfulness
    target_hormone = Column(String(50), nullable=True)  # For context
    
    # NEW: Text feedback from user (ActionDetailScreen)
    feedback_text = Column(Text, nullable=True)  # User's written feedback
    
    # Replacement info (if disliked and replaced)
    replacement_reason = Column(Text, nullable=True)
    replacement_category = Column(String(50), nullable=True)  # NEW: "allergic", "no_time", "dont_like", etc.
    was_replaced = Column(Boolean, default=False)
    
    # NEW: Feedback source to distinguish home vs detail screen
    feedback_source = Column(String(20), default="home")  # "home" (30-sec modal) or "detail" (ActionDetailScreen)
    
    # Context at time of feedback
    cycle_day = Column(Integer, nullable=True)
    cycle_phase = Column(String(50), nullable=True)
    
    # Time tracking (for 30-second rule)
    action_shown_at = Column(DateTime, nullable=True)
    feedback_given_at = Column(DateTime, default=datetime.utcnow)
    time_to_feedback_seconds = Column(Integer, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index('idx_feedback_user', 'uid', 'created_at'),
        Index('idx_feedback_type', 'feedback_type'),
        Index('idx_feedback_source', 'feedback_source'),  # NEW: Index for source filtering
    )


class ActionPlanDailyReview(Base):
    """
    Daily review of previous day's action plan.
    Stores user's retroactive status updates for each incomplete item.
    """
    __tablename__ = "action_plan_daily_reviews"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Review date (when the review was submitted, usually day after plan_date)
    review_date = Column(Date, nullable=False, index=True)
    review_completed_at = Column(DateTime, nullable=True)
    
    # Per-item review data stored as JSONB
    # Structure: [{"item_id": 1, "status": "forgot_to_mark"|"replaced"|"skipped"|"was_completed", 
    #              "replacement_text": "...", "replacement_category": "..."}]
    items_review_data = Column(JSONB, nullable=False, default=[])
    
    # Streak action taken
    streak_action = Column(String(20), nullable=True)  # "maintained", "used_freeze", "broken"
    freezes_used_count = Column(Integer, default=0)
    
    # Items that were carried forward to today's plan
    items_carried_forward = Column(JSONB, default=[])  # Array of item_ids
    
    # Summary counts for quick queries
    items_marked_complete = Column(Integer, default=0)  # forgot_to_mark + was_completed
    items_replaced = Column(Integer, default=0)
    items_skipped = Column(Integer, default=0)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index('idx_daily_review_user_date', 'uid', 'review_date'),
        Index('idx_daily_review_plan', 'plan_id'),
    )


class ImageLibrary(Base):
    """
    Semantic image library for reuse across users.
    Stores generated images with their prompts and embeddings for matching.
    """
    __tablename__ = "image_library"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Image identification
    image_url = Column(Text, nullable=False)  # URL or base64 data URL
    
    # Generation info
    prompt_text = Column(Text, nullable=False)  # The prompt used to generate
    prompt_embedding = Column(JSONB, nullable=True)  # 1536-dim embedding as JSON array (for pgvector migration later)
    
    # Categorization
    category = Column(String(20), nullable=False)  # food, movement, mindfulness
    variant_type = Column(String(20), nullable=True)  # hero, tasty, easy, healthy, etc.
    
    # Generation metadata
    generation_model = Column(String(50), default="flux-schnell")
    generation_cost = Column(String(50), nullable=True)
    generation_time_ms = Column(Integer, nullable=True)
    image_width = Column(Integer, default=512)
    image_height = Column(Integer, default=512)
    
    # Usage tracking
    usage_count = Column(Integer, default=1)
    last_used_at = Column(DateTime, default=datetime.utcnow)
    
    # Avoid showing same image to same user
    used_by_users = Column(JSONB, default=list)  # List of user IDs who have seen this image
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index('idx_image_library_category', 'category', 'variant_type'),
        Index('idx_image_library_usage', 'usage_count'),
    )


class PubMedCache(Base):
    """
    Cache for PubMed research citations.
    Stores real papers to avoid hitting API rate limits.
    """
    __tablename__ = "pubmed_cache"
    
    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String(32), unique=True, nullable=False, index=True)  # Keep for backward compatibility
    
    # Direct matching columns (easier to debug than MD5)
    query_normalized = Column(Text, nullable=True)  # The search query used
    category = Column(String(50), nullable=True)  # food/movement/mindfulness
    hormone = Column(String(50), nullable=True)  # Target hormone
    
    # Paper details
    pubmed_id = Column(String(20), nullable=True)  # PubMed ID for linking
    title = Column(Text, nullable=False)
    journal = Column(String(255), nullable=True)
    year = Column(Integer, nullable=True)
    authors = Column(Text, nullable=True)  # First 3 authors
    participants = Column(String(100), nullable=True)  # e.g., "120 women"
    finding = Column(Text, nullable=True)  # Key finding/result
    
    # Usage tracking
    access_count = Column(Integer, default=1)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    last_accessed_at = Column(DateTime, default=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index('idx_pubmed_cache_key', 'cache_key'),
        Index('idx_pubmed_cache_access', 'access_count'),
    )


class ActionPlanEvaluation(Base):
    """
    Stores quality evaluation metrics for each generated action plan.
    Used to track accuracy trends over time and identify quality issues.
    
    Metrics:
    - structure_valid: Pydantic validation passed (Boolean)
    - personalization_score: Actions tailored to user conditions (0-100, LLM)
    - condition_appropriateness: Safe for diagnosed conditions (0-100, LLM)
    - feedback_alignment_score: Respects prior likes/dislikes (0-100, LLM)
    - citation_validity_score: Research PMIDs are valid (0-100, Auto)
    - citation_relevance_score: Findings match recommendations (0-100, LLM)
    - overall_quality_score: Weighted average of all metrics (0-100)
    """
    __tablename__ = "action_plan_evaluations"
    
    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False, unique=True)
    uid = Column(String(255), nullable=False, index=True)
    
    # Structural Metrics
    structure_valid = Column(Boolean, nullable=False, default=True)
    
    # Relevance Metrics (0-100, LLM-evaluated)
    personalization_score = Column(Integer, nullable=True)
    condition_appropriateness = Column(Integer, nullable=True)
    feedback_alignment_score = Column(Integer, nullable=True)
    preference_compliance_score = Column(Integer, nullable=True)  # Diet/allergy/cuisine compliance
    
    # Citation Quality (0-100)
    citation_validity_score = Column(Integer, nullable=True)
    citation_relevance_score = Column(Integer, nullable=True)
    
    # Aggregate
    overall_quality_score = Column(Integer, nullable=True)
    
    # Metadata
    evaluation_cost = Column(String(50), nullable=True)  # $ spent on LLM evaluation
    evaluation_time_ms = Column(Integer, nullable=True)
    evaluator_model = Column(String(50), default="gpt-4o-mini")
    
    # Raw LLM evaluation response (for debugging)
    llm_evaluation_response = Column(JSONB, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_evaluation_plan', 'plan_id'),
        Index('idx_evaluation_user', 'uid', 'created_at'),
        Index('idx_evaluation_score', 'overall_quality_score'),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STREAK REWARDS SYSTEM TABLES
# ═══════════════════════════════════════════════════════════════════════════════

class UserReward(Base):
    """Track claimed rewards by users."""
    __tablename__ = "user_rewards"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), 
                 nullable=False, index=True)
    reward_id = Column(String(50), nullable=False)  # e.g., "streak_freeze", "diet_prefs"
    claimed_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_user_reward_uid', 'uid'),
    )


class UserStreakData(Base):
    """Persistent streak tracking with freeze support."""
    __tablename__ = "user_streak_data"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"),
                 nullable=False, unique=True)
    
    # Streak stats
    current_streak = Column(Integer, default=0)
    longest_streak = Column(Integer, default=0)
    last_activity_date = Column(Date, nullable=True)  # Last date action was completed
    
    # Freeze tokens
    freeze_count = Column(Integer, default=0)  # Available freezes
    freeze_used_date = Column(Date, nullable=True)  # DEPRECATED: Kept for backward compat
    freeze_used_dates = Column(JSONB, default=[])  # Array of dates when freeze was used ["2024-12-25", "2024-12-26"]
    
    # Plan refresh tracking (2x refresh reward)
    daily_refresh_count = Column(Integer, default=0)  # Refreshes used today
    last_refresh_date = Column(Date, nullable=True)  # Date refreshes were tracked
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_streak_uid', 'uid'),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# WEEKLY CHECK-IN SYSTEM - Doctor Consultation Feature
# ═══════════════════════════════════════════════════════════════════════════════

class WeeklyCheckIn(Base):
    """
    Weekly check-in records - structured doctor consultation data.
    
    Captures symptom severity, lifestyle factors, action reflections,
    and concerns through a guided conversation flow.
    """
    __tablename__ = "weekly_checkins"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    
    # Week identification
    week_number = Column(Integer, nullable=False)  # ISO week number (1-53)
    year = Column(Integer, nullable=False)
    check_in_date = Column(Date, nullable=False)
    
    # Primary concern tracking (from user's top_concern)
    top_concern = Column(String(100), nullable=True)  # e.g., "acne", "bloating", "fatigue"
    concern_severity = Column(Integer, nullable=True)  # 1-9 scale
    
    # Overall wellbeing
    overall_wellbeing = Column(Integer, nullable=True)  # 1-9 scale
    
    # Lifestyle factors (what affected symptoms this week)
    factors_positive = Column(JSONB, default=[])  # ["good_sleep", "less_stress", "ate_healthy"]
    factors_negative = Column(JSONB, default=[])  # ["more_dairy", "skipped_meals", "high_stress"]
    
    # Action plan reflections
    action_reflections = Column(JSONB, default={})
    # Structure:
    # {
    #     "worked_well": ["morning yoga", "pumpkin seeds"],
    #     "didnt_work": ["evening meditation - too tired"],
    #     "skipped": ["strength training"],
    #     "favorite_action": "seed cycling"
    # }
    
    # Free-form concerns for next week
    concerns_next_week = Column(Text, nullable=True)
    
    # Cycle context at check-in time
    cycle_day_at_checkin = Column(Integer, nullable=True)
    phase_at_checkin = Column(String(30), nullable=True)  # Menses, Follicular, Ovulation, Luteal
    
    # Conversation data
    conversation_summary = Column(Text, nullable=True)  # LLM-generated summary
    raw_messages = Column(JSONB, default=[])  # Full conversation for memory
    # Structure: [{"role": "bot", "content": "..."}, {"role": "user", "content": "..."}]
    
    # Actionable insights extracted from conversation (for action plan generation)
    actionable_insights = Column(JSONB, default={})
    # Structure:
    # {
    #     "triggers_identified": ["work stress", "poor sleep", "skipped meals"],
    #     "relief_factors_identified": ["morning meditation", "walking", "better sleep"],
    #     "severity_trend": "worsening" | "improving" | "stable",
    #     "suggested_additions": ["evening walk", "stress management exercise"],
    #     "suggested_removals": ["high-intensity workout during luteal"],
    #     "priority_focus": "stress management",
    #     "key_insight": "User's stress worsens with work demands, meditation helps"
    # }
    
    # Check-in state
    is_complete = Column(Boolean, default=False)
    current_question_index = Column(Integer, default=0)  # Track progress through questions
    
    # Timestamps
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    
    # Indexes
    __table_args__ = (
        Index('idx_checkin_user_week', 'uid', 'year', 'week_number', unique=True),
        Index('idx_checkin_user_date', 'uid', 'check_in_date'),
        Index('idx_checkin_complete', 'uid', 'is_complete'),
    )


class WeeklyCheckInQuestion(Base):
    """
    Dynamic question templates for weekly check-in flow.
    
    Allows configurable question sequences that can vary by concern type.
    LLM uses these templates to generate contextual questions and tap options.
    """
    __tablename__ = "weekly_checkin_questions"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Question identification
    question_key = Column(String(50), nullable=False, unique=True)  # e.g., "severity_rating", "negative_factors"
    question_type = Column(String(30), nullable=False)  # severity_scale, multi_select, single_select, free_text, action_reflection
    
    # Question content
    question_template = Column(Text, nullable=False)  # "How was your {concern} this week?"
    
    # Default tap options (LLM can override with personalized options)
    default_tap_options = Column(JSONB, default=[])
    # Structure for multi_select: [{"id": "more_dairy", "text": "Ate more dairy", "category": "food"}]
    
    # Targeting
    concern_type = Column(String(50), nullable=True)  # null = all concerns, or specific like "acne", "bloating"
    
    # Flow control
    question_order = Column(Integer, nullable=False)  # Order in the check-in flow
    is_required = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    
    # Conditional logic (optional - for advanced flows)
    show_condition = Column(JSONB, nullable=True)  # {"if_previous": "severity_rating", "value_gte": 5}
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_question_order', 'question_order', 'is_active'),
        Index('idx_question_concern', 'concern_type', 'is_active'),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CARE PLAN CHECK-IN SYSTEM - Daily threaded chat
# ═══════════════════════════════════════════════════════════════════════════════

class CarePlanCheckInThread(Base):
    """One per-user daily thread for Care Plan check-ins (keyed by user's local date)."""
    __tablename__ = "care_plan_checkin_threads"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)

    # Thread identity (in user's timezone)
    local_date = Column(Date, nullable=False, index=True)
    timezone = Column(String(100), nullable=True)

    # Conversation data (similar to WeeklyCheckIn)
    raw_messages = Column(JSONB, default=[])  # [{"role": "bot"|"user", "content": "...", "created_at": "..."}]

    # Sliding-window summary: summary of older messages + recent message tail kept verbatim
    rolling_summary = Column(Text, nullable=True)
    summarized_message_count = Column(Integer, default=0)  # Number of raw_messages included in rolling_summary
    last_summarized_at = Column(DateTime, nullable=True)

    # Actionable insights extracted for plan updates/replacements
    actionable_insights = Column(JSONB, default={})

    # Optional lifecycle flags
    is_closed = Column(Boolean, default=False)
    closed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # Changed from unique=True to allow multiple threads per day (ChatGPT-like)
        Index("idx_care_plan_thread_user_date", "uid", "local_date"),
        Index("idx_care_plan_thread_user_closed", "uid", "is_closed"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SYMPTOM CHECK-IN SYSTEM - Daily threaded chat
# ═══════════════════════════════════════════════════════════════════════════════

class SymptomCheckInThread(Base):
    """One per-user daily thread for symptom progress check-ins (keyed by user's local date)."""
    __tablename__ = "symptom_checkin_threads"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)

    local_date = Column(Date, nullable=False, index=True)
    timezone = Column(String(100), nullable=True)

    raw_messages = Column(JSONB, default=[])  # [{"role": "bot"|"user", "content": "...", "created_at": "..."}]

    rolling_summary = Column(Text, nullable=True)
    summarized_message_count = Column(Integer, default=0)
    last_summarized_at = Column(DateTime, nullable=True)

    # Insights for action plan + weekly check-in personalization
    actionable_insights = Column(JSONB, default={})

    is_closed = Column(Boolean, default=False)
    closed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # Changed from unique=True to allow multiple threads per day
        Index("idx_symptom_thread_user_date", "uid", "local_date"),
        Index("idx_symptom_thread_user_closed", "uid", "is_closed"),
    )

class AIModelUsageLog(Base):
    """
    Tracks which AI model was used for generation and any switching events.
    """
    __tablename__ = "ai_model_usage_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(255), nullable=False, index=True)
    
    primary_model = Column(String(100), nullable=False)
    fallback_model = Column(String(100), nullable=True)
    switch_reason = Column(Text, nullable=True)
    final_model_used = Column(String(100), nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# WEEKLY CHECK-IN SESSION - Stores weekly check-in insights and summary
# ═══════════════════════════════════════════════════════════════════════════════

class WeeklyCheckInSession(Base):
    """
    Stores weekly check-in session data including questions, answers, and generated insights.
    Critical for data persistence - without this model, weekly check-in data was being lost.
    """
    __tablename__ = "weekly_checkin_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    
    # Session timing
    session_date = Column(Date, nullable=False, index=True)
    timezone = Column(String(100), nullable=True)
    
    # Conversation data
    questions_asked = Column(JSONB, default=[])  # [{question: str, answer: str, topic: str}]
    question_count = Column(Integer, default=0)
    topics_covered = Column(ARRAY(String), default=[])  # ["sleep", "stress", "mood", etc.]
    
    # AI-generated insights
    weekly_summary = Column(Text, nullable=True)  # LLM-generated summary
    insights = Column(JSONB, default={})  # {patterns: [], recommendations: [], concerns: []}
    personalization_updates = Column(JSONB, default={})  # Profile fields that should be updated
    
    # Cycle context at time of check-in
    cycle_day = Column(Integer, nullable=True)
    cycle_phase = Column(String(50), nullable=True)
    
    # Session status
    is_complete = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_weekly_session_user_date", "uid", "session_date"),
        Index("idx_weekly_session_complete", "uid", "is_complete"),
    )





# ═══════════════════════════════════════════════════════════════════════════════
# ACTION PLAN REFRESH LOG - Tracks refresh token usage
# ═══════════════════════════════════════════════════════════════════════════════

class ActionPlanRefreshLog(Base):
    """
    Tracks refresh token usage for action plan replacements.
    Users get limited refreshes per day (gated by 16-day streak).
    """
    __tablename__ = "action_plan_refresh_logs"

    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("action_plans.id", ondelete="CASCADE"), nullable=True)
    
    # Refresh details
    refresh_date = Column(Date, nullable=False, index=True)
    refresh_count = Column(Integer, default=0)  # How many times refreshed today
    
    # What was replaced
    original_action = Column(JSONB, nullable=True)  # The action that was replaced
    replacement_action = Column(JSONB, nullable=True)  # The new action
    replacement_reason = Column(String(50), nullable=True)  # "no_time", "dont_like", "specific_request"
    
    # Thread context
    thread_id = Column(String(36), nullable=True)  # CarePlanCheckInThread.id
    
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_refresh_user_date", "uid", "refresh_date"),
    )

# Database table creation
def create_tables():
    """Create tables"""
    Base.metadata.create_all(bind=engine) 