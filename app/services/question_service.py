from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from app.core.database import QuestionSession, UserResponse, UserProfile, generate_session_id
from app.models.question_models import SessionData, UserResponseData, SessionDataCreate, UserProfileCreate
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class QuestionService:
    def __init__(self, db: Session):
        self.db = db

    def create_session(self, device_id: str) -> str:
        """Create new question session with 24-hour expiration"""
        try:
            session_id = generate_session_id()
            expires_at = datetime.utcnow() + timedelta(hours=24)
            
            session = QuestionSession(
                session_id=session_id,
                device_id=device_id,
                expires_at=expires_at,
                status="active"
            )
            
            self.db.add(session)
            self.db.commit()
            
            logger.info(f"New session created: {session_id}, device: {device_id}")
            return session_id
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Session creation failed: {str(e)}")
            raise Exception(f"Session creation failed: {str(e)}")

    def get_session(self, session_id: str) -> Optional[QuestionSession]:
        """Get active session"""
        try:
            session = self.db.query(QuestionSession).filter(
                QuestionSession.session_id == session_id,
                QuestionSession.status == "active",
                QuestionSession.expires_at > datetime.utcnow()
            ).first()
            
            if not session:
                logger.warning(f"Session not found or expired: {session_id}")
                return None
                
            return session
        except Exception as e:
            logger.error(f"Session retrieval failed: {str(e)}")
            raise Exception(f"Session retrieval failed: {str(e)}")

    def save_session_data(self, session_id: str, data: SessionData) -> bool:
        """Save survey data to session"""
        try:
            session = self.get_session(session_id)
            if not session:
                raise Exception("세션을 찾을 수 없거나 만료되었습니다")
            
            # Update session with survey data
            if data.age is not None:
                session.age = data.age
            if data.period_description is not None:
                session.period_description = data.period_description
            if data.birth_control is not None:
                session.birth_control = data.birth_control
            if data.last_period_date is not None and data.survey_timezone is not None:
                # last_period_date가 이미 datetime 객체인 경우
                if isinstance(data.last_period_date, datetime):
                    # datetime을 해당 시간대의 자정으로 설정
                    from app.utils.timezone_utils import ZoneInfo
                    tz = ZoneInfo(data.survey_timezone)
                    local_datetime = data.last_period_date.replace(tzinfo=tz)
                    utc_datetime = local_datetime.astimezone(ZoneInfo("UTC"))
                    session.last_period_date_utc = utc_datetime
                else:
                    # 문자열인 경우 기존 로직 사용
                    from app.utils.timezone_utils import convert_to_utc
                    utc_datetime = convert_to_utc(data.last_period_date, data.survey_timezone)
                    session.last_period_date_utc = utc_datetime
            if data.cycle_length is not None:
                session.cycle_length = data.cycle_length
            if data.period_concerns is not None:
                session.period_concerns = data.period_concerns
            if data.body_concerns is not None:
                session.body_concerns = data.body_concerns
            if data.skin_hair_concerns is not None:
                session.skin_hair_concerns = data.skin_hair_concerns
            if data.mental_health_concerns is not None:
                session.mental_health_concerns = data.mental_health_concerns
            if data.other_concerns is not None:
                session.other_concerns = data.other_concerns
            if data.top_concern is not None:
                session.top_concern = data.top_concern
            if data.diagnosed_conditions is not None:
                session.diagnosed_conditions = data.diagnosed_conditions
            if data.family_history is not None:
                session.family_history = data.family_history
            if data.workout_intensity is not None:
                session.workout_intensity = data.workout_intensity
            if data.sleep_duration is not None:
                session.sleep_duration = data.sleep_duration
            if data.stress_level is not None:
                session.stress_level = data.stress_level
            if data.survey_timezone is not None:
                session.survey_timezone = data.survey_timezone
            
            self.db.commit()
            logger.info(f"Session data saved: {session_id}")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Session data save failed: {str(e)}")
            raise Exception(f"Session data save failed: {str(e)}")

    def create_user_profile(self, uid: str, name: str, email: str) -> UserProfile:
        """Create user profile (get or create)"""
        try:
            logger.info(f"=== create_user_profile 시작 ===")
            logger.info(f"매개변수: uid={uid}, name={name}, email={email}")
            
            # 기존 프로필이 있는지 확인
            logger.info(f"기존 프로필 조회: uid={uid}")
            existing_profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
            logger.info(f"기존 프로필 조회 결과: {existing_profile}")
            
            if existing_profile:
                # 기존 프로필 업데이트
                logger.info(f"기존 프로필 업데이트: uid={uid}")
                existing_profile.name = name
                existing_profile.email = email
                existing_profile.updated_at = datetime.utcnow()
                self.db.commit()
                logger.info(f"User profile updated: {uid}")
                return existing_profile
            else:
                # 새 프로필 생성
                logger.info(f"새 프로필 생성: uid={uid}")
                profile = UserProfile(
                    uid=uid,
                    name=name,
                    email=email
                )
                
                self.db.add(profile)
                self.db.commit()
                
                logger.info(f"User profile created: {uid}")
                return profile
            
        except Exception as e:
            logger.error(f"User profile creation/update failed: {str(e)}", exc_info=True)
            self.db.rollback()
            raise Exception(f"User profile creation/update failed: {str(e)}")

    def _convert_session_to_response_data(self, session: QuestionSession) -> UserResponseData:
        """세션 데이터를 익명화된 응답 데이터로 변환"""
        return UserResponseData(
            age=session.age,  # 그대로 복사
            period_description=session.period_description,
            birth_control=session.birth_control,
            last_period_date_utc=session.last_period_date_utc,  # UTC 그대로 복사
            cycle_length=session.cycle_length,
            period_concerns=session.period_concerns,
            body_concerns=session.body_concerns,
            skin_hair_concerns=session.skin_hair_concerns,
            mental_health_concerns=session.mental_health_concerns,
            other_concerns=session.other_concerns,
            top_concern=session.top_concern,
            diagnosed_conditions=session.diagnosed_conditions,
            family_history=session.family_history,
            workout_intensity=session.workout_intensity,
            sleep_duration=session.sleep_duration,
            stress_level=session.stress_level,
            survey_timezone=session.survey_timezone
        )

    def _convert_session_data_with_timezone(self, session: QuestionSession, survey_timezone: str, current_timezone: str) -> UserResponseData:
        """
        세션 데이터를 시간대 변환하여 익명화된 응답 데이터로 변환
        
        Args:
            session: 세션 데이터
            survey_timezone: 설문 입력 시점 시간대
            current_timezone: 현재 사용자 시간대
        
        Returns:
            변환된 응답 데이터
        """
        from app.utils.timezone_utils import convert_date_between_timezones
        
        # 날짜 변환 (last_period_date만 변환)
        converted_last_period_date = session.last_period_date
        if session.last_period_date and survey_timezone != current_timezone:
            converted_last_period_date = convert_date_between_timezones(
                session.last_period_date, 
                survey_timezone, 
                current_timezone
            )
        
        return UserResponseData(
            age=session.age,
            period_description=session.period_description,
            birth_control=session.birth_control,
            last_period_date=converted_last_period_date,  # 변환된 날짜
            cycle_length=session.cycle_length,
            period_concerns=session.period_concerns,
            body_concerns=session.body_concerns,
            skin_hair_concerns=session.skin_hair_concerns,
            mental_health_concerns=session.mental_health_concerns,
            other_concerns=session.other_concerns,
            top_concern=session.top_concern,
            diagnosed_conditions=session.diagnosed_conditions,
            family_history=session.family_history,
            workout_intensity=session.workout_intensity,
            sleep_duration=session.sleep_duration,
            stress_level=session.stress_level,
            current_timezone=current_timezone
        )

    def link_session_to_user(self, session_id: str, uid: str, name: str, email: str, current_timezone: str = "Asia/Seoul") -> bool:
        """
        세션을 사용자에게 연결하고 영구 저장
        
        Args:
            session_id: 세션 ID
            uid: 사용자 ID
            name: 사용자 이름
            email: 사용자 이메일
            current_timezone: 현재 사용자 시간대
        
        Returns:
            성공 여부
        """
        try:
            logger.info(f"=== QuestionService.link_session_to_user 시작 ===")
            logger.info(f"매개변수: session_id={session_id}, uid={uid}, name={name}, email={email}, current_timezone={current_timezone}")
            
            # 1. 세션 데이터 조회
            session = self.get_session(session_id)
            logger.info(f"세션 조회 결과: session={session}")
            if not session:
                logger.error(f"세션을 찾을 수 없음: session_id={session_id}")
                raise Exception("세션을 찾을 수 없거나 만료되었습니다")
            
            # 2. 사용자 프로필 생성 (현재 시간대 저장)
            logger.info(f"사용자 프로필 생성 시작: uid={uid}")
            user_profile = self.create_user_profile(uid, name, email)
            # UserProfile에 current_timezone 저장
            user_profile.current_timezone = current_timezone
            self.db.commit()
            logger.info(f"사용자 프로필 생성 완료: uid={uid}, timezone={current_timezone}")
            
            # 3. UserResponse 생성 (UTC 데이터 그대로 저장)
            response_data = self._convert_session_to_response_data(session)
            
            user_response = UserResponse(
                uid=uid,
                age=response_data.age,
                period_description=response_data.period_description,
                birth_control=response_data.birth_control,
                last_period_date_utc=response_data.last_period_date_utc,  # UTC 그대로 저장
                cycle_length=response_data.cycle_length,
                period_concerns=response_data.period_concerns,
                body_concerns=response_data.body_concerns,
                skin_hair_concerns=response_data.skin_hair_concerns,
                mental_health_concerns=response_data.mental_health_concerns,
                other_concerns=response_data.other_concerns,
                top_concern=response_data.top_concern,
                diagnosed_conditions=response_data.diagnosed_conditions,
                family_history=response_data.family_history,
                workout_intensity=response_data.workout_intensity,
                sleep_duration=response_data.sleep_duration,
                stress_level=response_data.stress_level,
                survey_timezone=response_data.survey_timezone,
                primary_hormone=session.primary_hormone,
                secondary_hormones=session.secondary_hormones
            )
            self.db.add(user_response)
            logger.info(f"Session data saved for user {uid}")
            
            # 4. 세션 연결된 추천을 영구 저장으로 이전
            logger.info(f"세션 추천 영구 저장 이전 시작: session_id={session_id}")
            try:
                from app.core.database import RecommendationRecord, RecommendationAdvice
                
                # 세션 연결된 추천들을 찾아서 uid로 업데이트
                session_recommendations = self.db.query(RecommendationRecord).filter(
                    RecommendationRecord.session_id == session_id
                ).all()
                
                # 추천별로 개별 업데이트 및 즉시 커밋
                updated_recommendations = []
                failed_recommendations = []
                
                for rec in session_recommendations:
                    # 각 추천을 독립적인 서브트랜잭션으로 처리
                    savepoint = self.db.begin_nested()  # 서브트랜잭션 시작
                    try:
                        rec.uid = uid
                        rec.session_id = None  # 세션 ID 제거
                        savepoint.commit()  # 서브트랜잭션 커밋
                        updated_recommendations.append(rec.id)
                        logger.info(f"추천 영구 저장 이전 성공: recommendation_id={rec.id}")
                    except Exception as e:
                        logger.error(f"추천 영구 저장 이전 실패: recommendation_id={rec.id}, error={str(e)}")
                        failed_recommendations.append(rec.id)
                        savepoint.rollback()  # 개별 서브트랜잭션만 롤백
                        continue
                
                # 세션 연결된 조언들도 uid로 업데이트
                session_advices = self.db.query(RecommendationAdvice).filter(
                    RecommendationAdvice.session_id == session_id
                ).all()
                
                updated_advices = []
                failed_advices = []
                
                for advice in session_advices:
                    # 각 조언을 독립적인 서브트랜잭션으로 처리
                    savepoint = self.db.begin_nested()  # 서브트랜잭션 시작
                    try:
                        advice.uid = uid
                        advice.session_id = None  # 세션 ID 제거
                        savepoint.commit()  # 서브트랜잭션 커밋
                        updated_advices.append(advice.id)
                        logger.info(f"조언 영구 저장 이전 성공: advice_id={advice.id}")
                    except Exception as e:
                        logger.error(f"조언 영구 저장 이전 실패: advice_id={advice.id}, error={str(e)}")
                        failed_advices.append(advice.id)
                        savepoint.rollback()  # 개별 서브트랜잭션만 롤백
                        continue
                
                logger.info(f"세션 추천 영구 저장 이전 완료: {len(updated_recommendations)}개 추천 성공, {len(failed_recommendations)}개 실패")
                logger.info(f"세션 조언 영구 저장 이전 완료: {len(updated_advices)}개 조언 성공, {len(failed_advices)}개 실패")
                
                # 성공한 추천들만 스케줄 생성에 사용
                successful_recommendations = [rec for rec in session_recommendations if rec.id in updated_recommendations]
                
                # 5. 자동 스케줄 생성 (성공한 추천들만)
                logger.info(f"자동 스케줄 생성 시작: {len(successful_recommendations)}개 추천")
                try:
                    from app.services.new_scheduling_service import NewSchedulingService
                    scheduling_service = NewSchedulingService(self.db)
                    
                    created_schedules = []
                    failed_schedules = []
                    
                    for rec in successful_recommendations:
                        # 각 스케줄을 독립적인 서브트랜잭션으로 처리
                        savepoint = self.db.begin_nested()  # 서브트랜잭션 시작
                        try:
                            # 현재 사용자 시간대로 스케줄 생성
                            schedule = scheduling_service.create_schedule_from_recommendation(rec, current_timezone)
                            savepoint.commit()  # 서브트랜잭션 커밋
                            created_schedules.append(schedule.id)
                            logger.info(f"스케줄 생성 완료: recommendation_id={rec.id}, schedule_id={schedule.id}, timezone={current_timezone}")
                        except Exception as e:
                            logger.error(f"개별 스케줄 생성 실패: recommendation_id={rec.id}, error={str(e)}")
                            failed_schedules.append(rec.id)
                            savepoint.rollback()  # 개별 서브트랜잭션만 롤백
                    
                    logger.info(f"자동 스케줄 생성 완료: {len(created_schedules)}개 스케줄 생성됨, {len(failed_schedules)}개 실패")
                    
                except Exception as e:
                    logger.error(f"자동 스케줄 생성 실패: {str(e)}", exc_info=True)
                
                # 6. 세션 삭제
                self.db.delete(session)
                logger.info(f"세션 삭제 완료: {session_id}")
                
                # 7. 커밋
                self.db.commit()
                
                # 결과 요약 로깅
                total_recommendations = len(session_recommendations)
                total_advices = len(session_advices)
                success_rate_rec = len(updated_recommendations) / total_recommendations * 100 if total_recommendations > 0 else 0
                success_rate_adv = len(updated_advices) / total_advices * 100 if total_advices > 0 else 0
                
                logger.info(f"세션 연결 완료: session_id={session_id}, uid={uid}")
                logger.info(f"추천 연결 성공률: {success_rate_rec:.1f}% ({len(updated_recommendations)}/{total_recommendations})")
                logger.info(f"조언 연결 성공률: {success_rate_adv:.1f}% ({len(updated_advices)}/{total_advices})")
                logger.info(f"스케줄 생성 성공률: {len(created_schedules)}/{len(successful_recommendations)} 개")
                
                # 일부라도 성공했으면 True 반환
                return len(updated_recommendations) > 0 or len(updated_advices) > 0
                
            except Exception as e:
                logger.error(f"세션 추천 영구 저장 이전 실패: {str(e)}", exc_info=True)
                self.db.rollback()
                return False
                
        except Exception as e:
            logger.error(f"세션 연결 실패: {str(e)}", exc_info=True)
            self.db.rollback()
            return False

    def get_user_responses(self, uid: str) -> List[UserResponse]:
        """사용자의 모든 응답 조회"""
        try:
            return self.db.query(UserResponse).filter(
                UserResponse.uid == uid
            ).order_by(UserResponse.created_at.desc()).all()
        except Exception as e:
            logger.error(f"User response retrieval failed: {str(e)}")
            raise Exception(f"사용자 응답 조회 실패: {str(e)}")

    def get_session_data(self, session_id: str) -> Optional[SessionData]:
        """세션 데이터 조회"""
        try:
            session = self.get_session(session_id)
            if not session:
                return None
            
            return SessionData(
                age=session.age,
                period_description=session.period_description,
                birth_control=session.birth_control,
                last_period_date=session.last_period_date_utc,
                cycle_length=session.cycle_length,
                period_concerns=session.period_concerns,
                body_concerns=session.body_concerns,
                skin_hair_concerns=session.skin_hair_concerns,
                mental_health_concerns=session.mental_health_concerns,
                other_concerns=session.other_concerns,
                top_concern=session.top_concern,
                diagnosed_conditions=session.diagnosed_conditions,
                family_history=session.family_history,
                workout_intensity=session.workout_intensity,
                sleep_duration=session.sleep_duration,
                stress_level=session.stress_level
            )
        except Exception as e:
            logger.error(f"Session data retrieval failed: {str(e)}")
            raise Exception(f"세션 데이터 조회 실패: {str(e)}")

    def cleanup_expired_sessions(self) -> int:
        """만료된 세션 정리 (연결된 추천도 함께 삭제)"""
        try:
            expired_sessions = self.db.query(QuestionSession).filter(
                QuestionSession.expires_at <= datetime.utcnow()
            ).all()
            
            count = len(expired_sessions)
            for session in expired_sessions:
                # 세션 연결된 추천과 조언도 함께 삭제
                try:
                    from app.core.database import RecommendationRecord, RecommendationAdvice
                    
                    # 세션 연결된 추천들 삭제
                    session_recommendations = self.db.query(RecommendationRecord).filter(
                        RecommendationRecord.session_id == session.session_id
                    ).all()
                    
                    for rec in session_recommendations:
                        # 추천에 연결된 조언들도 삭제
                        rec_advices = self.db.query(RecommendationAdvice).filter(
                            RecommendationAdvice.recommendation_id == rec.id
                        ).all()
                        for advice in rec_advices:
                            self.db.delete(advice)
                        
                        self.db.delete(rec)
                    
                    logger.info(f"세션 {session.session_id} 연결된 추천 {len(session_recommendations)}개 삭제")
                    
                except Exception as e:
                    logger.error(f"세션 추천 삭제 실패: {str(e)}", exc_info=True)
                
                # 세션 삭제
                self.db.delete(session)
            
            self.db.commit()
            logger.info(f"Cleaned up {count} expired sessions")
            return count
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Session cleanup failed: {str(e)}")
            raise Exception(f"세션 정리 실패: {str(e)}") 

    def update_user_timezone(self, uid: str, new_timezone: str) -> bool:
        """사용자 시간대 업데이트"""
        try:
            logger.info(f"시간대 업데이트 시작: uid={uid}, new_timezone={new_timezone}")
            
            # UserProfile 조회
            user_profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
            if not user_profile:
                logger.error(f"사용자 프로필 없음: uid={uid}")
                return False
            
            # 기존 시간대 저장
            old_timezone = user_profile.current_timezone
            
            # 시간대 업데이트
            user_profile.current_timezone = new_timezone
            user_profile.updated_at = datetime.utcnow()
            
            self.db.commit()
            
            logger.info(f"시간대 변경 완료: {uid}, {old_timezone} → {new_timezone}")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"시간대 업데이트 실패: {str(e)}")
            return False 