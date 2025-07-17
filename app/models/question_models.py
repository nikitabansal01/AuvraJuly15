from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.core.validators import QuestionValidators

class SessionCreate(BaseModel):
    device_id: str = Field(..., description="디바이스 식별자")

class SessionResponse(BaseModel):
    session_id: str
    device_id: str
    created_at: datetime
    status: str

class UserResponseData(BaseModel):
    # 기본 정보
    name: Optional[str] = Field(None, description="사용자 이름")
    age: Optional[int] = Field(None, ge=0, le=120, description="사용자 나이")
    
    # 생리 관련
    period_description: Optional[str] = Field(None, description="생리 상태 설명")
    birth_control: Optional[List[str]] = Field(None, description="피임 방법")
    
    # 생리 세부사항
    last_period_date: Optional[str] = Field(None, description="마지막 생리 시작일 (MM/DD/YYYY)")
    cycle_length: Optional[str] = Field(None, description="평균 생리 주기")
    
    # 건강 문제
    period_concerns: Optional[List[str]] = Field(None, description="생리 관련 문제")
    body_concerns: Optional[List[str]] = Field(None, description="신체 관련 문제")
    skin_hair_concerns: Optional[List[str]] = Field(None, description="피부/모발 관련 문제")
    mental_health_concerns: Optional[List[str]] = Field(None, description="정신 건강 관련 문제")
    other_concerns: Optional[List[str]] = Field(None, description="기타 문제")
    
    # 최우선 문제
    top_concern: Optional[str] = Field(None, description="최우선 문제")
    
    # 진단된 질환
    diagnosed_conditions: Optional[List[str]] = Field(None, description="진단된 건강 상태")
    
    # 검증 메서드들
    @validator('period_description')
    def validate_period_description(cls, v):
        if v is not None:
            return QuestionValidators.validate_period_description(v)
        return v
    
    @validator('cycle_length')
    def validate_cycle_length(cls, v):
        if v is not None:
            return QuestionValidators.validate_cycle_length(v)
        return v
    
    @validator('birth_control')
    def validate_birth_control(cls, v):
        if v is not None:
            return QuestionValidators.validate_birth_control(v)
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
    
    @validator('other_concerns')
    def validate_other_concerns(cls, v):
        if v is not None:
            return QuestionValidators.validate_other_concerns(v)
        return v

class UserResponseCreate(BaseModel):
    session_id: str = Field(..., description="세션 ID")
    responses: UserResponseData

class SessionLinkRequest(BaseModel):
    uid: str = Field(..., description="Firebase UID")

class UserResponseFull(BaseModel):
    id: int
    session_id: str
    uid: Optional[str]
    name: Optional[str]
    age: Optional[int]
    period_description: Optional[str]
    birth_control: Optional[List[str]]
    last_period_date: Optional[str]
    cycle_length: Optional[str]
    period_concerns: Optional[List[str]]
    body_concerns: Optional[List[str]]
    skin_hair_concerns: Optional[List[str]]
    mental_health_concerns: Optional[List[str]]
    other_concerns: Optional[List[str]]
    top_concern: Optional[str]
    diagnosed_conditions: Optional[List[str]]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class AnalyticsResponse(BaseModel):
    total_users: int
    age_distribution: Dict[str, int]
    period_concerns_stats: Dict[str, int]
    body_concerns_stats: Dict[str, int]
    top_concerns_stats: Dict[str, int] 