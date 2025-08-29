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
                raise Exception("Session not found or expired")
            
            # Update session with survey data
            if data.age is not None:
                session.age = data.age
            if data.period_description is not None:
                session.period_description = data.period_description
            if data.birth_control is not None:
                session.birth_control = data.birth_control
            if data.last_period_date is not None and data.survey_timezone is not None:
                # If last_period_date is already a datetime object
                if isinstance(data.last_period_date, datetime):
                    # Set datetime to midnight in the timezone
                    from app.utils.timezone_utils import ZoneInfo
                    tz = ZoneInfo(data.survey_timezone)
                    local_datetime = data.last_period_date.replace(tzinfo=tz)
                    utc_datetime = local_datetime.astimezone(ZoneInfo("UTC"))
                    session.last_period_date_utc = utc_datetime
                else:
                    # Use existing logic for string
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
            logger.info(f"=== create_user_profile started ===")
            logger.info(f"Parameters: uid={uid}, name={name}, email={email}")
            
            # Check if profile exists
            logger.info(f"Checking existing profile: uid={uid}")
            existing_profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
            logger.info(f"Existing profile check result: {existing_profile}")
            
            if existing_profile:
                # Update existing profile
                logger.info(f"Updating existing profile: uid={uid}")
                existing_profile.name = name
                existing_profile.email = email
                existing_profile.updated_at = datetime.utcnow()
                self.db.commit()
                logger.info(f"User profile updated: {uid}")
                return existing_profile
            else:
                # Create new profile
                logger.info(f"Creating new profile: uid={uid}")
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
        """Convert session data to anonymized response data"""
        return UserResponseData(
            age=session.age,  # Copy as is
            period_description=session.period_description,
            birth_control=session.birth_control,
            last_period_date_utc=session.last_period_date_utc,  # Copy UTC as is
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
        Convert session data with timezone conversion to anonymized response data
        
        Args:
            session: Session data
            survey_timezone: Timezone when survey was taken
            current_timezone: Current user timezone
        
        Returns:
            Converted response data
        """
        from app.utils.timezone_utils import convert_date_between_timezones
        
        # Convert date (only last_period_date)
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
            last_period_date=converted_last_period_date,  # Converted date
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
        Link session to user and save permanently
        
        Args:
            session_id: Session ID
            uid: User ID
            name: User name
            email: User email
            current_timezone: Current user timezone
        
        Returns:
            Success status
        """
        try:
            logger.info(f"=== QuestionService.link_session_to_user started ===")
            logger.info(f"Parameters: session_id={session_id}, uid={uid}, name={name}, email={email}, current_timezone={current_timezone}")
            
            # 1. Get session data
            session = self.get_session(session_id)
            logger.info(f"Session retrieval result: session={session}")
            if not session:
                logger.error(f"Session not found: session_id={session_id}")
                raise Exception("Session not found or expired")
            
            # 2. Create user profile (save current timezone)
            logger.info(f"Creating user profile: uid={uid}")
            user_profile = self.create_user_profile(uid, name, email)
            # Save current_timezone to UserProfile
            user_profile.current_timezone = current_timezone
            self.db.commit()
            logger.info(f"User profile creation completed: uid={uid}, timezone={current_timezone}")
            
            # 3. Create UserResponse (save UTC data as is)
            response_data = self._convert_session_to_response_data(session)
            
            user_response = UserResponse(
                uid=uid,
                age=response_data.age,
                period_description=response_data.period_description,
                birth_control=response_data.birth_control,
                last_period_date_utc=response_data.last_period_date_utc,  # Save UTC as is
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
            
            # 4. Migrate session-linked recommendations to permanent storage
            logger.info(f"Session recommendation permanent storage migration started: session_id={session_id}")
            try:
                from app.core.database import RecommendationRecord, RecommendationAdvice
                
                # Find session-linked recommendations and update with uid
                session_recommendations = self.db.query(RecommendationRecord).filter(
                    RecommendationRecord.session_id == session_id
                ).all()
                
                # Update each recommendation individually and commit immediately
                updated_recommendations = []
                failed_recommendations = []
                
                for rec in session_recommendations:
                    # Process each recommendation as independent sub-transaction
                    savepoint = self.db.begin_nested()  # Start sub-transaction
                    try:
                        rec.uid = uid
                        rec.session_id = None  # Remove session ID
                        savepoint.commit()  # Commit sub-transaction
                        updated_recommendations.append(rec.id)
                        logger.info(f"Recommendation permanent storage migration successful: recommendation_id={rec.id}")
                    except Exception as e:
                        logger.error(f"Recommendation permanent storage migration failed: recommendation_id={rec.id}, error={str(e)}")
                        failed_recommendations.append(rec.id)
                        savepoint.rollback()  # Rollback only individual sub-transaction
                        continue
                
                # Update session-linked advice with uid
                session_advices = self.db.query(RecommendationAdvice).filter(
                    RecommendationAdvice.session_id == session_id
                ).all()
                
                updated_advices = []
                failed_advices = []
                
                for advice in session_advices:
                    # Process each advice as independent sub-transaction
                    savepoint = self.db.begin_nested()  # Start sub-transaction
                    try:
                        advice.uid = uid
                        advice.session_id = None  # Remove session ID
                        savepoint.commit()  # Commit sub-transaction
                        updated_advices.append(advice.id)
                        logger.info(f"Advice permanent storage migration successful: advice_id={advice.id}")
                    except Exception as e:
                        logger.error(f"Advice permanent storage migration failed: advice_id={advice.id}, error={str(e)}")
                        failed_advices.append(advice.id)
                        savepoint.rollback()  # Rollback only individual sub-transaction
                        continue
                
                logger.info(f"Session recommendation permanent storage migration completed: {len(updated_recommendations)} successful, {len(failed_recommendations)} failed")
                logger.info(f"Session advice permanent storage migration completed: {len(updated_advices)} successful, {len(failed_advices)} failed")
                
                # Use only successful recommendations for schedule creation
                successful_recommendations = [rec for rec in session_recommendations if rec.id in updated_recommendations]
                
                # 5. Create automatic schedules (only successful recommendations)
                logger.info(f"Automatic schedule creation started: {len(successful_recommendations)} recommendations")
                try:
                    from app.services.new_scheduling_service import NewSchedulingService
                    scheduling_service = NewSchedulingService(self.db)
                    
                    created_schedules = []
                    failed_schedules = []
                    
                    for rec in successful_recommendations:
                        # Process each schedule as independent sub-transaction
                        savepoint = self.db.begin_nested()  # Start sub-transaction
                        try:
                            # Create schedule in current user timezone
                            schedule = scheduling_service.create_schedule_from_recommendation(rec, current_timezone)
                            savepoint.commit()  # Commit sub-transaction
                            created_schedules.append(schedule.id)
                            logger.info(f"Schedule creation completed: recommendation_id={rec.id}, schedule_id={schedule.id}, timezone={current_timezone}")
                        except Exception as e:
                            logger.error(f"Individual schedule creation failed: recommendation_id={rec.id}, error={str(e)}")
                            failed_schedules.append(rec.id)
                            savepoint.rollback()  # Rollback only individual sub-transaction
                    
                    logger.info(f"Automatic schedule creation completed: {len(created_schedules)} schedules created, {len(failed_schedules)} failed")
                    
                except Exception as e:
                    logger.error(f"Automatic schedule creation failed: {str(e)}", exc_info=True)
                
                # 6. Delete session
                self.db.delete(session)
                logger.info(f"Session deletion completed: {session_id}")
                
                # 7. Commit
                self.db.commit()
                
                # Log result summary
                total_recommendations = len(session_recommendations)
                total_advices = len(session_advices)
                success_rate_rec = len(updated_recommendations) / total_recommendations * 100 if total_recommendations > 0 else 0
                success_rate_adv = len(updated_advices) / total_advices * 100 if total_advices > 0 else 0
                
                logger.info(f"Session linking completed: session_id={session_id}, uid={uid}")
                logger.info(f"Recommendation linking success rate: {success_rate_rec:.1f}% ({len(updated_recommendations)}/{total_recommendations})")
                logger.info(f"Advice linking success rate: {success_rate_adv:.1f}% ({len(updated_advices)}/{total_advices})")
                logger.info(f"Schedule creation success rate: {len(created_schedules)}/{len(successful_recommendations)} items")
                
                # Return True if at least some succeeded
                return len(updated_recommendations) > 0 or len(updated_advices) > 0
                
            except Exception as e:
                logger.error(f"Session recommendation permanent storage migration failed: {str(e)}", exc_info=True)
                self.db.rollback()
                return False
                
        except Exception as e:
            logger.error(f"Session linking failed: {str(e)}", exc_info=True)
            self.db.rollback()
            return False

    def get_user_responses(self, uid: str) -> List[UserResponse]:
        """Get all user responses"""
        try:
            return self.db.query(UserResponse).filter(
                UserResponse.uid == uid
            ).order_by(UserResponse.created_at.desc()).all()
        except Exception as e:
            logger.error(f"User response retrieval failed: {str(e)}")
            raise Exception(f"User response retrieval failed: {str(e)}")

    def get_session_data(self, session_id: str) -> Optional[SessionData]:
        """Get session data"""
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
            raise Exception(f"Session data retrieval failed: {str(e)}")

    def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions (also delete linked recommendations)"""
        try:
            expired_sessions = self.db.query(QuestionSession).filter(
                QuestionSession.expires_at <= datetime.utcnow()
            ).all()
            
            count = len(expired_sessions)
            for session in expired_sessions:
                # Also delete session-linked recommendations and advice
                try:
                    from app.core.database import RecommendationRecord, RecommendationAdvice
                    
                    # Delete session-linked recommendations
                    session_recommendations = self.db.query(RecommendationRecord).filter(
                        RecommendationRecord.session_id == session.session_id
                    ).all()
                    
                    for rec in session_recommendations:
                        # Delete advice linked to recommendation
                        rec_advices = self.db.query(RecommendationAdvice).filter(
                            RecommendationAdvice.recommendation_id == rec.id
                        ).all()
                        for advice in rec_advices:
                            self.db.delete(advice)
                        
                        self.db.delete(rec)
                    
                    logger.info(f"Session {session.session_id} linked recommendations {len(session_recommendations)} deleted")
                    
                except Exception as e:
                    logger.error(f"Session recommendation deletion failed: {str(e)}", exc_info=True)
                
                # Delete session
                self.db.delete(session)
            
            self.db.commit()
            logger.info(f"Cleaned up {count} expired sessions")
            return count
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Session cleanup failed: {str(e)}")
            raise Exception(f"Session cleanup failed: {str(e)}") 

    def update_user_timezone(self, uid: str, new_timezone: str) -> bool:
        """Update user timezone"""
        try:
            logger.info(f"Timezone update started: uid={uid}, new_timezone={new_timezone}")
            
            # Get UserProfile
            user_profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
            if not user_profile:
                logger.error(f"User profile not found: uid={uid}")
                return False
            
            # Save old timezone
            old_timezone = user_profile.current_timezone
            
            # Update timezone
            user_profile.current_timezone = new_timezone
            user_profile.updated_at = datetime.utcnow()
            
            self.db.commit()
            
            logger.info(f"Timezone change completed: {uid}, {old_timezone} → {new_timezone}")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Timezone update failed: {str(e)}")
            return False 