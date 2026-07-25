"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA CHATBOT - Main Chat Service
═══════════════════════════════════════════════════════════════════════════════
The main orchestration service that ties everything together.
This is the entry point for all chat interactions.
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chat_models import (
    ChatMessageRequest, ChatMessageResponse, VoiceMessageRequest,
    ConversationContext, InputMode, ResponseType, SliderConfig, ChatAction
)
from app.services.chat.user_context_service import UserContextService
from app.services.chat.chat_memory_service import ChatMemoryService
from app.services.chat.voice_service import VoiceService
from app.services.chat.langgraph_agent import run_chat_agent

logger = logging.getLogger(__name__)


def _non_empty_chat_content(content: Any, context: ConversationContext) -> str:
    """Guarantee every successful chat response has useful visible text."""
    normalized = str(content or "").strip()
    if normalized:
        return normalized

    fallbacks = {
        ConversationContext.KNOW_BODY: (
            "Hormones naturally rise and fall across your cycle, and those shifts can affect "
            "energy, mood, skin, sleep, and symptoms. Choose a topic below and I’ll explain it "
            "in simple terms. For anything specific to you, your doctor is your best resource 💜"
        ),
        ConversationContext.PERSONALISE: (
            "Tell me which part of your routine you want to personalize, and I’ll help make your "
            "plan fit you better."
        ),
        ConversationContext.SYMPTOM_CHECKIN: (
            "Tell me which symptom you want to track, and we’ll record how it feels today."
        ),
        ConversationContext.CARE_PLAN_MODAL: (
            "Tell me what you want to adjust in today’s plan, and I’ll guide you through the options."
        ),
        ConversationContext.GENERAL: "What would you like help with today?",
    }
    logger.warning("Chat agent returned empty content for context=%s; using safe fallback", context.value)
    return fallbacks[context]


class ChatService:
    """
    Main chat service - the central coordinator for all chatbot functionality.
    
    Flow:
    1. Receive message (text or voice)
    2. Get/create session
    3. Load user context (patient profile, plan, memory)
    4. Run through LangGraph agent
    5. Save message to history
    6. Return formatted response
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.user_context_service = UserContextService(db)
        self.memory_service = ChatMemoryService(db)
        self.voice_service = VoiceService()
    
    async def process_message(
        self,
        request: ChatMessageRequest
    ) -> ChatMessageResponse:
        """
        Process a chat message and return response.
        
        This is the main entry point for text messages.
        """
        session = None
        try:
            logger.info(f"Processing message for user {request.user_id}")
            
            # 1. Get or create session
            session = await self.memory_service.get_or_create_session(
                user_id=request.user_id,
                conversation_context=request.conversation_context,
                metadata=request.metadata
            )
            
            # 2. Save user message
            await self.memory_service.save_message(
                session_id=session.id,
                role="user",
                content=request.message,
                input_mode=request.input_mode.value if request.input_mode else "type"
            )

            # The LangGraph agent currently relies on OpenAI-compatible chat models
            # via LangChain's ChatOpenAI wrapper. If OPENAI_API_KEY isn't set,
            # fail gracefully while preserving the session for the client.
            if not settings.OPENAI_API_KEY:
                content = (
                    "I'm not fully connected right now, so I can't generate a full reply. "
                    "If this is your app, set OPENAI_API_KEY on the server and try again."
                )
                response = ChatMessageResponse(
                    session_id=str(session.id),
                    content=content,
                    response_type=ResponseType.TEXT,
                    timestamp=datetime.utcnow(),
                    metadata={
                        "error_code": "missing_openai_api_key",
                    },
                )

                try:
                    await self.memory_service.save_message(
                        session_id=session.id,
                        role="assistant",
                        content=response.content,
                        response_type=response.response_type.value,
                        metadata=response.metadata,
                    )
                except Exception:
                    logger.exception("Failed saving fallback assistant message")

                return response
            
            # 3. Load full context
            patient_profile = await self.user_context_service.get_patient_profile(request.user_id)
            todays_plan = await self.user_context_service.get_todays_plan(request.user_id)
            recent_summary = await self.user_context_service.get_recent_summary(request.user_id)
            memory_context = await self.memory_service.get_full_memory_context(
                user_id=request.user_id,
                session_id=session.id
            )
            
            # 4. Run through LangGraph agent
            agent_response = await run_chat_agent(
                user_id=request.user_id,
                session_id=str(session.id),
                message=request.message,
                conversation_context=request.conversation_context.value,
                input_mode=request.input_mode.value if request.input_mode else "type",
                patient_profile=patient_profile.model_dump(),
                todays_plan=todays_plan.model_dump(),
                recent_summary=recent_summary.model_dump(),
                memory_context=memory_context,
                db_session=self.db  # Pass database session
            )
            
            # 5. Build response
            response = ChatMessageResponse(
                session_id=str(session.id),
                content=_non_empty_chat_content(agent_response.get("content"), request.conversation_context),
                response_type=ResponseType(agent_response.get("response_type", "text")),
                choices=agent_response.get("choices"),
                slider_config=SliderConfig(**agent_response["slider_config"]) if agent_response.get("slider_config") else None,
                ui_blocks=agent_response.get("ui_blocks"),
                actions=[ChatAction(**a) for a in agent_response.get("actions", [])] if agent_response.get("actions") else None,
                timestamp=datetime.utcnow()
            )
            
            # 6. Save assistant message
            await self.memory_service.save_message(
                session_id=session.id,
                role="assistant",
                content=response.content,
                response_type=response.response_type.value,
                choices=response.choices,
                slider_config=response.slider_config.model_dump() if response.slider_config else None,
                actions=[a.model_dump() for a in response.actions] if response.actions else None,
                metadata={
                    **(agent_response.get("metadata") or {}),
                    "ui_blocks": [b.model_dump() for b in response.ui_blocks] if response.ui_blocks else None,
                },
                tools_called=agent_response.get("tool_calls")
            )
            
            return response
            
        except Exception as e:
            logger.exception("Error processing message")
            safe_session_id = str(session.id) if session is not None else "error"

            response = ChatMessageResponse(
                session_id=safe_session_id,
                content="I'm having trouble right now. Please try again in a moment. 💜",
                response_type=ResponseType.TEXT,
                timestamp=datetime.utcnow(),
                metadata={
                    "error_code": "chat_processing_error",
                    "error_type": type(e).__name__,
                },
            )

            if session is not None:
                try:
                    await self.memory_service.save_message(
                        session_id=session.id,
                        role="assistant",
                        content=response.content,
                        response_type=response.response_type.value,
                        metadata=response.metadata,
                    )
                except Exception:
                    logger.exception("Failed saving error fallback assistant message")

            return response
    
    async def process_voice_message(
        self,
        request: VoiceMessageRequest
    ) -> ChatMessageResponse:
        """
        Process a voice message - transcribe and then process as text.
        """
        try:
            logger.info(f"Processing voice message for user {request.user_id}")
            
            # 1. Transcribe audio
            transcription = await self.voice_service.transcribe_base64(
                base64_audio=request.audio_base64,
                audio_format=request.audio_format,
                language=request.language,
                prompt_context="Women's health, menstrual cycle, hormones, wellness"
            )
            
            if not transcription["success"]:
                return ChatMessageResponse(
                    session_id="error",
                    content="I couldn't understand the audio. Could you try again or type your message?",
                    response_type=ResponseType.TEXT,
                    choices=["Try again", "Type instead"],
                    timestamp=datetime.utcnow()
                )
            
            # 2. Process as text message
            text_request = ChatMessageRequest(
                user_id=request.user_id,
                message=transcription["text"],
                conversation_context=request.conversation_context,
                input_mode=InputMode.YAP,  # Voice input
                metadata={
                    "transcription_model": transcription["model"],
                    "transcription_confidence": transcription.get("confidence"),
                    "original_audio_format": request.audio_format
                }
            )
            
            response = await self.process_message(text_request)
            
            # Add transcription to response metadata
            response.metadata = response.metadata or {}
            response.metadata["transcribed_text"] = transcription["text"]
            
            return response
            
        except Exception as e:
            logger.exception("Error processing voice message")
            return ChatMessageResponse(
                session_id="error",
                content="I had trouble with the voice message. Could you try typing instead?",
                response_type=ResponseType.TEXT,
                choices=["Type my message"],
                timestamp=datetime.utcnow()
            )
    
    async def get_session_history(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get chat history for a session or all recent sessions.
        """
        try:
            if session_id:
                messages = await self.memory_service.get_session_messages(
                    session_id=session_id,
                    limit=limit
                )
            else:
                # Get recent sessions
                from app.core.database import ChatSession, ChatMessage
                from sqlalchemy import desc, and_
                
                sessions = self.db.query(ChatSession).filter(
                    ChatSession.user_id == user_id
                ).order_by(desc(ChatSession.created_at)).limit(5).all()
                
                messages = []
                for session in sessions:
                    session_messages = await self.memory_service.get_session_messages(
                        session_id=session.id,
                        limit=20
                    )
                    messages.extend(session_messages)
            
            return messages
            
        except Exception as e:
            logger.error(f"Error getting session history: {str(e)}")
            return []
    
    async def end_session(self, session_id: str) -> bool:
        """
        End a chat session and generate summary.
        """
        try:
            await self.memory_service.end_session(session_id)
            return True
        except Exception as e:
            logger.error(f"Error ending session: {str(e)}")
            return False
    
    async def get_proactive_greeting(self, user_id: str) -> Optional[str]:
        """
        Get a proactive greeting based on user's current state.
        Used when user opens the chat.
        """
        try:
            from app.services.chat.tools import check_proactive_triggers
            
            triggers = await check_proactive_triggers(user_id, self.db)
            
            if triggers["has_triggers"]:
                top_trigger = triggers["top_trigger"]
                return top_trigger["message"]
            
            # Default greeting based on time
            hour = datetime.now().hour
            if hour < 12:
                return "Good morning! 🌅 How are you feeling today?"
            elif hour < 17:
                return "Good afternoon! How's your day going? 💜"
            else:
                return "Good evening! How was your day? 🌙"
                
        except Exception as e:
            logger.error(f"Error getting proactive greeting: {str(e)}")
            return "Hi there! How can I help you today? 💜"
    
    async def handle_slider_response(
        self,
        user_id: str,
        session_id: Optional[str],
        value: int,
        context: Dict[str, Any]
    ) -> ChatMessageResponse:
        """
        Handle slider value submission (for symptom severity, etc.)
        """
        try:
            symptom_type = context.get("symptom_type", "symptom")
            
            # Process as message with context
            message = f"Severity {value} for {symptom_type}"
            
            request = ChatMessageRequest(
                user_id=user_id,
                message=message,
                conversation_context=ConversationContext.SYMPTOM_CHECKIN,
                input_mode=InputMode.TAP,
                session_id=session_id,
                metadata={
                    "slider_value": value,
                    "symptom_type": symptom_type,
                    **context
                }
            )
            
            return await self.process_message(request)
            
        except Exception as e:
            logger.error(f"Error handling slider response: {str(e)}")
            return ChatMessageResponse(
                session_id=session_id,
                content="Got it! Is there anything else you'd like to share about how you're feeling?",
                response_type=ResponseType.TEXT,
                timestamp=datetime.utcnow()
            )
    
    async def handle_choice_selection(
        self,
        user_id: str,
        session_id: str,
        choice: str,
        conversation_context: ConversationContext
    ) -> ChatMessageResponse:
        """
        Handle choice button selection.
        """
        # Process the choice as a regular message
        request = ChatMessageRequest(
            user_id=user_id,
            message=choice,
            conversation_context=conversation_context,
            input_mode=InputMode.TAP,
            session_id=session_id
        )
        
        return await self.process_message(request)
