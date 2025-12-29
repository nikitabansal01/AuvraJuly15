"""
Weekly Check-in API endpoints.

Provides endpoints for managing weekly check-in sessions:
- Status checking (is check-in due)
- Starting/resuming check-in sessions
- Submitting responses to questions
- Completing check-ins
- Retrieving history for insights
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional, Any, Dict

from app.core.database import get_db
from app.api.v1.endpoints.auth import get_current_user
from app.services.weekly_checkin_service import WeeklyCheckInService

router = APIRouter()


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


class QuestionResponse(BaseModel):
    """Response containing the next question in the check-in flow."""
    is_complete: bool
    question_key: Optional[str] = None
    question_type: Optional[str] = None  # "slider", "tap_choice", "multi_select", "free_text"
    message: str
    tap_options: List[TapOption] = []
    is_required: bool = False
    current_index: int = 0
    total_questions: int = 0
    summary: Optional[str] = None  # Only on completion
    history: List[ChatMessage] = []  # Chat history for context restoration


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
    checkin_id: str
    week_number: int
    year: int
    question: QuestionResponse


class SubmitResponseRequest(BaseModel):
    """Request to submit a response to a check-in question."""
    checkin_id: str
    question_key: str
    response: Any  # string, int, or list depending on question type
    message_text: Optional[str] = None  # Raw message for conversation log


class SubmitResponseResponse(BaseModel):
    """Response after submitting an answer."""
    checkin_id: str
    question: QuestionResponse


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
    
    return StartCheckInResponse(
        checkin_id=checkin.id,
        week_number=checkin.week_number,
        year=checkin.year,
        question=QuestionResponse(
            is_complete=question_data.get("is_complete", False),
            question_key=question_data.get("question_key"),
            question_type=question_data.get("question_type"),
            message=question_data.get("message", ""),
            tap_options=tap_options,
            is_required=question_data.get("is_required", False),
            current_index=question_data.get("current_index", 0),
            total_questions=question_data.get("total_questions", 0)
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
    uid = current_user["uid"]
    service = WeeklyCheckInService(db)
    
    try:
        checkin, question_data = service.submit_response(
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
    
    return SubmitResponseResponse(
        checkin_id=checkin.id,
        question=QuestionResponse(
            is_complete=question_data.get("is_complete", False),
            question_key=question_data.get("question_key"),
            question_type=question_data.get("question_type"),
            message=question_data.get("message", ""),
            tap_options=tap_options,
            is_required=question_data.get("is_required", False),
            current_index=question_data.get("current_index", 0),
            total_questions=question_data.get("total_questions", 0),
            summary=question_data.get("summary")
        )
    )


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
