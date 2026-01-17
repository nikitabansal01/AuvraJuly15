"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA CHATBOT - API Endpoints
═══════════════════════════════════════════════════════════════════════════════
REST API endpoints for the chatbot.

Endpoints:
- POST /chat/message - Send text message
- POST /chat/voice - Send voice message
- GET /chat/sessions - Get session history
- POST /chat/sessions/{session_id}/end - End a session
- GET /chat/greeting - Get proactive greeting
- POST /chat/slider - Handle slider response
- POST /chat/choice - Handle choice selection
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
import json

from app.core.database import get_db, UserProfile
from app.services.chat.chat_service import ChatService
from app.models.chat_models import (
    ChatMessageRequest, ChatMessageResponse, VoiceMessageRequest,
    ConversationContext, InputMode, ResponseType
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])  # No prefix here - api.py adds /chat prefix


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST/RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class TextMessageRequest(BaseModel):
    """Request for text message endpoint."""
    user_id: str
    message: str
    conversation_context: str = "care_plan_modal"
    input_mode: str = "type"
    session_id: Optional[str] = None  # UUID string
    metadata: Optional[Dict[str, Any]] = None


class VoiceMessageUploadRequest(BaseModel):
    """Request for voice message with base64 audio."""
    user_id: str
    audio_base64: str
    audio_format: str = "m4a"
    language: str = "en"
    conversation_context: str = "care_plan_modal"


class SliderRequest(BaseModel):
    """Request for slider response. session_id is OPTIONAL - slider can start a new session."""
    user_id: str
    value: int
    context: Dict[str, Any]
    session_id: Optional[str] = Field(default=None, description="UUID string (optional: slider can start a session)")


class ChoiceRequest(BaseModel):
    """Request for choice selection."""
    user_id: str
    session_id: str  # UUID string
    choice: str
    conversation_context: str = "care_plan_modal"


class SessionResponse(BaseModel):
    """Response for session info."""
    session_id: str  # UUID string
    conversation_context: str
    status: str
    created_at: datetime
    message_count: int
    summary: Optional[str] = None


class GreetingResponse(BaseModel):
    """Response for proactive greeting."""
    greeting: str
    triggers: Optional[List[Dict[str, Any]]] = None


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def validate_user(user_id: str, db: Session) -> bool:
    """Validate that user exists."""
    user = db.query(UserProfile).filter(UserProfile.uid == user_id).first()
    return user is not None


def parse_conversation_context(context_str: str) -> ConversationContext:
    """Parse conversation context string to enum."""
    context_map = {
        "care_plan_modal": ConversationContext.CARE_PLAN_MODAL,
        "symptom_checkin": ConversationContext.SYMPTOM_CHECKIN,
        "personalise": ConversationContext.PERSONALISE,
        "know_body": ConversationContext.KNOW_BODY,
        "general": ConversationContext.GENERAL
    }
    return context_map.get(context_str, ConversationContext.GENERAL)


def parse_input_mode(mode_str: str) -> InputMode:
    """Parse input mode string to enum."""
    mode_map = {
        "tap": InputMode.TAP,
        "yap": InputMode.YAP,
        "type": InputMode.TYPE
    }
    return mode_map.get(mode_str, InputMode.TYPE)


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/message", response_model=ChatMessageResponse)
async def send_message(
    request: TextMessageRequest,
    db: Session = Depends(get_db)
):
    """
    Send a text message to the chatbot.
    
    This is the main endpoint for chat interactions (non-streaming).
    
    Args:
        request: The message request containing user_id, message, context, etc.
        
    Returns:
        ChatMessageResponse with the AI's response
    """
    try:
        # Validate user
        if not validate_user(request.user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Build internal request
        chat_request = ChatMessageRequest(
            user_id=request.user_id,
            message=request.message,
            conversation_context=parse_conversation_context(request.conversation_context),
            input_mode=parse_input_mode(request.input_mode),
            session_id=request.session_id,
            metadata=request.metadata
        )
        
        # Process message
        chat_service = ChatService(db)
        response = await chat_service.process_message(chat_request)
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in send_message: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing message: {str(e)}"
        )


@router.post("/message/stream")
async def send_message_streaming(
    request: TextMessageRequest,
    db: Session = Depends(get_db)
):
    """
    Deprecated: streaming has been removed.

    For backwards compatibility this endpoint now behaves like /message and returns a
    standard JSON response (no SSE / token streaming).
    
    Args:
        request: The message request containing user_id, message, context, etc.
        
    Returns:
        ChatMessageResponse JSON (non-streaming)
    """
    try:
        # Delegate to the standard non-streaming implementation.
        return await send_message(request, db)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in send_message_streaming (deprecated): {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing message: {str(e)}"
        )


@router.post("/voice", response_model=ChatMessageResponse)
async def send_voice_message(
    request: VoiceMessageUploadRequest,
    db: Session = Depends(get_db)
):
    """
    Send a voice message to the chatbot.
    
    Audio is expected as base64 encoded string.
    Supports: mp3, mp4, mpeg, mpga, m4a, wav, webm, ogg, flac
    
    Args:
        request: Voice message request with base64 audio
        
    Returns:
        ChatMessageResponse with the AI's response (includes transcription)
    """
    try:
        # Validate user
        if not validate_user(request.user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Build internal request
        voice_request = VoiceMessageRequest(
            user_id=request.user_id,
            audio_base64=request.audio_base64,
            audio_format=request.audio_format,
            language=request.language,
            conversation_context=parse_conversation_context(request.conversation_context)
        )
        
        # Process voice message
        chat_service = ChatService(db)
        response = await chat_service.process_voice_message(voice_request)
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in send_voice_message: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing voice message: {str(e)}"
        )


@router.post("/voice/upload", response_model=ChatMessageResponse)
async def send_voice_file(
    user_id: str = Form(...),
    conversation_context: str = Form("care_plan_modal"),
    language: str = Form("en"),
    audio: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Upload voice file directly instead of base64.
    Alternative endpoint for voice input.
    
    Args:
        user_id: User's ID
        conversation_context: Chat context
        language: Language code
        audio: Audio file
        
    Returns:
        ChatMessageResponse with AI's response
    """
    try:
        # Validate user
        if not validate_user(user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Read audio data
        audio_data = await audio.read()
        
        # Get format from filename
        audio_format = audio.filename.split('.')[-1] if audio.filename else "m4a"
        
        # Convert to base64
        import base64
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        # Build internal request
        voice_request = VoiceMessageRequest(
            user_id=user_id,
            audio_base64=audio_base64,
            audio_format=audio_format,
            language=language,
            conversation_context=parse_conversation_context(conversation_context)
        )
        
        # Process voice message
        chat_service = ChatService(db)
        response = await chat_service.process_voice_message(voice_request)
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in send_voice_file: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing voice file: {str(e)}"
        )


@router.post("/voice-response")
async def generate_voice_response(
    request: Dict[str, Any],
    db: Session = Depends(get_db)
):
    """
    Generate voice (audio) response from text.
    
    Makes Auvra SPEAK! Premium conversational experience.
    
    Request body:
        {
            "text": "Response text to convert to speech",
            "voice": "nova",  // alloy, echo, fable, onyx, nova, shimmer
            "speed": 1.0,     // 0.25 to 4.0
            "model": "tts-1"  // tts-1 or tts-1-hd
        }
    
    Returns:
        Audio file (MP3) as response
    """
    try:
        from app.services.chat.voice_service import VoiceService
        
        text = request.get("text")
        if not text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Text is required"
            )
        
        voice = request.get("voice", "nova")
        speed = request.get("speed", 1.0)
        model = request.get("model", "tts-1")
        
        logger.info(f"🎤 Generating voice response: {len(text)} chars")
        
        voice_service = VoiceService()
        result = await voice_service.generate_speech(text, voice, speed, model)
        
        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"TTS generation failed: {result.get('error')}"
            )
        
        return Response(
            content=result["audio_bytes"],
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "attachment; filename=auvra_response.mp3",
                "X-Voice": voice,
                "X-Model": model,
                "X-Text-Length": str(result["text_length"]),
                "X-Audio-Size-KB": str(result["audio_size_kb"]),
            },
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error generating voice response: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating voice response: {str(e)}"
        )


@router.get("/sessions")
async def get_sessions_query(
    user_id: str,
    limit: int = 10,
    db: Session = Depends(get_db)
) -> List[SessionResponse]:
    """Compatibility endpoint: get user's chat sessions via query param.

    Mobile frontend calls: GET /api/v1/chat/sessions?user_id=...&limit=...
    """
    try:
        # Validate user
        if not validate_user(user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        from app.core.database import ChatSession, ChatMessage
        from sqlalchemy import desc, func

        sessions = db.query(ChatSession).filter(
            ChatSession.user_id == user_id
        ).order_by(desc(ChatSession.created_at)).limit(limit).all()

        result = []
        for session in sessions:
            # Count messages
            msg_count = db.query(func.count(ChatMessage.id)).filter(
                ChatMessage.session_id == session.id
            ).scalar()

            result.append(SessionResponse(
                session_id=session.id,
                conversation_context=session.conversation_context,
                status=session.status,
                created_at=session.created_at,
                message_count=msg_count,
                summary=session.summary
            ))

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting sessions: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting sessions: {str(e)}"
        )


@router.get("/sessions/{user_id}")
async def get_sessions(
    user_id: str,
    limit: int = 10,
    db: Session = Depends(get_db)
) -> List[SessionResponse]:
    """
    Get user's chat sessions.
    
    Args:
        user_id: User's ID
        limit: Max number of sessions to return
        
    Returns:
        List of session info
    """
    try:
        # Validate user
        if not validate_user(user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        from app.core.database import ChatSession, ChatMessage
        from sqlalchemy import desc, func
        
        sessions = db.query(ChatSession).filter(
            ChatSession.user_id == user_id
        ).order_by(desc(ChatSession.created_at)).limit(limit).all()
        
        result = []
        for session in sessions:
            # Count messages
            msg_count = db.query(func.count(ChatMessage.id)).filter(
                ChatMessage.session_id == session.id
            ).scalar()
            
            result.append(SessionResponse(
                session_id=session.id,
                conversation_context=session.conversation_context,
                status=session.status,
                created_at=session.created_at,
                message_count=msg_count,
                summary=session.summary
            ))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting sessions: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting sessions: {str(e)}"
        )


@router.get("/sessions/{user_id}/{session_id}/messages")
async def get_session_messages(
    user_id: str,
    session_id: str,
    limit: int = 50,
    db: Session = Depends(get_db)
) -> List[Dict[str, Any]]:
    """
    Get messages from a specific session.
    
    Args:
        user_id: User's ID
        session_id: Session ID (UUID)
        limit: Max messages to return
        
    Returns:
        List of messages
    """
    try:
        # Validate user
        if not validate_user(user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        chat_service = ChatService(db)
        messages = await chat_service.get_session_history(
            user_id=user_id,
            session_id=session_id,
            limit=limit
        )
        
        return messages
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting session messages: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting messages: {str(e)}"
        )


@router.post("/sessions/{session_id}/end")
async def end_session(
    session_id: str,
    db: Session = Depends(get_db)
) -> Dict[str, bool]:
    """
    End a chat session.
    
    This will generate a summary of the conversation.
    
    Args:
        session_id: Session ID (UUID) to end
        
    Returns:
        Success status
    """
    try:
        chat_service = ChatService(db)
        success = await chat_service.end_session(session_id)
        
        return {"success": success}
        
    except Exception as e:
        logger.error(f"Error ending session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error ending session: {str(e)}"
        )


@router.get("/greeting/{user_id}", response_model=GreetingResponse)
async def get_greeting(
    user_id: str,
    db: Session = Depends(get_db)
):
    """
    Get proactive greeting for user.
    
    Call this when user opens the chat to get a personalized greeting
    based on their current state (streak, phase change, etc.)
    
    Args:
        user_id: User's ID
        
    Returns:
        Greeting message and any active triggers
    """
    try:
        # Validate user
        if not validate_user(user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        chat_service = ChatService(db)
        greeting = await chat_service.get_proactive_greeting(user_id)
        
        # Get triggers for additional context
        from app.services.chat.tools import check_proactive_triggers
        triggers_result = await check_proactive_triggers(user_id, db)
        
        return GreetingResponse(
            greeting=greeting,
            triggers=triggers_result.get("triggers")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting greeting: {str(e)}")
        return GreetingResponse(
            greeting="Hi there! How can I help you today? 💜",
            triggers=None
        )


@router.get("/greeting", response_model=GreetingResponse)
async def get_greeting_query(
    user_id: str,
    db: Session = Depends(get_db)
):
    """Compatibility endpoint: get proactive greeting via query param.

    Mobile frontend calls: GET /api/v1/chat/greeting?user_id=...
    """
    return await get_greeting(user_id=user_id, db=db)


@router.post("/slider", response_model=ChatMessageResponse)
async def handle_slider(
    request: SliderRequest,
    db: Session = Depends(get_db)
):
    """
    Handle slider value submission.
    
    Used for symptom severity and other numeric inputs.
    
    Args:
        request: Slider request with value and context
        
    Returns:
        ChatMessageResponse with follow-up
    """
    try:
        # Validate user
        if not validate_user(request.user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        chat_service = ChatService(db)
        response = await chat_service.handle_slider_response(
            user_id=request.user_id,
            session_id=request.session_id,
            value=request.value,
            context=request.context
        )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling slider: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error handling slider: {str(e)}"
        )


@router.post("/choice", response_model=ChatMessageResponse)
async def handle_choice(
    request: ChoiceRequest,
    db: Session = Depends(get_db)
):
    """
    Handle choice button selection.
    
    Args:
        request: Choice request with selected option
        
    Returns:
        ChatMessageResponse
    """
    try:
        # Validate user
        if not validate_user(request.user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        chat_service = ChatService(db)
        response = await chat_service.handle_choice_selection(
            user_id=request.user_id,
            session_id=request.session_id,
            choice=request.choice,
            conversation_context=parse_conversation_context(request.conversation_context)
        )
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling choice: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error handling choice: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/health")
async def chat_health():
    """Health check for chat service."""
    return {
        "status": "healthy",
        "service": "auvra-chatbot",
        "version": "v3.0.0",  # Updated with new intelligence features
        "timestamp": datetime.utcnow().isoformat()
    }


# ═══════════════════════════════════════════════════════════════════════════════
# NEW INTELLIGENCE FEATURES
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/wellness-score/{user_id}")
async def get_wellness_score(
    user_id: str,
    db: Session = Depends(get_db)
):
    """
    Get daily wellness score for user.
    
    Calculates holistic wellness from sleep, mood, symptoms, habits, etc.
    Returns score 0-100 with dimension breakdown and recommendations.
    """
    try:
        from app.services.chat.intelligence.wellness_score import WellnessScoreCalculator
        
        # Validate user
        if not validate_user(user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        logger.info(f"Calculating wellness score for {user_id}")
        
        # Load mood data from in-memory storage
        user_moods = _mood_storage.get(user_id, [])
        today = datetime.now().strftime("%Y-%m-%d")
        today_mood = next((m for m in user_moods if m["date"] == today), None)
        
        # Build mood data from today's entry or defaults
        mood_data = {
            "mood": today_mood.get("mood_level", 5) if today_mood else 5,
            "energy": today_mood.get("energy_level", 3) if today_mood else 3,
            "stress": 5  # Default, could add stress tracking later
        }
        
        # TODO: Load actual sleep, symptom, and habit data from database
        # For now, use reasonable defaults
        calculator = WellnessScoreCalculator()
        score = calculator.calculate_daily_score(
            sleep_data={"hours": 7, "quality": 7},  # TODO: from sleep tracking
            mood_data=mood_data,
            symptom_data=[],  # TODO: from symptom tracking
            habit_data={"completed": 3, "total": 5}  # TODO: from habit tracking
        )
        
        return {
            "success": True,
            "user_id": user_id,
            "date": today,
            **score
        }
        
    except Exception as e:
        logger.error(f"Error calculating wellness score: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error calculating wellness score: {str(e)}"
        )


@router.get("/predict-symptoms/{user_id}")
async def predict_symptoms(
    user_id: str,
    db: Session = Depends(get_db)
):
    """
    Predict upcoming symptoms based on cycle + historical patterns.
    
    Returns predictions for next 2-3 days with proactive advice.
    """
    try:
        from app.services.chat.intelligence.symptom_predictor import SymptomPredictor
        from app.services.chat.user_context_service import UserContextService
        
        # Validate user
        if not validate_user(user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        logger.info(f"Predicting symptoms for {user_id}")
        
        # Load user's cycle data
        user_context_service = UserContextService(db)
        patient_profile = await user_context_service.get_patient_profile(user_id)
        profile_dict = patient_profile.model_dump() if patient_profile else {}
        
        # Get current phase and cycle info
        current_phase = profile_dict.get("phase", "luteal")
        cycle_day = profile_dict.get("cycle_day", 14)
        cycle_length = 28  # Default, could get from profile
        days_until_period = max(0, cycle_length - cycle_day)
        
        predictor = SymptomPredictor()
        predictions = predictor.predict_upcoming_symptoms(
            user_id=user_id,
            current_phase=current_phase,
            days_until_period=days_until_period,
            historical_symptoms=[],  # TODO: Load from symptom tracking table
            db_session=db
        )
        
        return {
            "success": True,
            "user_id": user_id,
            **predictions
        }
        
    except Exception as e:
        logger.error(f"Error predicting symptoms: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error predicting symptoms: {str(e)}"
        )


@router.get("/session-summary/{session_id}")
async def get_session_summary(
    session_id: str,
    user_id: str,
    db: Session = Depends(get_db)
):
    """
    Get intelligent summary of conversation session.
    
    Returns key topics, emotional journey, action items, insights.
    """
    try:
        from app.services.chat.intelligence.session_summarizer import SessionSummarizer
        from app.core.database import ChatSession, ChatMessage
        
        # Validate user
        if not validate_user(user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        logger.info(f"Generating session summary for {session_id}")
        
        # Load actual messages from database
        messages_query = db.query(ChatMessage).filter(
            ChatMessage.session_id == session_id
        ).order_by(ChatMessage.created_at).all()
        
        messages = [
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.created_at.isoformat() if msg.created_at else None
            }
            for msg in messages_query
        ]
        
        # Get session info
        session = db.query(ChatSession).filter(
            ChatSession.id == session_id
        ).first()
        
        session_metadata = {
            "user_id": user_id, 
            "session_id": session_id,
            "conversation_context": session.conversation_context if session else "general",
            "message_count": len(messages)
        }
        
        summarizer = SessionSummarizer()
        summary = summarizer.summarize_session(
            messages=messages,
            emotional_states=["neutral", "hopeful"],  # TODO: Extract from messages
            session_metadata=session_metadata
        )
        
        return {
            "success": True,
            "session_id": session_id,
            "user_id": user_id,
            **summary
        }
        
    except Exception as e:
        logger.error(f"Error generating session summary: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating session summary: {str(e)}"
        )


@router.get("/cache/stats")
async def get_cache_stats():
    """Get cache performance statistics."""
    try:
        from app.services.chat.intelligence.intelligent_cache import get_cache
        
        cache = get_cache()
        stats = cache.get_stats()
        
        return {
            "success": True,
            "cache_stats": stats,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error getting cache stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting cache stats: {str(e)}"
        )


@router.post("/cache/clear")
async def clear_cache(
    cache_type: Optional[str] = None,
    user_id: Optional[str] = None
):
    """Clear cache entries."""
    try:
        from app.services.chat.intelligence.intelligent_cache import get_cache
        
        cache = get_cache()
        
        if user_id:
            cache.invalidate_user(user_id)
            return {"success": True, "message": f"Cleared cache for user {user_id}"}
        elif cache_type:
            cache.invalidate_type(cache_type)
            return {"success": True, "message": f"Cleared cache type {cache_type}"}
        else:
            cache.clear_expired()
            return {"success": True, "message": "Cleared expired cache entries"}
        
    except Exception as e:
        logger.error(f"Error clearing cache: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error clearing cache: {str(e)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# MOOD TRACKING ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

class MoodLogRequest(BaseModel):
    """Request for mood logging."""
    user_id: str
    mood_level: int = Field(..., ge=1, le=7, description="Mood level 1-7")
    energy_level: int = Field(..., ge=1, le=5, description="Energy level 1-5")
    notes: Optional[str] = None
    timestamp: Optional[str] = None


class MoodEntry(BaseModel):
    """Response model for mood entry."""
    id: str
    user_id: str
    mood_level: int
    energy_level: int
    notes: Optional[str]
    timestamp: str
    date: str


# In-memory mood storage (TODO: Replace with database table for persistence)
# This data is lost on server restart - implement MoodLog table in database.py
_mood_storage: Dict[str, List[Dict[str, Any]]] = {}


@router.post("/mood-log")
async def log_mood(request: MoodLogRequest, db: Session = Depends(get_db)):
    """Log user's daily mood and energy level."""
    try:
        import uuid
        
        # Validate user
        if not validate_user(request.user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        logger.info(f"Logging mood for user {request.user_id}: mood={request.mood_level}, energy={request.energy_level}")
        
        # Create mood entry
        timestamp = request.timestamp or datetime.now().isoformat()
        date = timestamp.split("T")[0]
        
        entry = {
            "id": str(uuid.uuid4()),
            "user_id": request.user_id,
            "mood_level": request.mood_level,
            "energy_level": request.energy_level,
            "notes": request.notes,
            "timestamp": timestamp,
            "date": date
        }
        
        # Store in memory (TODO: Store in database)
        if request.user_id not in _mood_storage:
            _mood_storage[request.user_id] = []
        
        # Remove existing entry for today if any
        _mood_storage[request.user_id] = [
            m for m in _mood_storage[request.user_id] if m["date"] != date
        ]
        
        # Add new entry
        _mood_storage[request.user_id].append(entry)
        
        # Calculate streak
        streak = calculate_mood_streak(request.user_id)
        
        return {
            "success": True,
            "entry": entry,
            "streak": streak,
            "message": "Mood logged successfully! 🌟"
        }
        
    except Exception as e:
        logger.error(f"Error logging mood: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error logging mood: {str(e)}"
        )


@router.get("/mood-history/{user_id}")
async def get_mood_history(
    user_id: str,
    days: int = 7,
    db: Session = Depends(get_db)
):
    """Get mood history for user."""
    try:
        logger.info(f"Fetching mood history for user {user_id}, last {days} days")
        
        # Validate user
        if not validate_user(user_id, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Get from storage
        user_moods = _mood_storage.get(user_id, [])
        
        # Filter by date range - use timedelta for correct date math
        cutoff_date = datetime.now() - timedelta(days=days)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d")
        
        recent_moods = [
            m for m in user_moods
            if m["date"] >= cutoff_str
        ]
        
        # Sort by date descending
        recent_moods.sort(key=lambda x: x["date"], reverse=True)
        
        # Calculate statistics
        if recent_moods:
            avg_mood = sum(m["mood_level"] for m in recent_moods) / len(recent_moods)
            avg_energy = sum(m["energy_level"] for m in recent_moods) / len(recent_moods)
            trend = calculate_mood_trend(recent_moods)
        else:
            avg_mood = 0
            avg_energy = 0
            trend = "no_data"
        
        return {
            "success": True,
            "user_id": user_id,
            "entries": recent_moods,
            "statistics": {
                "average_mood": round(avg_mood, 1),
                "average_energy": round(avg_energy, 1),
                "total_entries": len(recent_moods),
                "trend": trend
            },
            "streak": calculate_mood_streak(user_id)
        }
        
    except Exception as e:
        logger.error(f"Error getting mood history: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting mood history: {str(e)}"
        )


def calculate_mood_streak(user_id: str) -> int:
    """Calculate consecutive days of mood logging."""
    user_moods = _mood_storage.get(user_id, [])
    if not user_moods:
        return 0
    
    # Get unique dates sorted descending
    dates = sorted(set(m["date"] for m in user_moods), reverse=True)
    
    if not dates:
        return 0
    
    # Check if today is logged
    today = datetime.now().strftime("%Y-%m-%d")
    if dates[0] != today:
        return 0
    
    # Count consecutive days - use timedelta for correct date math
    streak = 1
    for i in range(1, len(dates)):
        expected_date = datetime.now() - timedelta(days=i)
        expected_str = expected_date.strftime("%Y-%m-%d")
        
        if dates[i] == expected_str:
            streak += 1
        else:
            break
    
    return streak


def calculate_mood_trend(moods: List[Dict[str, Any]]) -> str:
    """Calculate mood trend from recent entries."""
    if len(moods) < 2:
        return "stable"
    
    # Compare first half vs second half
    half = len(moods) // 2
    recent_avg = sum(m["mood_level"] for m in moods[:half]) / half
    older_avg = sum(m["mood_level"] for m in moods[half:]) / (len(moods) - half)
    
    diff = recent_avg - older_avg
    
    if diff > 0.5:
        return "improving"
    elif diff < -0.5:
        return "declining"
    return "stable"


# ═══════════════════════════════════════════════════════════════════════════════
# THREAD HISTORY ENDPOINTS - Per-flow chat history
# ═══════════════════════════════════════════════════════════════════════════════

from app.core.database import CarePlanCheckInThread, SymptomCheckInThread
from app.api.v1.endpoints.auth import get_current_user


class ThreadSummary(BaseModel):
    """Summary of a chat thread for history view."""
    id: str
    flow_type: str
    local_date: str
    summary: Optional[str] = None
    message_count: int = 0
    created_at: str
    updated_at: str
    is_active: bool = True


class ThreadListResponse(BaseModel):
    """Response for thread listing."""
    flow_type: str
    threads: List[ThreadSummary]
    total: int


def _summarize_messages(messages: list, max_chars: int = 60) -> str:
    """Extract a short summary from messages."""
    if not messages:
        return "No messages yet"
    
    # Find last bot message for summary
    for msg in reversed(messages):
        if msg.get("role") == "bot":
            content = msg.get("content", "")
            if len(content) > max_chars:
                return content[:max_chars] + "..."
            return content
    
    return "Conversation started"


@router.get("/threads/{flow_type}", response_model=ThreadListResponse)
async def get_threads_by_flow(
    flow_type: str,
    limit: int = 20,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get thread history for a specific flow type.
    
    flow_type options:
    - care_plan: Care Plan Check-in threads
    - symptom: Symptom Check-in threads
    - personalise: Personalization threads (stored in care_plan table with context)
    - know_body: Know My Body threads (stored in care_plan table with context)
    """
    uid = current_user["uid"]
    threads = []
    
    if flow_type == "care_plan":
        # Query CarePlanCheckInThread
        rows = db.query(CarePlanCheckInThread).filter(
            CarePlanCheckInThread.uid == uid
        ).order_by(CarePlanCheckInThread.local_date.desc()).limit(limit).all()
        
        for row in rows:
            messages = row.raw_messages or []
            threads.append(ThreadSummary(
                id=row.id,
                flow_type="care_plan",
                local_date=row.local_date.isoformat() if row.local_date else "",
                summary=_summarize_messages(messages),
                message_count=len(messages),
                created_at=row.created_at.isoformat() if row.created_at else "",
                updated_at=row.updated_at.isoformat() if row.updated_at else "",
                is_active=not row.is_closed
            ))
    
    elif flow_type == "symptom":
        # Query SymptomCheckInThread
        rows = db.query(SymptomCheckInThread).filter(
            SymptomCheckInThread.uid == uid
        ).order_by(SymptomCheckInThread.local_date.desc()).limit(limit).all()
        
        for row in rows:
            messages = row.raw_messages or []
            threads.append(ThreadSummary(
                id=row.id,
                flow_type="symptom",
                local_date=row.local_date.isoformat() if row.local_date else "",
                summary=_summarize_messages(messages),
                message_count=len(messages),
                created_at=row.created_at.isoformat() if row.created_at else "",
                updated_at=row.updated_at.isoformat() if row.updated_at else "",
                is_active=not row.is_closed
            ))
    
    elif flow_type in ("personalise", "know_body"):
        # These use the LangGraph state storage - for now return empty
        # In production, create separate tables or use unified chat session table
        pass
    
    else:
        raise HTTPException(status_code=400, detail=f"Unknown flow_type: {flow_type}")
    
    return ThreadListResponse(
        flow_type=flow_type,
        threads=threads,
        total=len(threads)
    )


@router.get("/threads/{flow_type}/{thread_id}")
async def get_thread_messages(
    flow_type: str,
    thread_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get full message history for a specific thread.
    """
    uid = current_user["uid"]
    
    if flow_type == "care_plan":
        thread = db.query(CarePlanCheckInThread).filter(
            CarePlanCheckInThread.id == thread_id,
            CarePlanCheckInThread.uid == uid
        ).first()
    elif flow_type == "symptom":
        thread = db.query(SymptomCheckInThread).filter(
            SymptomCheckInThread.id == thread_id,
            SymptomCheckInThread.uid == uid
        ).first()
    else:
        raise HTTPException(status_code=400, detail=f"Unknown flow_type: {flow_type}")
    
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    messages = thread.raw_messages or []
    
    return {
        "thread_id": thread.id,
        "flow_type": flow_type,
        "local_date": thread.local_date.isoformat() if thread.local_date else "",
        "messages": [
            {
                "id": msg.get("id", ""),
                "text": msg.get("content", ""),
                "isBot": msg.get("role") == "bot",
                "created_at": msg.get("created_at", "")
            }
            for msg in messages
        ],
        "message_count": len(messages)
    }
