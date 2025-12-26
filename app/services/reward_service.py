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
