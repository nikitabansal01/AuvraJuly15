from fastapi import APIRouter

from app.api.v1.endpoints import health, users, auth, questions
from app.api.v1.endpoints import ai
from app.api.v1.endpoints import rag
from app.api.v1.endpoints import hybrid_search
from app.api.v1.endpoints import scheduling
from app.api.v1.endpoints import progress
from app.api.v1.endpoints import new_scheduling
from app.api.v1.endpoints import cycle

api_router = APIRouter()

# Health check router
api_router.include_router(health.router, prefix="/health", tags=["health"])

# Authentication router
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])

# User management router
api_router.include_router(users.router, prefix="/users", tags=["users"])

# Question and session router
api_router.include_router(questions.router, prefix="/questions", tags=["questions"])

# AI recommendation router (web/app integration)
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])

# RAG pipeline router
api_router.include_router(rag.router, prefix="/rag", tags=["rag"])

# Hybrid search router (BM25 + Pinecone)
api_router.include_router(hybrid_search.router, prefix="/hybrid-search", tags=["hybrid-search"])

# Scheduling router
api_router.include_router(scheduling.router, prefix="/scheduling", tags=["scheduling"])

# Progress tracking router
api_router.include_router(progress.router, prefix="/progress", tags=["progress"])

# New scheduling router (timezone-based)
api_router.include_router(new_scheduling.router, prefix="/new-scheduling", tags=["new-scheduling"])

# Menstrual cycle router
api_router.include_router(cycle.router, prefix="/cycle", tags=["cycle"]) 