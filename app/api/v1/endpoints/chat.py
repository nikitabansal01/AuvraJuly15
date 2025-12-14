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
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.database import get_db, UserProfile
from app.services.chat.chat_service import ChatService
from app.models.chat_models import (
    ChatMessageRequest, ChatMessageResponse, VoiceMessageRequest,
    ConversationContext, InputMode, ResponseType
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


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
    """Request for slider response."""
    user_id: str
    session_id: str  # UUID string
    value: int
    context: Dict[str, Any]


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
        "know_body": ConversationContext.KNOW_BODY
    }
    return context_map.get(context_str, ConversationContext.CARE_PLAN_MODAL)


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
    
    This is the main endpoint for chat interactions.
    
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
        "timestamp": datetime.utcnow().isoformat()
    }
