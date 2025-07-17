from typing import List, Dict, Any
from pydantic import validator, ValidationError

class QuestionValidators:
    """질문 옵션 검증 클래스"""
    
    # 허용된 옵션들 (QuestionScreen과 완전히 일치)
    PERIOD_DESCRIPTION_OPTIONS = [
        "Regular", "Irregular", "Occasional Skips", "I don't get periods", "I'm not sure"
    ]
    
    CYCLE_LENGTH_OPTIONS = [
        "Less than 21 days", "21-25 days", "26-30 days", "31-35 days", "35+ days", "I'm not sure"
    ]
    
    BIRTH_CONTROL_OPTIONS = [
        "Hormonal Birth Control Pills", "IUD (Intrauterine Device)"
    ]
    
    PERIOD_CONCERNS_OPTIONS = [
        "Irregular Periods", "Painful Periods", "Light periods / Spotting", "Heavy periods"
    ]
    
    BODY_CONCERNS_OPTIONS = [
        "Bloating", "Hot Flashes", "Nausea", 
        "Difficulty losing weight / stubborn belly fat", "Recent weight gain", "Menstrual headaches"
    ]
    
    SKIN_HAIR_CONCERNS_OPTIONS = [
        "Hirsutism (hair  growth on chin, nipples etc)", "Thinning of hair", "Adult Acne"
    ]
    
    MENTAL_HEALTH_CONCERNS_OPTIONS = [
        "Mood swings", "Stress", "Fatigue"
    ]
    
    TOP_CONCERN_OPTIONS = [
        "Painful Periods", "Bloating", "Recent weight gain", 
        "Hirsutism (hair growth on chin, nipples etc)", "Adult Acne", "Mood swings"
    ]
    
    DIAGNOSED_CONDITIONS_OPTIONS = [
        "PCOS", "PCOD", "Endometriosis", "Dysmenorrhea (painful periods)",
        "Amenorrhea (absence of periods)", "Menorrhagia (prolonged/heavy bleeding)",
        "Metrorrhagia (irregular bleeding)", "Cushing's Syndrome (PMS)",
        "Premenstrual Syndrome (PMS)", "None of the above", "Others (please specify)"
    ]
    
    OTHER_CONCERNS_OPTIONS = [
        "None of these", "Others (please specify)"
    ]
    
    @classmethod
    def validate_period_description(cls, value: str) -> str:
        """생리 상태 설명 검증"""
        if value not in cls.PERIOD_DESCRIPTION_OPTIONS:
            raise ValueError(f"Invalid period_description: {value}. Allowed options: {cls.PERIOD_DESCRIPTION_OPTIONS}")
        return value
    
    @classmethod
    def validate_cycle_length(cls, value: str) -> str:
        """생리 주기 길이 검증"""
        if value not in cls.CYCLE_LENGTH_OPTIONS:
            raise ValueError(f"Invalid cycle_length: {value}. Allowed options: {cls.CYCLE_LENGTH_OPTIONS}")
        return value
    
    @classmethod
    def validate_birth_control(cls, values: List[str]) -> List[str]:
        """피임 방법 검증"""
        for value in values:
            if value not in cls.BIRTH_CONTROL_OPTIONS:
                raise ValueError(f"Invalid birth_control option: {value}. Allowed options: {cls.BIRTH_CONTROL_OPTIONS}")
        return values
    
    @classmethod
    def validate_period_concerns(cls, values: List[str]) -> List[str]:
        """생리 관련 문제 검증"""
        for value in values:
            if value not in cls.PERIOD_CONCERNS_OPTIONS:
                raise ValueError(f"Invalid period_concern: {value}. Allowed options: {cls.PERIOD_CONCERNS_OPTIONS}")
        return values
    
    @classmethod
    def validate_body_concerns(cls, values: List[str]) -> List[str]:
        """신체 관련 문제 검증"""
        for value in values:
            if value not in cls.BODY_CONCERNS_OPTIONS:
                raise ValueError(f"Invalid body_concern: {value}. Allowed options: {cls.BODY_CONCERNS_OPTIONS}")
        return values
    
    @classmethod
    def validate_skin_hair_concerns(cls, values: List[str]) -> List[str]:
        """피부/모발 관련 문제 검증"""
        for value in values:
            if value not in cls.SKIN_HAIR_CONCERNS_OPTIONS:
                raise ValueError(f"Invalid skin_hair_concern: {value}. Allowed options: {cls.SKIN_HAIR_CONCERNS_OPTIONS}")
        return values
    
    @classmethod
    def validate_mental_health_concerns(cls, values: List[str]) -> List[str]:
        """정신 건강 관련 문제 검증"""
        for value in values:
            if value not in cls.MENTAL_HEALTH_CONCERNS_OPTIONS:
                raise ValueError(f"Invalid mental_health_concern: {value}. Allowed options: {cls.MENTAL_HEALTH_CONCERNS_OPTIONS}")
        return values
    
    @classmethod
    def validate_top_concern(cls, value: str) -> str:
        """최우선 문제 검증"""
        if value not in cls.TOP_CONCERN_OPTIONS:
            raise ValueError(f"Invalid top_concern: {value}. Allowed options: {cls.TOP_CONCERN_OPTIONS}")
        return value
    
    @classmethod
    def validate_diagnosed_conditions(cls, values: List[str]) -> List[str]:
        """진단된 질환 검증 - Others 텍스트 입력 허용"""
        for value in values:
            if value not in cls.DIAGNOSED_CONDITIONS_OPTIONS and not value.startswith("Others:"):
                raise ValueError(f"Invalid diagnosed_condition: {value}. Allowed options: {cls.DIAGNOSED_CONDITIONS_OPTIONS} or custom text starting with 'Others:'")
        return values
    
    @classmethod
    def validate_other_concerns(cls, values: List[str]) -> List[str]:
        """기타 문제 검증 - Others 텍스트 입력 허용"""
        for value in values:
            if value not in cls.OTHER_CONCERNS_OPTIONS and not value.startswith("Others:"):
                raise ValueError(f"Invalid other_concern: {value}. Allowed options: {cls.OTHER_CONCERNS_OPTIONS} or custom text starting with 'Others:'")
        return values 