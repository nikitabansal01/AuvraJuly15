import logging
from datetime import datetime, timedelta, date
from typing import Optional, List
from zoneinfo import ZoneInfo
import dateutil.rrule as rrule
from dateutil.parser import parse

logger = logging.getLogger(__name__)

def compute_next_fire_at_utc(tzid: str, hour: int = 0, minute: int = 0) -> datetime:
    """
    Calculate the next execution time in UTC for a specified HH:MM in user's timezone
    
    Args:
        tzid: IANA timezone ID (e.g., "Asia/Seoul")
        hour: Local hour (0-23)
        minute: Local minute (0-59)
    
    Returns:
        Next execution time in UTC
    """
    try:
        now_utc = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
        now_local = now_utc.astimezone(ZoneInfo(tzid))
        
        # Target time for today
        target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # If current time has passed target time, move to tomorrow
        if now_local >= target:
            target = target + timedelta(days=1)
        
        # Convert to UTC
        return target.astimezone(ZoneInfo("UTC"))
        
    except Exception as e:
        logger.error(f"Failed to calculate next execution time: {str(e)}")
        # Default: 1 hour later
        return datetime.utcnow() + timedelta(hours=1)

def get_local_date(tzid: str, utc_datetime: Optional[datetime] = None) -> date:
    """
    Convert UTC time to user's local date
    
    Args:
        tzid: IANA timezone ID
        utc_datetime: UTC time (None for current time)
    
    Returns:
        User's local date
    """
    try:
        if utc_datetime is None:
            utc_datetime = datetime.utcnow()
        
        if utc_datetime.tzinfo is None:
            utc_datetime = utc_datetime.replace(tzinfo=ZoneInfo("UTC"))
        
        local_datetime = utc_datetime.astimezone(ZoneInfo(tzid))
        return local_datetime.date()
        
    except Exception as e:
        logger.error(f"Failed to convert to local date: {str(e)}")
        return date.today()

def parse_rrule(rrule_str: str) -> Optional[rrule.rrule]:
    """
    Parse RRULE string to create rrule object
    
    Args:
        rrule_str: RRULE string (e.g., "FREQ=DAILY;" or "FREQ=WEEKLY;BYDAY=MO,WE,FR")
    
    Returns:
        rrule object or None
    """
    try:
        # Parse RRULE string
        return rrule.rrulestr(rrule_str)
    except Exception as e:
        logger.error(f"Failed to parse RRULE: {str(e)}")
        return None

def is_date_in_rrule(target_date: date, rrule_str: str, start_date: date, end_date: Optional[date] = None) -> bool:
    """
    Check if a specific date is included in RRULE
    
    Args:
        target_date: Date to check
        rrule_str: RRULE string
        start_date: Start date
        end_date: End date (None for unlimited)
    
    Returns:
        Whether the date is included
    """
    try:
        # Parse RRULE
        rule = parse_rrule(rrule_str)
        if not rule:
            return False
        
        # Set start date
        rule.dtstart = start_date
        
        # Set end date
        if end_date:
            rule.until = end_date
        
        # Check if target_date is included in rule
        target_datetime = datetime.combine(target_date, datetime.min.time())
        
        # Check if next occurrence is the same as target_date
        next_occurrence = rule.after(target_datetime - timedelta(days=1))
        
        if next_occurrence:
            return next_occurrence.date() == target_date
        
        return False
        
    except Exception as e:
        logger.error(f"Failed to check date in RRULE: {str(e)}")
        return False

def get_redistribution_overrides(schedule_id: int, target_date: date, db_session) -> tuple[List[date], List[date]]:
    """
    Get exclusion and inclusion dates from redistribution information
    
    Args:
        schedule_id: Schedule ID
        target_date: Date to check
        db_session: Database session
    
    Returns:
        Tuple of (exclusion dates, inclusion dates)
    """
    try:
        from app.core.database import ScheduleRedistribution
        
        # Dates to exclude (original_date = target_date)
        exclusions = db_session.query(ScheduleRedistribution.original_date).filter(
            ScheduleRedistribution.schedule_id == schedule_id,
            ScheduleRedistribution.original_date == target_date
        ).all()
        
        # Dates to include (override_date = target_date)
        inclusions = db_session.query(ScheduleRedistribution.override_date).filter(
            ScheduleRedistribution.schedule_id == schedule_id,
            ScheduleRedistribution.override_date == target_date
        ).all()
        
        exclusion_dates = [row[0] for row in exclusions]
        inclusion_dates = [row[0] for row in inclusions]
        
        return exclusion_dates, inclusion_dates
        
    except Exception as e:
        logger.error(f"Failed to retrieve redistribution information: {str(e)}")
        return [], []

def should_emit_for_date(schedule_id: int, target_date: date, rrule_str: str, 
                        start_date: date, end_date: Optional[date], db_session) -> bool:
    """
    Check if schedule should be emitted for a specific date
    
    Args:
        schedule_id: Schedule ID
        target_date: Date to check
        rrule_str: RRULE string
        start_date: Start date
        end_date: End date
        db_session: Database session
    
    Returns:
        Whether to emit the schedule
    """
    try:
        # 1. Check basic RRULE
        base_included = is_date_in_rrule(target_date, rrule_str, start_date, end_date)
        
        # 2. Check redistribution information
        exclusions, inclusions = get_redistribution_overrides(schedule_id, target_date, db_session)
        
        # 3. Final decision
        if target_date in exclusions:
            return False  # Excluded
        
        if target_date in inclusions:
            return True   # Explicitly included
        
        return base_included  # Basic RRULE result
        
    except Exception as e:
        logger.error(f"Failed to check emission status: {str(e)}")
        return False

def convert_frequency_detail_to_rrule(frequency_detail: str, duration_weeks: int) -> str:
    """
    Convert existing frequency_detail to RRULE format
    
    Args:
        frequency_detail: Existing format (e.g., "daily:1", "weekly:3")
        duration_weeks: Duration in weeks
    
    Returns:
        RRULE string
    """
    try:
        if not frequency_detail or ':' not in frequency_detail:
            return "FREQ=DAILY;"
        
        freq_type, times_str = frequency_detail.split(':', 1)
        times = int(times_str)
        
        if freq_type.lower() == 'daily':
            return "FREQ=DAILY;"
        
        elif freq_type.lower() == 'weekly':
            if times >= 7:
                return "FREQ=DAILY;"
            elif times == 1:
                return "FREQ=WEEKLY;BYDAY=WE;"  # Wednesday
            elif times == 2:
                return "FREQ=WEEKLY;BYDAY=MO,TH;"  # Monday, Thursday
            elif times == 3:
                return "FREQ=WEEKLY;BYDAY=MO,WE,FR;"  # Monday, Wednesday, Friday
            elif times == 4:
                return "FREQ=WEEKLY;BYDAY=MO,TU,TH,FR;"  # Monday, Tuesday, Thursday, Friday
            elif times == 5:
                return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;"  # Monday-Friday
            elif times == 6:
                return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA;"  # Monday-Saturday
            else:
                return "FREQ=DAILY;"
        
        elif freq_type.lower() == 'monthly':
            if times >= 30:
                return "FREQ=DAILY;"
            elif times == 1:
                return "FREQ=MONTHLY;BYMONTHDAY=15;"  # 15th of every month
            elif times == 2:
                return "FREQ=MONTHLY;BYMONTHDAY=1,15;"  # 1st and 15th of every month
            else:
                return "FREQ=DAILY;"
        
        return "FREQ=DAILY;"
        
    except Exception as e:
        logger.error(f"Failed to convert frequency_detail: {str(e)}")
        return "FREQ=DAILY;"

def convert_date_between_timezones(date_str: str, from_tzid: str, to_tzid: str) -> str:
    """
    Convert date string from one timezone to another
    
    Args:
        date_str: Date string (YYYY-MM-DD or MM/DD/YYYY format)
        from_tzid: Source timezone ID
        to_tzid: Target timezone ID
    
    Returns:
        Converted date string (YYYY-MM-DD format)
    """
    try:
        # Parse date
        from datetime import datetime
        formats = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"]
        
        parsed_date = None
        for fmt in formats:
            try:
                parsed_date = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue
        
        if not parsed_date:
            logger.error(f"Failed to parse date: {date_str}")
            return date_str
        
        # Create datetime at midnight in source timezone
        from_tz = ZoneInfo(from_tzid)
        from_datetime = datetime.combine(parsed_date, datetime.min.time())
        from_datetime = from_datetime.replace(tzinfo=from_tz)
        
        # Convert to target timezone
        to_tz = ZoneInfo(to_tzid)
        to_datetime = from_datetime.astimezone(to_tz)
        
        # Return only the date
        converted_date = to_datetime.date()
        
        logger.info(f"Date conversion: {date_str} ({from_tzid}) → {converted_date} ({to_tzid})")
        return converted_date.strftime("%Y-%m-%d")
        
    except Exception as e:
        logger.error(f"Failed to convert date: {str(e)}")
        return date_str

def convert_to_utc(date_str: str, timezone: str) -> datetime:
    """
    Convert date string to UTC
    
    Args:
        date_str: Date string (YYYY-MM-DD format)
        timezone: Source timezone ID
    
    Returns:
        UTC datetime
    """
    try:
        # Parse date
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        
        # Create datetime at midnight in the timezone
        tz = ZoneInfo(timezone)
        local_datetime = datetime.combine(parsed_date, datetime.min.time())
        local_datetime = local_datetime.replace(tzinfo=tz)
        
        # Convert to UTC
        utc_datetime = local_datetime.astimezone(ZoneInfo("UTC"))
        
        logger.info(f"UTC conversion: {date_str} ({timezone}) → {utc_datetime} (UTC)")
        return utc_datetime
        
    except Exception as e:
        logger.error(f"Failed to convert to UTC: {str(e)}")
        return datetime.utcnow()

def convert_from_utc(utc_datetime: datetime, target_timezone: str) -> date:
    """
    Convert UTC to date in specific timezone
    
    Args:
        utc_datetime: UTC datetime
        target_timezone: Target timezone ID
    
    Returns:
        Date in target timezone
    """
    try:
        if utc_datetime.tzinfo is None:
            utc_datetime = utc_datetime.replace(tzinfo=ZoneInfo("UTC"))
        
        target_tz = ZoneInfo(target_timezone)
        local_datetime = utc_datetime.astimezone(target_tz)
        
        logger.info(f"Timezone conversion: {utc_datetime} (UTC) → {local_datetime.date()} ({target_timezone})")
        return local_datetime.date()
        
    except Exception as e:
        logger.error(f"Failed to convert timezone: {str(e)}")
        return date.today()

def get_user_current_date(uid: str, db_session) -> date:
    """
    Get current date in user's timezone
    
    Args:
        uid: User ID
        db_session: Database session
    
    Returns:
        Current date in user's timezone
    """
    try:
        from app.core.database import UserProfile
        
        user_profile = db_session.query(UserProfile).filter(UserProfile.uid == uid).first()
        current_timezone = user_profile.current_timezone if user_profile else "Asia/Seoul"
        
        tz = ZoneInfo(current_timezone)
        current_date = datetime.now(tz).date()
        
        return current_date
        
    except Exception as e:
        logger.error(f"Failed to get user current date: {str(e)}")
        return date.today()

