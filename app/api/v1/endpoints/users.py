from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.core.firebase import get_user_by_uid, list_users, update_user, delete_user
from app.api.v1.endpoints.auth import get_current_active_user
from app.core.database import get_db, UserResponse as DBUserResponse

router = APIRouter()


class UserResponse(BaseModel):
    uid: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    email_verified: bool = False
    photo_url: Optional[str] = None
    disabled: bool = False


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None
    photo_url: Optional[str] = None


class UserProfileResponse(BaseModel):
    """User profile with concerns and diagnosis from onboarding"""
    concerns: List[str] = []
    diagnosis: List[str] = []
    top_concern: Optional[str] = None


@router.get("/profile/me", response_model=UserProfileResponse)
async def get_my_profile(
    current_user: dict = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get current user's profile with concerns and diagnosis from onboarding survey."""
    try:
        uid = current_user.get("uid")
        if not uid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not authenticated"
            )
        
        # Get latest user response (onboarding data)
        logger.info(f"[PROFILE] Request from uid={uid}")
        user_response = db.query(DBUserResponse).filter(
            DBUserResponse.uid == uid
        ).order_by(desc(DBUserResponse.created_at)).first()
        
        if not user_response:
            return UserProfileResponse(concerns=[], diagnosis=[], top_concern=None)
        
        # Collect all concerns
        concerns = []
        concern_fields = [
            user_response.period_concerns,
            user_response.body_concerns,
            user_response.skin_hair_concerns,
            user_response.mental_health_concerns,
            user_response.other_concerns,
        ]
        
        for concern_field in concern_fields:
            if concern_field:
                if isinstance(concern_field, list):
                    concerns.extend(concern_field)
                elif isinstance(concern_field, dict):
                    # Extract values if it's a dict
                    for key, value in concern_field.items():
                        if isinstance(value, list):
                            concerns.extend(value)
                        elif isinstance(value, str):
                            concerns.append(value)
        
        # Filter out "None of the above" and empty values
        concerns = [c for c in concerns if c and c.lower() != 'none of the above' and c.strip()]
        
        # Remove duplicates while preserving order
        seen = set()
        unique_concerns = []
        for c in concerns:
            if c.lower() not in seen:
                seen.add(c.lower())
                unique_concerns.append(c)
        
        # Get diagnosis
        diagnosis = user_response.diagnosed_conditions or []
        diagnosis = [d for d in diagnosis if d and d.lower() != 'none of the above' and d.strip()]
        
        return UserProfileResponse(
            concerns=unique_concerns[:5],  # Top 5 concerns
            diagnosis=diagnosis,
            top_concern=user_response.top_concern
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get profile: {str(e)}"
        )


@router.get("/", response_model=List[UserResponse])
async def get_users(current_user: dict = Depends(get_current_active_user)):
    """Get all users list (admin only)."""
    try:
        users = list_users()
        return [UserResponse(**user) for user in users]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get users list: {str(e)}"
        )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: str, current_user: dict = Depends(get_current_active_user)):
    """Get specific user information."""
    try:
        user = get_user_by_uid(user_id)
        return UserResponse(**user)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User not found: {str(e)}"
        )


@router.put("/{user_id}", response_model=UserResponse)
async def update_user_info(user_id: str, user_update: UserUpdate, current_user: dict = Depends(get_current_active_user)):
    """Update user information."""
    try:
        # Check if user can only modify their own information
        if current_user.get("uid") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only modify your own information."
            )
        
        update_data = {}
        if user_update.display_name is not None:
            update_data["display_name"] = user_update.display_name
        if user_update.email is not None:
            update_data["email"] = user_update.email
        if user_update.photo_url is not None:
            update_data["photo_url"] = user_update.photo_url
        
        updated_user = update_user(user_id, **update_data)
        return UserResponse(**updated_user)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update user information: {str(e)}"
        )


@router.delete("/{user_id}")
async def delete_user_account(user_id: str, current_user: dict = Depends(get_current_active_user)):
    """Delete user account."""
    try:
        # Check if user can only delete their own account
        if current_user.get("uid") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only delete your own account."
            )
        
        result = delete_user(user_id)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to delete user: {str(e)}"
        )
 