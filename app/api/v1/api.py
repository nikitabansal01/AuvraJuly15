from fastapi import APIRouter

from app.api.v1.endpoints import health, users, auth, questions

api_router = APIRouter()

# 헬스체크 라우터
api_router.include_router(health.router, prefix="/health", tags=["health"])

# 인증 라우터
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])

# 사용자 라우터
api_router.include_router(users.router, prefix="/users", tags=["users"])

# 질문 라우터
api_router.include_router(questions.router, prefix="/questions", tags=["questions"]) 