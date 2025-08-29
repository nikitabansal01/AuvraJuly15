import logging
from datetime import datetime, date, timedelta
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app.core.database import UserResponse, UserProfile
from app.models.cycle_models import CyclePhaseInfo
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

class CycleService:
    """Menstrual cycle calculation service"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_cycle_phase_info(self, uid: str) -> CyclePhaseInfo:
        """
        Calculate user's menstrual cycle information
        
        Args:
            uid: User ID
        
        Returns:
            Menstrual cycle information
        """
        try:
            # Get user profile (current timezone)
            user_profile = self.db.query(UserProfile).filter(
                UserProfile.uid == uid
            ).first()
            
            if not user_profile:
                logger.info(f"User profile not found: uid={uid}")
                return CyclePhaseInfo(
                    user_name="Unknown",
                    cycle_day=None,
                    phase=None
                )
            
            # Get user response data
            user_response = self.db.query(UserResponse).filter(
                UserResponse.uid == uid
            ).first()
            
            if not user_response:
                logger.info(f"User response data not found: uid={uid}")
                return CyclePhaseInfo(
                    user_name=user_profile.name or "Unknown",
                    cycle_day=None,
                    phase=None
                )
            
            # Check required data
            logger.info(f"Data verification: last_period_date_utc={user_response.last_period_date_utc}, cycle_length={user_response.cycle_length}")
            if not user_response.last_period_date_utc or not user_response.cycle_length:
                logger.info(f"Required data missing: last_period_date_utc={user_response.last_period_date_utc}, cycle_length={user_response.cycle_length}")
                return CyclePhaseInfo(
                    user_name=user_profile.name or "Unknown",
                    cycle_day=None,
                    phase=None
                )
            
            # Calculate menstrual cycle (based on user's current timezone)
            cycle_day, phase = self._calculate_cycle_phase(
                user_response.last_period_date_utc,
                user_response.cycle_length,
                user_response.period_description,
                user_response.diagnosed_conditions,
                user_profile.current_timezone
            )
            
            logger.info(f"Calculation result: cycle_day={cycle_day}, phase={phase}")
            
            return CyclePhaseInfo(
                user_name=user_profile.name or "Unknown",
                cycle_day=cycle_day,
                phase=phase
            )
            
        except Exception as e:
            logger.error(f"Failed to calculate menstrual cycle information: {str(e)}")
            return CyclePhaseInfo(
                user_name="Unknown",
                cycle_day=None,
                phase=None
            )
    
    def _calculate_cycle_phase(self, last_period_date_utc: datetime, cycle_length: str, 
                             period_description: Optional[str], 
                             diagnosed_conditions: Optional[list],
                             user_timezone: str) -> Tuple[Optional[int], Optional[str]]:
        """
        Calculate menstrual cycle and phase
        
        Args:
            last_period_date_utc: Last period start date (UTC)
            cycle_length: Menstrual cycle length
            period_description: Period status description
            diagnosed_conditions: Diagnosed conditions
            user_timezone: User's current timezone
        
        Returns:
            (cycle_day, phase)
        """
        try:
            logger.info(f"Calculation started: last_period_date_utc={last_period_date_utc}, cycle_length={cycle_length}")
            logger.info(f"Additional data: period_description={period_description}, diagnosed_conditions={diagnosed_conditions}")
            logger.info(f"User timezone: {user_timezone}")
            
            # Convert UTC date to user timezone
            from app.utils.timezone_utils import convert_from_utc
            last_period = convert_from_utc(last_period_date_utc, user_timezone)
            logger.info(f"Converted last period date: {last_period}")
            
            # Parse cycle length
            cycle_days = self._parse_cycle_length(cycle_length)
            if not cycle_days:
                logger.info(f"Failed to parse cycle length: {cycle_length}")
                return None, None
            
            logger.info(f"Parsed cycle length: {cycle_days} days")
            
            # Current date (based on user timezone)
            if not user_timezone:
                user_timezone = "Asia/Seoul" # Default value
                logger.warning(f"User timezone not found, using default: {user_timezone}")
            
            try:
                tz = ZoneInfo(user_timezone)
                current_date = datetime.now(tz).date()
                logger.info(f"Current date (user timezone {user_timezone}): {current_date}")
            except Exception as e:
                logger.warning(f"Failed to parse timezone, using default: {e}")
                # Use Korean timezone as default
                korea_tz = ZoneInfo("Asia/Seoul")
                current_date = datetime.now(korea_tz).date()
                logger.info(f"Current date (default Korean time): {current_date}")
            
            # Calculate days since last period
            days_since_last = (current_date - last_period).days
            logger.info(f"Days since last period: {days_since_last} days")
            
            # Negative case (future date)
            if days_since_last < 0:
                logger.info(f"Recognized as future date: days_since_last={days_since_last}")
                return None, None
            
            # Calculate day within current cycle
            cycle_day = (days_since_last % cycle_days) + 1
            logger.info(f"Calculated cycle day: {cycle_day} days")
            
            # Check if phase determination is difficult
            if self._is_phase_unclear(period_description, diagnosed_conditions):
                logger.info(f"Phase determination difficult: period_description={period_description}, diagnosed_conditions={diagnosed_conditions}")
                return cycle_day, "Cycle Phase unclear"
            
            # Calculate phase
            phase = self._determine_phase(cycle_day, cycle_days)
            logger.info(f"Determined phase: {phase}")
            
            return cycle_day, phase
            
        except Exception as e:
            logger.error(f"Failed to calculate menstrual cycle: {str(e)}")
            return None, None
    
    def _parse_date(self, date_str: str) -> Optional[date]:
        """Parse date string"""
        try:
            # Support various date formats
            formats = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"]
            
            for fmt in formats:
                try:
                    return datetime.strptime(date_str, fmt).date()
                except ValueError:
                    continue
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to parse date: {str(e)}")
            return None
    
    def _parse_cycle_length(self, cycle_length: str) -> Optional[int]:
        """Parse menstrual cycle length"""
        try:
            if cycle_length == "Less than 21 days":
                return 21
            elif cycle_length == "21-25 days":
                return 23  # Median value
            elif cycle_length == "26-30 days":
                return 28  # Median value
            elif cycle_length == "31-35 days":
                return 33  # Median value
            elif cycle_length == "35+ days":
                return 35
            else:
                return None
                
        except Exception as e:
            logger.error(f"Failed to parse cycle length: {str(e)}")
            return None
    
    def _is_phase_unclear(self, period_description: Optional[str], 
                         diagnosed_conditions: Optional[list]) -> bool:
        """Check if phase determination is difficult"""
        try:
            # Irregular periods
            if period_description in ["Irregular", "Occasional Skips", "I don't get periods", "I'm not sure"]:
                return True
            
            # Specific conditions
            if diagnosed_conditions:
                unclear_conditions = [
                    "PCOS", "PCOD", "Endometriosis", "Amenorrhea", 
                    "Cushing's Syndrome", "PMDD"
                ]
                
                for condition in diagnosed_conditions:
                    if condition in unclear_conditions:
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check phase clarity: {str(e)}")
            return True
    
    def _determine_phase(self, cycle_day: int, cycle_days: int) -> str:
        """Determine menstrual cycle phase"""
        try:
            # Adjust based on standard 28-day cycle
            if cycle_days != 28:
                # Adjust proportionally
                adjusted_day = int((cycle_day - 1) * 28 / cycle_days) + 1
            else:
                adjusted_day = cycle_day
            
            # Determine phase
            if adjusted_day <= 5:
                return "Menses phase"
            elif adjusted_day <= 14:
                return "Follicular phase"
            elif adjusted_day <= 16:
                return "Ovulation phase"
            else:
                return "Luteal phase"
                
        except Exception as e:
            logger.error(f"Failed to determine phase: {str(e)}")
            return "Cycle Phase unclear"
