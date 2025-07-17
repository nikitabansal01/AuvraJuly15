from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, ARRAY, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime
from app.core.config import settings
import uuid

# Database engine creation
# Render PostgreSQL requires SSL
if settings.ENVIRONMENT == "production":
    # For Render, add SSL mode
    database_url = settings.DATABASE_URL
    if "?" not in database_url:
        database_url += "?sslmode=require"
    engine = create_engine(
        database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=300
    )
else:
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
    
    # Basic information
    name = Column(String(255), nullable=True)
    age = Column(Integer, nullable=True)
    
    # Menstrual related
    period_description = Column(String(100), nullable=True)
    birth_control = Column(ARRAY(String), nullable=True)
    
    # Menstrual details
    last_period_date = Column(String(50), nullable=True)  # MM/DD/YYYY format
    cycle_length = Column(String(50), nullable=True)
    
    # Health concerns (stored as JSONB)
    period_concerns = Column(JSONB, nullable=True)
    body_concerns = Column(JSONB, nullable=True)
    skin_hair_concerns = Column(JSONB, nullable=True)
    mental_health_concerns = Column(JSONB, nullable=True)
    other_concerns = Column(JSONB, nullable=True)
    
    # Top priority concern
    top_concern = Column(String(255), nullable=True)
    
    # Diagnosed conditions
    diagnosed_conditions = Column(ARRAY(String), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Database table creation
def create_tables():
    """Create tables"""
    Base.metadata.create_all(bind=engine) 