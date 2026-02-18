from pydantic_settings import BaseSettings
from typing import List, Optional
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


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
    
    # Gemini LLM settings (for "Others" text processing)
    GEMINI_API_KEY: str = ""
    ENABLE_LLM_OTHERS: bool = True
    LLM_OTHERS_TIMEOUT: int = 30
    
    # Redis settings
    REDIS_URL: str = "redis://localhost:6379"
    
    # Logging settings
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"
    
    # API settings
    API_V1_STR: str = "/api/v1"

    # Production hardening flags (2026 rollout controls)
    FEATURE_GRAPH_UNIFICATION: bool = False
    FEATURE_FRONTEND_ENGINE_V2: bool = False
    FEATURE_STRICT_CONTRACT_MODE: bool = False

    # LangGraph checkpointing defaults
    LANGGRAPH_CHECKPOINT_POSTGRES_DSN: str = ""
    LANGGRAPH_CHECKPOINT_SQLITE_PATH: str = ".langgraph/checkpoints/langgraph.sqlite"

    # Care-plan endpoint guardrails / alert thresholds
    CARE_PLAN_EVENT_ERROR_RATE_ALERT_THRESHOLD: float = 0.05
    CARE_PLAN_EVENT_P95_LATENCY_MS_ALERT_THRESHOLD: int = 2500
    
    # File upload settings
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB
    UPLOAD_DIR: str = "uploads"
    
    # Action Plan Generation Settings 
    ACTION_PLAN_MAX_RETRIES: int = 2
    ACTION_PLAN_TIMEOUT: int = 120
    PUBMED_RATE_LIMIT_DELAY: float = 0.5
    # Image similarity threshold for cache matching
    # 0.85 provides good balance: allows ~10-12 cache hits vs 2 at 0.90
    # Higher = stricter matches, more regeneration
    # Lower = more cache hits but potentially less accurate matches
    IMAGE_SIMILARITY_THRESHOLD: float = 0.85
    FEEDBACK_SUMMARIZE_THRESHOLD: int = 100
    
    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # Ignore additional fields
        # Allow fields with prefix model_ without warnings from Pydantic
        "protected_namespaces": ()
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
    LOG_LEVEL: str = "INFO"  # Changed from WARNING to INFO to see RAG debug logs
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
