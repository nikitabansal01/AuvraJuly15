from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from app.core.database import QuestionSession, UserResponse, generate_session_id
from app.models.question_models import UserResponseData, SessionCreate
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class QuestionService:
    def __init__(self, db: Session):
        self.db = db

    def create_session(self, device_id: str, uid: Optional[str] = None) -> str:
        """Create new question session"""
        try:
            session_id = generate_session_id()
            
            session = QuestionSession(
                session_id=session_id,
                uid=uid,
                device_id=device_id,
                status="in_progress"
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
        """Get session"""
        try:
            return self.db.query(QuestionSession).filter(
                QuestionSession.session_id == session_id
            ).first()
        except Exception as e:
            logger.error(f"Session retrieval failed: {str(e)}")
            raise Exception(f"Session retrieval failed: {str(e)}")

    def link_session_to_user(self, session_id: str, uid: str) -> bool:
        """세션을 사용자와 연결"""
        try:
            session = self.get_session(session_id)
            if not session:
                raise Exception("세션을 찾을 수 없습니다")
            
            # Update uid of existing session
            session.uid = uid
            session.status = "linked"
            session.completed_at = datetime.utcnow()
            
            # 관련된 응답들도 uid 업데이트
            responses = self.db.query(UserResponse).filter(
                UserResponse.session_id == session_id
            ).all()
            
            for response in responses:
                response.uid = uid
            
            self.db.commit()
            logger.info(f"Session {session_id} linked to user {uid}")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Session linking failed: {str(e)}")
            raise Exception(f"세션 연결 실패: {str(e)}")

    def save_user_responses(self, session_id: str, responses: UserResponseData, uid: Optional[str] = None) -> UserResponse:
        """사용자 응답 저장"""
        try:
            # 기존 응답이 있는지 확인
            existing_response = self.db.query(UserResponse).filter(
                UserResponse.session_id == session_id
            ).first()
            
            if existing_response:
                # 기존 응답 업데이트
                self._update_response_fields(existing_response, responses)
                existing_response.uid = uid or existing_response.uid
                existing_response.updated_at = datetime.utcnow()
                self.db.commit()
                logger.info(f"Response updated: session {session_id}")
                return existing_response
            else:
                # 새 응답 생성
                new_response = UserResponse(
                    session_id=session_id,
                    uid=uid,
                    name=responses.name,
                    age=responses.age,
                    period_description=responses.period_description,
                    birth_control=responses.birth_control,
                    last_period_date=responses.last_period_date,
                    cycle_length=responses.cycle_length,
                    period_concerns=responses.period_concerns,
                    body_concerns=responses.body_concerns,
                    skin_hair_concerns=responses.skin_hair_concerns,
                    mental_health_concerns=responses.mental_health_concerns,
                    other_concerns=responses.other_concerns,
                    top_concern=responses.top_concern,
                    diagnosed_conditions=responses.diagnosed_conditions
                )
                
                self.db.add(new_response)
                self.db.commit()
                logger.info(f"New response saved: session {session_id}")
                return new_response
                
        except Exception as e:
            self.db.rollback()
            logger.error(f"Response save failed: {str(e)}")
            raise Exception(f"응답 저장 실패: {str(e)}")

    def _update_response_fields(self, response: UserResponse, new_data: UserResponseData):
        """응답 필드 업데이트 (None이 아닌 값만)"""
        if new_data.name is not None:
            response.name = new_data.name
        if new_data.age is not None:
            response.age = new_data.age
        if new_data.period_description is not None:
            response.period_description = new_data.period_description
        if new_data.birth_control is not None:
            response.birth_control = new_data.birth_control
        if new_data.last_period_date is not None:
            response.last_period_date = new_data.last_period_date
        if new_data.cycle_length is not None:
            response.cycle_length = new_data.cycle_length
        if new_data.period_concerns is not None:
            response.period_concerns = new_data.period_concerns
        if new_data.body_concerns is not None:
            response.body_concerns = new_data.body_concerns
        if new_data.skin_hair_concerns is not None:
            response.skin_hair_concerns = new_data.skin_hair_concerns
        if new_data.mental_health_concerns is not None:
            response.mental_health_concerns = new_data.mental_health_concerns
        if new_data.other_concerns is not None:
            response.other_concerns = new_data.other_concerns
        if new_data.top_concern is not None:
            response.top_concern = new_data.top_concern
        if new_data.diagnosed_conditions is not None:
            response.diagnosed_conditions = new_data.diagnosed_conditions

    def get_user_responses(self, uid: str) -> List[UserResponse]:
        """사용자의 모든 응답 조회"""
        try:
            return self.db.query(UserResponse).filter(
                UserResponse.uid == uid
            ).order_by(UserResponse.created_at.desc()).all()
        except Exception as e:
            logger.error(f"User response retrieval failed: {str(e)}")
            raise Exception(f"사용자 응답 조회 실패: {str(e)}")

    def get_session_responses(self, session_id: str) -> Optional[UserResponse]:
        """세션의 응답 조회"""
        try:
            return self.db.query(UserResponse).filter(
                UserResponse.session_id == session_id
            ).first()
        except Exception as e:
            logger.error(f"Session response retrieval failed: {str(e)}")
            raise Exception(f"세션 응답 조회 실패: {str(e)}")

    def merge_user_sessions(self, uid: str, session_ids: List[str]) -> bool:
        """여러 세션을 하나의 사용자로 병합"""
        try:
            # Collect response data from all sessions
            all_responses = []
            for session_id in session_ids:
                response = self.get_session_responses(session_id)
                if response:
                    all_responses.append(response)
            
            if not all_responses:
                return False
            
            # 가장 최신 응답을 기준으로 병합
            latest_response = max(all_responses, key=lambda x: x.created_at)
            
            # Merge data from other sessions into the latest response
            for response in all_responses:
                if response.id != latest_response.id:
                    self._merge_response_data(latest_response, response)
            
            # 최신 응답의 uid 업데이트
            latest_response.uid = uid
            latest_response.updated_at = datetime.utcnow()
            
            # Delete other sessions
            for session_id in session_ids:
                if session_id != latest_response.session_id:
                    self.db.query(UserResponse).filter(
                        UserResponse.session_id == session_id
                    ).delete()
            
            self.db.commit()
            logger.info(f"Session merge completed: user {uid}")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Session merge failed: {str(e)}")
            raise Exception(f"세션 병합 실패: {str(e)}")

    def _merge_response_data(self, target: UserResponse, source: UserResponse):
        """응답 데이터 병합 (None이 아닌 값만)"""
        if source.name and not target.name:
            target.name = source.name
        if source.age and not target.age:
            target.age = source.age
        if source.period_description and not target.period_description:
            target.period_description = source.period_description
        if source.birth_control and not target.birth_control:
            target.birth_control = source.birth_control
        if source.last_period_date and not target.last_period_date:
            target.last_period_date = source.last_period_date
        if source.cycle_length and not target.cycle_length:
            target.cycle_length = source.cycle_length
        if source.period_concerns and not target.period_concerns:
            target.period_concerns = source.period_concerns
        if source.body_concerns and not target.body_concerns:
            target.body_concerns = source.body_concerns
        if source.skin_hair_concerns and not target.skin_hair_concerns:
            target.skin_hair_concerns = source.skin_hair_concerns
        if source.mental_health_concerns and not target.mental_health_concerns:
            target.mental_health_concerns = source.mental_health_concerns
        if source.other_concerns and not target.other_concerns:
            target.other_concerns = source.other_concerns
        if source.top_concern and not target.top_concern:
            target.top_concern = source.top_concern
        if source.diagnosed_conditions and not target.diagnosed_conditions:
            target.diagnosed_conditions = source.diagnosed_conditions

    def get_analytics(self) -> Dict[str, Any]:
        """분석 데이터 조회"""
        try:
            # 총 사용자 수
            total_users = self.db.query(UserResponse).filter(
                UserResponse.uid.isnot(None)
            ).distinct(UserResponse.uid).count()
            
            # 나이 분포
            age_distribution = {}
            age_responses = self.db.query(UserResponse.age).filter(
                and_(UserResponse.age.isnot(None), UserResponse.uid.isnot(None))
            ).all()
            
            for age_response in age_responses:
                age = age_response[0]
                if age:
                    age_group = f"{(age // 10) * 10}대"
                    age_distribution[age_group] = age_distribution.get(age_group, 0) + 1
            
            # 건강 문제 통계
            period_concerns_stats = self._get_concerns_stats("period_concerns")
            body_concerns_stats = self._get_concerns_stats("body_concerns")
            top_concerns_stats = self._get_top_concerns_stats()
            
            return {
                "total_users": total_users,
                "age_distribution": age_distribution,
                "period_concerns_stats": period_concerns_stats,
                "body_concerns_stats": body_concerns_stats,
                "top_concerns_stats": top_concerns_stats
            }
            
        except Exception as e:
            logger.error(f"Analytics data retrieval failed: {str(e)}")
            raise Exception(f"분석 데이터 조회 실패: {str(e)}")

    def _get_concerns_stats(self, field_name: str) -> Dict[str, int]:
        """특정 건강 문제 필드의 통계"""
        stats = {}
        responses = self.db.query(getattr(UserResponse, field_name)).filter(
            and_(getattr(UserResponse, field_name).isnot(None), UserResponse.uid.isnot(None))
        ).all()
        
        for response in responses:
            concerns = response[0]
            if concerns:
                for concern in concerns:
                    stats[concern] = stats.get(concern, 0) + 1
        
        return stats

    def _get_top_concerns_stats(self) -> Dict[str, int]:
        """최우선 문제 통계"""
        stats = {}
        responses = self.db.query(UserResponse.top_concern).filter(
            and_(UserResponse.top_concern.isnot(None), UserResponse.uid.isnot(None))
        ).all()
        
        for response in responses:
            concern = response[0]
            if concern:
                stats[concern] = stats.get(concern, 0) + 1
        
        return stats 