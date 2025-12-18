from fastapi import APIRouter

from app.api.v1.endpoints import health, users, auth, questions
from app.api.v1.endpoints import ai
from app.api.v1.endpoints import rag
from app.api.v1.endpoints import hybrid_search
from app.api.v1.endpoints import scheduling
from app.api.v1.endpoints import progress
from app.api.v1.endpoints import new_scheduling
from app.api.v1.endpoints import action_plan  # New action plan system
from app.api.v1.endpoints import cycle
from app.api.v1.endpoints import chat

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

# Scheduling router (legacy)
api_router.include_router(scheduling.router, prefix="/scheduling", tags=["scheduling"])

# Progress tracking router
api_router.include_router(progress.router, prefix="/progress", tags=["progress"])

# New scheduling router - NOW USES ACTION PLAN SYSTEM
# Keeping same path for backward compatibility
api_router.include_router(action_plan.router, prefix="/new-scheduling", tags=["action-plan"])

# Legacy new_scheduling (disabled - replaced by action_plan)
# api_router.include_router(new_scheduling.router, prefix="/new-scheduling-legacy", tags=["new-scheduling-legacy"])

# Menstrual cycle router
api_router.include_router(cycle.router, prefix="/cycle", tags=["cycle"])

# AI Chatbot router (LangGraph-powered)
api_router.include_router(chat.router, prefix="/chat", tags=["chat"]) 