"""
Unified Streak Service - SINGLE SOURCE OF TRUTH
All streak calculations must go through this service.

Based on Duolingo model: 1+ action completed per day = streak maintained.
"""
import logging
from datetime import date, timedelta, datetime
from typing import Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.core.database import ActionPlanItem, ActionPlan, UserStreakData, UserReward

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# REWARDS CONFIGURATION - Single source of truth
# ═══════════════════════════════════════════════════════════════════════════════

REWARDS_CONFIG = [
    {"id": "streak_freeze", "days": 3, "title": "Streak freeze", "category": "seed", "effect": "freeze_token", "icon": "🧊"},
    {"id": "diet_prefs", "days": 7, "title": "Diet preferences", "category": "seed", "effect": "personalization", "icon": "🥗"},
    {"id": "food_allergies", "days": 8, "title": "Food Allergies", "category": "seed", "effect": "personalization", "icon": "🥜"},
    {"id": "cuisine_prefs", "days": 12, "title": "Cuisine preferences", "category": "seed", "effect": "personalization", "icon": "🥘"},
    {"id": "symptom_patterns", "days": 14, "title": "Symptom patterns", "category": "seed", "effect": "insight", "icon": "✨"},
    {"id": "dine_out", "days": 14, "title": "Dine out habits", "category": "seed", "effect": "personalization", "icon": "🍔"},
    {"id": "plan_refresh_2x", "days": 16, "title": "2x plan refresh", "category": "rise", "effect": "refresh_token", "icon": "🧊"},
    {"id": "ethnicity", "days": 18, "title": "Ethnicity/cultural habits", "category": "rise", "effect": "personalization", "icon": "🌏"},
    {"id": "bmi_ratio", "days": 18, "title": "BMI/Waist ratio", "category": "rise", "effect": "personalization", "icon": "⚖️"},
    {"id": "cravings_healthy", "days": 18, "title": "Cravings made healthy", "category": "rise", "effect": "personalization", "icon": "🥮"},
    {"id": "first_improvement", "days": 21, "title": "First signs of improvement", "category": "rise", "effect": "badge", "icon": "✨"},
]


class StreakService:
    """
    Unified streak calculation and management.
    
    All streak-related operations go through this service to ensure consistency.
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_or_create_streak_data(self, uid: str) -> UserStreakData:
        """Get or create user's streak data record."""
        streak_data = self.db.query(UserStreakData).filter(
            UserStreakData.uid == uid
        ).first()
        
        if not streak_data:
            streak_data = UserStreakData(
                uid=uid, 
                current_streak=0, 
                longest_streak=0, 
                freeze_count=0
            )
            self.db.add(streak_data)
            self.db.commit()
            self.db.refresh(streak_data)
            logger.info(f"Created new streak data for user {uid}")
        
        return streak_data
    
    def calculate_streak_from_actions(self, uid: str) -> int:
        """
        Calculate streak based on ActionPlanItem completions.
        
        Following Duolingo model: 1+ action completed per day = streak day.
        
        Args:
            uid: User ID
            
        Returns:
            Current consecutive day streak count
        """
        streak = 0
        check_date = date.today()
        
        # Get streak data for freeze check
        streak_data = self.get_or_create_streak_data(uid)
        
        while True:
            # Check if ANY action was completed on this date
            completed_count = self.db.query(func.count(ActionPlanItem.id)).join(
                ActionPlan, ActionPlanItem.plan_id == ActionPlan.id
            ).filter(
                and_(
                    ActionPlan.uid == uid,
                    ActionPlanItem.is_completed == True,
                    func.date(ActionPlanItem.completed_at) == check_date
                )
            ).scalar()
            
            if completed_count and completed_count > 0:
                streak += 1
                check_date -= timedelta(days=1)
            else:
                # Check if freeze was used on this date
                if streak_data.freeze_used_date == check_date:
                    # Freeze covered this day, continue checking
                    check_date -= timedelta(days=1)
                else:
                    # No completion, no freeze - streak ends
                    break
        
        return streak
    
    def get_longest_streak(self, uid: str) -> int:
        """
        Calculate longest ever streak from completion history.
        
        Args:
            uid: User ID
            
        Returns:
            Longest consecutive day streak ever achieved
        """
        # Get all distinct completion dates
        completion_dates = self.db.query(
            func.date(ActionPlanItem.completed_at).label('completion_date')
        ).join(
            ActionPlan, ActionPlanItem.plan_id == ActionPlan.id
        ).filter(
            and_(
                ActionPlan.uid == uid,
                ActionPlanItem.is_completed == True,
                ActionPlanItem.completed_at.isnot(None)
            )
        ).distinct().order_by('completion_date').all()
        
        if not completion_dates:
            return 0
        
        dates = [c.completion_date for c in completion_dates if c.completion_date]
        if not dates:
            return 0
        
        longest = current = 1
        for i in range(1, len(dates)):
            if (dates[i] - dates[i-1]).days == 1:
                current += 1
                longest = max(longest, current)
            elif (dates[i] - dates[i-1]).days > 1:
                current = 1
        
        return longest
    
    def update_streak_on_completion(self, uid: str) -> Tuple[int, int]:
        """
        Called when user completes an action.
        
        Updates stored streak data and returns current stats.
        
        Args:
            uid: User ID
            
        Returns:
            Tuple of (current_streak, longest_streak)
        """
        streak_data = self.get_or_create_streak_data(uid)
        today = date.today()
        
        # Calculate current streak
        current = self.calculate_streak_from_actions(uid)
        longest = max(current, streak_data.longest_streak)
        
        # Update stored values
        streak_data.current_streak = current
        streak_data.longest_streak = longest
        streak_data.last_activity_date = today
        
        self.db.commit()
        
        logger.info(f"Streak updated for {uid}: current={current}, longest={longest}")
        return (current, longest)
    
    def try_use_freeze(self, uid: str) -> bool:
        """
        Attempt to use a streak freeze for yesterday.
        
        Called automatically when checking streak if yesterday has no completions.
        
        Args:
            uid: User ID
            
        Returns:
            True if freeze was used, False otherwise
        """
        streak_data = self.get_or_create_streak_data(uid)
        yesterday = date.today() - timedelta(days=1)
        
        # Only try to use freeze if we have one and haven't already used it for yesterday
        if streak_data.freeze_count > 0 and streak_data.freeze_used_date != yesterday:
            # Check if yesterday had no completions
            yesterday_completed = self.db.query(func.count(ActionPlanItem.id)).join(
                ActionPlan
            ).filter(
                and_(
                    ActionPlan.uid == uid,
                    ActionPlanItem.is_completed == True,
                    func.date(ActionPlanItem.completed_at) == yesterday
                )
            ).scalar()
            
            if yesterday_completed == 0:
                # Use the freeze
                streak_data.freeze_count -= 1
                streak_data.freeze_used_date = yesterday
                self.db.commit()
                logger.info(f"Streak freeze used for {uid} on {yesterday}. Remaining: {streak_data.freeze_count}")
                return True
        
        return False
    
    def add_freeze_token(self, uid: str) -> int:
        """
        Add a freeze token when reward is claimed.
        
        Args:
            uid: User ID
            
        Returns:
            New freeze count
        """
        streak_data = self.get_or_create_streak_data(uid)
        streak_data.freeze_count += 1
        self.db.commit()
        logger.info(f"Freeze token added for {uid}. Total: {streak_data.freeze_count}")
        return streak_data.freeze_count
    
    def get_full_streak_status(self, uid: str) -> Dict[str, Any]:
        """
        Get complete streak status for API response.
        
        This is the main method called by API endpoints.
        
        Args:
            uid: User ID
            
        Returns:
            Dictionary with current_streak, longest_streak, freeze_count, last_activity_date, freeze_used_today
        """
        streak_data = self.get_or_create_streak_data(uid)
        
        # Auto-check and use freeze if needed - capture if it was used
        freeze_used = self.try_use_freeze(uid)
        
        # Check if freeze was used today (yesterday's date in freeze_used_date)
        yesterday = date.today() - timedelta(days=1)
        freeze_used_today = streak_data.freeze_used_date == yesterday
        
        # Calculate current streak (recalculated to be accurate)
        # TEST MODE: If stored current_streak is >= 30, use it (allows testing rewards)
        if streak_data.current_streak >= 30:
            current = streak_data.current_streak
        else:
            current = self.calculate_streak_from_actions(uid)
        
        # Update longest if needed
        longest = max(current, streak_data.longest_streak)
        if longest > streak_data.longest_streak:
            streak_data.longest_streak = longest
            self.db.commit()
        
        return {
            "current_streak": current,
            "longest_streak": longest,
            "freeze_count": streak_data.freeze_count,
            "last_activity_date": streak_data.last_activity_date.isoformat() if streak_data.last_activity_date else None,
            "freeze_used_today": freeze_used_today,
            "freeze_just_used": freeze_used  # True if freeze was JUST consumed in this request
        }
