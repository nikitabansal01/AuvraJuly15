from pydantic_settings import BaseSettings
from typing import List, Optional
import os


class Settings(BaseSettings):
    # Basic settings
    PROJECT_NAME: str = "Auvra Backend API"
    PROJECT_DESCRIPTION: str = "Auvra Backend API Server"
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    
    # Server settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False
    
    # Firebase settings
    FIREBASE_PROJECT_ID: str = "your-firebase-project-id"
    FIREBASE_PRIVATE_KEY_ID: str = ""
    FIREBASE_PRIVATE_KEY: str = ""
    FIREBASE_CLIENT_EMAIL: str = ""
    FIREBASE_CLIENT_ID: str = ""
    FIREBASE_AUTH_DOMAIN: str = ""
    FIREBASE_STORAGE_BUCKET: str = ""
    FIREBASE_MESSAGING_SENDER_ID: str = ""
    FIREBASE_APP_ID: str = ""
    
    # CORS settings
    ALLOWED_HOSTS: List[str] = ["*"]
    
    # Database settings
    DATABASE_URL: str = "postgresql://user:password@localhost/auvra_db"
    
    # RAG settings
    FIRECRAWL_API_KEY: str = ""
    FIRECRAWL_BASE_URL: str = "https://api.firecrawl.dev/v0/scrape"
    
    # Pinecone settings
    PINECONE_API_KEY: str = ""
    PINECONE_ENVIRONMENT: str = ""
    PINECONE_INDEX: str = ""
    
    # OpenAI settings
    OPENAI_API_KEY: str = ""
    
    # Groq settings
    GROQ_API_KEY: str = ""
    
    # Redis settings
    REDIS_URL: str = "redis://localhost:6379"
    
    # Logging settings
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"
    
    # API settings
    API_V1_STR: str = "/api/v1"
    
    # File upload settings
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB
    UPLOAD_DIR: str = "uploads"
    
    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "env_file_encoding": "utf-8",
        "extra": "ignore"  # Ignore additional fields
    }


# Environment-specific settings
class DevelopmentSettings(Settings):
    DEBUG: bool = True
    LOG_LEVEL: str = "DEBUG"
    ENVIRONMENT: str = "development"
    # Development environment: allow all hosts, relaxed security
    ALLOWED_HOSTS: List[str] = ["*", "localhost", "127.0.0.1", "0.0.0.0"]


class ProductionSettings(Settings):
    DEBUG: bool = False
    LOG_LEVEL: str = "WARNING"
    ENVIRONMENT: str = "production"
    # Production environment: allow all hosts (for Render)
    ALLOWED_HOSTS: List[str] = ["*"]


# Select settings based on environment
def get_settings() -> Settings:
    environment = os.getenv("ENVIRONMENT", "development")
    
    if environment == "production":
        return ProductionSettings()
    else:
        return DevelopmentSettings()


settings = get_settings() 