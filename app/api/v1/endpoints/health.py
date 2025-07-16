from fastapi import APIRouter, Depends
from app.core.config import settings

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