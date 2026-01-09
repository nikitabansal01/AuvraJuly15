from fastapi import APIRouter

from app.api.v1.endpoints import health, users, auth, questions
from app.api.v1.endpoints import ai
from app.api.v1.endpoints import rag
from app.api.v1.endpoints import progress
from app.api.v1.endpoints import action_plan  # New action plan system
from app.api.v1.endpoints import cycle
from app.api.v1.endpoints import chat
from app.api.v1.endpoints import rewards
from app.api.v1.endpoints import preferences
from app.api.v1.endpoints import insights
from app.api.v1.endpoints import timezone  # Timezone management
from app.api.v1.endpoints import weekly_checkin  # Weekly check-in system
from app.api.v1.endpoints import care_plan_checkin  # Daily care plan check-in
from app.api.v1.endpoints import symptom_checkin  # Daily symptom check-in
from app.api.v1.endpoints import personalization  # 2026 Personalization Vision

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

# Progress tracking router
api_router.include_router(progress.router, prefix="/progress", tags=["progress"])

# Action Plan router (new scheduling system)
# Keeping /new-scheduling path for backward compatibility with mobile app
api_router.include_router(action_plan.router, prefix="/new-scheduling", tags=["action-plan"])

# Menstrual cycle router
api_router.include_router(cycle.router, prefix="/cycle", tags=["cycle"])

# AI Chatbot router (LangGraph-powered)
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])

# Rewards router (streak-based rewards system)
api_router.include_router(rewards.router, prefix="/rewards", tags=["rewards"])

# Preferences router (gated by rewards - diet, allergies, etc.)
api_router.include_router(preferences.router, prefix="/preferences", tags=["preferences"])

# Insights router (analytics - symptom patterns, etc.)
api_router.include_router(insights.router, prefix="/insights", tags=["insights"])

# Timezone router (timezone management for users)
api_router.include_router(timezone.router, prefix="/timezone", tags=["timezone"])

# Weekly Check-in router (structured symptom check-ins)
api_router.include_router(weekly_checkin.router, prefix="/weekly-checkin", tags=["weekly-checkin"]) 

# Care Plan Check-in router (daily threaded chat)
api_router.include_router(care_plan_checkin.router, prefix="/care-plan-checkin", tags=["care-plan-checkin"])

# Symptom Check-in router (daily threaded chat)
api_router.include_router(symptom_checkin.router, prefix="/symptom-checkin", tags=["symptom-checkin"])

# Personalization router (2026 Vision - profile summary, discovery prompts)
api_router.include_router(personalization.router, prefix="/personalization", tags=["personalization"])