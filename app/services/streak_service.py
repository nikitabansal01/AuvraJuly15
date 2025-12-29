"""
Unified Streak Service - SINGLE SOURCE OF TRUTH
All streak calculations must go through this service.

Based on Duolingo model: 1+ action completed per day = streak maintained.
"""
import logging
from datetime import date, timedelta, datetime
from typing import Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
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
        
        if streak_data:
            logger.info(f"🔍 Found streak data for {uid}: freeze_count={streak_data.freeze_count}, current={streak_data.current_streak}")
        
        if not streak_data:
            # Check if user profile exists first (FK constraint)
            from app.core.database import UserProfile
            profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
            if not profile:
                logger.warning(f"Cannot create streak data for user {uid} - no profile exists")
                # Return None or default streak data without persisting
                # Create transient object (not committed) to avoid FK violation
                return UserStreakData(
                    uid=uid,
                    current_streak=0,
                    longest_streak=0,
                    freeze_count=0
                )
            
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
    
    def _get_user_today(self, uid: str, timezone_str: str = None) -> date:
        """Get today's date in user's timezone."""
        from datetime import datetime
        from app.utils.timezone_utils import get_user_current_date
        
        # If timezone not provided, get from database
        if not timezone_str:
            return get_user_current_date(uid, self.db)
        
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(timezone_str)
            return datetime.now(tz).date()
        except Exception as e:
            logger.error(f"Failed to get user today with timezone {timezone_str}: {e}")
            # Fallback to database timezone
            return get_user_current_date(uid, self.db)
    
    def calculate_streak_from_actions(self, uid: str, user_timezone: str = None) -> int:
        """
        Calculate streak based on ActionPlanItem completions.
        
        STREAK RULES:
        1. Fully completed day (4/4 items) = counts towards streak (+1)
        2. Frozen day = counts towards streak (+1) - freeze protects the day
        3. Empty plan (0 items) = counts towards streak (+1) - nothing to complete
        4. Incomplete day (not frozen) = breaks streak
        
        Starts from YESTERDAY (today excluded - user hasn't had full day).
        
        Args:
            uid: User ID
            user_timezone: User's timezone string (e.g., "Asia/Kolkata")
            
        Returns:
            Current consecutive day streak count
        """
        streak = 0
        
        # Get today in user's timezone, then calculate yesterday
        today = self._get_user_today(uid, user_timezone)
        check_date = today - timedelta(days=1)
        
        logger.info(f"Streak calc for {uid}: user_timezone={user_timezone}, today={today}, starting from yesterday={check_date}")
        
        # Get streak data for freeze check
        streak_data = self.get_or_create_streak_data(uid)
        
        # Check if user has any plan history - if not, streak is 0
        first_plan = self.db.query(ActionPlan).filter(
            ActionPlan.uid == uid
        ).order_by(ActionPlan.plan_date.asc()).first()
        
        if not first_plan:
            logger.info(f"User {uid} has no action plan history - streak is 0")
            return 0
        
        first_plan_date = first_plan.plan_date
        
        while True:
            # Don't count days before user's first plan
            if check_date < first_plan_date:
                logger.info(f"Reached date {check_date} before first plan {first_plan_date} - stopping")
                break
            
            is_frozen = self._is_date_frozen(streak_data, check_date)
            
            # Get the plan for this date
            plan = self.db.query(ActionPlan).filter(
                and_(
                    ActionPlan.uid == uid,
                    ActionPlan.plan_date == check_date
                )
            ).first()
            
            if plan:
                # Count total items in this plan (excluding replaced items)
                total_items = self.db.query(func.count(ActionPlanItem.id)).filter(
                    and_(
                        ActionPlanItem.plan_id == plan.id,
                        ActionPlanItem.is_replaced.isnot(True)
                    )
                ).scalar() or 0
                
                # Count completed items
                completed_count = self.db.query(func.count(ActionPlanItem.id)).filter(
                    and_(
                        ActionPlanItem.plan_id == plan.id,
                        ActionPlanItem.is_completed == True,
                        ActionPlanItem.is_replaced.isnot(True)
                    )
                ).scalar() or 0
                
                logger.info(f"Streak calc: {check_date} - {completed_count}/{total_items} completed, frozen={is_frozen}")
                
                # Case 0: Empty plan (0 items) = streak day (nothing to complete)
                if total_items == 0:
                    streak += 1
                    logger.info(f"  → EMPTY PLAN (0 items): streak={streak}")
                    check_date -= timedelta(days=1)
                
                # Case 1: ALL items completed = streak day
                elif completed_count == total_items:
                    streak += 1
                    logger.info(f"  → FULL COMPLETE: streak={streak}")
                    check_date -= timedelta(days=1)
                
                # Case 2: FROZEN = streak day (freeze protects regardless of completion)
                elif is_frozen:
                    streak += 1
                    logger.info(f"  → FROZEN: streak={streak}")
                    check_date -= timedelta(days=1)
                
                # Case 3: NOT frozen and NOT complete = streak ends
                else:
                    logger.info(f"  → INCOMPLETE (not frozen): streak ends at {streak}")
                    break
            else:
                # No plan for this date
                if is_frozen:
                    # Proactive freeze - counts as streak
                    streak += 1
                    logger.info(f"Streak calc: {check_date} - NO PLAN but FROZEN, streak={streak}")
                    check_date -= timedelta(days=1)
                else:
                    # No plan and not frozen - streak ends
                    logger.info(f"Streak calc: {check_date} - NO PLAN, streak ends at {streak}")
                    break
        
        logger.info(f"Final streak for {uid}: {streak}")
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
    
    def update_streak_on_completion(self, uid: str, user_timezone: str = None) -> Tuple[int, int]:
        """
        Called when user completes an action.
        
        Updates stored streak data and returns current stats.
        
        Args:
            uid: User ID
            user_timezone: User's timezone string
            
        Returns:
            Tuple of (current_streak, longest_streak)
        """
        streak_data = self.get_or_create_streak_data(uid)
        today = self._get_user_today(uid, user_timezone)
        
        # Calculate current streak
        current = self.calculate_streak_from_actions(uid, user_timezone)
        longest = max(current, streak_data.longest_streak)
        
        # Update stored values
        streak_data.current_streak = current
        streak_data.longest_streak = longest
        streak_data.last_activity_date = today
        
        self.db.commit()
        
        logger.info(f"Streak updated for {uid}: current={current}, longest={longest}")
        return (current, longest)
    
    def try_use_freeze(self, uid: str, user_timezone: str = None) -> bool:
        """
        Attempt to use a streak freeze for yesterday.
        
        Called automatically when checking streak if yesterday has no completions.
        
        Args:
            uid: User ID
            user_timezone: User's timezone string
            
        Returns:
            True if freeze was used, False otherwise
        """
        streak_data = self.get_or_create_streak_data(uid)
        today = self._get_user_today(uid, user_timezone)
        yesterday = today - timedelta(days=1)
        
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
        # Create a new list to ensure SQLAlchemy detects the change (JSONB mutation)
        frozen_dates = list(streak_data.freeze_used_dates or [])
        date_str = freeze_date.isoformat()
        if date_str not in frozen_dates:
            frozen_dates.append(date_str)
            streak_data.freeze_used_dates = frozen_dates
            # Explicitly mark the JSONB field as modified for SQLAlchemy
            flag_modified(streak_data, 'freeze_used_dates')
    
    def _is_date_frozen(self, streak_data: UserStreakData, check_date: date) -> bool:
        """Check if a specific date is frozen."""
        frozen_dates = self._get_frozen_dates(streak_data)
        return check_date in frozen_dates
    
    def get_missed_days(self, uid: str, user_timezone: str = None) -> list:
        """
        Get list of consecutive missed days starting from yesterday going back.
        
        MISSED DAY RULES (consistent with streak calculation):
        - Fully completed = NOT missed, STOP looking (streak is intact from here back)
        - Frozen = NOT missed, STOP looking (freeze protects the day and streak)
        - Empty plan (0 items) = NOT missed (nothing to complete)
        - Incomplete and NOT frozen = MISSED
        - No plan exists AND user has plan history = MISSED
        - No plan exists AND user has NO plan history = NOT missed (new user)
        
        IMPORTANT: We only count days up to the FIRST complete/frozen day.
        Days before a complete/frozen day are part of an OLD streak - not recoverable.
        
        Args:
            uid: User ID
            user_timezone: User's timezone string
        
        Returns:
            List of date objects for each missed day (most recent first)
        """
        streak_data = self.get_or_create_streak_data(uid)
        missed_days = []
        today = self._get_user_today(uid, user_timezone)
        check_date = today - timedelta(days=1)  # Start from yesterday
        days_checked = 0  # Track total days examined, not just missed
        
        # Check if user has ANY action plan history
        # If no plans exist at all, user is new - no missed days possible
        first_plan = self.db.query(ActionPlan).filter(
            ActionPlan.uid == uid
        ).order_by(ActionPlan.plan_date.asc()).first()
        
        if not first_plan:
            logger.info(f"User {uid} has no action plan history - no missed days")
            return []
        
        # Don't count days before user's first action plan
        first_plan_date = first_plan.plan_date
        
        while True:
            days_checked += 1
            
            # Don't check days before user's first plan - they weren't on the platform
            if check_date < first_plan_date:
                logger.info(f"Reached date {check_date} before first plan {first_plan_date} - stopping")
                break
            
            # Get the plan for this date
            plan = self.db.query(ActionPlan).filter(
                and_(
                    ActionPlan.uid == uid,
                    ActionPlan.plan_date == check_date
                )
            ).first()
            
            # Check if this date is already frozen
            is_frozen = self._is_date_frozen(streak_data, check_date)
            
            if plan:
                # Count total items in this plan (excluding replaced)
                total_items = self.db.query(func.count(ActionPlanItem.id)).filter(
                    and_(
                        ActionPlanItem.plan_id == plan.id,
                        ActionPlanItem.is_replaced.isnot(True)
                    )
                ).scalar() or 0
                
                # Count completed items
                completed_count = self.db.query(func.count(ActionPlanItem.id)).filter(
                    and_(
                        ActionPlanItem.plan_id == plan.id,
                        ActionPlanItem.is_completed == True,
                        ActionPlanItem.is_replaced.isnot(True)
                    )
                ).scalar() or 0
                
                # Empty plan (0 items) = NOT missed, STOP (nothing to complete)
                if total_items == 0:
                    logger.info(f"Plan for {check_date} has 0 items - treating as complete, stopping")
                    break
                
                # Fully completed = stop, not missed (streak is intact from here back)
                if completed_count == total_items:
                    break
                
                # Frozen day = STOP - freeze protects the streak from this point back
                # User cannot recover days BEFORE a frozen day - those are part of old streak
                elif is_frozen:
                    logger.info(f"Found frozen day {check_date} - stopping missed days search")
                    break
                
                # NOT frozen and NOT complete = missed
                else:
                    missed_days.append(check_date)
                    check_date -= timedelta(days=1)
            else:
                # No plan for this date
                if is_frozen:
                    # Proactive freeze - STOP - protects streak from this point back
                    logger.info(f"Found frozen day {check_date} (no plan) - stopping missed days search")
                    break
                else:
                    # No plan and not frozen - missed (within user's plan history period)
                    missed_days.append(check_date)
                    check_date -= timedelta(days=1)
            
            # Safety limit - don't check more than 7 days total (not just missed days)
            if days_checked >= 7:
                break
        
        return missed_days
    
    def get_streak_risk_status(self, uid: str, user_timezone: str = None) -> Dict[str, Any]:
        """
        Check if user's streak is at risk and return status.
        
        Args:
            uid: User ID
            user_timezone: User's timezone string
        
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
        today = self._get_user_today(uid, user_timezone)
        
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
        
        # Get missed days (with timezone)
        missed_days = self.get_missed_days(uid, user_timezone)
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
    
    def use_freeze_proactive(self, uid: str, user_timezone: str = None) -> Dict[str, Any]:
        """
        Use freeze proactively for today (user knows they won't complete actions).
        
        Args:
            uid: User ID
            user_timezone: User's timezone string
        
        Returns:
            {
                "success": bool,
                "message": str,
                "freeze_count": int
            }
        """
        streak_data = self.get_or_create_streak_data(uid)
        today = self._get_user_today(uid, user_timezone)
        
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
    
    def use_freeze_reactive(self, uid: str, user_timezone: str = None) -> Dict[str, Any]:
        """
        Use freeze(s) reactively to protect streak from missed days.
        Supports multi-day: will use X freezes for X missed days.
        
        Args:
            uid: User ID
            user_timezone: User's timezone string
        
        Returns:
            {
                "success": bool,
                "message": str,
                "freeze_count": int,
                "days_frozen": int,
                "frozen_dates": list
            }
        """
        # Lock the row to prevent race conditions
        streak_data = self.db.query(UserStreakData).filter(
            UserStreakData.uid == uid
        ).with_for_update().first()
        
        if not streak_data:
            streak_data = self.get_or_create_streak_data(uid)
        
        # Get missed days (with timezone for accuracy)
        missed_days = self.get_missed_days(uid, user_timezone)
        days_needed = len(missed_days)
        
        if days_needed == 0:
            return {
                "success": False,
                "error": "No missed days to freeze",
                "freeze_count": streak_data.freeze_count
            }
        
        # Check if has enough freeze tokens
        # User can freeze ANY number of missed days if they have enough tokens
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
    
    def get_full_streak_status(self, uid: str, user_timezone: str = None) -> Dict[str, Any]:
        """
        Get complete streak status for API response.
        
        This is the main method called by API endpoints.
        NO AUTO-FREEZE: User must manually use freeze tokens.
        
        Args:
            uid: User ID
            user_timezone: User's timezone string (e.g., "Asia/Kolkata")
            
        Returns:
            Dictionary with streak status and risk information including:
            - is_broken: True if streak is broken (missed days and can't recover)
            - at_risk: True if streak can be recovered with freezes
        """
        streak_data = self.get_or_create_streak_data(uid)
        today = self._get_user_today(uid, user_timezone)
        
        # Get risk status (with timezone)
        risk_status = self.get_streak_risk_status(uid, user_timezone)
        
        # Calculate current streak (with timezone)
        current = self.calculate_streak_from_actions(uid, user_timezone)
        
        # Determine if streak is broken or at risk
        missed_days_count = risk_status["missed_days_count"]
        freeze_count = streak_data.freeze_count
        
        # At risk = has missed days AND has enough freezes to recover
        at_risk = missed_days_count > 0 and freeze_count >= missed_days_count
        
        # Broken = has missed days AND NOT enough freezes
        is_broken = missed_days_count > 0 and freeze_count < missed_days_count
        
        # Update longest if needed
        longest = max(current, streak_data.longest_streak)
        if longest > streak_data.longest_streak:
            streak_data.longest_streak = longest
            self.db.commit()
        
        logger.info(f"Streak status for {uid}: current={current}, missed={missed_days_count}, "
                   f"freezes={freeze_count}, at_risk={at_risk}, is_broken={is_broken}")
        
        return {
            "current_streak": current,
            "longest_streak": longest,
            "freeze_count": freeze_count,
            "last_activity_date": streak_data.last_activity_date.isoformat() if streak_data.last_activity_date else None,
            # Risk and freeze status
            "is_broken": is_broken,
            "at_risk": at_risk,
            "streak_at_risk": risk_status["streak_at_risk"],
            "missed_days_count": missed_days_count,
            "missed_dates": risk_status.get("missed_dates", []),
            "can_freeze": risk_status["can_freeze"],
            "freezes_needed": risk_status["freezes_needed"],
            "today_completed": risk_status["today_completed"],
            "today_frozen": risk_status["today_frozen"]
        }

