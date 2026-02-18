"""
Weekly Check-in API endpoints.

Provides endpoints for managing weekly check-in sessions:
- Status checking (is check-in due)
- Starting/resuming check-in sessions
- Submitting responses to questions
- Completing check-ins
- Retrieving history for insights
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from time import perf_counter

from app.core.database import get_db
from app.api.v1.endpoints.auth import get_current_user
from app.api.v1.endpoints._chatbot_runtime import ensure_actionable_insights
from app.services.weekly_checkin_service import WeeklyCheckInService

router = APIRouter()


def _weekly_trace(
    *,
    thread_id: str,
    action_id: str,
    latency_ms: Optional[float] = None,
    workflow_stage: Optional[str] = None,
) -> Dict[str, Any]:
    trace: Dict[str, Any] = {
        "flow": "weekly_checkin",
        "thread_id": thread_id,
        "action_id": action_id,
        "workflow_stage": workflow_stage,
    }
    if latency_ms is not None:
        trace["latency_ms"] = int(latency_ms)
    return trace


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST/RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class TapOption(BaseModel):
    id: str
    text: str


class ChatMessage(BaseModel):
    id: str
    text: str
    isBot: bool
    created_at: Optional[str] = None
    ui_blocks: List[Dict[str, Any]] = []


class QuestionResponse(BaseModel):
    """Response containing the next question in the check-in flow."""
    is_complete: bool
    question_key: Optional[str] = None
    question_type: Optional[str] = None  # "slider", "tap_choice", "multi_select", "free_text"
    message: str  # Combined message for backward compatibility
    messages: List[str] = []  # Array of short messages for multi-bubble display
    tap_options: List[TapOption] = []
    is_required: bool = False
    current_index: int = 0
    total_questions: int = 0
    summary: Optional[str] = None  # Only on completion
    history: List[ChatMessage] = []  # Chat history for context restoration
    slider_labels: Optional[Any] = None  # For slider questions (List[str] or Dict[str, str])


class CheckInStatusResponse(BaseModel):
    """Check-in availability and status for action plan card."""
    is_available: bool  # User has unlocked the feature (14-day streak)
    is_due: bool  # Check-in is due today
    due_date: Optional[str] = None
    incomplete_id: Optional[str] = None  # Resume this session
    last_completed: Optional[str] = None
    checkin_streak: int = 0  # Consecutive weeks of check-ins
    unlock_days_remaining: int = 0  # Days until feature unlocks


class StartCheckInResponse(BaseModel):
    """Response when starting a new check-in session."""
    # Legacy fields (backward compatible)
    checkin_id: str
    week_number: int
    year: int
    question: QuestionResponse
    is_already_completed: bool = False  # True if viewing completed check-in in read-only mode
    next_due_date: Optional[str] = None  # When the next check-in is available
    # Standardized chatbot contract fields
    thread_id: str
    local_date: str
    history: List[ChatMessage] = []
    tap_options: List[TapOption] = []
    ui_blocks: List[Dict[str, Any]] = []
    actionable_insights: Dict[str, Any] = {}
    trace: Optional[Dict[str, Any]] = None


class SubmitResponseRequest(BaseModel):
    """Request to submit a response to a check-in question."""
    checkin_id: str
    question_key: str
    response: Any  # string, int, or list depending on question type
    message_text: Optional[str] = None  # Raw message for conversation log


class SubmitResponseResponse(BaseModel):
    """Response after submitting an answer."""
    # Legacy fields (backward compatible)
    checkin_id: str
    question: QuestionResponse
    # Standardized chatbot contract fields
    thread_id: str
    local_date: str
    history: List[ChatMessage] = []
    tap_options: List[TapOption] = []
    ui_blocks: List[Dict[str, Any]] = []
    actionable_insights: Dict[str, Any] = {}
    trace: Optional[Dict[str, Any]] = None


class TranscribeResponse(BaseModel):
    """Speech-to-text result for Yap."""
    text: str


class TrendDataPoint(BaseModel):
    """Single data point for severity trends."""
    week: str
    date: str
    concern: Optional[str] = None
    severity: Optional[int] = None
    wellbeing: Optional[int] = None
    phase: Optional[str] = None


class FactorImpact(BaseModel):
    """Factor impact analysis."""
    factor: str
    avg_wellbeing: Optional[float] = None
    avg_severity: Optional[float] = None
    occurrences: int


class FactorCorrelationsResponse(BaseModel):
    """Factor correlation analysis for insights."""
    helps: List[FactorImpact]
    hurts: List[FactorImpact]


class CheckInHistoryItem(BaseModel):
    """Single check-in history entry."""
    id: str
    week_number: int
    year: int
    check_in_date: str
    top_concern: Optional[str] = None
    concern_severity: Optional[int] = None
    overall_wellbeing: Optional[int] = None
    phase_at_checkin: Optional[str] = None
    summary: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/status", response_model=CheckInStatusResponse)
async def get_checkin_status(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get weekly check-in status for the current user.
    
    Use this to determine:
    - If check-in feature is unlocked (requires 14-day streak)
    - If a check-in is currently due
    - If there's an incomplete session to resume
    """
    uid = current_user["uid"]
    service = WeeklyCheckInService(db)
    
    status = service.get_checkin_status(uid)
    
    return CheckInStatusResponse(**status)


@router.post("/start", response_model=StartCheckInResponse)
async def start_checkin(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Start a new weekly check-in session or resume an incomplete one.
    
    Returns the first question (or the current question if resuming).
    """
    started_at = perf_counter()
    uid = current_user["uid"]
    service = WeeklyCheckInService(db)
    
    # Check if available
    status = service.get_checkin_status(uid)
    if not status["is_available"]:
        raise HTTPException(
            status_code=403,
            detail=f"Weekly check-ins unlock after 14-day streak. {status['unlock_days_remaining']} days remaining."
        )
    
    checkin, question_data = service.start_checkin(uid)
    
    # Format tap options
    tap_options = [TapOption(**opt) for opt in question_data.get("tap_options", [])]
    
    # Get messages array or create from single message
    messages = question_data.get("messages", [])
    if not messages and question_data.get("message"):
        messages = [question_data.get("message")]

    # Format history for response (so resuming shows prior messages immediately)
    history: List[ChatMessage] = []
    for msg in question_data.get("history", []):
        if isinstance(msg, dict):
            history.append(
                ChatMessage(
                    id=msg.get("id", ""),
                    text=msg.get("text", ""),
                    isBot=msg.get("isBot", False),
                    created_at=msg.get("created_at"),
                    ui_blocks=msg.get("ui_blocks") if isinstance(msg.get("ui_blocks"), list) else [],
                )
            )

    actionable_insights = ensure_actionable_insights(
        checkin.actionable_insights if isinstance(checkin.actionable_insights, dict) else {},
        flow="weekly_checkin",
    )
    trace = _weekly_trace(
        thread_id=checkin.id,
        action_id="start",
        latency_ms=(perf_counter() - started_at) * 1000,
        workflow_stage="in_progress" if not checkin.is_complete else "complete",
    )
    
    return StartCheckInResponse(
        checkin_id=checkin.id,
        week_number=checkin.week_number,
        year=checkin.year,
        is_already_completed=question_data.get("is_already_completed", False),
        next_due_date=question_data.get("next_due_date"),
        thread_id=checkin.id,
        local_date=checkin.check_in_date.isoformat() if checkin.check_in_date else "",
        history=history,
        tap_options=tap_options,
        ui_blocks=[],
        actionable_insights=actionable_insights,
        trace=trace,
        question=QuestionResponse(
            is_complete=question_data.get("is_complete", False),
            question_key=question_data.get("question_key"),
            question_type=question_data.get("question_type"),
            message=question_data.get("message", ""),
            messages=messages,
            tap_options=tap_options,
            is_required=question_data.get("is_required", False),
            current_index=question_data.get("current_index", 0),
            total_questions=question_data.get("total_questions", 0),
            history=history,
            slider_labels=question_data.get("slider_labels")
        )
    )


@router.post("/respond", response_model=SubmitResponseResponse)
async def submit_response(
    request: SubmitResponseRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Submit a response to a check-in question.
    
    Returns the next question or completion message.
    """
    started_at = perf_counter()
    uid = current_user["uid"]
    service = WeeklyCheckInService(db)
    
    try:
        checkin, question_data = await service.submit_response(
            checkin_id=request.checkin_id,
            question_key=request.question_key,
            response=request.response,
            message_text=request.message_text
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Verify ownership
    if checkin.uid != uid:
        raise HTTPException(status_code=403, detail="Not authorized to modify this check-in")
    
    # Format tap options
    tap_options = [TapOption(**opt) for opt in question_data.get("tap_options", [])]
    
    # Get messages array or create from single message
    messages = question_data.get("messages", [])
    if not messages and question_data.get("message"):
        messages = [question_data.get("message")]
    
    # Format history for response
    history = []
    for msg in question_data.get("history", []):
        if isinstance(msg, dict):
            history.append(ChatMessage(
                id=msg.get("id", ""),
                text=msg.get("text", ""),
                isBot=msg.get("isBot", False),
                created_at=msg.get("created_at"),
                ui_blocks=msg.get("ui_blocks") if isinstance(msg.get("ui_blocks"), list) else [],
            ))

    actionable_insights = ensure_actionable_insights(
        checkin.actionable_insights if isinstance(checkin.actionable_insights, dict) else {},
        flow="weekly_checkin",
    )
    trace = _weekly_trace(
        thread_id=checkin.id,
        action_id="respond",
        latency_ms=(perf_counter() - started_at) * 1000,
        workflow_stage="complete" if question_data.get("is_complete") else "in_progress",
    )
    
    return SubmitResponseResponse(
        checkin_id=checkin.id,
        thread_id=checkin.id,
        local_date=checkin.check_in_date.isoformat() if checkin.check_in_date else "",
        history=history,
        tap_options=tap_options,
        ui_blocks=[],
        actionable_insights=actionable_insights,
        trace=trace,
        question=QuestionResponse(
            is_complete=question_data.get("is_complete", False),
            question_key=question_data.get("question_key"),
            question_type=question_data.get("question_type"),
            message=question_data.get("message", ""),
            messages=messages,
            tap_options=tap_options,
            is_required=question_data.get("is_required", False),
            current_index=question_data.get("current_index", 0),
            total_questions=question_data.get("total_questions", 0),
            summary=question_data.get("summary"),
            history=history,
            slider_labels=question_data.get("slider_labels")
        )
    )


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_yap_audio(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Transcribe an uploaded audio file (Yap) into text."""
    uid = current_user["uid"]
    service = WeeklyCheckInService(db)

    try:
        text = await service.transcribe_audio(uid=uid, file=file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)[:200]}")

    return TranscribeResponse(text=text)


@router.get("/history", response_model=List[CheckInHistoryItem])
async def get_checkin_history(
    limit: int = 12,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get user's check-in history.
    
    Returns completed check-ins for insights visualization.
    """
    uid = current_user["uid"]
    service = WeeklyCheckInService(db)
    
    checkins = service.get_checkin_history(uid, limit=limit)
    
    return [
        CheckInHistoryItem(
            id=c.id,
            week_number=c.week_number,
            year=c.year,
            check_in_date=c.check_in_date.isoformat(),
            top_concern=c.top_concern,
            concern_severity=c.concern_severity,
            overall_wellbeing=c.overall_wellbeing,
            phase_at_checkin=c.phase_at_checkin,
            summary=c.conversation_summary
        )
        for c in checkins
    ]


@router.get("/trends", response_model=List[TrendDataPoint])
async def get_severity_trends(
    weeks: int = 8,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get severity and wellbeing trends over time.
    
    Returns data for charting symptom severity and overall wellbeing.
    """
    uid = current_user["uid"]
    service = WeeklyCheckInService(db)
    
    trends = service.get_severity_trends(uid, weeks=weeks)
    
    return [TrendDataPoint(**t) for t in trends]


@router.get("/correlations", response_model=FactorCorrelationsResponse)
async def get_factor_correlations(
    weeks: int = 12,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get factor correlation analysis.
    
    Returns which lifestyle factors correlate with better/worse symptoms.
    This powers the "What works for you" insights section.
    """
    uid = current_user["uid"]
    service = WeeklyCheckInService(db)
    
    correlations = service.get_factor_correlations(uid, weeks=weeks)
    
    return FactorCorrelationsResponse(
        helps=[FactorImpact(**h) for h in correlations["helps"]],
        hurts=[FactorImpact(**h) for h in correlations["hurts"]]
    )


@router.get("/{checkin_id}", response_model=CheckInHistoryItem)
async def get_checkin(
    checkin_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get a specific check-in by ID.
    """
    uid = current_user["uid"]
    service = WeeklyCheckInService(db)
    
    from app.core.database import WeeklyCheckIn
    
    checkin = db.query(WeeklyCheckIn).filter(
        WeeklyCheckIn.id == checkin_id,
        WeeklyCheckIn.uid == uid
    ).first()
    
    if not checkin:
        raise HTTPException(status_code=404, detail="Check-in not found")
    
    return CheckInHistoryItem(
        id=checkin.id,
        week_number=checkin.week_number,
        year=checkin.year,
        check_in_date=checkin.check_in_date.isoformat(),
        top_concern=checkin.top_concern,
        concern_severity=checkin.concern_severity,
        overall_wellbeing=checkin.overall_wellbeing,
        phase_at_checkin=checkin.phase_at_checkin,
        summary=checkin.conversation_summary
    )
