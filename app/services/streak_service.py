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
                # Check if freeze was used on this date (supports multi-day)
                if self._is_date_frozen(streak_data, check_date):
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
    
    def _get_frozen_dates(self, streak_data: UserStreakData) -> list:
        """Get list of frozen dates as date objects."""
        if not streak_data.freeze_used_dates:
            return []
        try:
            return [date.fromisoformat(d) for d in streak_data.freeze_used_dates if d]
        except (ValueError, TypeError):
            return []
    
    def _add_frozen_date(self, streak_data: UserStreakData, freeze_date: date) -> None:
        """Add a date to frozen dates list."""
        frozen_dates = streak_data.freeze_used_dates or []
        date_str = freeze_date.isoformat()
        if date_str not in frozen_dates:
            frozen_dates.append(date_str)
            streak_data.freeze_used_dates = frozen_dates
    
    def _is_date_frozen(self, streak_data: UserStreakData, check_date: date) -> bool:
        """Check if a specific date is frozen."""
        frozen_dates = self._get_frozen_dates(streak_data)
        return check_date in frozen_dates
    
    def get_missed_days(self, uid: str) -> list:
        """
        Get list of consecutive missed days starting from yesterday going back.
        
        Returns:
            List of date objects for each missed day (most recent first)
        """
        streak_data = self.get_or_create_streak_data(uid)
        missed_days = []
        check_date = date.today() - timedelta(days=1)  # Start from yesterday
        
        while True:
            # Check if there were any completions on this date
            completed_count = self.db.query(func.count(ActionPlanItem.id)).join(
                ActionPlan, ActionPlanItem.plan_id == ActionPlan.id
            ).filter(
                and_(
                    ActionPlan.uid == uid,
                    ActionPlanItem.is_completed == True,
                    func.date(ActionPlanItem.completed_at) == check_date
                )
            ).scalar()
            
            # Check if this date is already frozen
            is_frozen = self._is_date_frozen(streak_data, check_date)
            
            if completed_count and completed_count > 0:
                # Day was completed, stop checking
                break
            elif is_frozen:
                # Day was frozen, skip but continue checking
                check_date -= timedelta(days=1)
            else:
                # Day was missed
                missed_days.append(check_date)
                check_date -= timedelta(days=1)
                
                # Safety limit - don't check more than 7 days back
                if len(missed_days) >= 7:
                    break
        
        return missed_days
    
    def get_streak_risk_status(self, uid: str) -> Dict[str, Any]:
        """
        Check if user's streak is at risk and return status.
        
        Returns:
            {
                "streak_at_risk": bool,
                "missed_days_count": int,
                "can_freeze": bool,
                "freeze_count": int,
                "freezes_needed": int,
                "today_completed": bool,
                "today_frozen": bool
            }
        """
        streak_data = self.get_or_create_streak_data(uid)
        today = date.today()
        
        # Check if today has any completions
        today_completed = self.db.query(func.count(ActionPlanItem.id)).join(
            ActionPlan, ActionPlanItem.plan_id == ActionPlan.id
        ).filter(
            and_(
                ActionPlan.uid == uid,
                ActionPlanItem.is_completed == True,
                func.date(ActionPlanItem.completed_at) == today
            )
        ).scalar() or 0
        
        # Check if today is already frozen
        today_frozen = self._is_date_frozen(streak_data, today)
        
        # Get missed days
        missed_days = self.get_missed_days(uid)
        missed_days_count = len(missed_days)
        
        # Determine if at risk
        streak_at_risk = missed_days_count > 0
        freezes_needed = missed_days_count
        can_freeze = streak_data.freeze_count >= freezes_needed and freezes_needed > 0
        
        return {
            "streak_at_risk": streak_at_risk,
            "missed_days_count": missed_days_count,
            "missed_dates": [d.isoformat() for d in missed_days],
            "can_freeze": can_freeze,
            "freeze_count": streak_data.freeze_count,
            "freezes_needed": freezes_needed,
            "today_completed": today_completed > 0,
            "today_frozen": today_frozen
        }
    
    def use_freeze_proactive(self, uid: str) -> Dict[str, Any]:
        """
        Use freeze proactively for today (user knows they won't complete actions).
        
        Returns:
            {
                "success": bool,
                "message": str,
                "freeze_count": int
            }
        """
        streak_data = self.get_or_create_streak_data(uid)
        today = date.today()
        
        # Check if already frozen today
        if self._is_date_frozen(streak_data, today):
            return {
                "success": False,
                "error": "Today is already frozen",
                "freeze_count": streak_data.freeze_count
            }
        
        # Check if has freeze tokens
        if streak_data.freeze_count <= 0:
            return {
                "success": False,
                "error": "No freeze tokens available",
                "freeze_count": 0
            }
        
        # Use the freeze
        streak_data.freeze_count -= 1
        self._add_frozen_date(streak_data, today)
        self.db.commit()
        
        logger.info(f"Proactive freeze used for {uid} on {today}. Remaining: {streak_data.freeze_count}")
        
        return {
            "success": True,
            "message": "Streak frozen for today! No actions needed.",
            "freeze_count": streak_data.freeze_count,
            "frozen_date": today.isoformat()
        }
    
    def use_freeze_reactive(self, uid: str) -> Dict[str, Any]:
        """
        Use freeze(s) reactively to protect streak from missed days.
        Supports multi-day: will use X freezes for X missed days.
        
        Returns:
            {
                "success": bool,
                "message": str,
                "freeze_count": int,
                "days_frozen": int,
                "frozen_dates": list
            }
        """
        streak_data = self.get_or_create_streak_data(uid)
        
        # Get missed days
        missed_days = self.get_missed_days(uid)
        days_needed = len(missed_days)
        
        if days_needed == 0:
            return {
                "success": False,
                "error": "No missed days to freeze",
                "freeze_count": streak_data.freeze_count
            }
        
        # Check if has enough freeze tokens
        if streak_data.freeze_count < days_needed:
            return {
                "success": False,
                "error": f"Need {days_needed} freezes but only have {streak_data.freeze_count}",
                "freeze_count": streak_data.freeze_count,
                "days_needed": days_needed
            }
        
        # Use freezes for all missed days
        frozen_dates = []
        for missed_day in missed_days:
            streak_data.freeze_count -= 1
            self._add_frozen_date(streak_data, missed_day)
            frozen_dates.append(missed_day.isoformat())
        
        self.db.commit()
        
        logger.info(f"Reactive freeze used for {uid}: {days_needed} days frozen. Remaining: {streak_data.freeze_count}")
        
        return {
            "success": True,
            "message": f"Streak protected! {days_needed} day(s) frozen.",
            "freeze_count": streak_data.freeze_count,
            "days_frozen": days_needed,
            "frozen_dates": frozen_dates
        }
    
    def get_full_streak_status(self, uid: str) -> Dict[str, Any]:
        """
        Get complete streak status for API response.
        
        This is the main method called by API endpoints.
        NO AUTO-FREEZE: User must manually use freeze tokens.
        
        Args:
            uid: User ID
            
        Returns:
            Dictionary with streak status and risk information
        """
        streak_data = self.get_or_create_streak_data(uid)
        today = date.today()
        
        # Get risk status
        risk_status = self.get_streak_risk_status(uid)
        
        # Calculate current streak (recalculated to be accurate)
        # TEST MODE: Only for specific test user - use stored value if >= 30
        TEST_USER_UID = "AMu7Bum6Kfbc3xIYdmpDVAyHQUF2"
        if uid == TEST_USER_UID and streak_data.current_streak >= 30:
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
            # Risk and freeze status
            "streak_at_risk": risk_status["streak_at_risk"],
            "missed_days_count": risk_status["missed_days_count"],
            "missed_dates": risk_status.get("missed_dates", []),
            "can_freeze": risk_status["can_freeze"],
            "freezes_needed": risk_status["freezes_needed"],
            "today_completed": risk_status["today_completed"],
            "today_frozen": risk_status["today_frozen"]
        }
