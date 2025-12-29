"""
Quick script to restore freeze tokens for user affected by JSONB bug.
Run with: python scripts/restore_user_freezes.py
"""
import sys
sys.path.insert(0, '.')

from app.core.database import get_db, UserStreakData
from sqlalchemy.orm.attributes import flag_modified

def restore_freezes():
    db = next(get_db())
    uid = 'QEAS0DwsVDfi4VvdulM6yvHWp3C3'
    
    streak_data = db.query(UserStreakData).filter(UserStreakData.uid == uid).first()
    if streak_data:
        print(f'Before: freeze_count={streak_data.freeze_count}, freeze_used_dates={streak_data.freeze_used_dates}')
        
        # Restore 2 freeze tokens (what they had before the bug)
        streak_data.freeze_count = 2
        
        # Clear the frozen dates since they weren't saved properly due to JSONB bug
        streak_data.freeze_used_dates = []
        flag_modified(streak_data, 'freeze_used_dates')
        
        db.commit()
        db.refresh(streak_data)
        
        print(f'After: freeze_count={streak_data.freeze_count}, freeze_used_dates={streak_data.freeze_used_dates}')
        print('✅ Restored 2 freeze tokens for user QEAS0DwsVDfi4VvdulM6yvHWp3C3')
    else:
        print('❌ User not found')

if __name__ == '__main__':
    restore_freezes()
