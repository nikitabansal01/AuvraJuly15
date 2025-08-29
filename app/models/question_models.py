from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime
from app.core.validators import QuestionValidators

class SessionCreate(BaseModel):
    device_id: str = Field(..., description="Device identifier")

class SessionResponse(BaseModel):
    session_id: str
    device_id: str
    created_at: datetime
    expires_at: datetime
    status: str

class SessionData(BaseModel):
    """Temporary survey data stored in session (excluding personal identification information)"""
    age: Optional[int] = Field(None, description="Age (number)")
    period_description: Optional[str] = Field(None, description="Period status")
    birth_control: Optional[List[str]] = Field(None, description="Birth control methods")
    last_period_date: Optional[datetime] = Field(None, description="Last period start date (UTC datetime)")
    cycle_length: Optional[str] = Field(None, description="Cycle length")
    period_concerns: Optional[List[str]] = Field(None, description="Period-related concerns")
    body_concerns: Optional[List[str]] = Field(None, description="Body-related concerns")
    skin_hair_concerns: Optional[List[str]] = Field(None, description="Skin/hair-related concerns")
    mental_health_concerns: Optional[List[str]] = Field(None, description="Mental health-related concerns")
    other_concerns: Optional[List[str]] = Field(None, description="Other concerns")
    top_concern: Optional[str] = Field(None, description="Top priority concern")
    diagnosed_conditions: Optional[List[str]] = Field(None, description="Diagnosed conditions")
    family_history: Optional[List[str]] = Field(None, description="Family history")
    workout_intensity: Optional[str] = Field(None, description="Workout intensity")
    sleep_duration: Optional[str] = Field(None, description="Sleep duration")
    stress_level: Optional[str] = Field(None, description="Stress level")
    survey_timezone: Optional[str] = Field("Asia/Seoul", description="Timezone at survey input time")

    @field_validator('age')
    @classmethod
    def validate_age(cls, v):
        # No age limit - all ages allowed
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
        """Convert string to datetime"""
        if v is None:
            return v
        if isinstance(v, str):
            try:
                from datetime import datetime
                # Try ISO 8601 format
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            except ValueError:
                try:
                    # Try YYYY-MM-DD format
                    return datetime.strptime(v, '%Y-%m-%d')
                except ValueError:
                    try:
                        # Try MM/DD/YYYY format (frontend format)
                        return datetime.strptime(v, '%m/%d/%Y')
                    except ValueError:
                        raise ValueError(f"Invalid date format: {v}. Supported formats: YYYY-MM-DD, MM/DD/YYYY, ISO 8601")
        return v

class UserResponseData(BaseModel):
    """Anonymized survey data (personal identification information removed)"""
    age: Optional[int] = Field(None, description="Age (number)")
    period_description: Optional[str] = Field(None, description="Period status")
    birth_control: Optional[List[str]] = Field(None, description="Birth control methods")
    last_period_date_utc: Optional[datetime] = Field(None, description="Last period start date (UTC)")
    cycle_length: Optional[str] = Field(None, description="Cycle length")
    period_concerns: Optional[List[str]] = Field(None, description="Period-related concerns")
    body_concerns: Optional[List[str]] = Field(None, description="Body-related concerns")
    skin_hair_concerns: Optional[List[str]] = Field(None, description="Skin/hair-related concerns")
    mental_health_concerns: Optional[List[str]] = Field(None, description="Mental health-related concerns")
    other_concerns: Optional[List[str]] = Field(None, description="Other concerns")
    top_concern: Optional[str] = Field(None, description="Top priority concern")
    diagnosed_conditions: Optional[List[str]] = Field(None, description="Diagnosed conditions")
    family_history: Optional[List[str]] = Field(None, description="Family history")
    workout_intensity: Optional[str] = Field(None, description="Workout intensity")
    sleep_duration: Optional[str] = Field(None, description="Sleep duration")
    stress_level: Optional[str] = Field(None, description="Stress level")
    survey_timezone: Optional[str] = Field("Asia/Seoul", description="Timezone at survey input time")

    @field_validator('age')
    @classmethod
    def validate_age(cls, v):
        # No age limit - all ages allowed
        return v

    # ... Keep existing validation logic ...

class SessionDataCreate(BaseModel):
    session_id: str = Field(..., description="Session ID")
    data: SessionData

class UserProfileCreate(BaseModel):
    name: str = Field(..., description="User name")
    email: str = Field(..., description="User email")

class SessionLinkRequest(BaseModel):
    user_profile: UserProfileCreate = Field(..., description="User profile")
    current_timezone: str = Field("Asia/Seoul", description="Current user timezone (IANA format)")

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
    new_timezone: str = Field(..., description="New timezone (IANA format)")

class TimezoneUpdateResponse(BaseModel):
    success: bool
    message: str
    old_timezone: Optional[str] = None
    new_timezone: Optional[str] = None 