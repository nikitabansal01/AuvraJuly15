import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, desc

from app.core.database import RecommendationRecord, RecommendationCompletion, UserProfile

logger = logging.getLogger(__name__)

class ProgressService:
    def __init__(self, db: Session):
        self.db = db
    
    def get_weekly_progress(self, uid: str, target_date: date = None) -> Dict[str, Any]:
        """Get weekly progress statistics."""
        if target_date is None:
            target_date = date.today()
        
        # Calculate start and end of the week
        week_start = target_date - timedelta(days=target_date.weekday())
        week_end = week_start + timedelta(days=6)
        
        try:
            # Get completion records for the week
            completions = self.db.query(RecommendationCompletion).filter(
                and_(
                    RecommendationCompletion.uid == uid,
                    RecommendationCompletion.completion_date >= week_start,
                    RecommendationCompletion.completion_date <= week_end
                )
            ).all()
            
            # Calculate total recommendations for the week
            total_recommendations = self._get_weekly_total_recommendations(uid, week_start, week_end)
            
            # Calculate consecutive completion days
            streak_days = self._calculate_streak_days(uid, target_date)
            
            # Daily completion statistics
            daily_completions = self._get_daily_completion_stats(uid, week_start, week_end)
            
            # Hormone-specific completion statistics
            hormone_stats = self._get_hormone_completion_stats(completions)
            
            return {
                "period": {
                    "week_start": week_start.isoformat(),
                    "week_end": week_end.isoformat(),
                    "current_date": target_date.isoformat()
                },
                "overall": {
                    "total_recommendations": total_recommendations,
                    "completed_recommendations": len(completions),
                    "completion_rate": round((len(completions) / total_recommendations * 100), 1) if total_recommendations > 0 else 0
                },
                "streak": {
                    "current_streak": streak_days,
                    "longest_streak": self._get_longest_streak(uid)
                },
                "daily_completions": daily_completions,
                "hormone_stats": hormone_stats
            }
            
        except Exception as e:
            logger.error(f"Failed to get weekly progress: {str(e)}")
            return self._create_empty_weekly_progress(week_start, week_end, target_date)
    
    def get_monthly_progress(self, uid: str, target_date: date = None) -> Dict[str, Any]:
        """Get monthly progress statistics."""
        if target_date is None:
            target_date = date.today()
        
        # Calculate start and end of the month
        month_start = target_date.replace(day=1)
        if target_date.month == 12:
            month_end = target_date.replace(year=target_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            month_end = target_date.replace(month=target_date.month + 1, day=1) - timedelta(days=1)
        
        try:
            # Get completion records for the month
            completions = self.db.query(RecommendationCompletion).filter(
                and_(
                    RecommendationCompletion.uid == uid,
                    RecommendationCompletion.completion_date >= month_start,
                    RecommendationCompletion.completion_date <= month_end
                )
            ).all()
            
            # Calculate total recommendations for the month
            total_recommendations = self._get_monthly_total_recommendations(uid, month_start, month_end)
            
            # Weekly completion statistics
            weekly_stats = self._get_weekly_completion_stats(uid, month_start, month_end)
            
            # Hormone-specific completion statistics
            hormone_stats = self._get_hormone_completion_stats(completions)
            
            # Best completion day
            best_day = self._get_best_completion_day(uid, month_start, month_end)
            
            return {
                "period": {
                    "month_start": month_start.isoformat(),
                    "month_end": month_end.isoformat(),
                    "current_date": target_date.isoformat()
                },
                "overall": {
                    "total_recommendations": total_recommendations,
                    "completed_recommendations": len(completions),
                    "completion_rate": round((len(completions) / total_recommendations * 100), 1) if total_recommendations > 0 else 0
                },
                "weekly_stats": weekly_stats,
                "hormone_stats": hormone_stats,
                "best_day": best_day
            }
            
        except Exception as e:
            logger.error(f"Failed to get monthly progress: {str(e)}")
            return self._create_empty_monthly_progress(month_start, month_end, target_date)
    
    def get_recommendation_progress(self, uid: str, recommendation_id: int) -> Dict[str, Any]:
        """Get progress for a specific recommendation."""
        try:
            # Get recommendation information
            recommendation = self.db.query(RecommendationRecord).filter(
                and_(
                    RecommendationRecord.id == recommendation_id,
                    RecommendationRecord.uid == uid
                )
            ).first()
            
            if not recommendation:
                return {"error": "Recommendation not found"}
            
            # Get completion records
            completions = self.db.query(RecommendationCompletion).filter(
                and_(
                    RecommendationCompletion.uid == uid,
                    RecommendationCompletion.recommendation_id == recommendation_id
                )
            ).order_by(RecommendationCompletion.completion_date.desc()).all()
            
            # Calculate consecutive completion days
            current_streak = self._calculate_recommendation_streak(uid, recommendation_id)
            
            # Total completion count
            total_completions = len(completions)
            
            # Recent completion records
            recent_completions = [
                {
                    "completion_date": comp.completion_date.isoformat(),
                    "completed_at": comp.completed_at.isoformat() if comp.completed_at else None,
                    "notes": comp.notes
                }
                for comp in completions[:5]  # Recent 5
            ]
            
            return {
                "recommendation": {
                    "id": recommendation.id,
                    "title": recommendation.title,
                    "category": recommendation.category,
                    "frequency_detail": recommendation.frequency_detail,
                    "duration_weeks": recommendation.duration_weeks
                },
                "progress": {
                    "total_completions": total_completions,
                    "current_streak": current_streak,
                    "longest_streak": self._get_recommendation_longest_streak(uid, recommendation_id)
                },
                "recent_completions": recent_completions
            }
            
        except Exception as e:
            logger.error(f"Failed to get recommendation progress: {str(e)}")
            return {"error": str(e)}
    
    def get_overall_progress(self, uid: str) -> Dict[str, Any]:
        """Get overall progress statistics."""
        try:
            # All completion records
            all_completions = self.db.query(RecommendationCompletion).filter(
                RecommendationCompletion.uid == uid
            ).all()
            
            # Total recommendations
            total_recommendations = self.db.query(RecommendationRecord).filter(
                RecommendationRecord.uid == uid
            ).count()
            
            # Active recommendations (within duration_weeks)
            active_recommendations = self.db.query(RecommendationRecord).filter(
                and_(
                    RecommendationRecord.uid == uid,
                    RecommendationRecord.duration_weeks.isnot(None)
                )
            ).count()
            
            # Consecutive completion days
            current_streak = self._calculate_streak_days(uid, date.today())
            
            # Hormone-specific statistics
            hormone_stats = self._get_hormone_completion_stats(all_completions)
            
            return {
                "overall": {
                    "total_recommendations": total_recommendations,
                    "active_recommendations": active_recommendations,
                    "total_completions": len(all_completions),
                    "current_streak": current_streak,
                    "longest_streak": self._get_longest_streak(uid)
                },
                "hormone_stats": hormone_stats
            }
            
        except Exception as e:
            logger.error(f"Failed to get overall progress: {str(e)}")
            return {"error": str(e)}
    
    def _get_weekly_total_recommendations(self, uid: str, week_start: date, week_end: date) -> int:
        """Calculate total weekly recommendations."""
        # This should be integrated with actual scheduling logic
        # Currently returns active recommendation count
        return self.db.query(RecommendationRecord).filter(
            and_(
                RecommendationRecord.uid == uid,
                RecommendationRecord.duration_weeks.isnot(None)
            )
        ).count()
    
    def _get_monthly_total_recommendations(self, uid: str, month_start: date, month_end: date) -> int:
        """Calculate total monthly recommendations."""
        return self.db.query(RecommendationRecord).filter(
            and_(
                RecommendationRecord.uid == uid,
                RecommendationRecord.duration_weeks.isnot(None)
            )
        ).count()
    
    def _calculate_streak_days(self, uid: str, target_date: date) -> int:
        """Calculate current consecutive completion days."""
        streak = 0
        current_date = target_date
        
        while True:
            # Check if there's a completion record for the date
            completion = self.db.query(RecommendationCompletion).filter(
                and_(
                    RecommendationCompletion.uid == uid,
                    RecommendationCompletion.completion_date == current_date
                )
            ).first()
            
            if completion:
                streak += 1
                current_date -= timedelta(days=1)
            else:
                break
        
        return streak
    
    def _get_longest_streak(self, uid: str) -> int:
        """Calculate longest consecutive completion days."""
        # Get all completion dates and calculate continuity
        completions = self.db.query(RecommendationCompletion.completion_date).filter(
            RecommendationCompletion.uid == uid
        ).distinct().order_by(RecommendationCompletion.completion_date).all()
        
        if not completions:
            return 0
        
        completion_dates = [comp[0] for comp in completions]
        longest_streak = 1
        current_streak = 1
        
        for i in range(1, len(completion_dates)):
            if (completion_dates[i] - completion_dates[i-1]).days == 1:
                current_streak += 1
                longest_streak = max(longest_streak, current_streak)
            else:
                current_streak = 1
        
        return longest_streak
    
    def _get_daily_completion_stats(self, uid: str, week_start: date, week_end: date) -> Dict[str, Any]:
        """Calculate daily completion statistics."""
        daily_stats = {}
        
        for i in range(7):
            current_date = week_start + timedelta(days=i)
            completions = self.db.query(RecommendationCompletion).filter(
                and_(
                    RecommendationCompletion.uid == uid,
                    RecommendationCompletion.completion_date == current_date
                )
            ).count()
            
            day_name = current_date.strftime("%A").lower()
            daily_stats[day_name] = {
                "date": current_date.isoformat(),
                "completions": completions
            }
        
        return daily_stats
    
    def _get_weekly_completion_stats(self, uid: str, month_start: date, month_end: date) -> List[Dict[str, Any]]:
        """Calculate weekly completion statistics."""
        weekly_stats = []
        current_date = month_start
        
        while current_date <= month_end:
            week_start = current_date - timedelta(days=current_date.weekday())
            week_end = week_start + timedelta(days=6)
            
            completions = self.db.query(RecommendationCompletion).filter(
                and_(
                    RecommendationCompletion.uid == uid,
                    RecommendationCompletion.completion_date >= week_start,
                    RecommendationCompletion.completion_date <= week_end
                )
            ).count()
            
            weekly_stats.append({
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "completions": completions
            })
            
            current_date += timedelta(days=7)
        
        return weekly_stats
    
    def _get_hormone_completion_stats(self, completions: List[RecommendationCompletion]) -> Dict[str, Any]:
        """Calculate hormone-specific completion statistics."""
        hormone_stats = {}
        
        for completion in completions:
            # Get hormone information from recommendation
            recommendation = self.db.query(RecommendationRecord).filter(
                RecommendationRecord.id == completion.recommendation_id
            ).first()
            
            if recommendation and recommendation.hormones:
                for hormone in recommendation.hormones:
                    if hormone not in hormone_stats:
                        hormone_stats[hormone] = 0
                    hormone_stats[hormone] += 1
        
        return hormone_stats
    
    def _get_best_completion_day(self, uid: str, month_start: date, month_end: date) -> str:
        """Find the day with highest completion rate."""
        day_counts = {}
        
        completions = self.db.query(RecommendationCompletion).filter(
            and_(
                RecommendationCompletion.uid == uid,
                RecommendationCompletion.completion_date >= month_start,
                RecommendationCompletion.completion_date <= month_end
            )
        ).all()
        
        for completion in completions:
            day_name = completion.completion_date.strftime("%A").lower()
            day_counts[day_name] = day_counts.get(day_name, 0) + 1
        
        if not day_counts:
            return "no_data"
        
        return max(day_counts, key=day_counts.get)
    
    def _calculate_recommendation_streak(self, uid: str, recommendation_id: int) -> int:
        """Calculate consecutive completion days for a specific recommendation."""
        streak = 0
        current_date = date.today()
        
        while True:
            completion = self.db.query(RecommendationCompletion).filter(
                and_(
                    RecommendationCompletion.uid == uid,
                    RecommendationCompletion.recommendation_id == recommendation_id,
                    RecommendationCompletion.completion_date == current_date
                )
            ).first()
            
            if completion:
                streak += 1
                current_date -= timedelta(days=1)
            else:
                break
        
        return streak
    
    def _get_recommendation_longest_streak(self, uid: str, recommendation_id: int) -> int:
        """Calculate longest consecutive completion days for a specific recommendation."""
        completions = self.db.query(RecommendationCompletion.completion_date).filter(
            and_(
                RecommendationCompletion.uid == uid,
                RecommendationCompletion.recommendation_id == recommendation_id
            )
        ).order_by(RecommendationCompletion.completion_date).all()
        
        if not completions:
            return 0
        
        completion_dates = [comp[0] for comp in completions]
        longest_streak = 1
        current_streak = 1
        
        for i in range(1, len(completion_dates)):
            if (completion_dates[i] - completion_dates[i-1]).days == 1:
                current_streak += 1
                longest_streak = max(longest_streak, current_streak)
            else:
                current_streak = 1
        
        return longest_streak
    
    def _create_empty_weekly_progress(self, week_start: date, week_end: date, target_date: date) -> Dict[str, Any]:
        """Create empty weekly progress."""
        return {
            "period": {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "current_date": target_date.isoformat()
            },
            "overall": {
                "total_recommendations": 0,
                "completed_recommendations": 0,
                "completion_rate": 0
            },
            "streak": {
                "current_streak": 0,
                "longest_streak": 0
            },
            "daily_completions": {},
            "hormone_stats": {}
        }
    
    def _create_empty_monthly_progress(self, month_start: date, month_end: date, target_date: date) -> Dict[str, Any]:
        """Create empty monthly progress."""
        return {
            "period": {
                "month_start": month_start.isoformat(),
                "month_end": month_end.isoformat(),
                "current_date": target_date.isoformat()
            },
            "overall": {
                "total_recommendations": 0,
                "completed_recommendations": 0,
                "completion_rate": 0
            },
            "weekly_stats": [],
            "hormone_stats": {},
            "best_day": "no_data"
        }

