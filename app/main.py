"""
AUVRA Backend Application
FastAPI server for women's health recommendations
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
import time
import logging
from contextlib import asynccontextmanager

from app.core.config import settings
from app.api.v1.api import api_router
from app.core.logging import setup_logging
from app.core.firebase import initialize_firebase
from app.core.rate_limiter import get_rate_limiter, custom_rate_limit_handler

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown events."""
    
    # Startup
    logger.info("Starting AUVRA application...")
    
    # Initialize database. This is deliberately fail-closed: migrations run in
    # Render's start command, and the server must not accept traffic unless the
    # configured database connections are reachable.
    try:
        from app.core.database import initialize_database
        initialize_database()
        logger.info("Database connections verified")
    except Exception:
        logger.exception("Database initialization failed; aborting startup")
        raise
    
    # Initialize Firebase
    try:
        initialize_firebase()
        logger.info("Firebase initialized")
    except Exception as e:
        logger.warning(f"Firebase initialization failed: {e}")
    
    logger.info("AUVRA application started successfully")
    
    yield
    
    # Shutdown - clean up resources
    try:
        from app.langgraph.helpers.llm_cache import close_redis_client
        await close_redis_client()
        logger.info("Redis connections closed")
    except Exception as e:
        logger.warning(f"Redis shutdown error (non-fatal): {e}")

    try:
        from app.langgraph.graphs.care_plan_checkin import close_care_plan_graph_runtime
        await close_care_plan_graph_runtime()
        logger.info("Care plan graph runtime closed")
    except Exception as e:
        logger.warning(f"Care plan graph shutdown error (non-fatal): {e}")
    
    logger.info("AUVRA application shutdown")


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
    
    # ════════════════════════════════════════════════════════════════════════
    # RATE LIMITING - Initialize slowapi with Redis backend
    # ════════════════════════════════════════════════════════════════════════
    limiter = get_rate_limiter()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, custom_rate_limit_handler)
    logger.info(f"[RATE_LIMIT] Initialized with storage: {getattr(limiter, '_storage_uri', 'redis')}")

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    # Trusted Host middleware (production only)
    if settings.ENVIRONMENT == "production":
        allowed_hosts = settings.ALLOWED_HOSTS
        if isinstance(allowed_hosts, str):
            import json
            try:
                allowed_hosts = json.loads(allowed_hosts)
            except json.JSONDecodeError:
                allowed_hosts = [allowed_hosts]
        
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=allowed_hosts,
        )

    # Request logging middleware
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Log errors with more detail
        if response.status_code >= 400:
            logger.warning(
                f"{request.method} {request.url.path} - "
                f"Status: {response.status_code} - Time: {process_time:.3f}s"
            )
        else:
            logger.debug(
                f"{request.method} {request.url.path} - "
                f"Status: {response.status_code} - Time: {process_time:.3f}s"
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

    # Register routers
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health")
    @app.head("/health")
    async def health_check():
        """Health check endpoint for monitoring."""
        return {
            "status": "healthy",
            "environment": settings.ENVIRONMENT,
            "version": settings.VERSION
        }
    
    @app.get("/")
    @app.head("/")
    async def root():
        """Root path - handles Render health checks."""
        return {"status": "ok", "service": "AUVRA API"}

    return app


app = create_application()
