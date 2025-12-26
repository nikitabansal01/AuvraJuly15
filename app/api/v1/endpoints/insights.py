"""
Insights API - Analytics and patterns for users.

Provides endpoints for:
- Symptom patterns (requires 14-day streak reward)
- Action completion trends
- Progress analytics
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, extract
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import date, datetime, timedelta

from app.core.database import get_db, ActionPlanItem, ActionPlanFeedback
from app.api.v1.endpoints.auth import get_current_user
from app.services.reward_service import RewardService

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class CategoryStats(BaseModel):
    category: str
    total: int
    completed: int
    liked: int
    disliked: int
    completion_rate: float


class WeeklyTrend(BaseModel):
    week_start: str
    food_completed: int
    movement_completed: int
    mindfulness_completed: int
    total_completed: int


class SymptomPatternResponse(BaseModel):
    period_days: int  # Days of data analyzed
    category_breakdown: List[CategoryStats]
    weekly_trends: List[WeeklyTrend]
    top_liked_categories: List[str]
    insights: List[str]  # AI-generated insights


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/symptom-patterns", response_model=SymptomPatternResponse)
async def get_symptom_patterns(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get symptom patterns analytics.
    
    Requires: symptom_patterns reward to be claimed (14-day streak)
    
    Returns:
    - Category breakdown (food/movement/mindfulness stats)
    - Weekly trends
    - AI-generated insights
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    # Check if reward is unlocked
    reward_service = RewardService(db)
    if not reward_service.is_reward_unlocked(uid, "symptom_patterns"):
        raise HTTPException(
            status_code=403,
            detail="This feature requires the 'Symptom Patterns' reward (14-day streak) to be claimed"
        )
    
    # Get all action items for this user
    end_date = date.today()
    start_date = end_date - timedelta(days=30)
    
    items = db.query(ActionPlanItem).filter(
        and_(
            ActionPlanItem.uid == uid,
            ActionPlanItem.plan_date >= start_date,
            ActionPlanItem.plan_date <= end_date
        )
    ).all()
    
    if not items:
        return SymptomPatternResponse(
            period_days=30,
            category_breakdown=[],
            weekly_trends=[],
            top_liked_categories=[],
            insights=["Start completing actions to see your patterns!"]
        )
    
    # Calculate category breakdown
    categories = {}
    for item in items:
        cat = item.category or "unknown"
        if cat not in categories:
            categories[cat] = {"total": 0, "completed": 0, "liked": 0, "disliked": 0}
        categories[cat]["total"] += 1
        if item.is_completed:
            categories[cat]["completed"] += 1
    
    # Get feedback for items
    item_ids = [item.id for item in items]
    feedbacks = db.query(ActionPlanFeedback).filter(
        ActionPlanFeedback.item_id.in_(item_ids)
    ).all()
    
    feedback_map = {f.item_id: f for f in feedbacks}
    for item in items:
        cat = item.category or "unknown"
        if item.id in feedback_map:
            fb = feedback_map[item.id]
            if fb.feedback_type == "like":
                categories[cat]["liked"] += 1
            elif fb.feedback_type == "dislike":
                categories[cat]["disliked"] += 1
    
    category_stats = []
    for cat, stats in categories.items():
        completion_rate = (stats["completed"] / stats["total"] * 100) if stats["total"] > 0 else 0
        category_stats.append(CategoryStats(
            category=cat,
            total=stats["total"],
            completed=stats["completed"],
            liked=stats["liked"],
            disliked=stats["disliked"],
            completion_rate=round(completion_rate, 1)
        ))
    
    # Sort by total (most frequent categories first)
    category_stats.sort(key=lambda x: x.total, reverse=True)
    
    # Calculate weekly trends
    weekly_trends = []
    for week_offset in range(4):
        week_end = end_date - timedelta(days=7 * week_offset)
        week_start = week_end - timedelta(days=6)
        
        week_items = [i for i in items if week_start <= i.plan_date <= week_end and i.is_completed]
        
        food = len([i for i in week_items if i.category == "food"])
        movement = len([i for i in week_items if i.category == "movement"])
        mindfulness = len([i for i in week_items if i.category == "mindfulness"])
        
        weekly_trends.append(WeeklyTrend(
            week_start=week_start.isoformat(),
            food_completed=food,
            movement_completed=movement,
            mindfulness_completed=mindfulness,
            total_completed=food + movement + mindfulness
        ))
    
    weekly_trends.reverse()  # Oldest first
    
    # Top liked categories
    top_liked = sorted(
        [(cat, stats["liked"]) for cat, stats in categories.items()],
        key=lambda x: x[1],
        reverse=True
    )
    top_liked_categories = [cat for cat, _ in top_liked[:3] if _ > 0]
    
    # Generate insights
    insights = []
    
    # Total completion rate
    total_items = len(items)
    total_completed = len([i for i in items if i.is_completed])
    overall_rate = (total_completed / total_items * 100) if total_items > 0 else 0
    
    if overall_rate >= 80:
        insights.append(f"🌟 Amazing! You completed {round(overall_rate)}% of your actions this month!")
    elif overall_rate >= 50:
        insights.append(f"👍 Good progress! {round(overall_rate)}% completion rate. Keep going!")
    else:
        insights.append(f"💪 You completed {round(overall_rate)}% of actions. Every small step counts!")
    
    # Category-specific insights
    for stat in category_stats:
        if stat.completion_rate >= 80 and stat.total >= 5:
            insights.append(f"✨ You're crushing {stat.category} actions with {stat.completion_rate}% completion!")
        elif stat.liked > stat.disliked and stat.liked >= 3:
            insights.append(f"❤️ You seem to love {stat.category} actions!")
    
    # Trend insight
    if len(weekly_trends) >= 2:
        recent = weekly_trends[-1].total_completed
        previous = weekly_trends[-2].total_completed
        if recent > previous:
            insights.append(f"📈 You're completing more actions this week than last week!")
        elif recent < previous and previous > 0:
            insights.append(f"📊 A bit quieter this week - that's okay, consistency matters more than perfection!")
    
    return SymptomPatternResponse(
        period_days=30,
        category_breakdown=category_stats,
        weekly_trends=weekly_trends,
        top_liked_categories=top_liked_categories,
        insights=insights[:5]  # Max 5 insights
    )


@router.get("/summary")
async def get_quick_summary(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get quick summary stats (no reward required).
    
    Returns basic stats like total completions and streak.
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    # Get completed count
    completed = db.query(func.count(ActionPlanItem.id)).filter(
        and_(
            ActionPlanItem.uid == uid,
            ActionPlanItem.is_completed == True
        )
    ).scalar() or 0
    
    # Get streak
    from app.services.streak_service import StreakService
    streak_service = StreakService(db)
    streak = streak_service.calculate_streak_from_actions(uid)
    longest = streak_service.get_longest_streak(uid)
    
    return {
        "total_completed": completed,
        "current_streak": streak,
        "longest_streak": longest
    }
