from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ARRAY, Text, ForeignKey, Date, Index, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, date
import uuid
import hashlib
import logging
from app.core.config import settings
from typing import List

logger = logging.getLogger(__name__)

# Database engine creation
# Render PostgreSQL requires SSL
if settings.ENVIRONMENT == "production":
    # For Render with Session Pooler, optimize settings
    database_url = settings.DATABASE_URL
    if "?" not in database_url:
        database_url += "?sslmode=require"
    
    # Session Pooler에 최적화된 설정
    engine = create_engine(
        database_url,
        pool_size=5,           # Session Pooler가 관리하므로 작게 설정
        max_overflow=10,       # 적절한 오버플로우
        pool_pre_ping=False,   # Session Pooler가 관리하므로 비활성화
        pool_recycle=300,      # 5분마다 연결 재생성
        pool_timeout=30,       # 연결 대기 시간 (초) - 최대치 초과 시 30초 대기
        pool_reset_on_return='commit',  # 연결 반환 시 커밋으로 리셋
        echo=False             # 프로덕션에서는 SQL 로그 비활성화
    )
else:
    # 개발 환경에서도 연결 풀링 설정
    engine = create_engine(
        settings.DATABASE_URL,
        pool_size=5,              # 기본 연결 풀 크기
        max_overflow=10,          # 최대 추가 연결 수
        pool_pre_ping=True,       # 연결 상태 확인 (개발 환경에서 유용)
        pool_recycle=3600,        # 1시간마다 연결 재생성
        pool_timeout=30,          # 연결 대기 시간 (초) - 최대치 초과 시 30초 대기
        pool_reset_on_return='commit',  # 연결 반환 시 커밋으로 리셋
        echo=False                 # SQL 쿼리 로그 비활성화 (성능 향상)
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
        # 연결 풀 상태 로깅 비활성화 (성능 향상)
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
    expires_at = Column(DateTime, nullable=False)  # 세션 만료 시간 (24시간)
    status = Column(String(50), default="active")  # active, completed, expired
    
    # 임시 설문 데이터 (개인 식별 정보 제외)
    age = Column(Integer, nullable=True)  # 숫자 그대로 저장
    period_description = Column(String(100), nullable=True)
    birth_control = Column(ARRAY(String), nullable=True)
    last_period_date_utc = Column(DateTime, nullable=True)  # 마지막 생리 시작일 (UTC)
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
    survey_timezone = Column(String(50), nullable=True, default="Asia/Seoul")  # 설문 입력 시점 시간대

class SessionProcessingStatus(Base):
    """세션별 AI 추천 생성 진행상황 추적"""
    __tablename__ = "session_processing_status"
    
    session_id = Column(String(255), primary_key=True, index=True)
    processing_status = Column(String(50), default="queued")  # queued, in_progress, completed, failed, canceled, stalled
    phase = Column(String(100), nullable=True)  # 사람이 읽을 수 있는 단계 이름
    progress = Column(Integer, default=0)  # 0-100
    message = Column(Text, nullable=True)  # UI용 짧은 상태 메시지
    
    # 카테고리별 처리 상태
    food_status = Column(String(50), default="pending")  # pending, processing, completed, failed
    movement_status = Column(String(50), default="pending")
    mindfulness_status = Column(String(50), default="pending")
    
    # 요청/결과 데이터
    request_payload = Column(JSONB, nullable=True)  # UserProfile 데이터
    result = Column(JSONB, nullable=True)  # 생성된 추천 요약
    error = Column(JSONB, nullable=True)  # 에러 정보
    
    # 시간 정보
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)  # 마지막 업데이트 시간
    
    # 재시도 정보
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 인덱스
    __table_args__ = (
        Index('idx_processing_status', 'processing_status'),
        Index('idx_heartbeat_at', 'heartbeat_at'),
    )

class UserProfile(Base):
    __tablename__ = "user_profiles"
    
    uid = Column(String(255), primary_key=True, index=True)  # Firebase UID
    name = Column(String(255), nullable=True)  # Firebase Auth에서 가져옴
    email = Column(String(255), nullable=True)  # Firebase Auth에서 가져옴
    current_timezone = Column(String(50), default="Asia/Seoul")  # 현재 사용자 시간대
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserResponse(Base):
    __tablename__ = "user_responses"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), nullable=False, index=True)  # Firebase UID
    
    # 익명화된 설문 데이터 (개인 식별 정보 제거)
    age = Column(Integer, nullable=True)  # 실제 나이 (숫자)
    period_description = Column(String(100), nullable=True)
    birth_control = Column(ARRAY(String), nullable=True)
    last_period_date_utc = Column(DateTime, nullable=True)  # 마지막 생리 시작일 (UTC)
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
    survey_timezone = Column(String(50), nullable=True)  # 설문 입력 시점 시간대 (참고용)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class RecommendationRecord(Base):
    __tablename__ = "recommendation_records"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), nullable=True, index=True)  # Firebase UID (임시 세션에서는 NULL)
    session_id = Column(String(255), nullable=True, index=True)  # 세션 ID (임시 저장용)
    
    # 추천 메타데이터
    recommendation_type = Column(String(50), nullable=False)  # "general" or "rag"
    category = Column(String(50), nullable=False)  # "food", "movement", "mindfulness"
    confidence = Column(Integer, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)
    
    # 추천 카드 정보
    title = Column(String(500), nullable=True)  # 간결한 방법 설명 (1-2단어)
    purpose = Column(String(500), nullable=True)  # 추천 목적/효과
    specific_action = Column(Text, nullable=True)
    priority = Column(String(50), nullable=True)
    contraindications = Column(JSONB, nullable=True)
    
    # 태그 정보
    conditions = Column(ARRAY(String), nullable=True)
    symptoms = Column(ARRAY(String), nullable=True)
    hormones = Column(ARRAY(String), nullable=True)
    
    # 카테고리별 배열 필드들
    # 음식 관련 (배열)
    food_amounts = Column(ARRAY(String), nullable=True)  # ["150g", "100g", "2 tablespoons"]
    food_items = Column(ARRAY(String), nullable=True)    # ["oats", "lentils", "flaxseed"]
    
    # 운동 관련 (배열)
    exercise_durations = Column(ARRAY(String), nullable=True)  # ["30 minutes", "45 minutes"]
    exercise_types = Column(ARRAY(String), nullable=True)      # ["yoga", "walking"]
    exercise_intensities = Column(ARRAY(String), nullable=True) # ["moderate", "low"]
    
    # 마음챙김 관련 (배열)
    mindfulness_durations = Column(ARRAY(String), nullable=True)  # ["15 minutes", "20 minutes"]
    mindfulness_techniques = Column(ARRAY(String), nullable=True) # ["meditation", "deep breathing"]
    
    # 공통 필드
    frequency_detail = Column(String(100), nullable=True)  # 구조화된 형식 (예: "daily:1", "weekly:3")
    duration_weeks = Column(Integer, nullable=True)  # 숫자만 (예: 12, 8, 16)
    optimal_times = Column(ARRAY(String), nullable=True)  # ["morning", "afternoon", "night"]
    
    # 연구 근거
    research_summary = Column(Text, nullable=True)
    research_studies = Column(JSONB, nullable=True)
    
    # 사용자 프로필 스냅샷
    user_profile_snapshot = Column(JSONB, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 관계 설정
    schedules = relationship("RecommendationSchedule", back_populates="recommendation")

class RecommendationAdvice(Base):
    __tablename__ = "recommendation_advices"
    
    id = Column(Integer, primary_key=True, index=True)
    recommendation_id = Column(Integer, ForeignKey("recommendation_records.id", ondelete="CASCADE"), nullable=False)
    uid = Column(String(255), nullable=True, index=True)  # Firebase UID (임시 세션에서는 NULL)
    session_id = Column(String(255), nullable=True, index=True)  # 세션 ID (임시 저장용)
    
    # 조언 정보
    advice_type = Column(String(50), nullable=False)  # "easy", "tasty", "healthy" for food; "tip1", "tip2", "tip3" for movement/mindfulness
    category = Column(String(50), nullable=False)  # "food", "movement", "mindfulness"
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    recommendation_context = Column(JSONB, nullable=True)  # 추천 컨텍스트 정보
    
    # 메타데이터
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserSchedule(Base):
    """사용자의 일일 스케줄 정보"""
    __tablename__ = "user_schedules"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)  # 스케줄 날짜 (YYYY-MM-DD)
    
    # 스케줄링된 추천들 (JSONB로 저장)
    scheduled_recommendations = Column(JSONB, nullable=False)  # {morning: [rec_ids], afternoon: [rec_ids], night: [rec_ids], anytime: [rec_ids]}
    
    # 완료된 추천들
    completed_recommendations = Column(JSONB, nullable=False, default=list)  # [rec_ids]
    
    # 메타데이터
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 복합 인덱스
    __table_args__ = (
        Index('idx_user_schedule_date', 'uid', 'date'),
    )

class RecommendationCompletion(Base):
    """추천 완료 기록"""
    __tablename__ = "recommendation_completions"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    recommendation_id = Column(Integer, ForeignKey("recommendation_records.id", ondelete="CASCADE"), nullable=False, index=True)
    completion_date = Column(Date, nullable=False, index=True)  # 완료 날짜
    
    # 완료 정보
    completed_at = Column(DateTime, default=datetime.utcnow)  # 완료 시간
    notes = Column(Text, nullable=True)  # 사용자 메모
    
    # 복합 인덱스
    __table_args__ = (
        Index('idx_completion_user_date', 'uid', 'completion_date'),
        Index('idx_completion_rec_date', 'recommendation_id', 'completion_date'),
    )

class RecommendationRedistribution(Base):
    """추천 재배치 정보"""
    __tablename__ = "recommendation_redistributions"
    
    id = Column(Integer, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    recommendation_id = Column(Integer, ForeignKey("recommendation_records.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # 재배치 정보
    original_date = Column(Date, nullable=False, index=True)  # 원래 예정된 날짜
    redistributed_dates = Column(JSONB, nullable=False)  # 재배치된 날짜들 [date1, date2, ...]
    redistribution_reason = Column(String(100), nullable=False, default="uncompleted")  # 재배치 이유
    
    # 메타데이터
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 복합 인덱스
    __table_args__ = (
        Index('idx_redistribution_user_rec', 'uid', 'recommendation_id'),
        Index('idx_redistribution_date', 'original_date'),
    )

class RecommendationSchedule(Base):
    __tablename__ = "recommendation_schedules"
    
    id = Column(BigInteger, primary_key=True, index=True)
    uid = Column(String(255), nullable=False, index=True)  # Firebase UID
    recommendation_id = Column(BigInteger, ForeignKey("recommendation_records.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # UTC 기반 날짜/시간
    start_date_utc = Column(DateTime, nullable=False)  # 시작 날짜 (UTC)
    end_date_utc = Column(DateTime, nullable=True)     # 종료 날짜 (UTC)
    next_fire_at_utc = Column(DateTime, nullable=True) # 다음 실행 시각 (UTC)
    
    # RRULE (Recurrence Rule)
    rrule = Column(String(500), nullable=False)  # RRULE 문자열
    
    # 메타데이터
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 관계 설정
    recommendation = relationship("RecommendationRecord", back_populates="schedules")

class ScheduleRedistribution(Base):
    """재배치(예외/보정) - 새로운 스케줄링 시스템"""
    __tablename__ = "schedule_redistributions"
    
    id = Column(BigInteger, primary_key=True, index=True)
    schedule_id = Column(BigInteger, ForeignKey("recommendation_schedules.id", ondelete="CASCADE"), nullable=False, index=True)
    original_date = Column(Date, nullable=False)
    override_date = Column(Date, nullable=False)
    reason = Column(Text, nullable=True)
    source = Column(String(50), nullable=False, default="system")  # 'system' | 'user' | 'admin'
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 유니크 제약조건
    __table_args__ = (
        Index('idx_redistribution_schedule', 'schedule_id'),
        Index('idx_redistribution_dates', 'original_date', 'override_date'),
    )

class DailyAssignment(Base):
    """일일 과제/스케줄 - 새로운 스케줄링 시스템"""
    __tablename__ = "daily_assignments"
    
    id = Column(BigInteger, primary_key=True, index=True)
    uid = Column(String(255), ForeignKey("user_profiles.uid", ondelete="CASCADE"), nullable=False, index=True)
    schedule_id = Column(BigInteger, ForeignKey("recommendation_schedules.id", ondelete="CASCADE"), nullable=False, index=True)
    recommendation_id = Column(Integer, ForeignKey("recommendation_records.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # 일일 스케줄 정보
    assignment_date = Column(Date, nullable=False, index=True)  # 사용자 로컬 날짜
    time_group = Column(String(50), nullable=False)  # morning, afternoon, night, anytime
    is_completed = Column(Boolean, default=False)
    
    # 완료 정보
    completed_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 인덱스
    __table_args__ = (
        Index('idx_assignment_user_date', 'uid', 'assignment_date'),
        Index('idx_assignment_schedule_date', 'schedule_id', 'assignment_date'),
    )

# Database table creation
def create_tables():
    """Create tables"""
    Base.metadata.create_all(bind=engine) 