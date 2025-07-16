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
    """모든 사용자 목록 조회 (관리자만)"""
    try:
        users = list_users()
        return [UserResponse(**user) for user in users]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"사용자 목록 조회 실패: {str(e)}"
        )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: str, current_user: dict = Depends(get_current_active_user)):
    """특정 사용자 정보 조회"""
    try:
        user = get_user_by_uid(user_id)
        return UserResponse(**user)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"사용자를 찾을 수 없습니다: {str(e)}"
        )


@router.put("/{user_id}", response_model=UserResponse)
async def update_user_info(user_id: str, user_update: UserUpdate, current_user: dict = Depends(get_current_active_user)):
    """사용자 정보 업데이트"""
    try:
        # 본인만 수정 가능하도록 체크
        if current_user.get("uid") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="본인의 정보만 수정할 수 있습니다."
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
            detail=f"사용자 정보 업데이트 실패: {str(e)}"
        )


@router.delete("/{user_id}")
async def delete_user_account(user_id: str, current_user: dict = Depends(get_current_active_user)):
    """사용자 계정 삭제"""
    try:
        # 본인만 삭제 가능하도록 체크
        if current_user.get("uid") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="본인의 계정만 삭제할 수 있습니다."
            )
        
        result = delete_user(user_id)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"사용자 삭제 실패: {str(e)}"
        ) 