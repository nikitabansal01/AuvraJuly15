"""
===============================================================================
MENSTRUAL CYCLE PHASE CALCULATION SERVICE
===============================================================================

SCIENTIFIC REFERENCE: NCBI Endotext - Physiology of the Normal Menstrual Cycle
https://www.ncbi.nlm.nih.gov/books/NBK279054/

KEY SCIENTIFIC FACTS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. LUTEAL PHASE IS CONSTANT (~14 days)
   - "The luteal phase of the cycle is relatively constant in all women, 
      with a duration of 14 days" (NCBI Endotext)
   - Range: 12-16 days, but remarkably consistent for each individual
   
2. FOLLICULAR PHASE VARIES
   - "The variability of cycle length is usually derived from varying 
      lengths of the follicular phase" (NCBI Endotext)
   - This is what causes different cycle lengths (21-35+ days)
   
3. OVULATION TIMING
   - Ovulation occurs approximately 14 days BEFORE the next expected period
   - NOT 14 days after the last period (common misconception)
   - Occurs 10-12 hours after LH peak, 34-36 hours after LH surge onset

4. MENSTRUAL BLEEDING
   - Average duration: 4-6 days (range: 2-8 days)
   - Blood loss: 30-80ml average

PHASE CALCULATION FORMULA:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

For ANY cycle length, calculate BACKWARD from expected next period:

    ovulation_day = cycle_length - luteal_phase_length
    
Example calculations:
┌─────────────────┬───────────┬─────────────┬──────────────┬────────────┐
│ Cycle Length    │ Ovulation │ Menses      │ Follicular   │ Luteal     │
├─────────────────┼───────────┼─────────────┼──────────────┼────────────┤
│ 21 days         │ Day 7     │ Day 1-5     │ Day 6        │ Day 8-21   │
│ 28 days         │ Day 14    │ Day 1-5     │ Day 6-13     │ Day 15-28  │
│ 35 days         │ Day 21    │ Day 1-5     │ Day 6-20     │ Day 22-35  │
│ 42 days         │ Day 28    │ Day 1-5     │ Day 6-27     │ Day 29-42  │
└─────────────────┴───────────┴─────────────┴──────────────┴────────────┘

EDGE CASES & CLINICAL CONSIDERATIONS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Polymenorrhea (<21 days): Very short follicular phase, may indicate issues
- Oligomenorrhea (>35 days): Extended follicular phase, often seen in PCOS
- PCOS/PCOD: Irregular/anovulatory cycles - phase determination unreliable
- Hormonal BC: May suppress ovulation entirely
- Irregular periods: Unpredictable ovulation timing

===============================================================================
"""

import logging
from datetime import datetime, date, timedelta, time, timezone
from typing import Optional, Tuple, Dict, NamedTuple, Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import UserResponse, UserProfile
from app.models.cycle_models import CyclePhaseInfo
from zoneinfo import ZoneInfo
from calendar import monthrange
from app.models.cycle_models import CycleCalendarDay, CycleCalendarResponse

logger = logging.getLogger(__name__)


# ============================================================================
# CYCLE LENGTH CONFIGURATION
# ============================================================================

class CycleLengthConfig(NamedTuple):
    """Configuration for each cycle length category"""
    avg_days: int           # Average cycle length for this category
    luteal_length: int      # Luteal phase length (constant ~14 days)
    menstrual_length: int   # Expected menstrual bleeding days
    ovulation_window: int   # Days around ovulation to consider "ovulation phase"
    is_potentially_irregular: bool  # Whether this length may indicate irregularity


# Cycle length configurations based on medical literature
CYCLE_LENGTH_CONFIG: Dict[str, CycleLengthConfig] = {
    "Less than 21 days": CycleLengthConfig(
        avg_days=19,
        luteal_length=12,      # May have shorter luteal phase (luteal phase defect)
        menstrual_length=4,
        ovulation_window=2,
        is_potentially_irregular=True  # Polymenorrhea - may indicate hormonal issues
    ),
    "21-25 days": CycleLengthConfig(
        avg_days=23,
        luteal_length=13,
        menstrual_length=5,
        ovulation_window=2,
        is_potentially_irregular=False
    ),
    "26-30 days": CycleLengthConfig(
        avg_days=28,
        luteal_length=14,      # Standard luteal phase
        menstrual_length=5,
        ovulation_window=2,
        is_potentially_irregular=False
    ),
    "31-35 days": CycleLengthConfig(
        avg_days=33,
        luteal_length=14,
        menstrual_length=5,
        ovulation_window=2,
        is_potentially_irregular=False
    ),
    "35+ days": CycleLengthConfig(
        avg_days=42,
        luteal_length=14,      # Luteal phase stays constant even in long cycles
        menstrual_length=5,
        ovulation_window=3,    # Wider window due to uncertainty in long cycles
        is_potentially_irregular=True  # Oligomenorrhea - may indicate PCOS
    ),
}


# ============================================================================
# CONDITIONS & DESCRIPTIONS THAT MAKE PHASE UNCLEAR
# ============================================================================

# Conditions that affect ovulation regularity
PHASE_UNCLEAR_CONDITIONS = {
    "PCOS",              # Polycystic Ovary Syndrome - irregular/absent ovulation
    "PCOD",              # Polycystic Ovarian Disease
    "Amenorrhea",        # Absence of periods
    "Cushing's Syndrome",  # Hormonal disruption affects ovulation
    # Note: PMDD does NOT make phase unclear - symptoms are severe but cycle is trackable
    # Note: Endometriosis - can track phase despite symptoms
}

# Period descriptions indicating unreliable phase calculation
# DISABLED FOR NOW - will show phase regardless of period regularity
PHASE_UNCLEAR_DESCRIPTIONS = {
    # "Irregular",          # Cycle length varies significantly
    # "Occasional Skips",   # Sometimes misses periods
    "I don't get periods",  # No menstruation - this should still be unclear
    # "I'm not sure",       # User doesn't know their cycle
}

# Birth control methods that suppress ovulation
# Note: Currently NOT used to mark phase unclear, but available for future use
OVULATION_SUPPRESSING_BC = {
    "Hormonal Birth Control Pills",
    "IUD (Intrauterine Device)",  # Hormonal IUD (Mirena, etc.)
    # Note: Copper IUD does NOT suppress ovulation - natural cycle continues
}


# ============================================================================
# CYCLE SERVICE CLASS
# ============================================================================

class CycleService:
    """
    Menstrual cycle calculation service with scientifically-accurate phase determination.
    
    Uses the key scientific insight that:
    - Luteal phase is CONSTANT (~14 days) across all women
    - Follicular phase VARIES - this causes different cycle lengths
    - Ovulation occurs ~14 days BEFORE next period (not after last period)
    """
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db

    def _default_cycle_info(self, user_name: str = "Unknown") -> CyclePhaseInfo:
        return CyclePhaseInfo(user_name=user_name, cycle_day=None, phase=None)

    @staticmethod
    def _profile_timezone(user_profile: UserProfile) -> str:
        zone = user_profile.current_timezone or "Asia/Seoul"
        try:
            ZoneInfo(zone)
        except (ValueError, KeyError):
            zone = "Asia/Seoul"
        return zone

    def _build_cycle_phase_info(
        self,
        uid: str,
        user_profile: Optional[UserProfile],
        user_response: Optional[UserResponse],
    ) -> CyclePhaseInfo:
        """Build cycle phase payload from profile + response records."""
        if not user_profile:
            logger.info(f"User profile not found: uid={uid}")
            return self._default_cycle_info()

        user_timezone = self._profile_timezone(user_profile)
        as_of_date = self._get_current_date_in_timezone(user_timezone)
        metadata = {"as_of_date": as_of_date, "timezone": user_timezone}

        if not user_response:
            logger.info(f"User response data not found: uid={uid}")
            return CyclePhaseInfo(user_name=user_profile.name or "Unknown", **metadata)

        metadata["can_update_period"] = True
        if user_response.last_period_date_utc:
            instant = user_response.last_period_date_utc
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
            reported_zone = getattr(user_response, "survey_timezone", None) or user_timezone
            try:
                ZoneInfo(reported_zone)
            except (ValueError, KeyError):
                reported_zone = user_timezone
            metadata["last_period_start"] = instant.astimezone(ZoneInfo(reported_zone)).date()
            metadata["last_period_timezone"] = reported_zone

        logger.info(
            "Data verification: last_period_date_utc=%s, cycle_length=%s",
            user_response.last_period_date_utc,
            user_response.cycle_length,
        )
        if not user_response.last_period_date_utc or not user_response.cycle_length:
            logger.info(
                "Required data missing: last_period_date_utc=%s, cycle_length=%s",
                user_response.last_period_date_utc,
                user_response.cycle_length,
            )
            return CyclePhaseInfo(user_name=user_profile.name or "Unknown", **metadata)

        cycle_day, phase = self._calculate_cycle_phase(
            user_response.last_period_date_utc,
            user_response.cycle_length,
            user_response.period_description,
            user_response.diagnosed_conditions,
            user_timezone,
            as_of_date,
        )
        logger.info(f"Calculation result: cycle_day={cycle_day}, phase={phase}")
        return CyclePhaseInfo(
            user_name=user_profile.name or "Unknown",
            cycle_day=cycle_day,
            phase=phase,
            cycle_length_days=CYCLE_LENGTH_CONFIG[user_response.cycle_length].avg_days
                if user_response.cycle_length in CYCLE_LENGTH_CONFIG else None,
            **metadata,
        )

    def update_last_period_start(self, uid: str, reported_date: date) -> CyclePhaseInfo:
        """Update the signed-in user's saved start date atomically.

        The existing survey field remains authoritative. This does not invent
        a bleeding duration, create predicted period events or replace plans.
        """
        if self.db is None or not uid:
            raise ValueError("Sign in to update your period information")
        try:
            profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).with_for_update().first()
            response = self.db.query(UserResponse).filter(UserResponse.uid == uid).order_by(
                UserResponse.created_at.desc(), UserResponse.id.desc()
            ).with_for_update().first()
            if not profile or not response:
                raise LookupError("Complete your health setup before updating this date")
            zone = self._profile_timezone(profile)
            if reported_date > self._get_current_date_in_timezone(zone):
                raise ValueError("The period start date cannot be in the future")
            # user_responses uses a UTC-naive DateTime column. Convert local
            # midnight explicitly instead of letting the phone/DB timezone shift it.
            try:
                instant = datetime.combine(reported_date, time.min, tzinfo=ZoneInfo(zone)).astimezone(timezone.utc)
            except (OverflowError, ValueError) as error:
                raise ValueError("Choose a valid period start date") from error
            response.last_period_date_utc = instant.replace(tzinfo=None)
            response.survey_timezone = zone
            snapshot = self._build_cycle_phase_info(uid, profile, response)
            self.db.commit()
            return snapshot
        except Exception:
            self.db.rollback()
            raise

    def get_calendar_month(self, uid: str, year: int, month: int) -> CycleCalendarResponse:
        """Read monthly estimates from the same owned answers/rules as Home.

        A projected phase is never a recorded bleeding event. Only the actual
        saved start date receives the reported-period marker.
        """
        if self.db is None or not uid:
            raise ValueError("Sign in to view your cycle information")
        profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
        response = self.db.query(UserResponse).filter(UserResponse.uid == uid).order_by(
            UserResponse.created_at.desc(), UserResponse.id.desc()
        ).first()
        info = self._build_cycle_phase_info(uid, profile, response)
        days = []
        for number in range(1, monthrange(year, month)[1] + 1):
            day = date(year, month, number)
            cycle_day, phase = None, None
            if profile and response and response.last_period_date_utc and response.cycle_length:
                cycle_day, phase = self._calculate_cycle_phase(
                    response.last_period_date_utc, response.cycle_length,
                    response.period_description, response.diagnosed_conditions,
                    info.timezone or "Asia/Seoul", day, log_details=False,
                )
            days.append(CycleCalendarDay(date=day, cycle_day=cycle_day, phase=phase,
                is_projection=bool(info.as_of_date and day > info.as_of_date),
                reported_period_start=day == info.last_period_start))
        return CycleCalendarResponse(month=f"{year:04d}-{month:02d}", cycle_info=info, days=days)
    
    def get_cycle_phase_info(self, uid: str) -> CyclePhaseInfo:
        """
        Calculate user's menstrual cycle information
        
        Args:
            uid: User ID
        
        Returns:
            Menstrual cycle information
        """
        try:
            if self.db is None:
                logger.error("CycleService.get_cycle_phase_info called without db session")
                return self._default_cycle_info()

            user_profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
            user_response = self.db.query(UserResponse).filter(UserResponse.uid == uid).order_by(
                UserResponse.created_at.desc(), UserResponse.id.desc()
            ).first()
            return self._build_cycle_phase_info(uid, user_profile, user_response)
        except Exception as e:
            logger.error(f"Failed to calculate menstrual cycle information: {str(e)}")
            return self._default_cycle_info()

    async def get_cycle_phase_info_async(self, uid: str, db: Any) -> Dict[str, Optional[Any]]:
        """
        Async-compatible cycle phase lookup used by async services.

        Returns:
            Dict with user_name, cycle_day, phase keys (legacy-compatible shape).
        """
        try:
            profile_result = await db.execute(
                select(UserProfile).where(UserProfile.uid == uid)
            )
            user_profile = profile_result.scalar_one_or_none()

            response_result = await db.execute(
                select(UserResponse)
                .where(UserResponse.uid == uid)
                .order_by(UserResponse.created_at.desc(), UserResponse.id.desc())
                .limit(1)
            )
            user_response = response_result.scalar_one_or_none()

            cycle_info = self._build_cycle_phase_info(uid, user_profile, user_response)
            return {
                "user_name": cycle_info.user_name,
                "cycle_day": cycle_info.cycle_day,
                "phase": cycle_info.phase,
            }
        except Exception as e:
            logger.error(f"Failed to calculate async menstrual cycle information: {str(e)}")
            return {"user_name": "Unknown", "cycle_day": None, "phase": None}
    
    def _calculate_cycle_phase(
        self, 
        last_period_date_utc: datetime, 
        cycle_length: str, 
        period_description: Optional[str], 
        diagnosed_conditions: Optional[list],
        user_timezone: str,
        as_of_date: Optional[date] = None,
        *,
        log_details: bool = True,
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        Estimate cycle day and phase from the saved date and configured cycle length.
        
        SCIENTIFIC BASIS:
        - Luteal phase is constant (~14 days)
        - Follicular phase varies with total cycle length  
        - Ovulation = cycle_length - luteal_length
        
        Args:
            last_period_date_utc: Last period start date (UTC)
            cycle_length: Menstrual cycle length category
            period_description: Period status description
            diagnosed_conditions: Diagnosed conditions
            user_timezone: User's current timezone
        
        Returns:
            (cycle_day, phase)
        """
        info_log = logger.info if log_details else lambda *_args, **_kwargs: None
        try:
            info_log(f"=== CYCLE CALCULATION START ===")
            info_log(f"Input: last_period_date_utc={last_period_date_utc}")
            info_log(f"Input: cycle_length={cycle_length}")
            info_log(f"Input: period_description={period_description}")
            info_log(f"Input: diagnosed_conditions={diagnosed_conditions}")
            info_log(f"Input: user_timezone={user_timezone}")
            
            # Step 1: Get cycle configuration
            config = CYCLE_LENGTH_CONFIG.get(cycle_length)
            if not config:
                logger.warning(f"Unknown cycle length category: {cycle_length}")
                return None, None
            
            cycle_days = config.avg_days
            luteal_length = config.luteal_length
            menstrual_length = config.menstrual_length
            ovulation_window = config.ovulation_window
            
            info_log(f"Config: cycle_days={cycle_days}, luteal={luteal_length}, menstrual={menstrual_length}")
            
            # Step 2: Convert UTC date to user timezone
            from app.utils.timezone_utils import convert_from_utc
            last_period = convert_from_utc(last_period_date_utc, user_timezone)
            info_log(f"Converted last period date: {last_period}")
            
            # Step 3: Get current date in user's timezone
            current_date = as_of_date or self._get_current_date_in_timezone(user_timezone)
            info_log(f"Current date (user timezone): {current_date}")
            
            # Step 4: Calculate days since last period
            days_since_last = (current_date - last_period).days
            info_log(f"Days since last period: {days_since_last}")
            
            # Handle edge case: future date
            if days_since_last < 0:
                info_log(f"Last period date is in the future, returning None")
                return None, None
            
            # Step 5: Calculate cycle day (which cycle we're in and what day)
            # If days_since_last >= cycle_days, we've moved to subsequent cycle(s)
            cycle_day = (days_since_last % cycle_days) + 1
            info_log(f"Calculated cycle day: {cycle_day}")
            
            # Step 6: Determine phase using scientific calculation
            # Note: We always calculate phase regardless of conditions
            # The backward calculation method works for all cycle types
            phase = self._determine_phase_scientific(
                cycle_day=cycle_day,
                total_cycle_days=cycle_days,
                luteal_length=luteal_length,
                menstrual_length=menstrual_length,
                ovulation_window=ovulation_window
            )
            
            info_log(f"=== RESULT: Day {cycle_day}, Phase: {phase} ===")
            return cycle_day, phase
            
        except Exception as e:
            logger.error(f"Failed to calculate menstrual cycle: {str(e)}")
            return None, None
    
    def _get_current_date_in_timezone(self, timezone_str: str) -> date:
        """Get current date in user's timezone."""
        if not timezone_str:
            timezone_str = "Asia/Seoul"  # Default timezone
            logger.warning(f"No timezone provided, using default: {timezone_str}")
        
        try:
            tz = ZoneInfo(timezone_str)
            return datetime.now(tz).date()
        except Exception as e:
            logger.warning(f"Failed to parse timezone '{timezone_str}': {e}")
            # Fallback to Korea timezone
            korea_tz = ZoneInfo("Asia/Seoul")
            return datetime.now(korea_tz).date()
    
    def _is_phase_unclear(
        self, 
        period_description: Optional[str], 
        diagnosed_conditions: Optional[list]
    ) -> bool:
        """
        Check if menstrual phase cannot be determined reliably.
        
        Returns True for:
        - Irregular periods (unpredictable ovulation timing)
        - Conditions that affect ovulation (PCOS, PCOD, Amenorrhea, etc.)
        - Unknown cycle patterns
        """
        # Check period description
        if period_description in PHASE_UNCLEAR_DESCRIPTIONS:
            logger.info(f"Phase unclear: period_description='{period_description}'")
            return True
        
        # Check diagnosed conditions
        if diagnosed_conditions:
            for condition in diagnosed_conditions:
                if condition in PHASE_UNCLEAR_CONDITIONS:
                    logger.info(f"Phase unclear: diagnosed with '{condition}'")
                    return True
        
        return False
    
    def _determine_phase_scientific(
        self,
        cycle_day: int,
        total_cycle_days: int,
        luteal_length: int,
        menstrual_length: int,
        ovulation_window: int
    ) -> str:
        """
        Determine menstrual phase using SCIENTIFICALLY ACCURATE calculation.
        
        KEY INSIGHT: Ovulation occurs ~14 days BEFORE the next period,
        NOT 14 days after the last period.
        
        CALCULATION:
        - ovulation_day = total_cycle_days - luteal_length
        - Menses: Day 1 to menstrual_length
        - Follicular: Day (menstrual_length + 1) to (ovulation_day - 1)
        - Ovulation: Day ovulation_day ± (ovulation_window / 2)
        - Luteal: Day (ovulation_end + 1) to total_cycle_days
        
        EXAMPLES:
        ┌──────────────────────────────────────────────────────────────────┐
        │ 28-day cycle (luteal=14, menstrual=5):                          │
        │   - Ovulation day = 28 - 14 = Day 14                            │
        │   - Menses: Day 1-5                                             │
        │   - Follicular: Day 6-13                                        │
        │   - Ovulation: Day 13-15 (window of 2)                          │
        │   - Luteal: Day 16-28                                           │
        ├──────────────────────────────────────────────────────────────────┤
        │ 35-day cycle (luteal=14, menstrual=5):                          │
        │   - Ovulation day = 35 - 14 = Day 21                            │
        │   - Menses: Day 1-5                                             │
        │   - Follicular: Day 6-20                                        │
        │   - Ovulation: Day 20-22 (window of 2)                          │
        │   - Luteal: Day 23-35                                           │
        ├──────────────────────────────────────────────────────────────────┤
        │ 21-day cycle (luteal=13, menstrual=5):                          │
        │   - Ovulation day = 21 - 13 = Day 8                             │
        │   - Menses: Day 1-5                                             │
        │   - Follicular: Day 6-7                                         │
        │   - Ovulation: Day 7-9 (window of 2)                            │
        │   - Luteal: Day 10-21                                           │
        └──────────────────────────────────────────────────────────────────┘
        """
        try:
            # Calculate ovulation day (counting backward from end of cycle)
            ovulation_day = total_cycle_days - luteal_length
            
            # Calculate phase boundaries
            ovulation_start = ovulation_day - (ovulation_window // 2)
            ovulation_end = ovulation_day + (ovulation_window // 2)
            
            # Ensure ovulation window doesn't overlap with menstrual phase
            ovulation_start = max(menstrual_length + 1, ovulation_start)
            
            # Luteal phase starts after ovulation window
            luteal_start = ovulation_end + 1
            
            logger.debug(f"Phase boundaries for {total_cycle_days}-day cycle:")
            logger.debug(f"  - Menses: Day 1-{menstrual_length}")
            logger.debug(f"  - Follicular: Day {menstrual_length + 1}-{ovulation_start - 1}")
            logger.debug(f"  - Ovulation: Day {ovulation_start}-{ovulation_end} (peak: Day {ovulation_day})")
            logger.debug(f"  - Luteal: Day {luteal_start}-{total_cycle_days}")
            
            # Determine which phase the current day falls into
            if cycle_day <= menstrual_length:
                return "Menses phase"
            elif cycle_day < ovulation_start:
                return "Follicular phase"
            elif cycle_day <= ovulation_end:
                return "Ovulation phase"
            else:
                return "Luteal phase"
                
        except Exception as e:
            logger.error(f"Failed to determine phase: {str(e)}")
            return "Cycle Phase unclear"


# ============================================================================
# FACTORY FUNCTION
# ============================================================================

def get_cycle_service(db: Optional[Session] = None) -> CycleService:
    """Factory function to create CycleService instance."""
    return CycleService(db)
