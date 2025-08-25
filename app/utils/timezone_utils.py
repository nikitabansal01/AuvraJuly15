import logging
from datetime import datetime, timedelta, date
from typing import Optional, List
from zoneinfo import ZoneInfo
import dateutil.rrule as rrule
from dateutil.parser import parse

logger = logging.getLogger(__name__)

def compute_next_fire_at_utc(tzid: str, hour: int = 0, minute: int = 0) -> datetime:
    """
    사용자 시간대에서 지정된 HH:MM의 다음 실행 시각을 UTC로 계산
    
    Args:
        tzid: IANA 시간대 ID (예: "Asia/Seoul")
        hour: 로컬 시간 (0-23)
        minute: 로컬 분 (0-59)
    
    Returns:
        UTC 기준 다음 실행 시각
    """
    try:
        now_utc = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
        now_local = now_utc.astimezone(ZoneInfo(tzid))
        
        # 오늘의 목표 시각
        target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # 현재 시각이 목표 시각을 지났으면 내일로
        if now_local >= target:
            target = target + timedelta(days=1)
        
        # UTC로 변환
        return target.astimezone(ZoneInfo("UTC"))
        
    except Exception as e:
        logger.error(f"다음 실행 시각 계산 실패: {str(e)}")
        # 기본값: 1시간 후
        return datetime.utcnow() + timedelta(hours=1)

def get_local_date(tzid: str, utc_datetime: Optional[datetime] = None) -> date:
    """
    UTC 시각을 사용자 로컬 날짜로 변환
    
    Args:
        tzid: IANA 시간대 ID
        utc_datetime: UTC 시각 (None이면 현재 시각)
    
    Returns:
        사용자 로컬 날짜
    """
    try:
        if utc_datetime is None:
            utc_datetime = datetime.utcnow()
        
        if utc_datetime.tzinfo is None:
            utc_datetime = utc_datetime.replace(tzinfo=ZoneInfo("UTC"))
        
        local_datetime = utc_datetime.astimezone(ZoneInfo(tzid))
        return local_datetime.date()
        
    except Exception as e:
        logger.error(f"로컬 날짜 변환 실패: {str(e)}")
        return date.today()

def parse_rrule(rrule_str: str) -> Optional[rrule.rrule]:
    """
    RRULE 문자열을 파싱하여 rrule 객체 생성
    
    Args:
        rrule_str: RRULE 문자열 (예: "FREQ=DAILY;" 또는 "FREQ=WEEKLY;BYDAY=MO,WE,FR")
    
    Returns:
        rrule 객체 또는 None
    """
    try:
        # RRULE 문자열을 파싱
        return rrule.rrulestr(rrule_str)
    except Exception as e:
        logger.error(f"RRULE 파싱 실패: {str(e)}")
        return None

def is_date_in_rrule(target_date: date, rrule_str: str, start_date: date, end_date: Optional[date] = None) -> bool:
    """
    특정 날짜가 RRULE에 포함되는지 확인
    
    Args:
        target_date: 확인할 날짜
        rrule_str: RRULE 문자열
        start_date: 시작 날짜
        end_date: 종료 날짜 (None이면 무제한)
    
    Returns:
        포함 여부
    """
    try:
        # RRULE 파싱
        rule = parse_rrule(rrule_str)
        if not rule:
            return False
        
        # 시작 날짜 설정
        rule.dtstart = start_date
        
        # 종료 날짜 설정
        if end_date:
            rule.until = end_date
        
        # target_date가 rule에 포함되는지 확인
        target_datetime = datetime.combine(target_date, datetime.min.time())
        
        # 다음 발생 시각이 target_date와 같은지 확인
        next_occurrence = rule.after(target_datetime - timedelta(days=1))
        
        if next_occurrence:
            return next_occurrence.date() == target_date
        
        return False
        
    except Exception as e:
        logger.error(f"RRULE 날짜 확인 실패: {str(e)}")
        return False

def get_redistribution_overrides(schedule_id: int, target_date: date, db_session) -> tuple[List[date], List[date]]:
    """
    재배치 정보에서 제외/포함 날짜들 가져오기
    
    Args:
        schedule_id: 스케줄 ID
        target_date: 확인할 날짜
        db_session: 데이터베이스 세션
    
    Returns:
        (제외할 날짜들, 포함할 날짜들)
    """
    try:
        from app.core.database import ScheduleRedistribution
        
        # 제외할 날짜들 (original_date = target_date)
        exclusions = db_session.query(ScheduleRedistribution.original_date).filter(
            ScheduleRedistribution.schedule_id == schedule_id,
            ScheduleRedistribution.original_date == target_date
        ).all()
        
        # 포함할 날짜들 (override_date = target_date)
        inclusions = db_session.query(ScheduleRedistribution.override_date).filter(
            ScheduleRedistribution.schedule_id == schedule_id,
            ScheduleRedistribution.override_date == target_date
        ).all()
        
        exclusion_dates = [row[0] for row in exclusions]
        inclusion_dates = [row[0] for row in inclusions]
        
        return exclusion_dates, inclusion_dates
        
    except Exception as e:
        logger.error(f"재배치 정보 조회 실패: {str(e)}")
        return [], []

def should_emit_for_date(schedule_id: int, target_date: date, rrule_str: str, 
                        start_date: date, end_date: Optional[date], db_session) -> bool:
    """
    특정 날짜에 스케줄을 발행해야 하는지 확인
    
    Args:
        schedule_id: 스케줄 ID
        target_date: 확인할 날짜
        rrule_str: RRULE 문자열
        start_date: 시작 날짜
        end_date: 종료 날짜
        db_session: 데이터베이스 세션
    
    Returns:
        발행 여부
    """
    try:
        # 1. 기본 RRULE 확인
        base_included = is_date_in_rrule(target_date, rrule_str, start_date, end_date)
        
        # 2. 재배치 정보 확인
        exclusions, inclusions = get_redistribution_overrides(schedule_id, target_date, db_session)
        
        # 3. 최종 판단
        if target_date in exclusions:
            return False  # 제외됨
        
        if target_date in inclusions:
            return True   # 명시적으로 포함됨
        
        return base_included  # 기본 RRULE 결과
        
    except Exception as e:
        logger.error(f"발행 여부 확인 실패: {str(e)}")
        return False

def convert_frequency_detail_to_rrule(frequency_detail: str, duration_weeks: int) -> str:
    """
    기존 frequency_detail을 RRULE 형식으로 변환
    
    Args:
        frequency_detail: 기존 형식 (예: "daily:1", "weekly:3")
        duration_weeks: 기간 (주)
    
    Returns:
        RRULE 문자열
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
                return "FREQ=WEEKLY;BYDAY=WE;"  # 수요일
            elif times == 2:
                return "FREQ=WEEKLY;BYDAY=MO,TH;"  # 월, 목
            elif times == 3:
                return "FREQ=WEEKLY;BYDAY=MO,WE,FR;"  # 월, 수, 금
            elif times == 4:
                return "FREQ=WEEKLY;BYDAY=MO,TU,TH,FR;"  # 월, 화, 목, 금
            elif times == 5:
                return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;"  # 월-금
            elif times == 6:
                return "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA;"  # 월-토
            else:
                return "FREQ=DAILY;"
        
        elif freq_type.lower() == 'monthly':
            if times >= 30:
                return "FREQ=DAILY;"
            elif times == 1:
                return "FREQ=MONTHLY;BYMONTHDAY=15;"  # 매월 15일
            elif times == 2:
                return "FREQ=MONTHLY;BYMONTHDAY=1,15;"  # 매월 1일, 15일
            else:
                return "FREQ=DAILY;"
        
        return "FREQ=DAILY;"
        
    except Exception as e:
        logger.error(f"frequency_detail 변환 실패: {str(e)}")
        return "FREQ=DAILY;"

def convert_date_between_timezones(date_str: str, from_tzid: str, to_tzid: str) -> str:
    """
    날짜 문자열을 한 시간대에서 다른 시간대로 변환
    
    Args:
        date_str: 날짜 문자열 (YYYY-MM-DD 또는 MM/DD/YYYY 형식)
        from_tzid: 원본 시간대 ID
        to_tzid: 대상 시간대 ID
    
    Returns:
        변환된 날짜 문자열 (YYYY-MM-DD 형식)
    """
    try:
        # 날짜 파싱
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
            logger.error(f"날짜 파싱 실패: {date_str}")
            return date_str
        
        # 원본 시간대에서 자정으로 datetime 생성
        from_tz = ZoneInfo(from_tzid)
        from_datetime = datetime.combine(parsed_date, datetime.min.time())
        from_datetime = from_datetime.replace(tzinfo=from_tz)
        
        # 대상 시간대로 변환
        to_tz = ZoneInfo(to_tzid)
        to_datetime = from_datetime.astimezone(to_tz)
        
        # 날짜만 반환
        converted_date = to_datetime.date()
        
        logger.info(f"날짜 변환: {date_str} ({from_tzid}) → {converted_date} ({to_tzid})")
        return converted_date.strftime("%Y-%m-%d")
        
    except Exception as e:
        logger.error(f"날짜 변환 실패: {str(e)}")
        return date_str

def convert_to_utc(date_str: str, timezone: str) -> datetime:
    """
    날짜 문자열을 UTC로 변환
    
    Args:
        date_str: 날짜 문자열 (YYYY-MM-DD 형식)
        timezone: 원본 시간대 ID
    
    Returns:
        UTC datetime
    """
    try:
        # 날짜 파싱
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        
        # 해당 시간대의 자정으로 datetime 생성
        tz = ZoneInfo(timezone)
        local_datetime = datetime.combine(parsed_date, datetime.min.time())
        local_datetime = local_datetime.replace(tzinfo=tz)
        
        # UTC로 변환
        utc_datetime = local_datetime.astimezone(ZoneInfo("UTC"))
        
        logger.info(f"UTC 변환: {date_str} ({timezone}) → {utc_datetime} (UTC)")
        return utc_datetime
        
    except Exception as e:
        logger.error(f"UTC 변환 실패: {str(e)}")
        return datetime.utcnow()

def convert_from_utc(utc_datetime: datetime, target_timezone: str) -> date:
    """
    UTC를 특정 시간대의 날짜로 변환
    
    Args:
        utc_datetime: UTC datetime
        target_timezone: 대상 시간대 ID
    
    Returns:
        대상 시간대의 날짜
    """
    try:
        if utc_datetime.tzinfo is None:
            utc_datetime = utc_datetime.replace(tzinfo=ZoneInfo("UTC"))
        
        target_tz = ZoneInfo(target_timezone)
        local_datetime = utc_datetime.astimezone(target_tz)
        
        logger.info(f"시간대 변환: {utc_datetime} (UTC) → {local_datetime.date()} ({target_timezone})")
        return local_datetime.date()
        
    except Exception as e:
        logger.error(f"시간대 변환 실패: {str(e)}")
        return date.today()

def get_user_current_date(uid: str, db_session) -> date:
    """
    사용자의 현재 시간대 기준 날짜 반환
    
    Args:
        uid: 사용자 ID
        db_session: 데이터베이스 세션
    
    Returns:
        사용자 현재 시간대의 날짜
    """
    try:
        from app.core.database import UserProfile
        
        user_profile = db_session.query(UserProfile).filter(UserProfile.uid == uid).first()
        current_timezone = user_profile.current_timezone if user_profile else "Asia/Seoul"
        
        tz = ZoneInfo(current_timezone)
        current_date = datetime.now(tz).date()
        
        return current_date
        
    except Exception as e:
        logger.error(f"사용자 현재 날짜 조회 실패: {str(e)}")
        return date.today()

