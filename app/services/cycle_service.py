import logging
from datetime import datetime, date, timedelta
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app.core.database import UserResponse, UserProfile
from app.models.cycle_models import CyclePhaseInfo
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

class CycleService:
    """생리 주기 계산 서비스"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_cycle_phase_info(self, uid: str) -> CyclePhaseInfo:
        """
        사용자의 생리 주기 정보 계산
        
        Args:
            uid: 사용자 ID
        
        Returns:
            생리 주기 정보
        """
        try:
            # 사용자 프로필 조회 (현재 시간대)
            user_profile = self.db.query(UserProfile).filter(
                UserProfile.uid == uid
            ).first()
            
            if not user_profile:
                logger.info(f"사용자 프로필 없음: uid={uid}")
                return CyclePhaseInfo(
                    user_name="Unknown",
                    cycle_day=None,
                    phase=None
                )
            
            # 사용자 응답 데이터 조회
            user_response = self.db.query(UserResponse).filter(
                UserResponse.uid == uid
            ).first()
            
            if not user_response:
                logger.info(f"사용자 응답 데이터 없음: uid={uid}")
                return CyclePhaseInfo(
                    user_name=user_profile.name or "Unknown",
                    cycle_day=None,
                    phase=None
                )
            
            # 필수 데이터 확인
            logger.info(f"데이터 확인: last_period_date_utc={user_response.last_period_date_utc}, cycle_length={user_response.cycle_length}")
            if not user_response.last_period_date_utc or not user_response.cycle_length:
                logger.info(f"필수 데이터 없음: last_period_date_utc={user_response.last_period_date_utc}, cycle_length={user_response.cycle_length}")
                return CyclePhaseInfo(
                    user_name=user_profile.name or "Unknown",
                    cycle_day=None,
                    phase=None
                )
            
            # 생리 주기 계산 (사용자 현재 시간대 기준)
            cycle_day, phase = self._calculate_cycle_phase(
                user_response.last_period_date_utc,
                user_response.cycle_length,
                user_response.period_description,
                user_response.diagnosed_conditions,
                user_profile.current_timezone
            )
            
            logger.info(f"계산 결과: cycle_day={cycle_day}, phase={phase}")
            
            return CyclePhaseInfo(
                user_name=user_profile.name or "Unknown",
                cycle_day=cycle_day,
                phase=phase
            )
            
        except Exception as e:
            logger.error(f"생리 주기 정보 계산 실패: {str(e)}")
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
        생리 주기와 페이즈 계산
        
        Args:
            last_period_date_utc: 마지막 생리 시작일 (UTC)
            cycle_length: 생리 주기
            period_description: 생리 상태 설명
            diagnosed_conditions: 진단된 질환들
            user_timezone: 사용자의 현재 시간대
        
        Returns:
            (cycle_day, phase)
        """
        try:
            logger.info(f"계산 시작: last_period_date_utc={last_period_date_utc}, cycle_length={cycle_length}")
            logger.info(f"추가 데이터: period_description={period_description}, diagnosed_conditions={diagnosed_conditions}")
            logger.info(f"사용자 시간대: {user_timezone}")
            
            # UTC 날짜를 사용자 시간대로 변환
            from app.utils.timezone_utils import convert_from_utc
            last_period = convert_from_utc(last_period_date_utc, user_timezone)
            logger.info(f"변환된 마지막 생리일: {last_period}")
            
            # 주기 길이 파싱
            cycle_days = self._parse_cycle_length(cycle_length)
            if not cycle_days:
                logger.info(f"주기 길이 파싱 실패: {cycle_length}")
                return None, None
            
            logger.info(f"파싱된 주기 길이: {cycle_days}일")
            
            # 현재 날짜 (사용자 시간대 기준)
            if not user_timezone:
                user_timezone = "Asia/Seoul" # 기본값
                logger.warning(f"사용자 시간대 없음, 기본값 사용: {user_timezone}")
            
            try:
                tz = ZoneInfo(user_timezone)
                current_date = datetime.now(tz).date()
                logger.info(f"현재 날짜 (사용자 시간대 {user_timezone}): {current_date}")
            except Exception as e:
                logger.warning(f"시간대 파싱 실패, 기본값 사용: {e}")
                # 기본값으로 한국 시간대 사용
                korea_tz = ZoneInfo("Asia/Seoul")
                current_date = datetime.now(korea_tz).date()
                logger.info(f"현재 날짜 (기본 한국 시간): {current_date}")
            
            # 마지막 생리일부터 경과일 계산
            days_since_last = (current_date - last_period).days
            logger.info(f"마지막 생리일부터 경과일: {days_since_last}일")
            
            # 음수인 경우 (미래 날짜)
            if days_since_last < 0:
                logger.info(f"미래 날짜로 인식됨: days_since_last={days_since_last}")
                return None, None
            
            # 현재 주기 내에서의 일수 계산
            cycle_day = (days_since_last % cycle_days) + 1
            logger.info(f"계산된 주기 일수: {cycle_day}일")
            
            # 페이즈 판단이 어려운 경우 확인
            if self._is_phase_unclear(period_description, diagnosed_conditions):
                logger.info(f"페이즈 판단이 어려운 경우: period_description={period_description}, diagnosed_conditions={diagnosed_conditions}")
                return cycle_day, "Cycle Phase unclear"
            
            # 페이즈 계산
            phase = self._determine_phase(cycle_day, cycle_days)
            logger.info(f"결정된 페이즈: {phase}")
            
            return cycle_day, phase
            
        except Exception as e:
            logger.error(f"생리 주기 계산 실패: {str(e)}")
            return None, None
    
    def _parse_date(self, date_str: str) -> Optional[date]:
        """날짜 문자열 파싱"""
        try:
            # 다양한 날짜 형식 지원
            formats = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"]
            
            for fmt in formats:
                try:
                    return datetime.strptime(date_str, fmt).date()
                except ValueError:
                    continue
            
            return None
            
        except Exception as e:
            logger.error(f"날짜 파싱 실패: {str(e)}")
            return None
    
    def _parse_cycle_length(self, cycle_length: str) -> Optional[int]:
        """생리 주기 길이 파싱"""
        try:
            if cycle_length == "Less than 21 days":
                return 21
            elif cycle_length == "21-25 days":
                return 23  # 중간값
            elif cycle_length == "26-30 days":
                return 28  # 중간값
            elif cycle_length == "31-35 days":
                return 33  # 중간값
            elif cycle_length == "35+ days":
                return 35
            else:
                return None
                
        except Exception as e:
            logger.error(f"주기 길이 파싱 실패: {str(e)}")
            return None
    
    def _is_phase_unclear(self, period_description: Optional[str], 
                         diagnosed_conditions: Optional[list]) -> bool:
        """페이즈 판단이 어려운 경우 확인"""
        try:
            # 불규칙한 생리
            if period_description in ["Irregular", "Occasional Skips", "I don't get periods", "I'm not sure"]:
                return True
            
            # 특정 질환들
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
            logger.error(f"페이즈 명확성 확인 실패: {str(e)}")
            return True
    
    def _determine_phase(self, cycle_day: int, cycle_days: int) -> str:
        """생리 주기 페이즈 결정"""
        try:
            # 표준 28일 주기 기준으로 조정
            if cycle_days != 28:
                # 비례적으로 조정
                adjusted_day = int((cycle_day - 1) * 28 / cycle_days) + 1
            else:
                adjusted_day = cycle_day
            
            # 페이즈 결정
            if adjusted_day <= 5:
                return "Menses phase"
            elif adjusted_day <= 14:
                return "Follicular phase"
            elif adjusted_day <= 16:
                return "Ovulation phase"
            else:
                return "Luteal phase"
                
        except Exception as e:
            logger.error(f"페이즈 결정 실패: {str(e)}")
            return "Cycle Phase unclear"
