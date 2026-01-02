"""
Create a test plan for yesterday to trigger the Daily Review modal.

This script creates a fake action plan for yesterday with some incomplete items,
so when the user opens the app, they'll see the Daily Review modal.

Usage:
    python scripts/create_test_review_plan.py <user_uid>
    
Example:
    python scripts/create_test_review_plan.py WLQeYWDnbFbElJRftwr44ZFgcuF2
"""

import sys
import os
from datetime import date, datetime, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


def create_test_plan_for_review(uid: str):
    """Create a test plan for yesterday that needs review."""
    from app.core.database import ActionPlan, ActionPlanItem, UserProfile
    
    session = Session()
    
    try:
        # Check if user exists
        user = session.query(UserProfile).filter(UserProfile.uid == uid).first()
        if not user:
            print(f"❌ User {uid} not found!")
            return False
        
        print(f"✅ Found user: {user.name} ({user.email})")
        
        # Get user's timezone
        user_tz = user.current_timezone or "Asia/Kolkata"
        print(f"📍 User timezone: {user_tz}")
        
        # Calculate yesterday (simple approach - just subtract 1 day)
        yesterday = date.today() - timedelta(days=1)
        
        print(f"📅 Creating plan for yesterday: {yesterday}")
        
        # Check if plan already exists for yesterday
        existing_plan = session.query(ActionPlan).filter(
            ActionPlan.uid == uid,
            ActionPlan.plan_date == yesterday
        ).first()
        
        if existing_plan:
            print(f"⚠️ Plan already exists for {yesterday} (id={existing_plan.id})")
            print(f"   review_completed = {existing_plan.review_completed}")
            
            # Reset review_completed to False to force review
            if existing_plan.review_completed:
                existing_plan.review_completed = False
                session.commit()
                print(f"✅ Reset review_completed to False - review will now appear!")
            else:
                print(f"✅ Plan already needs review - modal should appear!")
            
            # Show items
            items = session.query(ActionPlanItem).filter(
                ActionPlanItem.plan_id == existing_plan.id,
                ActionPlanItem.is_replaced.isnot(True)
            ).all()
            print(f"\n📋 Items in plan:")
            for item in items:
                status = "✅" if item.is_completed else "❌"
                print(f"   {status} {item.title} ({item.category})")
            
            return True
        
        # Create new plan for yesterday
        plan = ActionPlan(
            uid=uid,
            plan_date=yesterday,
            primary_hormone="cortisol",
            cycle_phase="follicular",
            review_completed=False,  # This is key - needs review!
            created_at=datetime.utcnow() - timedelta(days=1)
        )
        session.add(plan)
        session.flush()  # Get the plan ID
        
        print(f"✅ Created plan with id={plan.id}")
        
        # Create 4 test action items (2 complete, 2 incomplete)
        test_items = [
            {
                "slot": 1,
                "title": "Morning Meditation",
                "category": "mindfulness",
                "time_slot": "morning",
                "target_hormone": "cortisol",
                "is_completed": True,  # Completed
                "description": "A calming 10-minute meditation to start your day.",
                "hero_image_url": "https://res.cloudinary.com/dqir7ej4j/image/upload/v1735000000/test_meditation.png"
            },
            {
                "slot": 2,
                "title": "Healthy Breakfast Bowl",
                "category": "food",
                "time_slot": "morning",
                "target_hormone": "insulin",
                "is_completed": False,  # NOT completed - needs review
                "description": "A nutritious breakfast with oats, berries, and nuts.",
                "hero_image_url": "https://res.cloudinary.com/dqir7ej4j/image/upload/v1735000000/test_breakfast.png"
            },
            {
                "slot": 3,
                "title": "Afternoon Walk",
                "category": "movement",
                "time_slot": "afternoon",
                "target_hormone": "cortisol",
                "is_completed": False,  # NOT completed - needs review
                "description": "A 20-minute walk to boost energy and reduce stress.",
                "hero_image_url": "https://res.cloudinary.com/dqir7ej4j/image/upload/v1735000000/test_walk.png"
            },
            {
                "slot": 4,
                "title": "Evening Herbal Tea",
                "category": "food",
                "time_slot": "evening",
                "target_hormone": "insulin",
                "is_completed": True,  # Completed
                "description": "A soothing chamomile tea before bed.",
                "hero_image_url": "https://res.cloudinary.com/dqir7ej4j/image/upload/v1735000000/test_tea.png"
            }
        ]
        
        for item_data in test_items:
            item = ActionPlanItem(
                plan_id=plan.id,
                uid=uid,  # Required field
                slot=item_data["slot"],
                title=item_data["title"],
                category=item_data["category"],
                time_slot=item_data["time_slot"],
                target_hormone=item_data["target_hormone"],
                specific_action=item_data["description"],  # Use description as specific_action
                is_completed=item_data["is_completed"],
                completed_at=datetime.utcnow() - timedelta(days=1) if item_data["is_completed"] else None,
                hero_image_url=item_data["hero_image_url"],
                created_at=datetime.utcnow() - timedelta(days=1)
            )
            session.add(item)
            status = "✅" if item_data["is_completed"] else "❌"
            print(f"   {status} Added: {item_data['title']} ({item_data['category']})")
        
        session.commit()
        
        print(f"\n🎉 SUCCESS! Plan created for {yesterday}")
        print(f"   Plan ID: {plan.id}")
        print(f"   Completed: 2/4 items")
        print(f"   review_completed: False")
        print(f"\n👉 Now open the app - the Daily Review modal should appear!")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
        return False
    finally:
        session.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/create_test_review_plan.py <user_uid>")
        print("\nExample:")
        print("  python scripts/create_test_review_plan.py WLQeYWDnbFbElJRftwr44ZFgcuF2")
        sys.exit(1)
    
    uid = sys.argv[1]
    print(f"🔧 Creating test plan for user: {uid}")
    print("=" * 50)
    
    success = create_test_plan_for_review(uid)
    
    if success:
        print("\n" + "=" * 50)
        print("✅ Done! Refresh the app to see the Daily Review modal.")
    else:
        print("\n❌ Failed to create test plan.")
        sys.exit(1)
