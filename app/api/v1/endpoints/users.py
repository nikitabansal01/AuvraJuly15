from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from typing import List, Optional
from app.core.firebase import get_user_by_uid, list_users, update_user, delete_user
from app.api.v1.endpoints.auth import get_current_active_user

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