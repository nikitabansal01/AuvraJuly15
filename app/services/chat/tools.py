"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA CHATBOT - Tools Registry
═══════════════════════════════════════════════════════════════════════════════
All tools available to the LangGraph agent.
These are the "actions" the AI doctor can take.

Categories:
1. Action Plan Tools - Modify, skip, reschedule assignments
2. Symptom Tools - Log symptoms, get trends
3. Context Tools - Get user profile, today's plan
4. Education Tools - RAG for health questions
5. Safety Tools - Check for emergency, add disclaimers
6. Proactive Tools - Triggers for engagement
"""

from typing import Any, Dict, List, Optional
from langchain_core.tools import tool
from datetime import date, datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. ACTION PLAN TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
async def get_current_assignments(user_id: str, db_session: Any) -> Dict[str, Any]:
    """
    Get all assignments for today with their details.
    Use this when user asks about their action plan or today's tasks.
    
    Args:
        user_id: The user's ID
        db_session: Database session
        
    Returns:
        Dict with morning, afternoon, evening, anytime assignments
    """
    from app.services.chat.user_context_service import UserContextService
    
    context_service = UserContextService(db_session)
    todays_plan = await context_service.get_todays_plan(user_id)
    
    return {
        "date": str(todays_plan.date),
        "total": todays_plan.total_assignments,
        "completed": todays_plan.completed_assignments,
        "completion_rate": round(todays_plan.completion_rate * 100, 1),
        "morning": todays_plan.morning,
        "afternoon": todays_plan.afternoon,
        "evening": todays_plan.evening,
        "anytime": todays_plan.anytime,
        "hormone_progress": todays_plan.hormone_stats
    }


@tool
async def complete_assignment(
    user_id: str,
    assignment_id: int,
    db_session: Any,
    notes: Optional[str] = None
) -> Dict[str, Any]:
    """
    Mark an assignment as completed.
    Use when user says they've done something from their plan.
    
    Args:
        user_id: The user's ID
        assignment_id: ID of the assignment to complete
        db_session: Database session
        notes: Optional notes about completion
        
    Returns:
        Completion confirmation with updated stats
    """
    from app.core.database import DailyAssignment, RecommendationCompletion, RecommendationRecord
    from sqlalchemy import and_
    
    assignment = db_session.query(DailyAssignment).filter(
        and_(
            DailyAssignment.id == assignment_id,
            DailyAssignment.uid == user_id
        )
    ).first()
    
    if not assignment:
        return {"success": False, "error": "Assignment not found"}
    
    if assignment.is_completed:
        return {"success": True, "message": "Already completed", "already_done": True}
    
    # Mark as completed
    assignment.is_completed = True
    assignment.completed_at = datetime.utcnow()
    
    # Create completion record
    completion = RecommendationCompletion(
        uid=user_id,
        recommendation_id=assignment.recommendation_id,
        completion_date=date.today(),
        completed_at=datetime.utcnow(),
        notes=notes
    )
    db_session.add(completion)
    db_session.commit()
    
    # Get recommendation details for response
    rec = db_session.query(RecommendationRecord).filter(
        RecommendationRecord.id == assignment.recommendation_id
    ).first()
    
    return {
        "success": True,
        "message": f"Great job completing '{rec.title}' 🎉",
        "assignment_id": assignment_id,
        "title": rec.title if rec else "Task",
        "hormones_benefited": rec.hormones if rec else [],
        "category": rec.category if rec else None
    }


@tool
async def skip_assignment(
    user_id: str,
    assignment_id: int,
    reason: str,
    db_session: Any
) -> Dict[str, Any]:
    """
    Skip an assignment with a reason.
    Use when user says they can't or don't want to do something.
    
    Args:
        user_id: The user's ID
        assignment_id: ID of the assignment to skip
        reason: Why the user is skipping
        db_session: Database session
        
    Returns:
        Skip confirmation
    """
    from app.core.database import DailyAssignment, AssignmentSkipLog, RecommendationRecord
    from sqlalchemy import and_
    
    assignment = db_session.query(DailyAssignment).filter(
        and_(
            DailyAssignment.id == assignment_id,
            DailyAssignment.uid == user_id
        )
    ).first()
    
    if not assignment:
        return {"success": False, "error": "Assignment not found"}
    
    # Log the skip
    skip_log = AssignmentSkipLog(
        user_id=user_id,
        assignment_id=assignment_id,
        recommendation_id=assignment.recommendation_id,
        skip_reason=reason,
        skip_date=date.today(),
        skipped_at=datetime.utcnow()
    )
    db_session.add(skip_log)
    db_session.commit()
    
    # Get recommendation for response
    rec = db_session.query(RecommendationRecord).filter(
        RecommendationRecord.id == assignment.recommendation_id
    ).first()
    
    return {
        "success": True,
        "message": f"No problem, I've skipped '{rec.title if rec else 'the task'}' for today.",
        "assignment_id": assignment_id,
        "reason_logged": reason,
        "can_suggest_alternative": True
    }


@tool
async def reschedule_assignment(
    user_id: str,
    assignment_id: int,
    new_time_slot: str,
    db_session: Any
) -> Dict[str, Any]:
    """
    Reschedule an assignment to a different time slot.
    Use when user wants to move a task to a different time.
    
    Args:
        user_id: The user's ID
        assignment_id: ID of the assignment
        new_time_slot: New time slot (morning, afternoon, evening, night)
        db_session: Database session
        
    Returns:
        Reschedule confirmation
    """
    from app.core.database import DailyAssignment, RecommendationRecord
    from sqlalchemy import and_
    
    valid_slots = ["morning", "afternoon", "evening", "night"]
    if new_time_slot not in valid_slots:
        return {"success": False, "error": f"Invalid time slot. Use: {valid_slots}"}
    
    assignment = db_session.query(DailyAssignment).filter(
        and_(
            DailyAssignment.id == assignment_id,
            DailyAssignment.uid == user_id
        )
    ).first()
    
    if not assignment:
        return {"success": False, "error": "Assignment not found"}
    
    old_slot = assignment.time_group
    assignment.time_group = new_time_slot
    db_session.commit()
    
    rec = db_session.query(RecommendationRecord).filter(
        RecommendationRecord.id == assignment.recommendation_id
    ).first()
    
    return {
        "success": True,
        "message": f"Moved '{rec.title if rec else 'task'}' from {old_slot} to {new_time_slot}",
        "assignment_id": assignment_id,
        "old_time_slot": old_slot,
        "new_time_slot": new_time_slot
    }


@tool
async def suggest_alternative_assignment(
    user_id: str,
    original_assignment_id: int,
    reason: str,
    db_session: Any
) -> Dict[str, Any]:
    """
    Suggest an alternative when user skips an assignment.
    Finds similar recommendations for the same hormones/symptoms.
    
    Args:
        user_id: The user's ID
        original_assignment_id: ID of the skipped assignment
        reason: Why original was skipped (helps find better alternative)
        db_session: Database session
        
    Returns:
        Alternative recommendation suggestions
    """
    from app.core.database import DailyAssignment, RecommendationRecord
    from sqlalchemy import and_, or_
    
    # Get original assignment
    original = db_session.query(DailyAssignment).filter(
        DailyAssignment.id == original_assignment_id
    ).first()
    
    if not original:
        return {"success": False, "error": "Original assignment not found"}
    
    original_rec = db_session.query(RecommendationRecord).filter(
        RecommendationRecord.id == original.recommendation_id
    ).first()
    
    if not original_rec:
        return {"success": False, "error": "Original recommendation not found"}
    
    # Find alternatives with same hormones but different category/approach
    alternatives = db_session.query(RecommendationRecord).filter(
        and_(
            RecommendationRecord.id != original_rec.id,
            RecommendationRecord.category == original_rec.category,
            # Match at least one hormone
            or_(*[
                RecommendationRecord.hormones.contains([h])
                for h in (original_rec.hormones or [])
            ]) if original_rec.hormones else True
        )
    ).limit(3).all()
    
    return {
        "success": True,
        "original_title": original_rec.title,
        "alternatives": [
            {
                "id": alt.id,
                "title": alt.title,
                "purpose": alt.purpose,
                "category": alt.category,
                "hormones": alt.hormones
            }
            for alt in alternatives
        ],
        "message": "Here are some alternatives that work on the same hormones"
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SYMPTOM TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
async def log_symptom(
    user_id: str,
    symptom_type: str,
    severity: int,
    db_session: Any,
    notes: Optional[str] = None,
    factors: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Log a symptom with severity and optional factors.
    Use when user reports any symptom or feeling.
    
    Args:
        user_id: The user's ID
        symptom_type: Type of symptom (bloating, cramps, fatigue, mood, etc.)
        severity: Severity 1-9 (1=minimal, 9=severe)
        db_session: Database session
        notes: Optional notes
        factors: Optional contributing factors
        
    Returns:
        Logging confirmation with context
    """
    from app.core.database import SymptomLog, UserResponse
    from app.services.cycle_service import CycleService
    
    # Get cycle info for context
    cycle_service = CycleService(db_session)
    cycle_info = cycle_service.get_cycle_phase_info(user_id)
    
    # Create symptom log
    symptom_log = SymptomLog(
        user_id=user_id,
        symptom_type=symptom_type,
        severity=severity,
        notes=notes,
        factors=factors or [],
        cycle_day=cycle_info.cycle_day if cycle_info else None,
        phase=cycle_info.phase if cycle_info else None,
        logged_date=date.today(),
        logged_at=datetime.utcnow()
    )
    db_session.add(symptom_log)
    db_session.commit()
    
    return {
        "success": True,
        "message": f"Logged {symptom_type} (severity {severity}/9)",
        "symptom_type": symptom_type,
        "severity": severity,
        "cycle_day": cycle_info.cycle_day if cycle_info else None,
        "phase": cycle_info.phase if cycle_info else None,
        "phase_context": _get_symptom_phase_context(symptom_type, cycle_info.phase if cycle_info else None)
    }


@tool
async def get_symptom_trends(
    user_id: str,
    symptom_type: str,
    days: int,
    db_session: Any
) -> Dict[str, Any]:
    """
    Get symptom trends over time.
    Use when discussing patterns or trends with user.
    
    Args:
        user_id: The user's ID
        symptom_type: Type of symptom to analyze
        days: Number of days to look back
        db_session: Database session
        
    Returns:
        Trend analysis with averages and patterns
    """
    from app.core.database import SymptomLog
    from sqlalchemy import and_
    
    start_date = date.today() - timedelta(days=days)
    
    logs = db_session.query(SymptomLog).filter(
        and_(
            SymptomLog.user_id == user_id,
            SymptomLog.symptom_type == symptom_type,
            SymptomLog.logged_date >= start_date
        )
    ).order_by(SymptomLog.logged_date).all()
    
    if not logs:
        return {
            "success": True,
            "has_data": False,
            "message": f"No {symptom_type} logs in the past {days} days"
        }
    
    # Calculate stats
    severities = [log.severity for log in logs]
    avg_severity = sum(severities) / len(severities)
    
    # Phase correlation
    phase_severities = {}
    for log in logs:
        if log.phase:
            if log.phase not in phase_severities:
                phase_severities[log.phase] = []
            phase_severities[log.phase].append(log.severity)
    
    phase_averages = {
        phase: sum(sevs) / len(sevs)
        for phase, sevs in phase_severities.items()
    }
    
    # Find worst phase
    worst_phase = max(phase_averages.items(), key=lambda x: x[1])[0] if phase_averages else None
    
    # Trend calculation
    if len(severities) >= 2:
        first_half = severities[:len(severities)//2]
        second_half = severities[len(severities)//2:]
        trend = "improving" if sum(second_half)/len(second_half) < sum(first_half)/len(first_half) else "worsening"
    else:
        trend = "stable"
    
    return {
        "success": True,
        "has_data": True,
        "symptom_type": symptom_type,
        "days_analyzed": days,
        "total_logs": len(logs),
        "average_severity": round(avg_severity, 1),
        "trend": trend,
        "phase_averages": phase_averages,
        "worst_phase": worst_phase,
        "common_factors": _get_common_factors(logs)
    }


def _get_symptom_phase_context(symptom_type: str, phase: Optional[str]) -> Optional[str]:
    """Get contextual information about symptom in relation to phase."""
    if not phase:
        return None
    
    contexts = {
        "menstrual": {
            "cramps": "Cramps are common during menstruation due to uterine contractions.",
            "bloating": "Water retention during menstruation can cause bloating.",
            "fatigue": "Low iron and hormone shifts can cause fatigue during your period."
        },
        "follicular": {
            "energy": "Rising estrogen typically brings higher energy in this phase.",
            "mood": "You may feel more optimistic as estrogen rises."
        },
        "ovulation": {
            "pain": "Mittelschmerz (ovulation pain) is common and normal.",
            "libido": "Peak fertility often comes with increased desire."
        },
        "luteal": {
            "bloating": "Progesterone can cause water retention before your period.",
            "mood": "PMS symptoms are common as progesterone and estrogen fluctuate.",
            "cravings": "Craving carbs is linked to serotonin changes in luteal phase."
        }
    }
    
    return contexts.get(phase, {}).get(symptom_type)


def _get_common_factors(logs: List) -> List[str]:
    """Extract common factors from symptom logs."""
    from collections import Counter
    all_factors = []
    for log in logs:
        all_factors.extend(log.factors or [])
    
    if not all_factors:
        return []
    
    counter = Counter(all_factors)
    return [f for f, _ in counter.most_common(3)]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CONTEXT TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
async def get_patient_profile(user_id: str, db_session: Any) -> Dict[str, Any]:
    """
    Get complete patient profile for context.
    Use to understand user's health background, concerns, and cycle info.
    
    Args:
        user_id: The user's ID
        db_session: Database session
        
    Returns:
        Complete patient profile
    """
    from app.services.chat.user_context_service import UserContextService
    
    context_service = UserContextService(db_session)
    profile = await context_service.get_patient_profile(user_id)
    
    return profile.model_dump()


@tool
async def get_cycle_info(user_id: str, db_session: Any) -> Dict[str, Any]:
    """
    Get current cycle information.
    Use when discussing cycle-related topics.
    
    Args:
        user_id: The user's ID
        db_session: Database session
        
    Returns:
        Cycle day, phase, and relevant information
    """
    from app.services.cycle_service import CycleService
    
    cycle_service = CycleService(db_session)
    cycle_info = cycle_service.get_cycle_phase_info(user_id)
    
    if not cycle_info or not cycle_info.cycle_day:
        return {"success": False, "error": "No cycle information available"}
    
    phase = cycle_info.phase or "unknown"
    return {
        "success": True,
        "cycle_day": cycle_info.cycle_day,
        "phase": phase,
        "user_name": cycle_info.user_name,
        "phase_recommendations": _get_phase_recommendations(phase.lower().replace(" phase", ""))
    }


def _get_phase_recommendations(phase: str) -> List[str]:
    """Get phase-specific wellness recommendations."""
    recommendations = {
        "menstrual": [
            "Prioritize rest and gentle movement",
            "Iron-rich foods can help replenish",
            "Warm compresses for cramp relief"
        ],
        "follicular": [
            "Great time for trying new things",
            "Higher energy supports strength training",
            "Social activities feel more natural"
        ],
        "ovulation": [
            "Peak energy - ideal for challenging workouts",
            "Communication skills are heightened",
            "Protein-rich foods support energy"
        ],
        "luteal": [
            "Honor the need for more rest",
            "Complex carbs can help with cravings",
            "Self-care and stress management are key"
        ]
    }
    return recommendations.get(phase, [])


# ═══════════════════════════════════════════════════════════════════════════════
# 4. EDUCATION TOOLS (RAG)
# ═══════════════════════════════════════════════════════════════════════════════

@tool
async def search_health_knowledge(
    query: str,
    user_id: str,
    db_session: Any,
    top_k: int = 5
) -> Dict[str, Any]:
    """
    Search health knowledge base using RAG.
    Use when user asks educational questions about health topics.
    
    Args:
        query: The health question
        user_id: The user's ID
        db_session: Database session
        top_k: Number of results to return
        
    Returns:
        Relevant health information from knowledge base
    """
    from pinecone import Pinecone
    from openai import OpenAI
    import os
    
    try:
        # Initialize clients
        pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
        openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        # Get embedding for query
        embedding_response = openai_client.embeddings.create(
            model="text-embedding-ada-002",
            input=query
        )
        query_embedding = embedding_response.data[0].embedding
        
        # Search Pinecone
        index = pc.Index("auvra-papers")
        results = index.query(
            vector=query_embedding,
            top_k=top_k,
            include_metadata=True,
            namespace="combined"  # Use combined namespace
        )
        
        # Format results
        formatted_results = []
        for match in results.matches:
            formatted_results.append({
                "score": match.score,
                "title": match.metadata.get("title", ""),
                "content": match.metadata.get("text", match.metadata.get("content", "")),
                "source": match.metadata.get("source", "")
            })
        
        return {
            "success": True,
            "query": query,
            "results": formatted_results,
            "disclaimer": "This information is educational and not a substitute for medical advice."
        }
        
    except Exception as e:
        logger.error(f"Health knowledge search error: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "fallback": "I can share general health information, but for specific concerns, please consult a healthcare provider."
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SAFETY TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def check_emergency_keywords(message: str) -> Dict[str, Any]:
    """
    Check if message contains emergency keywords.
    ALWAYS run this on user messages before processing.
    
    Args:
        message: The user's message
        
    Returns:
        Safety check result
    """
    emergency_keywords = [
        "suicide", "kill myself", "end my life", "want to die",
        "severe bleeding", "hemorrhage", "can't breathe",
        "chest pain", "heart attack", "stroke symptoms",
        "overdose", "poisoning"
    ]
    
    urgent_keywords = [
        "emergency", "hospital", "911", "ambulance",
        "severe pain", "passing out", "fainted",
        "very dizzy", "heavy bleeding"
    ]
    
    message_lower = message.lower()
    
    # Check for emergency
    for keyword in emergency_keywords:
        if keyword in message_lower:
            return {
                "is_emergency": True,
                "is_urgent": True,
                "matched_keyword": keyword,
                "action": "immediate_redirect",
                "message": "If you're having a medical emergency, please call 911 or your local emergency services immediately. You can also text HOME to 741741 (Crisis Text Line) if you need support."
            }
    
    # Check for urgent
    for keyword in urgent_keywords:
        if keyword in message_lower:
            return {
                "is_emergency": False,
                "is_urgent": True,
                "matched_keyword": keyword,
                "action": "add_disclaimer",
                "message": "If this is a medical emergency, please seek immediate medical attention."
            }
    
    return {
        "is_emergency": False,
        "is_urgent": False,
        "matched_keyword": None,
        "action": "proceed"
    }


@tool
def add_medical_disclaimer(topic: str, response: str) -> str:
    """
    Add appropriate medical disclaimer based on topic.
    
    Args:
        topic: The health topic being discussed
        response: The response to add disclaimer to
        
    Returns:
        Response with appropriate disclaimer
    """
    disclaimer_topics = {
        "diagnosis": "\n\n💡 *This is general information only. For a proper diagnosis, please consult a healthcare provider.*",
        "medication": "\n\n💊 *Always consult your doctor before starting or changing any medication.*",
        "treatment": "\n\n🏥 *Treatment recommendations should be discussed with your healthcare provider.*",
        "symptoms": "\n\n📋 *Persistent or severe symptoms should be evaluated by a medical professional.*",
        "default": "\n\n💜 *This is educational information, not medical advice. Please consult a healthcare provider for personalized guidance.*"
    }
    
    disclaimer = disclaimer_topics.get(topic, disclaimer_topics["default"])
    return response + disclaimer


# ═══════════════════════════════════════════════════════════════════════════════
# 6. PROACTIVE TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
async def check_proactive_triggers(user_id: str, db_session: Any) -> Dict[str, Any]:
    """
    Check for proactive engagement triggers.
    Use at start of conversation to personalize greeting.
    
    Args:
        user_id: The user's ID
        db_session: Database session
        
    Returns:
        List of active triggers and suggested messages
    """
    from app.core.database import UserProfile, DailyAssignment, SymptomLog, ChatSession
    from app.services.cycle_service import CycleService
    from sqlalchemy import and_, func
    
    triggers = []
    
    # 1. Streak check
    recent_completions = db_session.query(func.count(DailyAssignment.id)).filter(
        and_(
            DailyAssignment.uid == user_id,
            DailyAssignment.is_completed == True,
            DailyAssignment.assignment_date >= date.today() - timedelta(days=7)
        )
    ).scalar()
    
    if recent_completions >= 7:
        triggers.append({
            "type": "streak",
            "message": f"Amazing streak! You've completed {recent_completions} assignments this week 🔥",
            "priority": 2
        })
    
    # 2. Phase transition
    cycle_service = CycleService(db_session)
    cycle_info = cycle_service.get_cycle_phase_info(user_id)
    
    if cycle_info and cycle_info.cycle_day in [1, 8, 14, 22]:  # Phase transition days
        triggers.append({
            "type": "phase_transition",
            "message": f"You're entering your {cycle_info.phase} phase - your plan has been optimized for this time! 🌙",
            "priority": 1
        })
    
    # 3. Inactivity check
    last_session = db_session.query(ChatSession).filter(
        ChatSession.user_id == user_id
    ).order_by(ChatSession.created_at.desc()).first()
    
    if last_session and (datetime.utcnow() - last_session.created_at).days >= 3:
        triggers.append({
            "type": "inactivity",
            "message": "Welcome back! I've been keeping track of things while you were away 💜",
            "priority": 3
        })
    
    # 4. Symptom pattern detection
    recent_symptoms = db_session.query(SymptomLog).filter(
        and_(
            SymptomLog.user_id == user_id,
            SymptomLog.logged_date >= date.today() - timedelta(days=3),
            SymptomLog.severity >= 6
        )
    ).all()
    
    if len(recent_symptoms) >= 2:
        symptom_types = list(set([s.symptom_type for s in recent_symptoms]))
        triggers.append({
            "type": "symptom_pattern",
            "message": f"I noticed you've been experiencing some {', '.join(symptom_types)} lately. Would you like to talk about it?",
            "priority": 1
        })
    
    # 5. Completion celebration
    today_assignments = db_session.query(DailyAssignment).filter(
        and_(
            DailyAssignment.uid == user_id,
            DailyAssignment.assignment_date == date.today()
        )
    ).all()
    
    if today_assignments:
        completed = sum(1 for a in today_assignments if a.is_completed)
        if completed == len(today_assignments) and len(today_assignments) > 0:
            triggers.append({
                "type": "completion_celebration",
                "message": "You completed ALL your tasks today! That's incredible dedication 🎉",
                "priority": 1
            })
    
    # Sort by priority
    triggers.sort(key=lambda x: x["priority"])
    
    return {
        "has_triggers": len(triggers) > 0,
        "triggers": triggers,
        "top_trigger": triggers[0] if triggers else None
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 7. EDUCATION TOOLS (Additional)
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def explain_hormone(hormone_name: str) -> Dict[str, Any]:
    """
    Explain a specific hormone and its effects on the body.
    Use when user asks about hormones or wants to understand their imbalances.
    
    Args:
        hormone_name: Name of the hormone (estrogen, progesterone, testosterone, cortisol, insulin)
        
    Returns:
        Educational information about the hormone
    """
    hormone_info = {
        "estrogen": {
            "name": "Estrogen",
            "also_known_as": "The feminizing hormone",
            "primary_functions": [
                "Regulates menstrual cycle",
                "Supports bone health",
                "Affects mood and cognitive function",
                "Maintains vaginal and skin health"
            ],
            "when_high": "Can cause bloating, breast tenderness, mood swings, heavy periods",
            "when_low": "Can cause hot flashes, vaginal dryness, irregular periods, low mood",
            "natural_fluctuation": "Rises during follicular phase, peaks at ovulation, drops before period",
            "lifestyle_tips": [
                "Fiber-rich foods help metabolize excess estrogen",
                "Cruciferous vegetables (broccoli, kale) support estrogen balance",
                "Limit alcohol and processed foods"
            ]
        },
        "progesterone": {
            "name": "Progesterone",
            "also_known_as": "The calming hormone",
            "primary_functions": [
                "Prepares uterus for pregnancy",
                "Promotes relaxation and sleep",
                "Balances estrogen effects",
                "Supports mood stability"
            ],
            "when_high": "Usually only during pregnancy - can cause fatigue, bloating",
            "when_low": "PMS symptoms, anxiety, irregular periods, difficulty sleeping",
            "natural_fluctuation": "Rises after ovulation during luteal phase, drops before period",
            "lifestyle_tips": [
                "Manage stress (cortisol blocks progesterone)",
                "Vitamin B6 rich foods support production",
                "Adequate sleep is essential"
            ]
        },
        "testosterone": {
            "name": "Testosterone",
            "also_known_as": "The energy hormone (yes, women need it too!)",
            "primary_functions": [
                "Supports energy and motivation",
                "Maintains muscle mass",
                "Influences libido",
                "Supports cognitive function"
            ],
            "when_high": "Can cause acne, excess hair growth, irregular periods (common in PCOS)",
            "when_low": "Fatigue, low libido, difficulty building muscle, depression",
            "natural_fluctuation": "Peaks around ovulation",
            "lifestyle_tips": [
                "Strength training supports healthy levels",
                "Zinc-rich foods (pumpkin seeds, oysters)",
                "Adequate protein intake"
            ]
        },
        "cortisol": {
            "name": "Cortisol",
            "also_known_as": "The stress hormone",
            "primary_functions": [
                "Manages stress response",
                "Regulates blood sugar",
                "Controls inflammation",
                "Affects sleep-wake cycle"
            ],
            "when_high": "Weight gain (especially belly), anxiety, insomnia, hormone disruption",
            "when_low": "Extreme fatigue, weakness, difficulty handling stress",
            "natural_fluctuation": "Should be highest in morning, lowest at night",
            "lifestyle_tips": [
                "Morning sunlight exposure helps regulate",
                "Mindfulness and breathing exercises",
                "Limit caffeine after 2pm",
                "Regular sleep schedule"
            ]
        },
        "insulin": {
            "name": "Insulin",
            "also_known_as": "The blood sugar regulator",
            "primary_functions": [
                "Regulates blood sugar levels",
                "Affects energy and hunger",
                "Influences other hormone production",
                "Affects weight management"
            ],
            "when_high": "Weight gain, sugar cravings, fatigue after meals, dark skin patches",
            "when_low": "Diabetes symptoms - rare without medication",
            "natural_fluctuation": "Rises after eating, should return to baseline within 2 hours",
            "lifestyle_tips": [
                "Eat protein and fiber with carbs",
                "Avoid sugary drinks and processed foods",
                "Regular movement after meals",
                "Don't skip meals"
            ]
        }
    }
    
    hormone_key = hormone_name.lower().strip()
    
    if hormone_key in hormone_info:
        return {
            "success": True,
            "hormone": hormone_info[hormone_key],
            "disclaimer": "This is educational information. For personalized hormone assessment, consult a healthcare provider."
        }
    else:
        return {
            "success": False,
            "error": f"Unknown hormone: {hormone_name}",
            "available_hormones": list(hormone_info.keys())
        }


@tool
async def get_hormone_analysis(user_id: str, db_session: Any) -> Dict[str, Any]:
    """
    Get user's hormone imbalance analysis from their health survey.
    Use when user asks why they have certain symptoms or recommendations.
    
    Args:
        user_id: The user's ID
        db_session: Database session
        
    Returns:
        User's hormone analysis with explanations
    """
    from app.core.database import UserResponse
    from sqlalchemy import desc
    
    user_response = db_session.query(UserResponse).filter(
        UserResponse.uid == user_id
    ).order_by(desc(UserResponse.created_at)).first()
    
    if not user_response:
        return {"success": False, "error": "No health survey data found"}
    
    primary = user_response.primary_hormone
    secondary = user_response.secondary_hormones or []
    
    # Hormone explanations
    hormone_explanations = {
        "progesterone": {
            "concern": "Progesterone Imbalance",
            "symptoms": ["PMS", "anxiety", "sleep issues", "irregular periods"],
            "why": "Your symptoms suggest your body may not be producing enough progesterone, especially in the luteal phase.",
            "focus": "Stress management and cycle-synced nutrition"
        },
        "estrogen": {
            "concern": "Estrogen Imbalance",
            "symptoms": ["bloating", "heavy periods", "mood swings", "breast tenderness"],
            "why": "Your symptoms indicate potential estrogen dominance or fluctuation issues.",
            "focus": "Liver support and fiber-rich foods"
        },
        "testosterone": {
            "concern": "Testosterone Imbalance",
            "symptoms": ["low energy", "low libido", "difficulty with muscle", "acne/hair growth if high"],
            "why": "Your energy and vitality symptoms suggest testosterone levels may need attention.",
            "focus": "Strength exercises and zinc-rich foods"
        },
        "cortisol": {
            "concern": "Cortisol Dysregulation",
            "symptoms": ["fatigue", "stress", "belly weight", "sleep issues"],
            "why": "Your stress and energy patterns suggest cortisol rhythm may be off.",
            "focus": "Stress reduction and sleep hygiene"
        },
        "insulin": {
            "concern": "Insulin Sensitivity",
            "symptoms": ["sugar cravings", "energy crashes", "weight around middle"],
            "why": "Your eating patterns and energy levels suggest blood sugar management could help.",
            "focus": "Balanced meals and movement after eating"
        }
    }
    
    result = {
        "success": True,
        "primary_hormone": {
            "hormone": primary,
            "details": hormone_explanations.get(primary, {"concern": primary, "focus": "Personalized approach"})
        },
        "secondary_hormones": [
            {
                "hormone": h,
                "details": hormone_explanations.get(h, {"concern": h})
            }
            for h in secondary
        ],
        "user_concerns": {
            "period": user_response.period_concerns,
            "body": user_response.body_concerns,
            "skin_hair": user_response.skin_hair_concerns,
            "mental_health": user_response.mental_health_concerns,
            "top_concern": user_response.top_concern
        },
        "recommendation": f"Your action plan is specifically designed to support {primary} balance through targeted foods, movement, and mindfulness practices."
    }
    
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 8. PROGRESS TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
async def get_progress_stats(
    user_id: str,
    db_session: Any,
    period: str = "weekly"
) -> Dict[str, Any]:
    """
    Get completion stats and streaks for the user.
    Use when user asks about their progress or achievements.
    
    Args:
        user_id: The user's ID
        db_session: Database session
        period: Time period (daily, weekly, monthly)
        
    Returns:
        Progress statistics including completion rate, streak, hormone progress
    """
    from app.core.database import DailyAssignment, RecommendationCompletion, RecommendationRecord
    from sqlalchemy import and_, func
    
    # Determine date range
    if period == "daily":
        start_date = date.today()
    elif period == "weekly":
        start_date = date.today() - timedelta(days=7)
    else:  # monthly
        start_date = date.today() - timedelta(days=30)
    
    # Get assignments in range
    assignments = db_session.query(DailyAssignment).filter(
        and_(
            DailyAssignment.uid == user_id,
            DailyAssignment.assignment_date >= start_date
        )
    ).all()
    
    total = len(assignments)
    completed = sum(1 for a in assignments if a.is_completed)
    completion_rate = completed / total if total > 0 else 0
    
    # Calculate streak
    streak = 0
    check_date = date.today()
    
    while True:
        day_assignments = [a for a in assignments if a.assignment_date == check_date]
        if not day_assignments:
            break
        
        day_completed = all(a.is_completed for a in day_assignments)
        if day_completed:
            streak += 1
            check_date -= timedelta(days=1)
        else:
            break
    
    # Hormone progress (what hormones are being targeted)
    hormone_stats = {}
    for assignment in assignments:
        rec = db_session.query(RecommendationRecord).filter(
            RecommendationRecord.id == assignment.recommendation_id
        ).first()
        
        if rec and rec.hormones:
            for hormone in rec.hormones:
                if hormone not in hormone_stats:
                    hormone_stats[hormone] = {"total": 0, "completed": 0}
                hormone_stats[hormone]["total"] += 1
                if assignment.is_completed:
                    hormone_stats[hormone]["completed"] += 1
    
    # Calculate hormone completion rates
    for hormone in hormone_stats:
        stats = hormone_stats[hormone]
        stats["rate"] = round(stats["completed"] / stats["total"], 2) if stats["total"] > 0 else 0
    
    # Category breakdown
    category_stats = {"food": 0, "movement": 0, "mindfulness": 0}
    for assignment in assignments:
        if assignment.is_completed:
            rec = db_session.query(RecommendationRecord).filter(
                RecommendationRecord.id == assignment.recommendation_id
            ).first()
            if rec and rec.category in category_stats:
                category_stats[rec.category] += 1
    
    return {
        "success": True,
        "period": period,
        "total_assignments": total,
        "completed": completed,
        "completion_rate": round(completion_rate * 100, 1),
        "current_streak": streak,
        "hormone_progress": hormone_stats,
        "category_breakdown": category_stats,
        "encouragement": _get_encouragement(completion_rate, streak)
    }


def _get_encouragement(rate: float, streak: int) -> str:
    """Get encouraging message based on progress."""
    if streak >= 7:
        return "🔥 Amazing streak! You're building real habits!"
    elif streak >= 3:
        return "💪 Great consistency! Keep it going!"
    elif rate >= 0.8:
        return "🌟 Excellent completion rate! You're crushing it!"
    elif rate >= 0.5:
        return "👍 Good progress! Every action counts."
    else:
        return "💜 Remember, any progress is progress. You've got this!"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. NAVIGATION TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def navigate_to_screen(screen_name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Navigate user to a specific app screen.
    Use when user needs to go somewhere specific in the app.
    
    Args:
        screen_name: Name of the screen to navigate to
        params: Optional parameters to pass to the screen
        
    Returns:
        Navigation action for frontend to execute
    """
    valid_screens = {
        "home": "HomeScreen",
        "progress": "ProgressScreen",
        "insight": "InsightScreen",
        "profile": "ProfileScreen",
        "action_detail": "ActionDetailScreen",
        "personalize": "PersonalizeScreen",
        "community": "CommunityScreen",
        "chat_history": "ChatHistoryScreen",
        "settings": "SettingsScreen"
    }
    
    screen_key = screen_name.lower().replace(" ", "_")
    
    if screen_key in valid_screens:
        return {
            "success": True,
            "action_type": "navigate",
            "screen": valid_screens[screen_key],
            "params": params or {},
            "message": f"Taking you to {screen_name}..."
        }
    else:
        return {
            "success": False,
            "error": f"Unknown screen: {screen_name}",
            "available_screens": list(valid_screens.keys())
        }


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

def get_all_tools():
    """Return all available tools for the LangGraph agent."""
    return [
        # Action Plan
        get_current_assignments,
        complete_assignment,
        skip_assignment,
        reschedule_assignment,
        suggest_alternative_assignment,
        
        # Symptoms
        log_symptom,
        get_symptom_trends,
        
        # Context
        get_patient_profile,
        get_cycle_info,
        
        # Education
        search_health_knowledge,
        explain_hormone,
        get_hormone_analysis,
        
        # Progress
        get_progress_stats,
        
        # Safety
        check_emergency_keywords,
        add_medical_disclaimer,
        
        # Proactive
        check_proactive_triggers,
        
        # Navigation
        navigate_to_screen,
    ]


def get_tools_by_context(context: str) -> List:
    """Get tools relevant to a specific conversation context."""
    context_tools = {
        "care_plan_modal": [
            get_current_assignments,
            complete_assignment,
            skip_assignment,
            reschedule_assignment,
            suggest_alternative_assignment,
            get_cycle_info,
            get_progress_stats,
            check_emergency_keywords,
            navigate_to_screen,
        ],
        "symptom_checkin": [
            log_symptom,
            get_symptom_trends,
            get_cycle_info,
            explain_hormone,
            check_emergency_keywords,
            add_medical_disclaimer,
        ],
        "personalise": [
            get_patient_profile,
            get_cycle_info,
            get_hormone_analysis,
            search_health_knowledge,
            check_emergency_keywords,
        ],
        "know_body": [
            search_health_knowledge,
            explain_hormone,
            get_hormone_analysis,
            get_cycle_info,
            get_patient_profile,
            check_emergency_keywords,
            add_medical_disclaimer,
        ]
    }
    
    return context_tools.get(context, get_all_tools())
