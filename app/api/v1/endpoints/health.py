from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.database import check_database_connection
import logging
import os

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("")
@router.get("/")
async def health_check():
    """Lightweight liveness probe; intentionally performs no dependency I/O."""
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "version": settings.VERSION,
        # Render sets this env var automatically for builds; safe to expose.
        "render_git_commit": os.getenv("RENDER_GIT_COMMIT"),
        "render_service_id": os.getenv("RENDER_SERVICE_ID"),
    }


@router.get("/ready")
def database_readiness_check():
    """Report readiness only when the application database answers a query."""
    try:
        check_database_connection()
    except Exception:
        logger.exception("Database readiness probe failed")
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": "unavailable"},
        )

    return {"status": "ready", "database": "available"}


@router.get("/detailed")
async def detailed_health_check():
    """Check detailed application health status."""
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "version": settings.VERSION,
        "render_git_commit": os.getenv("RENDER_GIT_COMMIT"),
        "render_service_id": os.getenv("RENDER_SERVICE_ID"),
        "debug": settings.DEBUG,
        "host": settings.HOST,
        "port": settings.PORT
    }

@router.get("/db-pool")
async def db_pool_status():
    """Database connection pool status (development only)"""
    from app.core.database import engine
    
    if settings.ENVIRONMENT != "development":
        return {"error": "This endpoint is only available in development mode"}
    
    try:
        pool_size = engine.pool.size()
        checked_out = engine.pool.checkedout()
        overflow = engine.pool.overflow()
        checked_in = engine.pool.checkedin()
        
        return {
            "pool_size": pool_size,
            "checked_out": checked_out,
            "checked_in": checked_in,
            "overflow": overflow,
            "total_connections": checked_out + checked_in,
            "max_connections": pool_size + engine.pool._max_overflow,
            "utilization_percent": round((checked_out / (pool_size + engine.pool._max_overflow)) * 100, 2)
        }
    except Exception as e:
        return {"error": f"Failed to get pool status: {str(e)}"}
