"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA CHATBOT - Chat Memory Service
═══════════════════════════════════════════════════════════════════════════════
3-Layer Memory System:
1. Session Memory: Current conversation (in-context)
2. Recent Memory: Last 7 days summarized (ConversationSummary table)
3. Permanent Memory: User profile facts (PatientProfile)

This enables doctor-like memory across conversations.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc
import json
from openai import AsyncOpenAI
from groq import AsyncGroq

from app.core.database import (
    ChatSession, ChatMessage, ConversationSummary, UserProfile
)
from app.core.config import settings
from app.models.chat_models import ConversationContext

logger = logging.getLogger(__name__)

# Groq fallback configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_FALLBACK_MODEL = "llama-3.3-70b-versatile"  # Higher rate limits (30K TPM vs 8K)


class ChatMemoryService:
    """
    Manages the 3-layer memory system for the chatbot.
    Ensures the AI remembers what matters like a real doctor would.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None
        self.groq_client = AsyncGroq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
    
    # ═══════════════════════════════════════════════════════════════════════════
    # LAYER 1: Session Memory (Current Conversation)
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def get_or_create_session(
        self,
        user_id: str,
        conversation_context: ConversationContext,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ChatSession:
        """
        Get active session or create new one.
        Sessions are tied to conversation contexts (care_plan_modal, symptom_checkin, etc.)
        """
        try:
            # Check for existing active session with same context
            existing = self.db.query(ChatSession).filter(
                and_(
                    ChatSession.user_id == user_id,
                    ChatSession.conversation_context == conversation_context.value,
                    ChatSession.status == "active",
                    # Context-specific timeouts
                    # - Symptom Check-in: 1 hour (ephemeral)
                    # - Others (Know Body, Personalise): 24 hours (resume interaction)
                    ChatSession.last_message_at > datetime.utcnow() - (
                        timedelta(hours=1) if conversation_context == ConversationContext.SYMPTOM_CHECKIN 
                        else timedelta(hours=24)
                    )
                )
            ).order_by(desc(ChatSession.created_at)).first()
            
            if existing:
                return existing
            
            # Create new session
            session = ChatSession(
                user_id=user_id,
                conversation_context=conversation_context.value,
                status="active",
                current_step="0",
                current_flow_data=metadata or {},
                session_metadata=metadata or {},
                started_at=datetime.utcnow(),
                last_message_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            self.db.add(session)
            self.db.commit()
            self.db.refresh(session)
            
            logger.info(f"Created new chat session {session.id} for user {user_id}")
            return session
            
        except Exception as e:
            logger.error(f"Error creating session: {str(e)}")
            self.db.rollback()
            raise
    
    async def get_session_messages(
        self,
        session_id: str,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get messages from current session for context.
        Returns in format ready for LLM context.
        """
        messages = self.db.query(ChatMessage).filter(
            ChatMessage.session_id == session_id
        ).order_by(ChatMessage.created_at).limit(limit).all()
        
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "response_type": msg.response_type,
                "timestamp": msg.created_at.isoformat()
            }
            for msg in messages
        ]
    
    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        input_mode: Optional[str] = None,
        response_type: Optional[str] = "text",
        choices: Optional[List[str]] = None,
        slider_config: Optional[Dict] = None,
        tools_called: Optional[List[str]] = None,
        actions: Optional[List[Dict]] = None,
        metadata: Optional[Dict] = None
    ) -> ChatMessage:
        """
        Save a message to the session.
        """
        try:
            message = ChatMessage(
                session_id=session_id,
                role=role,
                content=content,
                input_mode=input_mode,
                response_type=response_type,
                choices=choices,
                slider_config=slider_config,
                tools_called=tools_called,
                actions=actions,
                message_metadata=metadata or {},
                created_at=datetime.utcnow()
            )
            self.db.add(message)
            self.db.commit()
            self.db.refresh(message)
            
            # Update session activity
            session = self.db.query(ChatSession).filter(
                ChatSession.id == session_id
            ).first()
            if session:
                session.updated_at = datetime.utcnow()
                session.last_message_at = datetime.utcnow()
                self.db.commit()
            
            return message
            
        except Exception as e:
            logger.error(f"Error saving message: {str(e)}")
            self.db.rollback()
            raise
    
    async def end_session(self, session_id: str, summary: Optional[str] = None):
        """
        End a session and optionally generate summary.
        """
        try:
            session = self.db.query(ChatSession).filter(
                ChatSession.id == session_id
            ).first()
            
            if session:
                session.status = "completed"
                session.ended_at = datetime.utcnow()
                
                # Generate summary if not provided
                if not summary:
                    summary = await self._generate_session_summary(session_id)
                
                session.summary = summary
                self.db.commit()
                
                logger.info(f"Ended session {session_id}")
                
        except Exception as e:
            logger.error(f"Error ending session: {str(e)}")
            self.db.rollback()
    
    # ═══════════════════════════════════════════════════════════════════════════
    # LAYER 2: Recent Memory (7-Day Summaries)
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def get_recent_memory(self, user_id: str, days: int = 7) -> Dict[str, Any]:
        """
        Get summarized memory from recent conversations.
        This is what the AI "remembers" from past sessions.
        """
        try:
            # Get recent conversation summaries
            start_date = (datetime.utcnow() - timedelta(days=days)).date()
            summaries = self.db.query(ConversationSummary).filter(
                and_(
                    ConversationSummary.user_id == user_id,
                    ConversationSummary.period_start >= start_date
                )
            ).order_by(desc(ConversationSummary.period_start)).limit(5).all()
            
            # Get recent session summaries directly
            recent_sessions = self.db.query(ChatSession).filter(
                and_(
                    ChatSession.user_id == user_id,
                    ChatSession.status == "completed",
                    ChatSession.ended_at >= datetime.utcnow() - timedelta(days=days)
                )
            ).order_by(desc(ChatSession.ended_at)).limit(10).all()
            
            return {
                "conversation_summaries": [
                    {
                        "period": f"{s.period_start} to {s.period_end}",
                        "summary": (s.summary_data or {}).get("summary"),
                        "topics": (s.summary_data or {}).get("topics_discussed") or (s.summary_data or {}).get("topics"),
                        "insights": (s.summary_data or {}).get("ai_insights") or (s.summary_data or {}).get("insights"),
                    }
                    for s in summaries
                ],
                "recent_sessions": [
                    {
                        "context": s.conversation_context,
                        "summary": s.summary,
                        "ended_at": s.ended_at.isoformat() if s.ended_at else None
                    }
                    for s in recent_sessions if s.summary
                ]
            }
            
        except Exception as e:
            logger.error(f"Error getting recent memory: {str(e)}")
            return {"conversation_summaries": [], "recent_sessions": []}
    
    async def create_weekly_summary(self, user_id: str) -> Optional[ConversationSummary]:
        """
        Create weekly summary of conversations.
        Should be run periodically (e.g., every Sunday).
        """
        try:
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=7)
            
            # Get all sessions from the week
            sessions = self.db.query(ChatSession).filter(
                and_(
                    ChatSession.user_id == user_id,
                    ChatSession.created_at >= start_date,
                    ChatSession.status == "completed"
                )
            ).all()
            
            if not sessions:
                return None
            
            # Gather all messages
            all_messages = []
            topics = set()
            
            for session in sessions:
                messages = self.db.query(ChatMessage).filter(
                    ChatMessage.session_id == session.id
                ).order_by(ChatMessage.created_at).all()
                
                topics.add(session.conversation_context)
                
                for msg in messages:
                    all_messages.append(f"{msg.role}: {msg.content}")
            
            # Generate summary using AI
            summary_prompt = f"""
            Summarize this week's health conversations with the user.
            Focus on:
            1. Main topics discussed
            2. Symptoms or concerns mentioned
            3. Actions taken (modifications to plan, etc.)
            4. User's mood/energy patterns
            5. Key insights for future conversations

            Messages:
            {chr(10).join(all_messages[-50:])}  # Last 50 messages

            Provide a concise summary (max 300 words) from a healthcare perspective.
            """
            
            # Generate summary - OpenAI primary, Groq fallback
            summary_text = None
            openai_error = None
            
            # Try OpenAI first
            if self.openai_client:
                try:
                    response = await self.openai_client.chat.completions.create(
                        model="gpt-5-mini",
                        messages=[
                            {"role": "system", "content": "You are a healthcare AI summarizing patient conversations."},
                            {"role": "user", "content": summary_prompt}
                        ],
                        max_completion_tokens=500,
                        temperature=0.7
                    )
                    summary_text = response.choices[0].message.content
                    logger.info("✅ Weekly summary generated via OpenAI")
                except Exception as e:
                    openai_error = str(e)
                    logger.warning(f"❌ OpenAI failed for weekly summary: {openai_error[:200]}")
            else:
                openai_error = "No OpenAI API key"
            
            # Groq fallback
            if openai_error and self.groq_client:
                try:
                    logger.info(f"🔄 Falling back to Groq ({GROQ_FALLBACK_MODEL})")
                    response = await self.groq_client.chat.completions.create(
                        model=GROQ_FALLBACK_MODEL,
                        messages=[
                            {"role": "system", "content": "You are a healthcare AI summarizing patient conversations."},
                            {"role": "user", "content": summary_prompt}
                        ],
                        max_tokens=500,
                        temperature=0.7
                    )
                    summary_text = response.choices[0].message.content
                    logger.info("✅ Weekly summary generated via Groq fallback")
                except Exception as e:
                    logger.error(f"❌ Groq fallback also failed: {e}")
                    raise Exception(f"Both OpenAI and Groq failed: {openai_error}")
            elif openai_error:
                raise Exception(f"OpenAI failed and no Groq fallback: {openai_error}")
            
            # Create summary record
            summary = ConversationSummary(
                user_id=user_id,
                period_start=start_date.date(),
                period_end=end_date.date(),
                summary_type="weekly",
                summary_data={
                    "summary": summary_text,
                    "topics_discussed": sorted(list(topics)),
                    "messages_count": len(all_messages),
                    "sessions_count": len(sessions),
                },
            )
            
            self.db.add(summary)
            self.db.commit()
            self.db.refresh(summary)
            
            logger.info(f"Created weekly summary for user {user_id}")
            return summary
            
        except Exception as e:
            logger.error(f"Error creating weekly summary: {str(e)}")
            self.db.rollback()
            return None
    
    # ═══════════════════════════════════════════════════════════════════════════
    # LAYER 3: Permanent Memory (User Profile Updates)
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def update_permanent_memory(
        self,
        user_id: str,
        memory_key: str,
        memory_value: Any
    ):
        """
        Update permanent user memory/preferences.
        These are facts that should be remembered forever.
        """
        try:
            profile = self.db.query(UserProfile).filter(
                UserProfile.uid == user_id
            ).first()
            
            if profile:
                # Get or create memory dict
                memory = profile.chatbot_memory or {}
                memory[memory_key] = {
                    "value": memory_value,
                    "updated_at": datetime.utcnow().isoformat()
                }
                profile.chatbot_memory = memory
                self.db.commit()
                
                logger.info(f"Updated permanent memory for {user_id}: {memory_key}")
                
        except Exception as e:
            logger.error(f"Error updating permanent memory: {str(e)}")
            self.db.rollback()
    
    async def get_permanent_memory(self, user_id: str) -> Dict[str, Any]:
        """
        Get all permanent memory/preferences for user.
        """
        try:
            profile = self.db.query(UserProfile).filter(
                UserProfile.uid == user_id
            ).first()
            
            return profile.chatbot_memory if profile and profile.chatbot_memory else {}
            
        except Exception as e:
            logger.error(f"Error getting permanent memory: {str(e)}")
            return {}
    
    # ═══════════════════════════════════════════════════════════════════════════
    # HELPER METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def _generate_session_summary(self, session_id: str) -> str:
        """
        Generate a summary of a session using AI.
        """
        try:
            messages = self.db.query(ChatMessage).filter(
                ChatMessage.session_id == session_id
            ).order_by(ChatMessage.created_at).all()
            
            if not messages:
                return "No messages in session"
            
            conversation = "\n".join([
                f"{msg.role}: {msg.content}" for msg in messages
            ])
            
            # Generate summary - OpenAI primary, Groq fallback
            summary = None
            openai_error = None
            
            # Try OpenAI first
            if self.openai_client:
                try:
                    response = await self.openai_client.chat.completions.create(
                        model="gpt-5-mini",
                        messages=[
                            {"role": "system", "content": "You are a healthcare AI. Summarize conversations concisely."},
                            {"role": "user", "content": f"Summarize this health conversation in 2-3 sentences:\n{conversation}"}
                        ],
                        max_completion_tokens=150,
                        temperature=0.7
                    )
                    summary = response.choices[0].message.content
                except Exception as e:
                    openai_error = str(e)
                    logger.warning(f"❌ OpenAI failed for session summary: {openai_error[:200]}")
            else:
                openai_error = "No OpenAI API key"
            
            # Groq fallback
            if openai_error and self.groq_client:
                try:
                    logger.info(f"🔄 Falling back to Groq ({GROQ_FALLBACK_MODEL})")
                    response = await self.groq_client.chat.completions.create(
                        model=GROQ_FALLBACK_MODEL,
                        messages=[
                            {"role": "system", "content": "You are a healthcare AI. Summarize conversations concisely."},
                            {"role": "user", "content": f"Summarize this health conversation in 2-3 sentences:\n{conversation}"}
                        ],
                        max_tokens=150,
                        temperature=0.7
                    )
                    summary = response.choices[0].message.content
                except Exception as e:
                    logger.error(f"❌ Groq fallback also failed: {e}")
                    return "Session completed"  # Graceful fallback
            elif openai_error:
                return "Session completed"  # Graceful fallback
            
            return summary
            
        except Exception as e:
            logger.error(f"Error generating session summary: {str(e)}")
            return "Session completed"
    
    async def get_full_memory_context(self, user_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get complete memory context for LangGraph agent.
        Combines all 3 layers of memory.
        """
        # Layer 1: Current session
        session_messages = []
        if session_id:
            session_messages = await self.get_session_messages(session_id)
        
        # Layer 2: Recent memory
        recent_memory = await self.get_recent_memory(user_id)
        
        # Layer 3: Permanent memory
        permanent_memory = await self.get_permanent_memory(user_id)
        
        return {
            "recent_messages": session_messages,  # Agent looks for this key in langgraph_agent.py
            "recent_memory": recent_memory,
            "permanent_memory": permanent_memory
        }
