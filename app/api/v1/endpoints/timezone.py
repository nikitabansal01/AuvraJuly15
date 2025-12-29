"""
Timezone API endpoints.

Allows users to update their timezone and get timezone-related information.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db, UserProfile
from app.api.v1.endpoints.auth import get_current_user
from app.utils.timezone_utils import validate_timezone, get_user_timezone

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST/RESPONSE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class TimezoneUpdateRequest(BaseModel):
    timezone: str  # IANA timezone identifier (e.g., "America/New_York")


class TimezoneResponse(BaseModel):
    success: bool
    timezone: str
    message: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/current")
async def get_current_timezone(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the user's current timezone setting.
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    timezone = get_user_timezone(uid, db)
    
    return TimezoneResponse(
        success=True,
        timezone=timezone,
        message="Current timezone retrieved successfully"
    )


@router.post("/update", response_model=TimezoneResponse)
async def update_timezone(
    request: TimezoneUpdateRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update the user's timezone.
    
    All future date/time calculations will use this timezone.
    This affects:
    - Daily action plan generation (what date is "today")
    - Streak calculations (what day is "today")
    - Scheduling (when to fire recommendations)
    - All date-based features
    
    Args:
        timezone: IANA timezone identifier (e.g., "America/New_York", "Asia/Tokyo")
    """
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=400, detail="User ID not found")
    
    # Validate timezone
    if not validate_timezone(request.timezone):
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid timezone: {request.timezone}. Use IANA timezone identifiers (e.g., 'America/New_York')"
        )
    
    # Get user profile
    user_profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
    
    if not user_profile:
        # Create profile if doesn't exist
        user_profile = UserProfile(
            uid=uid,
            current_timezone=request.timezone
        )
        db.add(user_profile)
    else:
        # Update timezone
        user_profile.current_timezone = request.timezone
    
    db.commit()
    
    return TimezoneResponse(
        success=True,
        timezone=request.timezone,
        message="Timezone updated successfully. All future calculations will use this timezone."
    )


@router.get("/validate/{timezone_str}")
async def validate_timezone_endpoint(
    timezone_str: str
):
    """
    Validate if a timezone string is valid.
    
    Useful for client-side validation before updating.
    """
    is_valid = validate_timezone(timezone_str)
    
    if is_valid:
        return {
            "valid": True,
            "timezone": timezone_str,
            "message": "Timezone is valid"
        }
    else:
        return {
            "valid": False,
            "timezone": timezone_str,
            "message": "Invalid timezone. Use IANA timezone identifiers (e.g., 'America/New_York')"
        }
