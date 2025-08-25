from pydantic import BaseModel, Field, field_validator
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
    last_period_date: Optional[datetime] = Field(None, description="마지막 생리 시작일 (UTC datetime)")
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
    survey_timezone: Optional[str] = Field("Asia/Seoul", description="설문 입력 시점 시간대")

    @field_validator('age')
    @classmethod
    def validate_age(cls, v):
        # 나이 제한 없음 - 모든 나이 허용
        return v

    @field_validator('period_description')
    @classmethod
    def validate_period_description(cls, v):
        if v is not None:
            return QuestionValidators.validate_period_description(v)
        return v

    @field_validator('birth_control')
    @classmethod
    def validate_birth_control(cls, v):
        if v is not None:
            return QuestionValidators.validate_birth_control(v)
        return v

    @field_validator('cycle_length')
    @classmethod
    def validate_cycle_length(cls, v):
        if v is not None:
            return QuestionValidators.validate_cycle_length(v)
        return v

    @field_validator('period_concerns')
    @classmethod
    def validate_period_concerns(cls, v):
        if v is not None:
            return QuestionValidators.validate_period_concerns(v)
        return v

    @field_validator('body_concerns')
    @classmethod
    def validate_body_concerns(cls, v):
        if v is not None:
            return QuestionValidators.validate_body_concerns(v)
        return v

    @field_validator('skin_hair_concerns')
    @classmethod
    def validate_skin_hair_concerns(cls, v):
        if v is not None:
            return QuestionValidators.validate_skin_hair_concerns(v)
        return v

    @field_validator('mental_health_concerns')
    @classmethod
    def validate_mental_health_concerns(cls, v):
        if v is not None:
            return QuestionValidators.validate_mental_health_concerns(v)
        return v

    @field_validator('other_concerns')
    @classmethod
    def validate_other_concerns(cls, v):
        if v is not None:
            return QuestionValidators.validate_other_concerns(v)
        return v

    @field_validator('top_concern')
    @classmethod
    def validate_top_concern(cls, v):
        if v is not None:
            return QuestionValidators.validate_top_concern(v)
        return v

    @field_validator('diagnosed_conditions')
    @classmethod
    def validate_diagnosed_conditions(cls, v):
        if v is not None:
            return QuestionValidators.validate_diagnosed_conditions(v)
        return v

    @field_validator('family_history')
    @classmethod
    def validate_family_history(cls, v):
        if v is not None:
            return QuestionValidators.validate_family_history(v)
        return v

    @field_validator('workout_intensity')
    @classmethod
    def validate_workout_intensity(cls, v):
        if v is not None:
            return QuestionValidators.validate_workout_intensity(v)
        return v

    @field_validator('sleep_duration')
    @classmethod
    def validate_sleep_duration(cls, v):
        if v is not None:
            return QuestionValidators.validate_sleep_duration(v)
        return v

    @field_validator('stress_level')
    @classmethod
    def validate_stress_level(cls, v):
        if v is not None:
            return QuestionValidators.validate_stress_level(v)
        return v

    @field_validator('last_period_date', mode='before')
    @classmethod
    def validate_last_period_date(cls, v):
        """문자열을 datetime으로 변환"""
        if v is None:
            return v
        if isinstance(v, str):
            try:
                from datetime import datetime
                # ISO 8601 형식 시도
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            except ValueError:
                try:
                    # YYYY-MM-DD 형식 시도
                    return datetime.strptime(v, '%Y-%m-%d')
                except ValueError:
                    try:
                        # MM/DD/YYYY 형식 시도 (프론트엔드 형식)
                        return datetime.strptime(v, '%m/%d/%Y')
                    except ValueError:
                        raise ValueError(f"Invalid date format: {v}. Supported formats: YYYY-MM-DD, MM/DD/YYYY, ISO 8601")
        return v

class UserResponseData(BaseModel):
    """익명화된 설문 데이터 (개인 식별 정보 제거)"""
    age: Optional[int] = Field(None, description="나이 (숫자)")
    period_description: Optional[str] = Field(None, description="생리 상태")
    birth_control: Optional[List[str]] = Field(None, description="피임 방법")
    last_period_date_utc: Optional[datetime] = Field(None, description="마지막 생리 시작일 (UTC)")
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
    survey_timezone: Optional[str] = Field("Asia/Seoul", description="설문 입력 시점 시간대")

    @field_validator('age')
    @classmethod
    def validate_age(cls, v):
        # 나이 제한 없음 - 모든 나이 허용
        return v

    # ... 기존 검증 로직들 유지 ...

class SessionDataCreate(BaseModel):
    session_id: str = Field(..., description="세션 ID")
    data: SessionData

class UserProfileCreate(BaseModel):
    name: str = Field(..., description="사용자 이름")
    email: str = Field(..., description="사용자 이메일")

class SessionLinkRequest(BaseModel):
    user_profile: UserProfileCreate = Field(..., description="사용자 프로필")
    current_timezone: str = Field("Asia/Seoul", description="현재 사용자 시간대 (IANA 형식)")

class UserResponseFull(BaseModel):
    id: int
    uid: str
    age: Optional[int]
    period_description: Optional[str]
    birth_control: Optional[List[str]]
    last_period_date_utc: Optional[datetime]
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
    survey_timezone: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {
        "from_attributes": True
    }

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

class TimezoneUpdateRequest(BaseModel):
    new_timezone: str = Field(..., description="새로운 시간대 (IANA 형식)")

class TimezoneUpdateResponse(BaseModel):
    success: bool
    message: str
    old_timezone: Optional[str] = None
    new_timezone: Optional[str] = None 