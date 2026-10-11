from fastapi import APIRouter, HTTPException, status, Depends
import logging
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.core.firebase import get_user_by_uid, list_users, update_user, delete_user
from app.api.v1.endpoints.auth import get_current_active_user, get_current_user
from app.core.database import get_db, UserResponse as DBUserResponse, UserProfile
from app.services.profile_service import ProfileService, health_record, DIAGNOSIS_OPTIONS

router = APIRouter()
logger = logging.getLogger(__name__)


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
    name: Optional[str] = None
    diagnosis_options: List[str] = DIAGNOSIS_OPTIONS
    can_update_profile: bool = True


class UserProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None
    top_concern: Optional[str] = None
    diagnosis: Optional[List[str]] = None

    @field_validator("name", mode="before")
    @classmethod
    def valid_name(cls, value):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or any(ord(c) < 32 for c in value):
            raise ValueError("Enter a name between 1 and 80 characters")
        return value.strip()

    @field_validator("diagnosis", mode="before")
    @classmethod
    def valid_diagnosis_list(cls, value):
        if not isinstance(value, list) or len(value) > 20 or any(not isinstance(item, str) or not 1 <= len(item) <= 4096 for item in value):
            raise ValueError("Choose valid reported diagnoses")
        return value

    @model_validator(mode="after")
    def nonempty_update(self):
        if not self.model_fields_set:
            raise ValueError("Choose a profile field to update")
        return self


@router.put("/profile/me", response_model=UserProfileResponse)
async def update_my_profile(
    payload: UserProfileUpdate,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    uid = current_user.get("uid")
    if not uid: raise HTTPException(status_code=401, detail="Sign in to update your profile")
    from starlette.concurrency import run_in_threadpool
    try:
        return await run_in_threadpool(ProfileService(db).update, uid, payload.model_dump(exclude_unset=True))
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        logger.error("Profile update failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Your profile could not be saved. Please try again.") from error


@router.get("/profile/me", response_model=UserProfileResponse)
async def get_my_profile(
    current_user: dict = Depends(get_current_user),
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
        ).order_by(desc(DBUserResponse.created_at), desc(DBUserResponse.id)).first()
        
        profile = db.query(UserProfile).filter(UserProfile.uid == uid).first()
        return UserProfileResponse(name=profile.name if profile else None, **health_record(user_response))
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
    # This dictionary comes from verified Firebase token claims. A verified
    # email alone does not authorize listing other people's account records.
    if current_user.get("admin") is not True:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access is required")
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
    if current_user.get("uid") != user_id and current_user.get("admin") is not True:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only view your own account")
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
    except HTTPException:
        raise
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to delete user: {str(e)}"
        )
