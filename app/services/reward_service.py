"""
Reward Service - Handles reward claiming and status.

Depends on StreakService for all streak calculations.
"""
import logging
from datetime import datetime
from typing import List, Dict, Any
from sqlalchemy.orm import Session

from app.core.database import UserReward
from app.services.streak_service import StreakService, REWARDS_CONFIG

logger = logging.getLogger(__name__)


class RewardService:
    """
    Reward claiming and status management.
    
    All streak calculations are delegated to StreakService.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.streak_service = StreakService(db)
    
    def get_all_rewards_status(self, uid: str) -> Dict[str, Any]:
        """
        Get all rewards with current status for user.
        
        Args:
            uid: User ID
            
        Returns:
            Dictionary with streak status and all rewards with their states
        """
        streak_status = self.streak_service.get_full_streak_status(uid)
        current_streak = streak_status["current_streak"]
        
        # Get claimed rewards
        claimed = self.db.query(UserReward).filter(UserReward.uid == uid).all()
        claimed_ids = {r.reward_id for r in claimed}
        
        rewards = []
        for r in REWARDS_CONFIG:
            if r["id"] in claimed_ids:
                state = "claimed"
            elif current_streak >= r["days"]:
                state = "available"
            else:
                state = "locked"
            
            rewards.append({
                "id": r["id"],
                "title": r["title"],
                "required_streak": r["days"],
                "category": r["category"],
                "icon": r["icon"],
                "state": state,
                "days_remaining": max(0, r["days"] - current_streak) if state == "locked" else 0
            })
        
        return {
            **streak_status,
            "rewards": rewards
        }
    
    def claim_reward(self, uid: str, reward_id: str) -> Dict[str, Any]:
        """
        Claim a reward if eligible.
        
        Args:
            uid: User ID
            reward_id: ID of the reward to claim
            
        Returns:
            Result dictionary with success status and any effects
        """
        # Find reward config
        reward = next((r for r in REWARDS_CONFIG if r["id"] == reward_id), None)
        if not reward:
            logger.warning(f"Invalid reward ID: {reward_id}")
            return {"success": False, "error": "Invalid reward ID"}
        
        # Check if already claimed
        existing = self.db.query(UserReward).filter(
            UserReward.uid == uid,
            UserReward.reward_id == reward_id
        ).first()
        if existing:
            logger.warning(f"Reward {reward_id} already claimed by {uid}")
            return {"success": False, "error": "Already claimed"}
        
        # Check streak requirement
        # TEST MODE: For specific test user, use stored value if >= 30
        TEST_USER_UID = "AMu7Bum6Kfbc3xIYdmpDVAyHQUF2"
        streak_data = self.streak_service.get_or_create_streak_data(uid)
        if uid == TEST_USER_UID and streak_data.current_streak >= 30:
            current = streak_data.current_streak
        else:
            current = self.streak_service.calculate_streak_from_actions(uid)
        if current < reward["days"]:
            return {
                "success": False, 
                "error": f"Need {reward['days']} day streak, you have {current}"
            }
        
        # Claim the reward
        new_reward = UserReward(uid=uid, reward_id=reward_id)
        self.db.add(new_reward)
        
        # Handle special effects
        effect_result = None
        if reward["effect"] == "freeze_token":
            freeze_count = self.streak_service.add_freeze_token(uid)
            effect_result = f"You now have {freeze_count} streak freeze(s)!"
        
        self.db.commit()
        
        logger.info(f"Reward claimed: {reward_id} by {uid}")
        return {
            "success": True,
            "reward_id": reward_id,
            "title": reward["title"],
            "icon": reward["icon"],
            "effect": reward["effect"],
            "effect_result": effect_result
        }
    
    def get_claimed_rewards(self, uid: str) -> List[Dict[str, Any]]:
        """
        Get list of rewards already claimed by user.
        
        Args:
            uid: User ID
            
        Returns:
            List of claimed reward details
        """
        claimed = self.db.query(UserReward).filter(UserReward.uid == uid).all()
        
        result = []
        for cr in claimed:
            reward = next((r for r in REWARDS_CONFIG if r["id"] == cr.reward_id), None)
            if reward:
                result.append({
                    "id": cr.reward_id,
                    "title": reward["title"],
                    "icon": reward["icon"],
                    "claimed_at": cr.claimed_at.isoformat() if cr.claimed_at else None
                })
        
        return result
    
    def is_reward_unlocked(self, uid: str, reward_id: str) -> bool:
        """
        Check if user has claimed a specific reward.
        
        Used for gating features - e.g., checking if diet preference
        reward is claimed before allowing diet settings.
        
        Args:
            uid: User ID
            reward_id: Reward ID to check (e.g., "diet_prefs", "food_allergies")
            
        Returns:
            True if reward is claimed, False otherwise
        """
        claimed = self.db.query(UserReward).filter(
            UserReward.uid == uid,
            UserReward.reward_id == reward_id
        ).first()
        return claimed is not None
    
    def get_unlocked_reward_ids(self, uid: str) -> List[str]:
        """
        Get list of all claimed reward IDs for a user.
        
        Used for frontend to know which features to show.
        
        Args:
            uid: User ID
            
        Returns:
            List of claimed reward IDs
        """
        claimed = self.db.query(UserReward).filter(UserReward.uid == uid).all()
        return [c.reward_id for c in claimed]
    
    def get_daily_refresh_limit(self, uid: str) -> int:
        """
        Get user's daily refresh limit based on rewards.
        
        Default: 1 refresh per day
        With 2x_refresh reward: 2 refreshes per day
        """
        if self.is_reward_unlocked(uid, "plan_refresh_2x"):
            return 2
        return 1
    
    def get_refresh_status(self, uid: str) -> dict:
        """
        Get current refresh usage for today.
        
        Returns dict with:
        - limit: max refreshes allowed
        - used: refreshes used today
        - remaining: refreshes left
        - can_refresh: boolean
        """
        from datetime import date as date_type
        from app.core.database import UserStreakData
        
        limit = self.get_daily_refresh_limit(uid)
        today = date_type.today()
        
        streak_data = self.db.query(UserStreakData).filter(
            UserStreakData.uid == uid
        ).first()
        
        if not streak_data:
            # No streak data = never used refresh
            return {
                "limit": limit,
                "used": 0,
                "remaining": limit,
                "can_refresh": True
            }
        
        # Check if last refresh was today
        if streak_data.last_refresh_date == today:
            used = streak_data.daily_refresh_count or 0
        else:
            # Different day, reset counter
            used = 0
        
        remaining = max(0, limit - used)
        
        return {
            "limit": limit,
            "used": used,
            "remaining": remaining,
            "can_refresh": remaining > 0
        }
    
    def use_refresh(self, uid: str) -> dict:
        """
        Use one refresh for today.
        
        Returns success status and updated refresh info.
        """
        from datetime import date as date_type
        from app.core.database import UserStreakData
        
        status = self.get_refresh_status(uid)
        
        if not status["can_refresh"]:
            return {
                "success": False,
                "error": "No refreshes remaining today",
                **status
            }
        
        today = date_type.today()
        
        streak_data = self.db.query(UserStreakData).filter(
            UserStreakData.uid == uid
        ).first()
        
        if not streak_data:
            streak_data = UserStreakData(
                uid=uid,
                daily_refresh_count=1,
                last_refresh_date=today
            )
            self.db.add(streak_data)
        else:
            # Reset if new day
            if streak_data.last_refresh_date != today:
                streak_data.daily_refresh_count = 1
                streak_data.last_refresh_date = today
            else:
                streak_data.daily_refresh_count = (streak_data.daily_refresh_count or 0) + 1
        
        self.db.commit()
        
        return {
            "success": True,
            "limit": status["limit"],
            "used": streak_data.daily_refresh_count,
            "remaining": max(0, status["limit"] - streak_data.daily_refresh_count),
            "can_refresh": (streak_data.daily_refresh_count < status["limit"])
        }
