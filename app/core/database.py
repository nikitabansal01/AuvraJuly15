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

# Database table creation
def create_tables():
    """Create tables"""
    Base.metadata.create_all(bind=engine) 