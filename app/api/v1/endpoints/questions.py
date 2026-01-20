from fastapi import APIRouter, HTTPException, status, Depends
from sqlalchemy.orm import Session
from typing import List
from app.core.database import get_db
from app.services.question_service import QuestionService
from app.services.recommendation_service import RecommendationService
from app.models.question_models import (
    SessionCreate, SessionResponse, SessionDataCreate, SessionData,
    UserResponseFull, SessionLinkRequest, AnalyticsResponse
)
from app.core.security import get_current_active_user, get_current_user
from typing import Optional, Dict
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.database import create_tables
import logging
from app.models.question_models import TimezoneUpdateRequest, TimezoneUpdateResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================
# SESSION-LEVEL HORMONE ANALYSIS CACHING
# ============================================
# Prevents running full rule-based + LLM analysis multiple times during signup flow:
# 1. get_session_hormone_results (user views hormone results)
# 2. start_session_recommendation_generation (user clicks generate)
# 3. _generate_recommendations_background (background task)
# All 3 calls within ~2 minutes should use the same cached result.

def get_cached_hormone_analysis(session_id: str, temp_user_profile: Dict) -> Dict:
    """
    Get hormone analysis with session-level caching.
    
    First call runs full analysis and caches by session_id.
    Subsequent calls within 30 minutes return cached result.
    
    Args:
        session_id: Unique session identifier
        temp_user_profile: User profile data for analysis
        
    Returns:
        Full hormone analysis result dict with all_scores, levels, etc.
    """
    from app.utils.cache_utils import session_hormone_analysis_cache
    from app.services.root_cause_engine import RootCauseEngine
    
    # Check session-level cache first
    cached = session_hormone_analysis_cache.get(session_id)
    if cached is not None:
        logger.info(f"✅ [HormoneAnalysis] SESSION CACHE HIT for {session_id[:8]}... (saved full re-computation)")
        return cached
    
    # Cache miss - run full analysis
    logger.info(f"🔬 [HormoneAnalysis] SESSION CACHE MISS for {session_id[:8]}... running full analysis")
    result = RootCauseEngine.analyze_hormone_imbalance(temp_user_profile)
    
    # Cache the full result by session_id
    session_hormone_analysis_cache.set(session_id, result)
    logger.info(f"💾 [HormoneAnalysis] Cached full result for session {session_id[:8]}...")
    
    return result

@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    session_data: SessionCreate,
    db: Session = Depends(get_db)
):
    """Create new question session (available without login)"""
    try:
        service = QuestionService(db)
        session_id = service.create_session(session_data.device_id)
        
        # Return created session information
        session = service.get_session(session_id)
        return SessionResponse(
            session_id=session.session_id,
            device_id=session.device_id,
            created_at=session.created_at,
            expires_at=session.expires_at,
            status=session.status
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session creation failed: {str(e)}"
        )

@router.post("/sessions/{session_id}/data", response_model=dict)
async def save_session_data(
    session_id: str,
    data_request: SessionDataCreate,
    db: Session = Depends(get_db)
):
    """Save survey data to session (available without login)"""
    try:
        logger.info(f"Session data save request: session_id={session_id}")
        
        service = QuestionService(db)
        
        # Check if session exists
        session = service.get_session(session_id)
        if not session:
            logger.error(f"Session not found: {session_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found or expired"
            )
        
        # Save data to session
        success = service.save_session_data(session_id, data_request.data)
        
        if success:
            logger.info(f"Session data saved successfully: {session_id}")
            return {"message": "Session data saved successfully"}
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save session data"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Exception during session data save: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session data save failed: {str(e)}"
        )

@router.get("/sessions/{session_id}/data", response_model=SessionData)
async def get_session_data(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get session data (available without login)"""
    try:
        service = QuestionService(db)
        data = service.get_session_data(session_id)
        
        if not data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session data not found"
            )
        
        return data
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session data retrieval failed: {str(e)}"
        )

@router.get("/sessions/{session_id}/recommendations", response_model=dict)
async def get_session_recommendations(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get session recommendations (available without login)"""
    try:
        from app.core.database import RecommendationRecord, RecommendationAdvice
        
        # Get recommendations linked to session
        recommendations = db.query(RecommendationRecord).filter(
            RecommendationRecord.session_id == session_id
        ).all()
        
        result = []
        for rec in recommendations:
            # Get advices linked to recommendation
            advices = db.query(RecommendationAdvice).filter(
                RecommendationAdvice.recommendation_id == rec.id
            ).all()
            
            recommendation_data = {
                "id": rec.id,
                "category": rec.category,
                "title": rec.title,
                "purpose": rec.purpose,
                "specific_action": rec.specific_action,
                "priority": rec.priority,
                "contraindications": rec.contraindications,
                "conditions": rec.conditions,
                "symptoms": rec.symptoms,
                "hormones": rec.hormones,
                "food_amounts": rec.food_amounts,
                "food_items": rec.food_items,
                "exercise_durations": rec.exercise_durations,
                "exercise_types": rec.exercise_types,
                "exercise_intensities": rec.exercise_intensities,
                "mindfulness_durations": rec.mindfulness_durations,
                "mindfulness_techniques": rec.mindfulness_techniques,
                "frequency_detail": rec.frequency_detail,
                "duration_weeks": rec.duration_weeks,
                "optimal_times": rec.optimal_times,
                "research_summary": rec.research_summary,
                "research_studies": rec.research_studies,
                "advices": [
                    {
                        "id": advice.id,
                        "advice_type": advice.advice_type,
                        "category": advice.category,
                        "title": advice.title,
                        "description": advice.description
                    }
                    for advice in advices
                ]
            }
            result.append(recommendation_data)
        
        return {
            "session_id": session_id,
            "recommendations": result
        }
        
    except Exception as e:
        logger.error(f"Session recommendations retrieval failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session recommendations retrieval failed: {str(e)}"
        )

@router.post("/sessions/{session_id}/generate-recommendations", response_model=dict)
async def start_session_recommendations_generation(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Start generating recommendations for session (available without login)"""
    try:
        logger.info(f"Session recommendation generation start request: session_id={session_id}")
        
        service = QuestionService(db)
        
        # Check if session exists and has data
        session = service.get_session(session_id)
        if not session:
            logger.error(f"Session not found: {session_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found or expired"
            )
        
        # Check if session has data
        if session.age is None and session.period_description is None:
            logger.error(f"Session has no data: {session_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session has no data to generate recommendations"
            )
        
        # Check if recommendations already exist
        from app.core.database import RecommendationRecord
        existing_recommendations = db.query(RecommendationRecord).filter(
            RecommendationRecord.session_id == session_id
        ).count()
        
        if existing_recommendations > 0:
            logger.info(f"Recommendations already exist: {session_id}, count={existing_recommendations}")
            return {
                "message": "Recommendations already exist",
                "status": "completed",
                "recommendations_count": existing_recommendations
            }
        
        # Check if already processing
        from app.services.processing_status_service import ProcessingStatusService
        processing_service = ProcessingStatusService(db)
        existing_processing = processing_service.get_processing_status(session_id)
        if existing_processing and existing_processing.processing_status in ["queued", "in_progress"]:
            logger.info(f"Already processing: {session_id}, status={existing_processing.processing_status}")
            return {
                "message": "Recommendation generation already in progress",
                "status": existing_processing.processing_status,
                "session_id": session_id
            }
        
        # Create temporary UserProfile from session data
        session_data = service.get_session_data(session_id)
        if not session_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session data not found"
            )
        
        # Create temporary UserProfile
        temp_user_profile = {
            "age": session_data.age,
            "period_description": session_data.period_description,
            "birth_control": session_data.birth_control,
            "cycle_length": session_data.cycle_length,
            "period_concerns": session_data.period_concerns,
            "body_concerns": session_data.body_concerns,
            "skin_hair_concerns": session_data.skin_hair_concerns,
            "mental_health_concerns": session_data.mental_health_concerns,
            "other_concerns": session_data.other_concerns,
            "top_concern": session_data.top_concern,
            "diagnosed_conditions": session_data.diagnosed_conditions,
            "family_history": session_data.family_history,
            "workout_intensity": session_data.workout_intensity,
            "sleep_duration": session_data.sleep_duration,
            "stress_level": session_data.stress_level
        }
        
        # Use Root cause engine with session-level caching
        # This may already be cached from get_session_hormone_results call
        root_cause_analysis = get_cached_hormone_analysis(session_id, temp_user_profile)
        temp_user_profile["primaryImbalance"] = root_cause_analysis["primary_imbalance"]
        temp_user_profile["secondaryImbalances"] = root_cause_analysis["secondary_imbalances"]
        
        # Save root cause results to QuestionSession
        session.primary_hormone = root_cause_analysis["primary_imbalance"]
        session.secondary_hormones = root_cause_analysis["secondary_imbalances"]
        db.commit()
        
        # Create processing status record
        processing_status = processing_service.create_processing_status(session_id, temp_user_profile)
        
        # Start recommendation generation in background
        import asyncio
        asyncio.create_task(_generate_recommendations_background(session_id, service, processing_service, db))
        
        logger.info(f"Session recommendation generation started: {session_id}")
        return {
            "message": "Recommendation generation started",
            "status": "queued",
            "session_id": session_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start session recommendation generation: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start recommendation generation: {str(e)}"
        )

async def _generate_recommendations_background(session_id: str, service, processing_service, db) -> None:
    """
    Generate session recommendations in background using FULL ActionPlanGenerator.
    
    This ensures guests get the exact same high-quality plan as signed-up users,
    including pre-generated images. When they sign up, we just link this plan
    instead of regenerating.
    """
    try:
        from datetime import date
        from app.services.action_plan_generator import ActionPlanGenerator
        from app.core.database import AsyncSessionLocal
        
        logger.info(f"🚀 Background FULL PLAN generation started for session: {session_id}")
        
        # Update to processing started status
        processing_service.update_processing_started(session_id)
        
        # Create temporary UserProfile from session data for context
        session_data = service.get_session_data(session_id)
        if not session_data:
            raise ValueError(f"Session data not found for {session_id}")

        # Set status for all categories to processing to show UI progress
        for category in ["food", "movement", "mindfulness"]:
            processing_service.update_category_status(session_id, category, "processing", f"{category} plan generation in progress")

        # ═══════════════════════════════════════════════════════════════════════
        # NEW: use ActionPlanGenerator directly
        # ═══════════════════════════════════════════════════════════════════════
        
        # We need a new AsyncSession for the generator
        async with AsyncSessionLocal() as async_session:
            generator = ActionPlanGenerator()  # No args needed - uses internal engine
            
            # Generate the plan!
            # We pass session_id and NO user_id
            response = await generator.generate_new_plan(
                user_id=None,
                plan_date=date.today(),
                user_timezone="UTC", # Default for guest, can update on signup
                db=async_session,
                image_mode="full", # Generate ALL images now!
                session_id=session_id
            )
            
            if response.get("success"):
                # Plan generated successfully!
                plan_data = response.get("plan", {})
                plan_id = plan_data.get("id")
                
                # Update status for all categories to completed
                for cat in ["food", "movement", "mindfulness"]:
                    processing_service.update_category_status(session_id, cat, "completed", f"{cat} plan ready")
                
                logger.info(f"✅ Saved FULL ACTION PLAN for guest session {session_id}")
                
                # ═══════════════════════════════════════════════════════════════════════
                # AUTO-TRANSFER: Check if user signed up during generation
                # If session is marked as "linked:{uid}", transfer plan immediately
                # ═══════════════════════════════════════════════════════════════════════
                try:
                    from app.core.database import QuestionSession, ActionPlan, ActionPlanItem
                    
                    # Get fresh session status
                    session_check = await async_session.execute(
                        "SELECT session_id, status FROM question_sessions WHERE session_id = :sid",
                        {"sid": session_id}
                    )
                    session_row = session_check.fetchone()
                    
                    if session_row and session_row.status and session_row.status.startswith("linked:"):
                        target_uid = session_row.status.split(":")[1]
                        logger.info(f"🔄 [AUTO-TRANSFER] User {target_uid} signed up during generation! Transferring plan {plan_id}...")
                        
                        # Get the plan and transfer ownership
                        plan_query = await async_session.execute(
                            "SELECT id FROM action_plans WHERE id = :plan_id",
                            {"plan_id": plan_id}
                        )
                        plan_row = plan_query.fetchone()
                        
                        if plan_row:
                            # Transfer plan to user
                            await async_session.execute(
                                "UPDATE action_plans SET uid = :uid, session_id = NULL WHERE id = :plan_id",
                                {"uid": target_uid, "plan_id": plan_id}
                            )
                            
                            # Transfer plan items
                            await async_session.execute(
                                "UPDATE action_plan_items SET uid = :uid, session_id = NULL WHERE plan_id = :plan_id",
                                {"uid": target_uid, "plan_id": plan_id}
                            )
                            
                            # Delete the session (it's no longer needed)
                            await async_session.execute(
                                "DELETE FROM question_sessions WHERE session_id = :sid",
                                {"sid": session_id}
                            )
                            
                            await async_session.commit()
                            logger.info(f"🚀 [AUTO-TRANSFER] Plan {plan_id} successfully transferred to user {target_uid}")
                        else:
                            logger.warning(f"⚠️ [AUTO-TRANSFER] Plan {plan_id} not found for transfer")
                    else:
                        logger.info(f"📍 Session {session_id} not linked yet, plan stays as guest plan")
                        
                except Exception as transfer_err:
                    logger.error(f"❌ [AUTO-TRANSFER] Failed to auto-transfer plan: {transfer_err}", exc_info=True)
                    # Don't fail the whole generation - plan is still created
                
                result_summary = {
                    "successful_categories": ["food", "movement", "mindfulness"],
                    "failed_categories": [],
                    "total_recommendations": len(plan_data.get("actions", [])),
                    "plan_id": plan_id
                }
                
                # Complete overall processing
                processing_service.update_heartbeat(session_id)
                processing_service.update_processing_completed(session_id, result_summary)
                
            else:
                error_msg = response.get("error", "Unknown generation error")
                logger.error(f"❌ Guest plan generation failed: {error_msg}")
                processing_service.update_processing_failed(session_id, {"error": error_msg})

    except Exception as e:
        logger.error(f"Background session recommendation generation failed: {str(e)}", exc_info=True)
        try:
            processing_service.update_processing_failed(session_id, {"error": str(e)})
        except:
            pass

@router.post("/sessions/{session_id}/link")
async def link_session_to_user(
    session_id: str,
    link_data: SessionLinkRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """Link session to user and delete session"""
    import asyncio
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"=== Endpoint entry successful ===")
    logger.info(f"session_id: {session_id}")
    logger.info(f"link_data: {link_data}")
    logger.info(f"current_user: {current_user}")
    logger.info(f"db object: {type(db)}")
    
    try:
        logger.info(f"=== _link_session_to_user_internal call start ===")
        # Increased timeout to 120s to allow waiting for guest plan generation (up to 90s)
        result = await asyncio.wait_for(
            _link_session_to_user_internal(session_id, link_data, db, current_user),
            timeout=120.0
        )
        logger.info(f"=== _link_session_to_user_internal completed ===")
        return result
    except asyncio.TimeoutError:
        logger.error(f"Session linking timeout: session_id={session_id}")
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail="Session linking timeout"
        )

async def _link_session_to_user_internal(
    session_id: str,
    link_data: SessionLinkRequest,
    db: Session,
    current_user: dict
):
    """Link session to user and delete session"""
    try:
        logger.info(f"=== Session linking start ===")
        logger.info(f"Session linking attempt: session_id={session_id}")
        logger.info(f"Current user info: uid={current_user.get('uid')}, email={current_user.get('email')}")
        logger.info(f"Request data: name={link_data.user_profile.name}, email={link_data.user_profile.email}")
        
        service = QuestionService(db)
        
        # Check that only the user can link their own session
        # Verify Firebase UID and email match
        logger.info(f"Email match verification: current={current_user.get('email')}, request={link_data.user_profile.email}")
        if current_user.get("email") != link_data.user_profile.email:
            logger.warning(f"Email mismatch: current_user_email={current_user.get('email')}, request_email={link_data.user_profile.email}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only link your own sessions"
            )
        
        logger.info(f"Session linking service call: uid={current_user.get('uid')}, name={link_data.user_profile.name}, current_timezone={link_data.current_timezone}, lifestyle_focus={link_data.lifestyle_focus}")
        
        # Execute in threadpool to allow new event loop creation in service
        from starlette.concurrency import run_in_threadpool
        success = await run_in_threadpool(
            service.link_session_to_user,
            session_id, 
            current_user.get("uid"),
            link_data.user_profile.name,
            link_data.user_profile.email,
            link_data.current_timezone,
            link_data.lifestyle_focus
        )
        
        if success:
            logger.info(f"Session linking successful: session_id={session_id}")
            return {"message": "Session linked successfully and deleted"}
        else:
            logger.error(f"Session linking failed: session_id={session_id}, success=False")
            logger.error(f"=== 400 error occurred: Session linking failed ===")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session linking failed"
            )
            
    except HTTPException as he:
        logger.error(f"HTTPException occurred: session_id={session_id}, status_code={he.status_code}, detail={he.detail}")
        raise
    except Exception as e:
        logger.error(f"Exception during session linking: session_id={session_id}, error={str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session linking failed: {str(e)}"
        )

@router.post("/test-auth")
async def test_auth(
    current_user: dict = Depends(get_current_active_user)
):
    """Test authentication endpoint"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"=== Test authentication successful ===")
    logger.info(f"current_user: {current_user}")
    
    return {"message": "Authentication successful", "user": current_user}

@router.post("/test-db")
async def test_db(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """Test database connection endpoint"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"=== Database connection test start ===")
    logger.info(f"db object: {type(db)}")
    logger.info(f"current_user: {current_user}")
    
    try:
        # Simple database query test
        from app.core.database import QuestionSession
        session_count = db.query(QuestionSession).count()
        logger.info(f"Session count: {session_count}")
        
        logger.info(f"=== Database connection test successful ===")
        return {"message": "Database connection successful", "session_count": session_count}
    except Exception as e:
        logger.error(f"Database connection test failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database test failed: {str(e)}")

@router.post("/test-pydantic")
async def test_pydantic(
    current_user: dict = Depends(get_current_active_user)
):
    """Test Pydantic model endpoint"""
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"=== Pydantic test start ===")
    logger.info(f"current_user: {current_user}")
    
    try:
        # Simple Pydantic model test
        from app.models.question_models import UserProfileCreate
        
        test_data = {"name": "Test", "email": "test@test.com"}
        logger.info(f"Test data: {test_data}")
        
        profile = UserProfileCreate(**test_data)
        logger.info(f"Pydantic model creation successful: {profile}")
        
        logger.info(f"=== Pydantic test successful ===")
        return {"message": "Pydantic test successful", "profile": profile.dict()}
    except Exception as e:
        logger.error(f"Pydantic test failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Pydantic test failed: {str(e)}")

@router.get("/users/{uid}/responses", response_model=List[UserResponseFull])
async def get_user_responses(
    uid: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """Get user responses (requires authentication)"""
    try:
        # Check that user can only access their own data
        if current_user.get("uid") != uid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only access your own data"
            )
        
        service = QuestionService(db)
        responses = service.get_user_responses(uid)
        
        return [UserResponseFull.from_orm(response) for response in responses]
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Response retrieval failed: {str(e)}"
        )

@router.post("/cleanup/expired-sessions")
async def cleanup_expired_sessions(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """Clean up expired sessions (admin only)"""
    try:
        # TODO: Add admin check
        service = QuestionService(db)
        count = service.cleanup_expired_sessions()
        
        return {"message": f"Cleaned up {count} expired sessions"}
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cleanup failed: {str(e)}"
        ) 

@router.get("/sessions/{session_id}/recommendations/status", response_model=dict)
async def get_session_recommendations_status(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get session recommendations generation status"""
    try:
        from app.core.database import RecommendationRecord
        from app.services.processing_status_service import ProcessingStatusService
        
        # Check if session exists
        service = QuestionService(db)
        session = service.get_session(session_id)
        
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found or expired"
            )
        
        # Check processing status
        processing_service = ProcessingStatusService(db)
        processing_status = processing_service.get_processing_status(session_id)
        
        if processing_status:
            # Use processing status if available
            return {
                "session_id": session_id,
                "status": processing_status.processing_status,
                "phase": processing_status.phase,
                "progress": processing_status.progress,
                "message": processing_status.message,
                "category_breakdown": {
                    "food": processing_status.food_status,
                    "movement": processing_status.movement_status,
                    "mindfulness": processing_status.mindfulness_status
                },
                "started_at": processing_status.started_at.isoformat() if processing_status.started_at else None,
                "finished_at": processing_status.finished_at.isoformat() if processing_status.finished_at else None,
                "result": processing_status.result,
                "error": processing_status.error
            }
        else:
            # Use legacy method if no processing status
            categories = ["food", "movement", "mindfulness"]
            category_counts = {}
            total_recommendations = 0
            
            for category in categories:
                count = db.query(RecommendationRecord).filter(
                    RecommendationRecord.session_id == session_id,
                    RecommendationRecord.category == category
                ).count()
                category_counts[category] = count
                total_recommendations += count
            
            # More sophisticated status determination
            completed_categories = [cat for cat, count in category_counts.items() if count > 0]
            
            if len(completed_categories) == 3:  # All categories completed
                # Additional check for minimum recommendations (at least 1 per category)
                if all(count > 0 for count in category_counts.values()):
                    status = "completed"
                else:
                    status = "in_progress"
            elif len(completed_categories) > 0:  # Some categories completed
                status = "in_progress"
            else:  # Not started yet
                status = "pending"
            
            return {
                "session_id": session_id,
                "status": status,
                "phase": "Legacy Mode",
                "progress": len(completed_categories) * 33,
                "message": f"{len(completed_categories)}/3 categories completed",
                "recommendations_count": total_recommendations,
                "expected_count": 3,
                "category_breakdown": category_counts,
                "completed_categories": completed_categories
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session recommendations status retrieval failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Session recommendations status retrieval failed: {str(e)}"
        ) 

@router.get("/sessions/{session_id}/hormone-analysis", response_model=dict)
async def get_hormone_analysis(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Get hormone analysis results for a session
    Returns detailed hormone information formatted for frontend display
    """
    try:
        service = QuestionService(db)
        session = service.get_session(session_id)
        
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found or expired"
            )
        
        # Hormone display metadata (base info without static descriptions)
        HORMONE_METADATA = {
            "estrogen": {
                "name": "Estrogen",
                "subtitle": "The energizer",
                "high": {"icon": "🔺"},
                "low": {"icon": "🔻"}
            },
            "progesterone": {
                "name": "Progesterone",
                "subtitle": "The calmer",
                "low": {"icon": "🔻"},
                "high": {"icon": "🔺"}
            },
            "androgens": {
                "name": "Testosterone",
                "subtitle": "The titan",
                "high": {"icon": "🔺"},
                "low": {"icon": "🔻"}
            },
            "insulin": {
                "name": "Insulin",
                "subtitle": "The regulator",
                "high": {"icon": "🔺"},
                "low": {"icon": "🔻"}
            },
            "cortisol": {
                "name": "Cortisol",
                "subtitle": "The stress hormone",
                "high": {"icon": "🔺"},
                "low": {"icon": "🔻"}
            },
            "thyroid": {
                "name": "Thyroid",
                "subtitle": "The metabolism master",
                "low": {"icon": "🔻"},
                "high": {"icon": "🔺"}
            }
        }
        
        # Map: symptom -> [(hormone, level)]
        # Based on root_cause_engine.py scoring tables
        SYMPTOM_TO_HORMONE_MAP = {
            # Period concerns
            "Irregular Periods": [("androgens", "high"), ("thyroid", "low")],
            "Painful Periods": [("estrogen", "high"), ("progesterone", "low")],
            "Light periods / Spotting": [("estrogen", "low"), ("progesterone", "low")],
            "Heavy periods": [("estrogen", "high"), ("progesterone", "low")],
            # Body concerns
            "Bloating": [("estrogen", "high"), ("insulin", "high")],
            "Hot Flashes": [("estrogen", "low")],
            "Nausea": [("estrogen", "high"), ("cortisol", "low")],
            "Difficulty losing weight / stubborn belly fat": [("insulin", "high"), ("cortisol", "high"), ("thyroid", "low")],
            "Recent weight gain": [("insulin", "high"), ("thyroid", "low"), ("cortisol", "high")],
            "Menstrual headaches": [("estrogen", "high"), ("progesterone", "low")],
            # Skin/hair concerns
            "Hirsutism (hair growth on chin, nipples etc)": [("androgens", "high")],
            "Thinning of hair": [("thyroid", "low"), ("androgens", "high")],
            "Adult Acne": [("androgens", "high"), ("insulin", "high")],
            # Mental health
            "Mood swings": [("progesterone", "low"), ("estrogen", "high")],
            "Stress": [("cortisol", "high")],
            "Fatigue": [("thyroid", "low"), ("cortisol", "low"), ("insulin", "high")],
            # Diagnosed conditions
            "PCOS": [("androgens", "high"), ("insulin", "high")],
            "PCOD": [("androgens", "high"), ("insulin", "high")],
            "Endometriosis": [("estrogen", "high")],
            "PMDD": [("progesterone", "low"), ("cortisol", "high")],
            "Premenstrual Syndrome": [("progesterone", "low"), ("estrogen", "high")],
            "Diabetes": [("insulin", "high")],
            "Hypothyroidism": [("thyroid", "low")],
            "Hashimoto's": [("thyroid", "low")],
        }
        
        def get_user_symptoms_for_hormone(temp_profile: dict, hormone: str, level: str) -> list:
            """Get user's actual symptoms that contributed to this hormone imbalance"""
            matched_symptoms = []
            hormone_key = (hormone, level)
            
            # Collect all user symptoms from all categories
            all_user_symptoms = []
            for field in ["period_concerns", "body_concerns", "skin_hair_concerns", 
                          "mental_health_concerns", "diagnosed_conditions"]:
                field_data = temp_profile.get(field) or []
                if isinstance(field_data, dict):
                    field_data = field_data.get("concerns", [])
                if isinstance(field_data, list):
                    all_user_symptoms.extend(field_data)
            
            # Filter out "None of the above"
            all_user_symptoms = [s for s in all_user_symptoms 
                                 if s and s.lower() != "none of the above"]
            
            # Find which symptoms map to this hormone+level
            for symptom in all_user_symptoms:
                if symptom in SYMPTOM_TO_HORMONE_MAP:
                    if hormone_key in SYMPTOM_TO_HORMONE_MAP[symptom]:
                        # Convert to readable format
                        readable = symptom.lower()
                        matched_symptoms.append(readable)
            
            return matched_symptoms[:3]  # Max 3 symptoms for readability
        
        def build_personalized_description(hormone: str, level: str, symptoms: list) -> str:
            """Build personalized description based on user's actual symptoms"""
            level_word = "Higher" if level == "high" else "Lower"
            
            if symptoms:
                symptom_text = ", ".join(symptoms[:-1]) + f", and {symptoms[-1]}" if len(symptoms) > 1 else symptoms[0]
                return f"{level_word} levels may be contributing to {symptom_text}."
            else:
                # Fallback generic descriptions when no specific match
                fallbacks = {
                    ("estrogen", "high"): "Higher levels may be contributing to hormonal imbalances.",
                    ("estrogen", "low"): "Lower levels may be contributing to energy and mood changes.",
                    ("progesterone", "low"): "Lower levels may be contributing to cycle irregularities.",
                    ("progesterone", "high"): "Higher levels may be affecting your cycle.",
                    ("androgens", "high"): "Higher levels may be contributing to hormonal symptoms.",
                    ("insulin", "high"): "Higher levels may be affecting your metabolism.",
                    ("cortisol", "high"): "Higher levels may be contributing to stress-related symptoms.",
                    ("cortisol", "low"): "Lower levels may be affecting your energy.",
                    ("thyroid", "low"): "Lower levels may be affecting your metabolism and energy.",
                }
                return fallbacks.get((hormone, level), f"{level_word} levels detected.")
        
        
        # Get session data to re-run analysis with levels
        session_data = service.get_session_data(session_id)
        
        if not session_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session data not found"
            )

        # Debug: surface raw stored other_concerns before analysis to diagnose unintended carry-over
        try:
            print(f"🔎 Session {session_id} raw other_concerns: {getattr(session_data, 'other_concerns', None)}")
        except Exception as _e:
            print(f"⚠️ Unable to log other_concerns for session {session_id}: {_e}")
        
        # Create temp user profile for analysis
        temp_user_profile = {
            "age": session_data.age,
            "period_description": session_data.period_description,
            "birth_control": session_data.birth_control,
            "cycle_length": session_data.cycle_length,
            "period_concerns": session_data.period_concerns,
            "body_concerns": session_data.body_concerns,
            "skin_hair_concerns": session_data.skin_hair_concerns,
            "mental_health_concerns": session_data.mental_health_concerns,
            "other_concerns": session_data.other_concerns,
            "top_concern": session_data.top_concern,
            "diagnosed_conditions": session_data.diagnosed_conditions,
            "family_history": session_data.family_history,
            "workout_intensity": session_data.workout_intensity,
            "sleep_duration": session_data.sleep_duration,
            "stress_level": session_data.stress_level
        }
        
        # Use session-level caching - this is often the FIRST call in the flow
        root_cause_analysis = get_cached_hormone_analysis(session_id, temp_user_profile)
        
        # Build hormone cards array for frontend
        hormone_cards = []
        
        # Primary hormone (High Priority)
        primary_hormone = root_cause_analysis["primary_imbalance"]
        primary_level = root_cause_analysis["primary_level"]
        
        if primary_hormone in HORMONE_METADATA:
            meta = HORMONE_METADATA[primary_hormone]
            level_data = meta.get(primary_level, {})
            # Derive score key (e.g. androgens_high)
            all_scores = root_cause_analysis.get("all_scores", {})
            score_key = f"{primary_hormone}_{primary_level}" if primary_level else primary_hormone
            primary_score = all_scores.get(score_key, 0)
            
            # Get personalized symptoms and description
            user_symptoms = get_user_symptoms_for_hormone(temp_user_profile, primary_hormone, primary_level)
            personalized_desc = build_personalized_description(primary_hormone, primary_level, user_symptoms)
            
            hormone_cards.append({
                "hormone": primary_hormone,
                "name": meta["name"],
                "subtitle": meta["subtitle"],
                "level": primary_level,
                "score": primary_score,
                "icon": level_data.get("icon", ""),
                "description": personalized_desc,
                "symptoms": user_symptoms,
                "priority": "High Priority",
                "is_primary": True
            })
        
        # Secondary hormones (Moderate priority)
        secondary_hormones = root_cause_analysis.get("secondary_imbalances", [])
        secondary_levels = root_cause_analysis.get("secondary_levels", [])
        
        for idx, hormone in enumerate(secondary_hormones):
            if hormone in HORMONE_METADATA:
                level = secondary_levels[idx] if idx < len(secondary_levels) else "unknown"
                meta = HORMONE_METADATA[hormone]
                level_data = meta.get(level, {})
                all_scores = root_cause_analysis.get("all_scores", {})
                score_key = f"{hormone}_{level}" if level else hormone
                hormone_score = all_scores.get(score_key, 0)
                
                # Get personalized symptoms and description
                user_symptoms = get_user_symptoms_for_hormone(temp_user_profile, hormone, level)
                personalized_desc = build_personalized_description(hormone, level, user_symptoms)
                
                hormone_cards.append({
                    "hormone": hormone,
                    "name": meta["name"],
                    "subtitle": meta["subtitle"],
                    "level": level,
                    "score": hormone_score,
                    "icon": level_data.get("icon", ""),
                    "description": personalized_desc,
                    "symptoms": user_symptoms,
                    "priority": "Moderate",
                    "is_primary": False
                })
        
        return {
            "session_id": session_id,
            "primary_hormone": primary_hormone,
            "primary_level": primary_level,
            "secondary_hormones": secondary_hormones,
            "secondary_levels": secondary_levels,
            "hormone_cards": hormone_cards,
            "total_imbalances": len(hormone_cards)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Hormone analysis retrieval failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Hormone analysis retrieval failed: {str(e)}"
        )

@router.put("/users/timezone")
async def update_user_timezone(
    timezone_update: TimezoneUpdateRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update user timezone"""
    try:
        logger.info(f"Timezone change request: uid={current_user.get('uid')}, new_timezone={timezone_update.new_timezone}")
        
        service = QuestionService(db)
        success = service.update_user_timezone(current_user.get("uid"), timezone_update.new_timezone)
        
        if success:
            return TimezoneUpdateResponse(
                success=True,
                message="Timezone updated successfully",
                new_timezone=timezone_update.new_timezone
            )
        else:
            return TimezoneUpdateResponse(
                success=False,
                message="Timezone update failed"
            )
            
    except Exception as e:
        logger.error(f"Timezone change failed: {str(e)}")
        return TimezoneUpdateResponse(
            success=False,
            message=f"Error occurred during timezone change: {str(e)}"
        )