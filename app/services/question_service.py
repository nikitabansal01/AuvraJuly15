from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from app.core.database import QuestionSession, UserResponse, UserProfile, generate_session_id
from app.models.question_models import SessionData, UserResponseData, SessionDataCreate, UserProfileCreate
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import logging

# Import data sanitization utilities (SINGLE SOURCE OF TRUTH for cleaning health data)
from app.utils.data_sanitization import (
    sanitize_list_field,
    sanitize_string_field,
    sanitize_user_response,
)

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
            
            logger.info(f"New session created: {session_id}, device: {device_id}, expires: {expires_at}")
            return session_id
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Session creation failed: {str(e)}")
            raise Exception(f"Session creation failed: {str(e)}")

    def get_session(self, session_id: str) -> Optional[QuestionSession]:
        """Get active session"""
        try:
            # DEBUG: Split query to identify why session is missing
            session = self.db.query(QuestionSession).filter(
                QuestionSession.session_id == session_id
            ).first()
            
            if not session:
                logger.warning(f"get_session: Session {session_id} NOT FOUND in DB")
                return None
                
            if session.status != "active":
                # FIX: Allow getting linked sessions for late data arrival
                if session.status.startswith("linked:"):
                    # Log but allow proceeding
                    # logger.info(f"get_session: Session {session_id} is linked ({session.status}), allowing access for data sync")
                    return session
                    
                logger.warning(f"get_session: Session {session_id} found but status is '{session.status}'")
                return None
                
            if session.expires_at <= datetime.utcnow():
                logger.warning(f"get_session: Session {session_id} found but EXPIRED. Expires: {session.expires_at}, Now: {datetime.utcnow()}")
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
            # If caller explicitly sends other_concerns, use them; else proactively clear to avoid stale carry-over
            if data.other_concerns is not None:
                session.other_concerns = sanitize_list_field(data.other_concerns, "other_concerns")
                logger.info(f"other_concerns updated: {session.other_concerns}")
            else:
                # Production-safe: Clear previous free-text / selections if omitted (prevents unintended reuse)
                if session.other_concerns not in (None, [], {}):
                    logger.info("other_concerns omitted in request -> clearing previous stored value to prevent stale reuse")
                session.other_concerns = []
            if data.top_concern is not None:
                # Sanitize: "None of the above" should become None
                session.top_concern = sanitize_string_field(data.top_concern, "top_concern")
            if data.diagnosed_conditions is not None:
                # CRITICAL: Remove UI placeholders like "None of the above" before storage
                session.diagnosed_conditions = sanitize_list_field(data.diagnosed_conditions, "diagnosed_conditions")
            if data.family_history is not None:
                # CRITICAL: Remove UI placeholders like "None of the above" before storage
                session.family_history = sanitize_list_field(data.family_history, "family_history")
            if data.workout_intensity is not None:
                session.workout_intensity = data.workout_intensity
            if data.sleep_duration is not None:
                session.sleep_duration = data.sleep_duration
            if data.stress_level is not None:
                session.stress_level = data.stress_level
            if data.survey_timezone is not None:
                session.survey_timezone = data.survey_timezone
            # PERSONALIZATION: Eat/Move/Pause preference
            if data.lifestyle_focus is not None:
                session.lifestyle_focus = data.lifestyle_focus
                logger.info(f"lifestyle_focus saved: {session.lifestyle_focus}")
            
            # ---------------------------------------------------------
            # ROOT CAUSE FIX: Write-through to UserResponse if linked
            # ---------------------------------------------------------
            if session.status.startswith("linked:"):
                try:
                    uid = session.status.split(":")[1]
                    logger.info(f"Session {session_id} is linked to {uid}. Syncing data to UserResponse.")
                    
                    user_response = self.db.query(UserResponse).filter(
                        UserResponse.uid == uid
                    ).first()
                    
                    if user_response:
                        # Sync all fields that were present in 'data'
                        if data.age is not None: user_response.age = data.age
                        if data.period_description is not None: user_response.period_description = data.period_description
                        if data.birth_control is not None: user_response.birth_control = data.birth_control
                        # Dates
                        if data.last_period_date is not None: user_response.last_period_date_utc = session.last_period_date_utc
                        
                        if data.cycle_length is not None: user_response.cycle_length = data.cycle_length
                        if data.period_concerns is not None: user_response.period_concerns = data.period_concerns
                        if data.body_concerns is not None: user_response.body_concerns = data.body_concerns
                        if data.skin_hair_concerns is not None: user_response.skin_hair_concerns = data.skin_hair_concerns
                        if data.mental_health_concerns is not None: user_response.mental_health_concerns = data.mental_health_concerns
                        # Use sanitized values from session to avoid "None of the above" pollution
                        if data.other_concerns is not None: user_response.other_concerns = session.other_concerns
                        if data.top_concern is not None: user_response.top_concern = session.top_concern
                        if data.diagnosed_conditions is not None: user_response.diagnosed_conditions = session.diagnosed_conditions
                        if data.family_history is not None: user_response.family_history = session.family_history
                        if data.workout_intensity is not None: user_response.workout_intensity = data.workout_intensity
                        if data.sleep_duration is not None: user_response.sleep_duration = data.sleep_duration
                        if data.stress_level is not None: user_response.stress_level = data.stress_level
                        if data.survey_timezone is not None: user_response.survey_timezone = data.survey_timezone
                        
                        logger.info(f"✅ Synced late session data to UserResponse for {uid}")
                    else:
                        logger.warning(f"⚠️ Session linked to {uid} but UserResponse not found!")
                        
                except Exception as e:
                    logger.error(f"Failed to sync linked session data: {e}", exc_info=True)
                    # Don't fail the request, session update is still valid
            
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

    def _create_action_plan_from_session_recs(
        self, 
        uid: str, 
        recommendations: list, 
        user_response, 
        current_timezone: str,
        lifestyle_focus: list
    ) -> bool:
        """
        Create ActionPlan + ActionPlanItems directly from session recommendations.
        This eliminates the 100+ second GPT regeneration when HomeScreen loads.
        
        Args:
            uid: User ID
            recommendations: List of RecommendationRecord objects
            user_response: UserResponse object with hormone data
            current_timezone: User's timezone
            lifestyle_focus: User's lifestyle focus (eat, move, pause)
        
        Returns:
            True if ActionPlan was created successfully
        """
        try:
            from app.core.database import ActionPlan, ActionPlanItem, ActionPlanItemVariant
            from app.utils.timezone_utils import ZoneInfo
            from datetime import date
            import time
            
            start_time = time.time()
            
            # Get today's date in user's timezone
            try:
                tz = ZoneInfo(current_timezone)
                today = datetime.now(tz).date()
            except Exception:
                today = date.today()
            
            # Check if plan already exists for today
            existing_plan = self.db.query(ActionPlan).filter(
                ActionPlan.uid == uid,
                ActionPlan.plan_date == today
            ).first()
            
            if existing_plan:
                logger.info(f"[SESSION_LINK] ActionPlan already exists for {uid} on {today}, skipping creation")
                return True
            
            # Get hormone data from user_response
            primary_hormone = getattr(user_response, 'primary_hormone', None) or 'cortisol'
            secondary_hormones = getattr(user_response, 'secondary_hormones', None) or []
            
            # Create ActionPlan
            action_plan = ActionPlan(
                uid=uid,
                plan_date=today,
                primary_hormone=primary_hormone,
                secondary_hormones=secondary_hormones if secondary_hormones else None,
                cycle_day=1,  # Default for new user
                cycle_phase="menstrual",  # Default
                lifestyle_focus=lifestyle_focus,
                generation_cost="$0.00",  # No GPT cost - converted from session
                generation_time_ms=0,
                gpt_model_used="session_conversion"  # Mark as converted, not generated
            )
            self.db.add(action_plan)
            self.db.flush()  # Get plan ID
            
            logger.info(f"[SESSION_LINK] Created ActionPlan {action_plan.id} for {uid}")
            
            # Time slots for distributing actions
            time_slots = ["morning", "afternoon", "evening", "anytime"]
            
            # Select recommendations based on lifestyle_focus and hormone distribution
            # Goal: 2 primary hormone + 2 secondary hormone (if available)
            selected_recs = []
            
            # Filter by lifestyle focus categories
            focus_categories = []
            if lifestyle_focus:
                if 'eat' in lifestyle_focus:
                    focus_categories.append('food')
                if 'move' in lifestyle_focus:
                    focus_categories.append('movement')
                if 'pause' in lifestyle_focus:
                    focus_categories.append('mindfulness')
            
            if not focus_categories:
                focus_categories = ['food', 'movement', 'mindfulness']
            
            # Filter recommendations by category
            filtered_recs = [r for r in recommendations if r.category in focus_categories]
            if not filtered_recs:
                filtered_recs = recommendations  # Fallback to all
            
            # Select up to 4 recommendations
            selected_recs = filtered_recs[:4]
            
            if len(selected_recs) < 2:
                logger.warning(f"[SESSION_LINK] Only {len(selected_recs)} recommendations available, need at least 2")
                return False
            
            logger.info(f"[SESSION_LINK] Converting {len(selected_recs)} recommendations to ActionPlanItems")
            
            # Create ActionPlanItems from recommendations
            for idx, rec in enumerate(selected_recs):
                slot = idx + 1
                time_slot = time_slots[idx % len(time_slots)]
                
                # Determine target hormone (alternate between primary and secondary)
                if idx < 2:
                    target_hormone = primary_hormone
                elif secondary_hormones and len(secondary_hormones) > 0:
                    target_hormone = secondary_hormones[0]
                else:
                    target_hormone = primary_hormone
                
                # Get conditions and symptoms from recommendation
                conditions = rec.conditions if rec.conditions else []
                symptoms = rec.symptoms if rec.symptoms else []
                
                # Build image prompt for later generation
                category_prompts = {
                    "food": f"Healthy nutritious meal: {rec.title}, fresh ingredients, natural lighting, food photography, appetizing",
                    "movement": f"Woman exercising: {rec.title}, fitness lifestyle, energetic, bright studio, wellness photography",
                    "mindfulness": f"Peaceful meditation scene: {rec.title}, calm atmosphere, soft natural light, wellness and relaxation"
                }
                image_prompt = category_prompts.get(rec.category.lower() if rec.category else "food", 
                                                    f"Healthy lifestyle: {rec.title}, professional photography")
                
                # Create ActionPlanItem
                item = ActionPlanItem(
                    plan_id=action_plan.id,
                    uid=uid,
                    slot=slot,
                    time_slot=time_slot,
                    category=rec.category,
                    title=rec.title or f"Action {slot}",
                    specific_action=rec.specific_action or rec.purpose or f"Complete {rec.title}",
                    purpose=rec.purpose,
                    target_hormone=target_hormone,
                    hormone_persona_intro=f"Hi, I'm {target_hormone.title() if target_hormone else 'your hormone helper'}! This action helps balance me.",
                    food_amounts=rec.food_amounts,
                    food_items=rec.food_items,
                    exercise_durations=rec.exercise_durations,
                    exercise_types=rec.exercise_types,
                    exercise_intensities=rec.exercise_intensities,
                    mindfulness_durations=rec.mindfulness_durations,
                    mindfulness_techniques=rec.mindfulness_techniques,
                    conditions=conditions,
                    symptoms=symptoms,
                    research_studies=rec.research_studies,
                    hero_image_url=None,  # Will be generated on first HomeScreen view
                    hero_image_prompt=image_prompt,  # Prompt for image generation
                    is_completed=False
                )
                self.db.add(item)
                self.db.flush()  # Get item ID for variants
                
                # Create variant records (3 per item)
                variant_types = {
                    "food": [("easy", "A simpler version"), ("tasty", "A tastier version"), ("healthy", "An extra healthy version")],
                    "movement": [("gentle", "A gentler version"), ("energizing", "An energizing version"), ("quick", "A quicker version")],
                    "mindfulness": [("guided", "A guided version"), ("silent", "A silent version"), ("brief", "A brief version")]
                }
                
                cat_key = rec.category.lower() if rec.category else "food"
                variants_for_cat = variant_types.get(cat_key, [("alternative", "An alternative version"), ("simpler", "A simpler version"), ("advanced", "An advanced version")])
                
                for v_type, v_desc in variants_for_cat:
                    variant_prompt = f"{v_type.title()} {rec.title}, {cat_key} lifestyle, professional photography"
                    variant = ActionPlanItemVariant(
                        item_id=item.id,
                        variant_type=v_type,
                        title=f"{v_type.title()} {rec.title}",
                        description=f"{v_desc} of {rec.title}",
                        image_url=None,  # Will be generated later
                        image_prompt=variant_prompt
                    )
                    self.db.add(variant)
                
                logger.info(f"[SESSION_LINK]   Item {slot}: {rec.title} ({rec.category}) -> {target_hormone} + 3 variants")
            
            # Commit all changes
            self.db.commit()
            
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info(f"✅ [SESSION_LINK] ActionPlan {action_plan.id} created with {len(selected_recs)} items in {elapsed_ms}ms")
            logger.info(f"✅ [SESSION_LINK] HomeScreen will now load INSTANTLY for user {uid}!")
            
            return True
            
        except Exception as e:
            logger.error(f"[SESSION_LINK] Failed to create ActionPlan: {e}", exc_info=True)
            self.db.rollback()
            return False

    def link_session_to_user(self, session_id: str, uid: str, name: str, email: str, current_timezone: str = "Asia/Seoul", lifestyle_focus: list = None) -> bool:
        """
        Link session to user and save permanently
        
        Args:
            session_id: Session ID
            uid: User ID
            name: User name
            email: User email
            current_timezone: Current user timezone
            lifestyle_focus: User's preferred focus areas (eat, move, pause)
        
        Returns:
            Success status
        """
        try:
            logger.info(f"=== QuestionService.link_session_to_user started ===")
            logger.info(f"Parameters: session_id={session_id}, uid={uid}, name={name}, email={email}, current_timezone={current_timezone}, lifestyle_focus={lifestyle_focus}")
            
            # 1. Get session data
            session = self.get_session(session_id)
            logger.info(f"Session retrieval result: session={session}")
            if not session:
                logger.error(f"Session not found: session_id={session_id}")
                raise Exception("Session not found or expired")
            
            # 2. Create user profile (save current timezone and lifestyle focus)
            logger.info(f"Creating user profile: uid={uid}")
            user_profile = self.create_user_profile(uid, name, email)
            user_profile.current_timezone = current_timezone
            user_profile.lifestyle_focus = lifestyle_focus
            # NOTE: Do NOT commit here. We'll commit everything at the end for atomicity.
            logger.info(f"User profile creation completed: uid={uid}, timezone={current_timezone}, lifestyle_focus={lifestyle_focus}")
            
            # 3. Create UserResponse (save UTC data as is) - IDEMPOTENCY CHECK
            existing_user_response = self.db.query(UserResponse).filter(UserResponse.uid == uid).first()
            if existing_user_response:
                logger.info(f"✅ [IDEMPOTENCY] UserResponse already exists for {uid}, skipping creation")
                user_response = existing_user_response
            else:
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
                    secondary_hormones=session.secondary_hormones,
                    lifestyle_focus=lifestyle_focus
                )
                self.db.add(user_response)
                logger.info(f"Session data saved for user {uid}")
            
            # 4. Migrate session-linked recommendations to permanent storage
            logger.info(f"Session recommendation permanent storage migration started: session_id={session_id}")
            try:
                from app.core.database import RecommendationRecord, RecommendationAdvice
                
                # Find session-linked recommendations
                # ---------------------------------------------------------
                # NEW: Check for existing GUEST ActionPlan (generated by ActionPlanGenerator)
                # ---------------------------------------------------------
                from app.core.database import ActionPlan, ActionPlanItem
                
                guest_plan = self.db.query(ActionPlan).filter(
                    ActionPlan.session_id == session_id
                ).first()

                # A plan row can exist while generation is still running: the
                # generator inserts a placeholder up front and its items only at
                # the end. Adopting that placeholder here transfers an empty
                # plan and deletes the session, leaving the generator with no
                # way to learn who now owns it -- the items then land unowned
                # and every completion 404s. Only adopt a plan that actually has
                # items; otherwise fall through to the in-progress branch below,
                # which marks the session linked and lets the generator finish
                # the handover.
                if guest_plan:
                    guest_plan_has_items = self.db.query(ActionPlanItem).filter(
                        ActionPlanItem.plan_id == guest_plan.id
                    ).first() is not None
                    if not guest_plan_has_items:
                        logger.info(
                            f"⏳ Guest plan {guest_plan.id} for session {session_id} has no "
                            f"items yet (generation in flight); deferring transfer"
                        )
                        guest_plan = None

                if guest_plan:
                    logger.info(f"✅ Found existing GUEST ActionPlan {guest_plan.id} for session {session_id}")
                    
                    # 1. Link the ActionPlan to the user
                    guest_plan.uid = uid
                    guest_plan.session_id = None # Clear session owner

                    # CRITICAL FIX: Update plan_date to user's current local date
                    # Guest plans are generated with UTC timezone, but user may be in a different timezone
                    # e.g., Guest plan created at 19:16 UTC on Jan 9 → UTC date is Jan 9
                    # But user in Asia/Kolkata (UTC+5:30) → local date is Jan 10
                    # If we don't update the date, HomeScreen will generate a NEW plan for "today"
                    try:
                        from app.utils.timezone_utils import ZoneInfo
                        from datetime import date
                        tz = ZoneInfo(current_timezone)
                        today = datetime.now(tz).date()
                        
                        if guest_plan.plan_date != today:
                            logger.info(f"🔄 Updating guest plan {guest_plan.id} date from {guest_plan.plan_date} to {today} (user timezone: {current_timezone})")
                            guest_plan.plan_date = today
                            # Also mark as NOT needing review since it's effectively "today's" plan
                            guest_plan.review_completed = False
                        
                    except Exception as e:
                        logger.error(f"Failed to update plan date for guest plan migration: {e}")
                    
                    # 2. Link all ActionPlanItems
                    guest_plan_items = self.db.query(ActionPlanItem).filter(
                        ActionPlanItem.plan_id == guest_plan.id
                    ).all()
                    
                    for item in guest_plan_items:
                        item.uid = uid
                        item.session_id = None
                        
                    # 3. Clean up the session (it's now fully converted)
                    self.db.delete(session)
                    
                    self.db.commit()
                    logger.info(f"🚀 Successfully transferred guest plan {guest_plan.id} to user {uid}. Session deleted.")
                    return True
                
                # ---------------------------------------------------------
                # FALLBACK: No guest plan found yet
                # Check if background generation is still in progress
                # ---------------------------------------------------------
                
                # CRITICAL FIX: Check if background generation (Thing 1) is still running
                # If yes, just mark session as linked and let background task auto-transfer
                # This prevents duplicate generate_new_plan() calls!
                from app.services.processing_status_service import ProcessingStatusService
                processing_service = ProcessingStatusService(self.db)
                status = processing_service.get_processing_status(session_id)
                
                is_still_processing = status and status.processing_status in ["queued", "in_progress"]
                
                if is_still_processing:
                    # ═══════════════════════════════════════════════════════════════════════
                    # NON-BLOCKING: Generation still running from ResearchingScreen
                    # Mark session as linked, background task will auto-transfer when done
                    # ═══════════════════════════════════════════════════════════════════════
                    logger.info(f"📍 [SESSION_LINK] Generation in progress for {session_id}. Marking as linked:{uid}")
                    logger.info(f"📍 [SESSION_LINK] Background task will auto-transfer plan when ready - NO duplicate generation!")
                    session.status = f"linked:{uid}"
                    self.db.commit()
                    logger.info(f"✅ [SESSION_LINK] Session marked for auto-transfer. User will see 'Plan Generating' UI on HomeScreen.")
                    return True  # Success! Frontend will poll for plan availability
                
                # ---------------------------------------------------------
                # FALLBACK: Legacy Mode (Lite Recommendations)
                # Only reached if generation is NOT in progress (failed/never started)
                # ---------------------------------------------------------
                
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
                
                # ═══════════════════════════════════════════════════════════════════════
                # LEGACY FALLBACK: Only migrate recommendations, let HomeScreen generate plan
                # ═══════════════════════════════════════════════════════════════════════
                # If we reach here, generation failed or never started
                # DON'T call generate_new_plan() here - HomeScreen will handle it
                # This prevents duplicate GPT calls if user refreshes or network issues
                logger.info(f"📍 [SESSION_LINK] Legacy mode: {len(successful_recommendations)} recommendations migrated to user {uid}")
                logger.info(f"📍 [SESSION_LINK] HomeScreen will generate ActionPlan on first load")
                
                # NOTE: is_still_processing check already done at top of fallback section
                # If we reach here, generation failed/never started - legacy recommendations were migrated above
                
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
                
                # Return True if the process completed successfully
                # Even if there are no recommendations (e.g. generation failed), we should still link the user profile/responses
                return True
                
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
                stress_level=session.stress_level,
                # CRITICAL: Include lifestyle_focus for personalization!
                lifestyle_focus=session.lifestyle_focus
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