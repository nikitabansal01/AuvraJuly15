from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, ARRAY, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from app.core.config import settings
import uuid

# 데이터베이스 엔진 생성
engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Create database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def generate_session_id():
    """Generate session ID"""
    return f"session_{uuid.uuid4().hex[:12]}"

# Model definitions
class User(Base):
    __tablename__ = "users"
    
    uid = Column(String(255), primary_key=True)
    email = Column(String(255))
    display_name = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class QuestionSession(Base):
    __tablename__ = "question_sessions"
    
    session_id = Column(String(255), primary_key=True)
    uid = Column(String(255), nullable=True)
    device_id = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(50), default="in_progress")

class UserResponse(Base):
    __tablename__ = "user_responses"
    
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(255))
    uid = Column(String(255), nullable=True)
    
    # 기본 정보
    name = Column(String(255), nullable=True)
    age = Column(Integer, nullable=True)
    
    # 생리 관련
    period_description = Column(String(100), nullable=True)
    birth_control = Column(ARRAY(String), nullable=True)
    
    # 생리 세부사항
    last_period_date = Column(String(50), nullable=True)  # MM/DD/YYYY 형태
    cycle_length = Column(String(50), nullable=True)
    
    # 건강 문제 (JSONB로 저장)
    period_concerns = Column(JSONB, nullable=True)
    body_concerns = Column(JSONB, nullable=True)
    skin_hair_concerns = Column(JSONB, nullable=True)
    mental_health_concerns = Column(JSONB, nullable=True)
    other_concerns = Column(JSONB, nullable=True)
    
    # 최우선 문제
    top_concern = Column(String(255), nullable=True)
    
    # 진단된 질환
    diagnosed_conditions = Column(ARRAY(String), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Database table creation
def create_tables():
    """Create tables"""
    Base.metadata.create_all(bind=engine) 