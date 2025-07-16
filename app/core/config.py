from pydantic_settings import BaseSettings
from typing import List, Optional
import os


class Settings(BaseSettings):
    # 기본 설정
    PROJECT_NAME: str = "Auvra Backend API"
    PROJECT_DESCRIPTION: str = "Auvra Backend API Server"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    
    # 서버 설정
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False
    
    # Firebase 설정
    FIREBASE_PROJECT_ID: str = "your-firebase-project-id"
    FIREBASE_PRIVATE_KEY_ID: str = ""
    FIREBASE_PRIVATE_KEY: str = ""
    FIREBASE_CLIENT_EMAIL: str = ""
    FIREBASE_CLIENT_ID: str = ""
    FIREBASE_AUTH_DOMAIN: str = ""
    FIREBASE_STORAGE_BUCKET: str = ""
    FIREBASE_MESSAGING_SENDER_ID: str = ""
    FIREBASE_APP_ID: str = ""
    
    # CORS 설정
    ALLOWED_HOSTS: List[str] = ["*"]
    
    # 데이터베이스 설정
    DATABASE_URL: str = "postgresql://user:password@localhost/auvra_db"
    
    # Redis 설정
    REDIS_URL: str = "redis://localhost:6379"
    
    # 로깅 설정
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"
    
    # API 설정
    API_V1_STR: str = "/api/v1"
    
    # 파일 업로드 설정
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB
    UPLOAD_DIR: str = "uploads"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


# 환경별 설정
class DevelopmentSettings(Settings):
    DEBUG: bool = True
    LOG_LEVEL: str = "DEBUG"
    ENVIRONMENT: str = "development"
    # 개발 환경: 모든 호스트 허용, 보안 완화
    ALLOWED_HOSTS: List[str] = ["*", "localhost", "127.0.0.1", "0.0.0.0"]
    # 개발 환경: 환경변수 우선, 없으면 기본값
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/auvra_db")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")


class ProductionSettings(Settings):
    DEBUG: bool = False
    LOG_LEVEL: str = "WARNING"
    ENVIRONMENT: str = "production"
    # 프로덕션 환경: 특정 도메인만 허용
    ALLOWED_HOSTS: List[str] = ["your-domain.com", "api.your-domain.com"]
    # 프로덕션에서는 환경변수에서 가져와야 함
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/auvra_db")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    FIREBASE_PROJECT_ID: str = os.getenv("FIREBASE_PROJECT_ID", "")
    FIREBASE_PRIVATE_KEY: str = os.getenv("FIREBASE_PRIVATE_KEY", "")
    FIREBASE_CLIENT_EMAIL: str = os.getenv("FIREBASE_CLIENT_EMAIL", "")


# 환경에 따른 설정 선택
def get_settings() -> Settings:
    environment = os.getenv("ENVIRONMENT", "development")
    
    if environment == "production":
        return ProductionSettings()
    else:
        return DevelopmentSettings()


settings = get_settings() 