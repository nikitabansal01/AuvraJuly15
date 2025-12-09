from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
import time
import logging
from contextlib import asynccontextmanager

from app.core.config import settings
from app.api.v1.api import api_router
from app.core.logging import setup_logging
from app.core.firebase import initialize_firebase

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Application startup
    logger.info("Application started.")
    
    # Database initialization
    try:
        from app.core.database import create_tables
        create_tables()
        logger.info("Database tables created successfully.")
    except Exception as e:
        logger.warning(f"Database initialization failed: {e}")
    
    # Firebase initialization
    try:
        initialize_firebase()
        logger.info("Firebase initialized successfully.")
    except Exception as e:
        logger.warning(f"Firebase initialization failed: {e}")
    
    # ========================================
    # RAG Module Diagnostic - Test if RAG loads
    # ========================================
    logger.info("=" * 60)
    logger.info("🔬 RAG MODULE DIAGNOSTIC")
    logger.info("=" * 60)
    
    try:
        from app.services.rag.rag_orchestrator import generate_rag_recommendations
        if generate_rag_recommendations is not None:
            logger.info("✅ RAG: generate_rag_recommendations LOADED SUCCESSFULLY")
        else:
            logger.error("❌ RAG: generate_rag_recommendations is None (load failed)")
    except ImportError as e:
        logger.error(f"❌ RAG IMPORT FAILED: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
    except Exception as e:
        logger.error(f"❌ RAG EXCEPTION: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
    
    # Test Pinecone environment variables
    import os
    pinecone_key = os.getenv("PINECONE_API_KEY")
    pinecone_index = os.getenv("PINECONE_INDEX")
    openai_key = os.getenv("OPENAI_API_KEY")
    
    logger.info(f"🔧 PINECONE_API_KEY: {'SET (' + pinecone_key[:8] + '...)' if pinecone_key else 'NOT SET'}")
    logger.info(f"🔧 PINECONE_INDEX: {pinecone_index or 'NOT SET'}")
    logger.info(f"🔧 OPENAI_API_KEY: {'SET' if openai_key else 'NOT SET'}")
    logger.info("=" * 60)
    
    yield
    # Application shutdown
    logger.info("Application shutdown.")


def create_application() -> FastAPI:
    """Create FastAPI application instance."""
    
    app = FastAPI(
        title=settings.PROJECT_NAME,
        description=settings.PROJECT_DESCRIPTION,
        version=settings.VERSION,
        docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
        redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
        lifespan=lifespan,
    )

    # CORS middleware configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 모든 origin 허용
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    # Trusted Host middleware (security) - disabled in development
    if settings.ENVIRONMENT == "production":
        # Convert ALLOWED_HOSTS to list if it's a string
        allowed_hosts = settings.ALLOWED_HOSTS
        if isinstance(allowed_hosts, str):
            import json
            try:
                allowed_hosts = json.loads(allowed_hosts)
            except:
                allowed_hosts = [allowed_hosts]
        
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=allowed_hosts,
        )

    # Request logging middleware
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start_time = time.time()
        
        # Basic logging for all requests
        logger.info(f"=== Request Received ===")
        logger.info(f"URL: {request.url.path}")
        logger.info(f"Method: {request.method}")
        logger.info(f"Headers: {dict(request.headers)}")
        logger.info(f"Client IP: {request.client.host if request.client else 'Unknown'}")
        
        # Request body logging for debugging 400 errors - removed body reading
        if request.method == "POST" and "/link" in request.url.path:
            logger.info(f"=== Request Logging ===")
            logger.info(f"URL: {request.url.path}")
            logger.info(f"Headers: {dict(request.headers)}")
            logger.info(f"Content-Type: {request.headers.get('content-type', 'None')}")
        
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Detailed logging for 400 errors
        if response.status_code == 400:
            logger.error(f"=== 400 Error Occurred ===")
            logger.error(f"URL: {request.url.path}")
            logger.error(f"Method: {request.method}")
            logger.error(f"Status: {response.status_code}")
        
        logger.info(
            f"{request.method} {request.url.path} - "
            f"Status: {response.status_code} - "
            f"Process Time: {process_time:.4f}s"
        )
        
        return response

    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(f"Global exception occurred: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error occurred."}
        )

    # API router registration
    app.include_router(api_router, prefix="/api/v1")

    # Health check endpoint
    @app.get("/health")
    async def health_check():
        return {
            "status": "healthy",
            "environment": settings.ENVIRONMENT,
            "version": settings.VERSION
        }

    return app


app = create_application() 