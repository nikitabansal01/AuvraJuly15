from pydantic import BaseModel, Field, validator
from typing import List, Optional
from datetime import datetime
from app.core.validators import QuestionValidators

class SessionCreate(BaseModel):
    device_id: str = Field(..., description="디바이스 식별자")

class SessionResponse(BaseModel):
    session_id: str
    device_id: str
    created_at: datetime
    expires_at: datetime
    status: str

class SessionData(BaseModel):
    """세션에 저장될 임시 설문 데이터 (개인 식별 정보 제외)"""
    age: Optional[int] = Field(None, description="나이 (숫자)")
    period_description: Optional[str] = Field(None, description="생리 상태")
    birth_control: Optional[List[str]] = Field(None, description="피임 방법")
    last_period_date: Optional[str] = Field(None, description="마지막 생리 시작일")
    cycle_length: Optional[str] = Field(None, description="생리 주기")
    period_concerns: Optional[List[str]] = Field(None, description="생리 관련 우려")
    body_concerns: Optional[List[str]] = Field(None, description="신체 관련 우려")
    skin_hair_concerns: Optional[List[str]] = Field(None, description="피부/모발 관련 우려")
    mental_health_concerns: Optional[List[str]] = Field(None, description="정신건강 관련 우려")
    other_concerns: Optional[List[str]] = Field(None, description="기타 우려사항")
    top_concern: Optional[str] = Field(None, description="최우선 우려사항")
    diagnosed_conditions: Optional[List[str]] = Field(None, description="진단된 질환")
    family_history: Optional[List[str]] = Field(None, description="가족력")
    workout_intensity: Optional[str] = Field(None, description="운동 강도")
    sleep_duration: Optional[str] = Field(None, description="수면 시간")
    stress_level: Optional[str] = Field(None, description="스트레스 수준")

    @validator('age')
    def validate_age(cls, v):
        if v is not None and (v < 13 or v > 100):
            raise ValueError("나이는 13세 이상 100세 이하여야 합니다")
        return v

    @validator('period_description')
    def validate_period_description(cls, v):
        if v is not None:
            return QuestionValidators.validate_period_description(v)
        return v

    @validator('birth_control')
    def validate_birth_control(cls, v):
        if v is not None:
            return QuestionValidators.validate_birth_control(v)
        return v

    @validator('cycle_length')
    def validate_cycle_length(cls, v):
        if v is not None:
            return QuestionValidators.validate_cycle_length(v)
        return v

    @validator('period_concerns')
    def validate_period_concerns(cls, v):
        if v is not None:
            return QuestionValidators.validate_period_concerns(v)
        return v

    @validator('body_concerns')
    def validate_body_concerns(cls, v):
        if v is not None:
            return QuestionValidators.validate_body_concerns(v)
        return v

    @validator('skin_hair_concerns')
    def validate_skin_hair_concerns(cls, v):
        if v is not None:
            return QuestionValidators.validate_skin_hair_concerns(v)
        return v

    @validator('mental_health_concerns')
    def validate_mental_health_concerns(cls, v):
        if v is not None:
            return QuestionValidators.validate_mental_health_concerns(v)
        return v

    @validator('other_concerns')
    def validate_other_concerns(cls, v):
        if v is not None:
            return QuestionValidators.validate_other_concerns(v)
        return v

    @validator('top_concern')
    def validate_top_concern(cls, v):
        if v is not None:
            return QuestionValidators.validate_top_concern(v)
        return v

    @validator('diagnosed_conditions')
    def validate_diagnosed_conditions(cls, v):
        if v is not None:
            return QuestionValidators.validate_diagnosed_conditions(v)
        return v

    @validator('family_history')
    def validate_family_history(cls, v):
        if v is not None:
            return QuestionValidators.validate_family_history(v)
        return v

    @validator('workout_intensity')
    def validate_workout_intensity(cls, v):
        if v is not None:
            return QuestionValidators.validate_workout_intensity(v)
        return v

    @validator('sleep_duration')
    def validate_sleep_duration(cls, v):
        if v is not None:
            return QuestionValidators.validate_sleep_duration(v)
        return v

    @validator('stress_level')
    def validate_stress_level(cls, v):
        if v is not None:
            return QuestionValidators.validate_stress_level(v)
        return v

class UserResponseData(BaseModel):
    """익명화된 설문 데이터 (개인 식별 정보 제거)"""
    age: Optional[int] = Field(None, description="나이 (숫자)")
    period_description: Optional[str] = Field(None, description="생리 상태")
    birth_control: Optional[List[str]] = Field(None, description="피임 방법")
    cycle_length: Optional[str] = Field(None, description="생리 주기")
    period_concerns: Optional[List[str]] = Field(None, description="생리 관련 우려")
    body_concerns: Optional[List[str]] = Field(None, description="신체 관련 우려")
    skin_hair_concerns: Optional[List[str]] = Field(None, description="피부/모발 관련 우려")
    mental_health_concerns: Optional[List[str]] = Field(None, description="정신건강 관련 우려")
    other_concerns: Optional[List[str]] = Field(None, description="기타 우려사항")
    top_concern: Optional[str] = Field(None, description="최우선 우려사항")
    diagnosed_conditions: Optional[List[str]] = Field(None, description="진단된 질환")
    family_history: Optional[List[str]] = Field(None, description="가족력")
    workout_intensity: Optional[str] = Field(None, description="운동 강도")
    sleep_duration: Optional[str] = Field(None, description="수면 시간")
    stress_level: Optional[str] = Field(None, description="스트레스 수준")

    @validator('age')
    def validate_age(cls, v):
        if v is not None and (v < 13 or v > 100):
            raise ValueError("나이는 13세 이상 100세 이하여야 합니다")
        return v

    # ... 기존 검증 로직들 유지 ...

class SessionDataCreate(BaseModel):
    session_id: str = Field(..., description="세션 ID")
    data: SessionData

class UserProfileCreate(BaseModel):
    name: str = Field(..., description="사용자 이름")
    email: str = Field(..., description="사용자 이메일")

class SessionLinkRequest(BaseModel):
    session_id: str = Field(..., description="세션 ID")
    user_profile: UserProfileCreate

class UserResponseFull(BaseModel):
    id: int
    uid: str
    age: Optional[int]
    period_description: Optional[str]
    birth_control: Optional[List[str]]
    cycle_length: Optional[str]
    period_concerns: Optional[List[str]]
    body_concerns: Optional[List[str]]
    skin_hair_concerns: Optional[List[str]]
    mental_health_concerns: Optional[List[str]]
    other_concerns: Optional[List[str]]
    top_concern: Optional[str]
    diagnosed_conditions: Optional[List[str]]
    family_history: Optional[List[str]]
    workout_intensity: Optional[str]
    sleep_duration: Optional[str]
    stress_level: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

class AnalyticsResponse(BaseModel):
    total_responses: int
    age_distribution: dict
    period_description_stats: dict
    top_concerns: dict
    diagnosed_conditions_stats: dict
    family_history_stats: dict
    workout_intensity_stats: dict
    sleep_duration_stats: dict
    stress_level_stats: dict 