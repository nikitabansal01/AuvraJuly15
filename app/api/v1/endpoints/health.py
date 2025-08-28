from fastapi import APIRouter, Depends
from app.core.config import settings
from datetime import datetime

router = APIRouter()


@router.get("/")
async def health_check():
    """애플리케이션 상태를 확인합니다."""
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "version": settings.VERSION
    }


@router.get("/detailed")
async def detailed_health_check():
    """상세한 애플리케이션 상태를 확인합니다."""
    return {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "version": settings.VERSION,
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