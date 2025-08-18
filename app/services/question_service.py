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
            if data.last_period_date is not None:
                session.last_period_date = data.last_period_date
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
            
            self.db.commit()
            logger.info(f"Session data saved: {session_id}")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Session data save failed: {str(e)}")
            raise Exception(f"Session data save failed: {str(e)}")

    def create_user_profile(self, uid: str, name: str, email: str) -> UserProfile:
        """Create user profile"""
        try:
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
            self.db.rollback()
            logger.error(f"User profile creation failed: {str(e)}")
            raise Exception(f"User profile creation failed: {str(e)}")

    def _convert_session_to_response_data(self, session: QuestionSession) -> UserResponseData:
        """세션 데이터를 익명화된 응답 데이터로 변환"""
        return UserResponseData(
            age=session.age,  # 그대로 복사
            period_description=session.period_description,
            birth_control=session.birth_control,
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

    def link_session_to_user(self, session_id: str, uid: str, name: str, email: str) -> bool:
        """세션을 사용자와 연결하고 세션 삭제"""
        try:
            session = self.get_session(session_id)
            if not session:
                raise Exception("세션을 찾을 수 없거나 만료되었습니다")
            
            # 1. 사용자 프로필 생성
            self.create_user_profile(uid, name, email)
            
            # 2. 세션 데이터를 익명화하여 영구 저장
            response_data = self._convert_session_to_response_data(session)
            new_response = UserResponse(
                uid=uid,
                age=response_data.age,
                period_description=response_data.period_description,
                birth_control=response_data.birth_control,
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
                stress_level=response_data.stress_level
            )
            
            self.db.add(new_response)
            
            # 3. 세션 삭제
            self.db.delete(session)
            
            self.db.commit()
            logger.info(f"Session {session_id} linked to user {uid} and deleted")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Session linking failed: {str(e)}")
            raise Exception(f"세션 연결 실패: {str(e)}")

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
                last_period_date=session.last_period_date,
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
        """만료된 세션 정리"""
        try:
            expired_sessions = self.db.query(QuestionSession).filter(
                QuestionSession.expires_at <= datetime.utcnow()
            ).all()
            
            count = len(expired_sessions)
            for session in expired_sessions:
                self.db.delete(session)
            
            self.db.commit()
            logger.info(f"Cleaned up {count} expired sessions")
            return count
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Session cleanup failed: {str(e)}")
            raise Exception(f"세션 정리 실패: {str(e)}") 