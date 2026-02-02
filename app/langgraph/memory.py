"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA - Unified Memory System for All LangGraph Chatbots
═══════════════════════════════════════════════════════════════════════════════

This module provides CROSS-CHATBOT MEMORY so all 5 LangGraph chatbots can:
1. Know what user said in OTHER chatbot conversations
2. Remember feedback and preferences across sessions
3. Build a coherent mental model of the user

Based on LangGraph Memory Best Practices:
- Short-term memory: Thread-scoped (current conversation)
- Long-term memory: Cross-thread (stored in namespaces)
- Semantic memory: Facts about the user
- Episodic memory: Past experiences/conversations

USAGE IN LANGGRAPH NODES:
```python
from app.langgraph.memory import get_unified_context

async def my_node(state: MyState) -> MyState:
    context = await get_unified_context(state["user_id"])
    # Now you have access to ALL conversations, not just this one
    prompt = build_prompt_with_context(context)
    ...
```
"""

import logging
from datetime import datetime, timedelta, date
from typing import Dict, Any, Optional, List
from sqlalchemy import select, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.database import (
    SessionLocal,
    ChatSession,
    ChatMessage,
    ConversationSummary,
    UserProfile,
    UserResponse,
    ActionPlan,
    ActionPlanItem,
    SymptomLog,
    WeeklyCheckIn,
    ActionPlanDailyReview,
    CarePlanCheckInThread,
)
from app.utils.data_sanitization import sanitize_list_field, sanitize_string_field

logger = logging.getLogger(__name__)


class AuvraUnifiedMemory:
    """
    Unified memory system that provides context from ALL chatbots.
    
    Memory Layers:
    1. SEMANTIC (Facts): User profile, diagnosed conditions, preferences
    2. EPISODIC (Experiences): Past conversations, feedback, symptoms logged
    3. PROCEDURAL (Rules): What works for this user, what to avoid
    
    Cross-Chatbot Contexts:
    - care_plan_checkin: What actions user completed/skipped/changed today
    - symptom_checkin: What symptoms user reported recently
    - weekly_checkin: Insights from weekly reflections
    - personalization: User preferences learned over time
    - know_my_body: Educational interests and questions asked
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    async def get_full_context(
        self,
        user_id: str,
        current_chatbot: str = "unknown",
        max_recent_days: int = 7
    ) -> Dict[str, Any]:
        """
        Get complete cross-chatbot context for intelligent response generation.
        
        Returns:
        {
            "user_profile": {...},           # Semantic memory - who is this user
            "today": {...},                  # What happened today
            "recent_conversations": {...},   # Episodic memory - past 7 days
            "learned_preferences": {...},    # Procedural memory - what works
            "current_context": {...}         # Where we are now
        }
        """
        context = {
            "user_profile": await self._get_user_profile(user_id),
            "today": await self._get_today_context(user_id),
            "recent_conversations": await self._get_recent_conversations(user_id, max_recent_days),
            "learned_preferences": await self._get_learned_preferences(user_id),
            "current_context": {
                "chatbot": current_chatbot,
                "timestamp": datetime.utcnow().isoformat(),
                "date": date.today().isoformat(),
            }
        }
        
        return context
    
    async def _get_user_profile(self, user_id: str) -> Dict[str, Any]:
        """
        SEMANTIC MEMORY: Who is this user?
        Facts that should always be known.
        """
        try:
            # Get user profile
            profile = self.db.query(UserProfile).filter(
                UserProfile.uid == user_id
            ).first()
            
            # Get user responses (onboarding data)
            response = self.db.query(UserResponse).filter(
                UserResponse.uid == user_id
            ).order_by(desc(UserResponse.created_at)).first()
            
            if not response:
                return {"error": "No user data found"}
            
            return {
                # Core identity
                "age": response.age,
                "cycle_length": response.cycle_length,
                "period_description": response.period_description,
                
                # Health conditions - SANITIZED to remove UI placeholders like "None of the above"
                "diagnosed_conditions": sanitize_list_field(
                    response.diagnosed_conditions or [], "diagnosed_conditions"
                ),
                "top_concern": sanitize_string_field(response.top_concern, "top_concern"),
                "period_concerns": response.period_concerns or {},
                "body_concerns": response.body_concerns or {},
                "skin_hair_concerns": response.skin_hair_concerns or {},
                "mental_health_concerns": response.mental_health_concerns or {},
                "family_history": sanitize_list_field(
                    response.family_history or [], "family_history"
                ),
                
                # Hormones
                "primary_hormone": response.primary_hormone,
                "secondary_hormones": response.secondary_hormones or [],
                
                # Lifestyle
                "lifestyle_focus": response.lifestyle_focus or ["eat", "move", "pause"],
                "birth_control": response.birth_control or [],
                "workout_intensity": response.workout_intensity,
                "sleep_duration": response.sleep_duration,
                "stress_level": response.stress_level,
                
                # Preferences from chatbot memory
                "chatbot_memory": profile.chatbot_memory if profile else {},
                
                # Computed fields
                "profile_completeness": self._calculate_profile_completeness(profile, response),
            }
            
        except Exception as e:
            logger.error(f"Error getting user profile: {e}")
            return {"error": str(e)}
    
    async def _get_today_context(self, user_id: str) -> Dict[str, Any]:
        """
        What happened TODAY across all chatbots?
        """
        today = date.today()
        
        try:
            # Today's action plan items - need to join with ActionPlan to filter by plan_date
            # ActionPlanItem doesn't have plan_date directly, it's on ActionPlan
            action_items = self.db.query(ActionPlanItem).join(
                ActionPlan, ActionPlanItem.plan_id == ActionPlan.id
            ).filter(
                and_(
                    ActionPlan.uid == user_id,
                    ActionPlan.plan_date == today
                )
            ).all()

            # Optional: derive "skipped" from feedback (ActionPlanItem has no is_skipped column)
            skipped_item_ids = set()
            try:
                if action_items:
                    from app.core.database import ActionPlanFeedback

                    item_ids = [a.id for a in action_items if getattr(a, "id", None) is not None]
                    plan_ids = list({a.plan_id for a in action_items if getattr(a, "plan_id", None) is not None})

                    if item_ids and plan_ids:
                        skipped_feedback = (
                            self.db.query(ActionPlanFeedback)
                            .filter(
                                and_(
                                    ActionPlanFeedback.uid == user_id,
                                    ActionPlanFeedback.plan_id.in_(plan_ids),
                                    ActionPlanFeedback.item_id.in_(item_ids),
                                    ActionPlanFeedback.feedback_type == "skipped",
                                )
                            )
                            .all()
                        )
                        skipped_item_ids = {f.item_id for f in skipped_feedback if f.item_id is not None}
            except Exception as e:
                # Don't fail unified memory if feedback query fails
                logger.warning(f"Today context: could not load skipped feedback: {e}")
            
            # Today's symptom logs
            symptoms = self.db.query(SymptomLog).filter(
                and_(
                    SymptomLog.user_id == user_id,
                    SymptomLog.logged_date == today
                )
            ).all()
            
            # Today's care plan check-in thread
            care_plan_thread = self.db.query(CarePlanCheckInThread).filter(
                and_(
                    CarePlanCheckInThread.uid == user_id,
                    CarePlanCheckInThread.local_date == today  # Fixed: was plan_date, should be local_date
                )
            ).first()
            
            return {
                "date": today.isoformat(),
                
                # Action plan status
                "action_plan": {
                    "total_actions": len(action_items),
                    "completed": len([a for a in action_items if bool(getattr(a, "is_completed", False))]),
                    "skipped": len([a for a in action_items if getattr(a, "id", None) in skipped_item_ids]),
                    "actions": [
                        {
                            "title": a.title,
                            "category": a.category,
                            "is_completed": bool(getattr(a, "is_completed", False)),
                            # Backward-compatible key used by prompt formatter
                            "is_skipped": getattr(a, "id", None) in skipped_item_ids,
                            # ActionPlanItem uses replacement_reason/is_replaced (not skip_reason/was_replaced)
                            "is_replaced": bool(getattr(a, "is_replaced", False)),
                            "was_replaced": bool(getattr(a, "is_replaced", False)),
                            "replacement_reason": getattr(a, "replacement_reason", None),
                        }
                        for a in action_items
                    ]
                },
                
                # Symptoms logged today
                "symptoms": [
                    {
                        "name": s.symptom_name,
                        "severity": s.severity,
                        "notes": s.notes,
                    }
                    for s in symptoms
                ],
                
                # Care plan conversation
                "care_plan_conversation": {
                    "has_thread": care_plan_thread is not None,
                    # raw_messages is a JSONB array; len() gives message count
                    "message_count": len(care_plan_thread.raw_messages or []) if care_plan_thread else 0,
                    # rolling_summary is the correct attribute (not 'summary')
                    "last_summary": care_plan_thread.rolling_summary if care_plan_thread else None,
                } if care_plan_thread else None,
            }
            
        except Exception as e:
            logger.error(f"Error getting today context: {e}")
            return {"error": str(e)}
    
    async def _get_recent_conversations(
        self, 
        user_id: str, 
        days: int = 7
    ) -> Dict[str, Any]:
        """
        EPISODIC MEMORY: What happened in recent conversations?
        Organized by chatbot type.
        """
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        try:
            # Get recent completed chat sessions
            sessions = self.db.query(ChatSession).filter(
                and_(
                    ChatSession.user_id == user_id,
                    ChatSession.status == "completed",
                    ChatSession.ended_at >= cutoff
                )
            ).order_by(desc(ChatSession.ended_at)).limit(20).all()
            
            # Organize by chatbot type
            by_chatbot = {
                "symptom_checkin": [],
                "weekly_checkin": [],
                "care_plan_modal": [],
                "know_my_body": [],
                "personalise_profile": [],
            }
            
            for session in sessions:
                context = session.conversation_context
                if context in by_chatbot:
                    by_chatbot[context].append({
                        "date": session.ended_at.date().isoformat() if session.ended_at else None,
                        "summary": session.summary,
                    })
            
            # Get recent weekly check-ins
            weekly_checkins = self.db.query(WeeklyCheckIn).filter(
                and_(
                    WeeklyCheckIn.uid == user_id,
                    WeeklyCheckIn.is_complete == True,
                    WeeklyCheckIn.completed_at >= cutoff
                )
            ).order_by(desc(WeeklyCheckIn.completed_at)).limit(4).all()
            
            # Get recent daily reviews
            daily_reviews = self.db.query(ActionPlanDailyReview).filter(
                and_(
                    ActionPlanDailyReview.uid == user_id,
                    ActionPlanDailyReview.review_date >= cutoff.date()
                )
            ).order_by(desc(ActionPlanDailyReview.review_date)).limit(7).all()
            
            return {
                "chat_sessions": by_chatbot,
                
                "weekly_checkins": [
                    {
                        "date": wc.completed_at.date().isoformat() if wc.completed_at else None,
                        "summary": wc.summary if hasattr(wc, 'summary') else wc.conversation_summary,
                        "insights": wc.insights if hasattr(wc, 'insights') else None,
                        # Include the rich actionable data
                        "top_concern": wc.top_concern,
                        "concern_severity": wc.concern_severity,
                        "overall_wellbeing": wc.overall_wellbeing,
                        "factors_positive": wc.factors_positive or [],  # What helped
                        "factors_negative": wc.factors_negative or [],  # What made things worse
                        "action_reflections": wc.action_reflections or {},  # What worked/didn't work
                        "actionable_insights": wc.actionable_insights or {},  # AI-extracted insights
                    }
                    for wc in weekly_checkins
                ],
                
                "daily_reviews": [
                    {
                        "date": dr.review_date.isoformat() if dr.review_date else None,
                        "items_marked_complete": dr.items_marked_complete,
                        "items_replaced": dr.items_replaced,
                        "items_skipped": dr.items_skipped,
                        "streak_action": dr.streak_action,
                        # Include actual review data for personalization
                        "items_review_data": dr.items_review_data or [],
                    }
                    for dr in daily_reviews
                ],
            }
            
        except Exception as e:
            logger.error(f"Error getting recent conversations: {e}")
            return {"error": str(e)}
    
    async def _get_learned_preferences(self, user_id: str) -> Dict[str, Any]:
        """
        PROCEDURAL MEMORY: What have we learned works for this user?
        Based on feedback patterns and conversation history.
        """
        try:
            profile = self.db.query(UserProfile).filter(
                UserProfile.uid == user_id
            ).first()
            
            chatbot_memory = profile.chatbot_memory if profile and profile.chatbot_memory else {}
            
            # Safely extract values from chatbot_memory with proper None handling
            def safe_get(key: str, default=None):
                """Safely get nested value from chatbot_memory."""
                val = chatbot_memory.get(key)
                if val is None:
                    return default
                if isinstance(val, dict):
                    return val.get("value", default)
                return val
            
            # Extract key preferences from chatbot memory
            return {
                # Diet preferences (from personalization)
                "diet_preference": safe_get("diet_preference"),
                "food_allergies": safe_get("food_allergies"),
                "cuisine_preference": safe_get("cuisine_preference"),
                
                # Communication preferences
                "preferred_communication_style": safe_get("communication_style"),
                
                # Educational interests (from know_my_body)
                "educational_interests": safe_get("educational_interests", []),
                
                # What works/doesn't work (learned over time)
                "foods_liked": safe_get("foods_liked", []),
                "foods_disliked": safe_get("foods_disliked", []),
                "activities_liked": safe_get("activities_liked", []),
                "activities_disliked": safe_get("activities_disliked", []),
                
                # Barriers identified
                "common_barriers": safe_get("common_barriers", []),
            }
            
        except Exception as e:
            logger.error(f"Error getting learned preferences: {e}")
            return {"error": str(e)}
    
    def _calculate_profile_completeness(
        self, 
        profile: Optional[UserProfile], 
        response: Optional[UserResponse]
    ) -> float:
        """Calculate how complete the user's profile is (0-100%)."""
        if not response:
            return 0.0
        
        fields = [
            response.age is not None,
            response.top_concern is not None,
            response.diagnosed_conditions is not None and len(response.diagnosed_conditions) > 0,
            response.period_concerns is not None,
            response.lifestyle_focus is not None,
            response.workout_intensity is not None,
            response.sleep_duration is not None,
            response.stress_level is not None,
        ]
        
        # Add chatbot memory fields
        if profile and profile.chatbot_memory:
            memory = profile.chatbot_memory
            fields.extend([
                "diet_preference" in memory,
                "food_allergies" in memory,
                "cuisine_preference" in memory,
            ])
        
        return (sum(fields) / len(fields)) * 100 if fields else 0.0


def format_context_for_prompt(context: Dict[str, Any]) -> str:
    """
    Format the unified context into a structured string for LLM prompts.
    
    This creates a comprehensive but readable summary that the LLM can use
    to generate personalized, context-aware responses.
    """
    lines = []
    
    # User Profile Section
    profile = context.get("user_profile", {})
    if profile and "error" not in profile:
        lines.append("═══ USER PROFILE ═══")
        lines.append(f"• Age: {profile.get('age', 'unknown')}")
        lines.append(f"• Top Concern: {profile.get('top_concern', 'general wellness')}")
        
        conditions = profile.get('diagnosed_conditions', [])
        if conditions:
            lines.append(f"• Diagnosed Conditions: {', '.join(conditions)}")
        
        lines.append(f"• Primary Hormone: {profile.get('primary_hormone', 'unknown')}")
        lines.append(f"• Stress Level: {profile.get('stress_level', 'unknown')}")
        lines.append(f"• Workout Intensity: {profile.get('workout_intensity', 'unknown')}")
        lines.append("")
    
    # Today Section
    today = context.get("today", {})
    if today and "error" not in today:
        lines.append("═══ TODAY ═══")
        
        plan = today.get("action_plan", {})
        if plan:
            completed = plan.get('completed', 0)
            total = plan.get('total_actions', 0)
            lines.append(f"• Action Plan: {completed}/{total} completed")
            
            actions = plan.get('actions', [])
            for a in actions:
                status = "✓" if a.get('is_completed') else ("⏭" if a.get('is_skipped') else "○")
                lines.append(f"  {status} {a.get('title')}")
        
        symptoms = today.get("symptoms", [])
        if symptoms:
            lines.append(f"• Symptoms Logged Today:")
            for s in symptoms:
                lines.append(f"  - {s.get('name')} (severity: {s.get('severity')})")
        
        lines.append("")
    
    # Recent Conversations Section
    recent = context.get("recent_conversations", {})
    if recent and "error" not in recent:
        lines.append("═══ RECENT CONTEXT (Past 7 Days) ═══")
        
        # Symptom check-in summaries
        symptom_sessions = recent.get("chat_sessions", {}).get("symptom_checkin", [])
        if symptom_sessions:
            lines.append("• Recent Symptom Check-ins:")
            for s in symptom_sessions[:3]:
                if s.get('summary'):
                    lines.append(f"  [{s.get('date')}]: {s.get('summary')[:100]}...")
        
        # Weekly check-in insights
        weekly = recent.get("weekly_checkins", [])
        if weekly:
            lines.append("• Weekly Check-in Insights:")
            for w in weekly[:2]:
                date_str = w.get('date', 'Unknown')
                
                # Show the summary if available
                if w.get('summary'):
                    lines.append(f"  [{date_str}]: {w.get('summary')[:100]}...")
                
                # Show concern severity
                if w.get('top_concern') and w.get('concern_severity'):
                    severity = w.get('concern_severity')
                    level = "mild" if severity <= 3 else ("moderate" if severity <= 6 else "severe")
                    lines.append(f"    {w.get('top_concern')}: {level} ({severity}/9)")
                
                # Show what helped/hurt
                positive = w.get('factors_positive', [])
                if positive:
                    lines.append(f"    ✓ Helped: {', '.join(positive[:3])}")
                negative = w.get('factors_negative', [])
                if negative:
                    lines.append(f"    ✗ Worsened: {', '.join(negative[:3])}")
                
                # Show action reflections
                reflections = w.get('action_reflections', {})
                if reflections.get('worked_well'):
                    lines.append(f"    → Actions that worked: {', '.join(reflections['worked_well'][:3])}")
                if reflections.get('didnt_work'):
                    lines.append(f"    → Actions that didn't work: {', '.join(reflections['didnt_work'][:2])}")
                
                # Show actionable insights
                insights = w.get('actionable_insights', {})
                if insights.get('triggers_identified'):
                    lines.append(f"    ⚡ Triggers: {', '.join(insights['triggers_identified'][:3])}")
                if insights.get('relief_factors_identified'):
                    lines.append(f"    💚 Relief: {', '.join(insights['relief_factors_identified'][:3])}")
        
        # Daily review feedback
        reviews = recent.get("daily_reviews", [])
        if reviews:
            lines.append("• Recent Plan Feedback:")
            for r in reviews[:3]:
                date_str = r.get('date', 'Unknown date')
                completed = r.get('items_marked_complete', 0)
                skipped = r.get('items_skipped', 0)
                replaced = r.get('items_replaced', 0)
                streak = r.get('streak_action', '')
                
                # Summarize the review
                summary_parts = []
                if completed > 0:
                    summary_parts.append(f"{completed} completed")
                if skipped > 0:
                    summary_parts.append(f"{skipped} skipped")
                if replaced > 0:
                    summary_parts.append(f"{replaced} replaced")
                if streak:
                    summary_parts.append(f"streak: {streak}")
                
                if summary_parts:
                    lines.append(f"  [{date_str}]: {', '.join(summary_parts)}")
                
                # Include replacement details if any (what did user swap to?)
                review_data = r.get('items_review_data', [])
                for item in review_data[:2]:  # Show first 2 replacements
                    if item.get('status') == 'replaced' and item.get('replacement_text'):
                        lines.append(f"    → Replaced with: {item.get('replacement_text')}")
        
        lines.append("")
    
    # Learned Preferences Section
    prefs = context.get("learned_preferences", {})
    if prefs and "error" not in prefs:
        has_prefs = any(v for v in prefs.values() if v)
        if has_prefs:
            lines.append("═══ LEARNED PREFERENCES ═══")
            
            # Diet preferences
            if prefs.get('diet_preference'):
                lines.append(f"• Diet: {prefs.get('diet_preference')}")
            if prefs.get('food_allergies'):
                lines.append(f"• Allergies: {prefs.get('food_allergies')}")
            if prefs.get('cuisine_preference'):
                cuisine = prefs.get('cuisine_preference')
                if isinstance(cuisine, list):
                    lines.append(f"• Preferred Cuisines: {', '.join(cuisine)}")
                else:
                    lines.append(f"• Preferred Cuisine: {cuisine}")
            
            # Food likes/dislikes (from feedback history)
            if prefs.get('foods_liked'):
                lines.append(f"• Foods Liked: {', '.join(prefs.get('foods_liked', []))}")
            if prefs.get('foods_disliked'):
                lines.append(f"• Foods Disliked: {', '.join(prefs.get('foods_disliked', []))}")
            
            # Activity likes/dislikes (from feedback history)
            if prefs.get('activities_liked'):
                lines.append(f"• Activities Liked: {', '.join(prefs.get('activities_liked', []))}")
            if prefs.get('activities_disliked'):
                lines.append(f"• Activities Disliked: {', '.join(prefs.get('activities_disliked', []))}")
            
            # Barriers and challenges
            if prefs.get('common_barriers'):
                lines.append(f"• Common Barriers: {', '.join(prefs.get('common_barriers', []))}")
            
            # Educational interests (from know_my_body chatbot)
            if prefs.get('educational_interests'):
                interests = prefs.get('educational_interests', [])
                if interests:
                    lines.append(f"• Educational Interests: {', '.join(interests)}")
            
            # Communication style preference
            if prefs.get('preferred_communication_style'):
                lines.append(f"• Preferred Communication Style: {prefs.get('preferred_communication_style')}")
            
            lines.append("")
    
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS FOR LANGGRAPH NODES
# ═══════════════════════════════════════════════════════════════════════════════

async def get_unified_context(
    user_id: str,
    current_chatbot: str = "unknown",
    db: Optional[Session] = None
) -> Dict[str, Any]:
    """
    Get unified cross-chatbot context for use in LangGraph nodes.
    
    Usage in a LangGraph node:
    ```python
    from app.langgraph.memory import get_unified_context, format_context_for_prompt
    
    async def generate_response(state: MyState) -> MyState:
        context = await get_unified_context(state["user_id"], "care_plan_checkin")
        context_str = format_context_for_prompt(context)
        
        prompt = f'''
        {context_str}
        
        User message: {state["user_message"]}
        
        Generate a personalized response...
        '''
        response = await call_llm(prompt)
        ...
    ```
    """
    if db is None:
        db = SessionLocal()
        should_close = True
    else:
        should_close = False
    
    try:
        memory = AuvraUnifiedMemory(db)
        return await memory.get_full_context(user_id, current_chatbot)
    finally:
        if should_close:
            db.close()


async def get_formatted_context(
    user_id: str,
    current_chatbot: str = "unknown",
    db: Optional[Session] = None
) -> str:
    """
    Get pre-formatted context string ready to inject into prompts.
    """
    context = await get_unified_context(user_id, current_chatbot, db)
    return format_context_for_prompt(context)
