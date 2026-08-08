"""update_checkin_questions_order

Revision ID: 63b5f93e3606
Revises: add_weekly_checkin
Create Date: 2025-12-29 19:13:27.424914

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '63b5f93e3606'
down_revision: Union[str, None] = 'add_weekly_checkin'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Deactivate greeting and top_concern questions
    op.execute("""
        UPDATE weekly_checkin_questions 
        SET is_active = false 
        WHERE question_key IN ('greeting', 'top_concern');
    """)
    
    # 2. Update concern_severity to be the first question and match screenshot text
    op.execute("""
        UPDATE weekly_checkin_questions 
        SET question_order = 1,
            question_template = 'How was your {top_concern} this week?'
        WHERE question_key = 'concern_severity';
    """)
    
    # 3. Reorder remaining questions to follow sequentially
    op.execute("""
        UPDATE weekly_checkin_questions SET question_order = 2 WHERE question_key = 'factors_negative';
        UPDATE weekly_checkin_questions SET question_order = 3 WHERE question_key = 'factors_positive';
        UPDATE weekly_checkin_questions SET question_order = 4 WHERE question_key = 'action_reflection';
        UPDATE weekly_checkin_questions SET question_order = 5 WHERE question_key = 'overall_wellbeing';
        UPDATE weekly_checkin_questions SET question_order = 6 WHERE question_key = 'concerns_next_week';
        UPDATE weekly_checkin_questions SET question_order = 7 WHERE question_key = 'closing';
    """)


def downgrade() -> None:
    # Restore original state
    op.execute("""
        UPDATE weekly_checkin_questions 
        SET is_active = true 
        WHERE question_key IN ('greeting', 'top_concern');
    """)
    
    op.execute("""
        UPDATE weekly_checkin_questions 
        SET question_order = 3,
            question_template = 'On a scale of 1-9, how severe has your {top_concern} been?'
        WHERE question_key = 'concern_severity';
    """)
    
    # Restore original order
    op.execute("""
        UPDATE weekly_checkin_questions SET question_order = 1 WHERE question_key = 'greeting';
        UPDATE weekly_checkin_questions SET question_order = 2 WHERE question_key = 'top_concern';
        UPDATE weekly_checkin_questions SET question_order = 4 WHERE question_key = 'factors_negative';
        UPDATE weekly_checkin_questions SET question_order = 5 WHERE question_key = 'factors_positive';
        UPDATE weekly_checkin_questions SET question_order = 6 WHERE question_key = 'action_reflection';
        UPDATE weekly_checkin_questions SET question_order = 7 WHERE question_key = 'overall_wellbeing';
        UPDATE weekly_checkin_questions SET question_order = 8 WHERE question_key = 'concerns_next_week';
        UPDATE weekly_checkin_questions SET question_order = 9 WHERE question_key = 'closing';
    """)
