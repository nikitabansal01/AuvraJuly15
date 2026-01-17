"""
Database Helper Functions for LangGraph Nodes
Provides convenient access to user data, cycle info, streaks, etc.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.core.database import get_db, UserProfile, SymptomLog, ActionPlan, ActionPlanItem
from app.services.cycle_service import CycleService
from app.services.streak_service import StreakService
from app.services.reward_service import RewardService


def get_user_profile(user_id: str, db: Session) -> Optional[UserProfile]:
    """Get user profile by ID."""
    return db.query(UserProfile).filter(UserProfile.uid == user_id).first()


def get_cycle_info(user_id: str, db: Session) -> Dict[str, Any]:
    """
    Get current cycle information for user.
    
    Returns:
        Dict with cycle_day, phase, primary_hormone, secondary_hormones
    """
    profile = get_user_profile(user_id, db)
    if not profile:
        return {
            "cycle_day": None,
            "phase": None,
            "primary_hormone": None,
            "secondary_hormones": []
        }
    
    cycle_service = CycleService()
    cycle_info = cycle_service.get_cycle_info(profile.to_dict())
    
    return {
        "cycle_day": cycle_info.get("cycle_day"),
        "phase": cycle_info.get("phase"),
        "primary_hormone": cycle_info.get("primary_hormone"),
        "secondary_hormones": cycle_info.get("secondary_hormones", [])
    }


def get_streak_info(user_id: str, db: Session) -> Dict[str, Any]:
    """
    Get user's current streak information.
    
    Returns:
        Dict with current_streak, longest_streak, freeze_count, etc.
    """
    streak_service = StreakService(db)
    return streak_service.get_full_streak_status(user_id)


def get_recent_symptoms(user_id: str, days: int, db: Session) -> List[Dict[str, Any]]:
    """Get recent symptom logs."""
    cutoff_date = datetime.utcnow() - timedelta(days=days)
   
    symptoms = db.query(SymptomLog).filter(
        SymptomLog.user_id == user_id,
        SymptomLog.logged_at >= cutoff_date
    ).order_by(SymptomLog.logged_at.desc()).all()
    
    return [
        {
            "id": s.id,
            "type": s.symptom_type,
            "severity": s.severity,
            "logged_at": s.logged_at.isoformat(),
            "triggers": s.triggers,
            "notes": s.notes,
            "cycle_day": s.cycle_day,
            "phase": s.phase
        }
        for s in symptoms
    ]


def get_todays_action_plan(user_id: str, db: Session) -> Optional[Dict[str, Any]]:
    """Get today's action plan with all items."""
    from app.services.action_plan_generator import get_user_current_date
    
    today = get_user_current_date(user_id, db)
    
    plan = db.query(ActionPlan).filter(
        ActionPlan.uid == user_id,
        ActionPlan.plan_date == today
    ).first()
    
    if not plan:
        return None
    
    items = db.query(ActionPlanItem).filter(
        ActionPlanItem.plan_id == plan.id,
        ActionPlanItem.is_replaced == False
    ).all()
    
    return {
        "plan_id": plan.id,
        "plan_date": plan.plan_date.isoformat(),
        "primary_hormone": plan.primary_hormone,
        "items": [
            {
                "id": item.id,
                "slot": item.slot,
                "title": item.title,
                "category": item.category,
                "specific_action": item.specific_action,
                "purpose": item.purpose,
                "target_hormone": item.target_hormone,
                "is_completed": item.is_completed,
                "is_replaced": item.is_replaced,
                "time_slot": item.time_slot
            }
            for item in items
        ]
    }


def get_action_completion_stats(user_id: str, days: int, db: Session) -> Dict[str, int]:
    """Get action plan completion statistics."""
    from app.services.action_plan_generator import get_user_current_date
    
    today = get_user_current_date(user_id, db)
    cutoff_date = today - timedelta(days=days)
    
    plans = db.query(ActionPlan).filter(
        ActionPlan.uid == user_id,
        ActionPlan.plan_date >= cutoff_date,
        ActionPlan.plan_date <= today
    ).all()
    
    total_actions = 0
    completed_actions = 0
    
    for plan in plans:
        items = db.query(ActionPlanItem).filter(
            ActionPlanItem.plan_id == plan.id,
            ActionPlanItem.is_replaced == False
        ).all()
        
        total_actions += len(items)
        completed_actions += sum(1 for item in items if item.is_completed)
    
    return {
        "total": total_actions,
        "completed": completed_actions,
        "completion_rate": completed_actions / max(total_actions, 1)
    }


def get_reward_status(user_id: str, db: Session) -> Dict[str, Any]:
    """Get user's reward unlock status."""
    reward_service = RewardService(db)
    
    # Check which rewards are unlocked
    unlocked_rewards = reward_service.get_unlocked_rewards(user_id)
    
    # Check refresh token availability (16-day streak)
    streak_info = get_streak_info(user_id, db)
    current_streak = streak_info["current_streak"]
    
    refresh_unlocked = current_streak >= 16
    
    # Get today's refresh usage if unlocked
    refresh_tokens_available = 0
    if refresh_unlocked:
        from app.services.action_plan_generator import get_user_current_date
        today = get_user_current_date(user_id, db)
        
        # Check usage (max 2 per day)
        # TODO: Query ActionPlanRefreshLog table
        refresh_tokens_available = 2  # Placeholder
    
    return {
        "unlocked_rewards": unlocked_rewards,
        "current_streak": current_streak,
        "refresh_tokens_unlocked": refresh_unlocked,
        "refresh_tokens_available": refresh_tokens_available
    }
