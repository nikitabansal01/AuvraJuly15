import sys
import os
from datetime import datetime, timedelta

# Add the project root to sys.path
sys.path.append(os.getcwd())

from app.core.database import SessionLocal, QuestionSession, generate_session_id
from app.services.question_service import QuestionService

def test_session_lifecycle():
    db = SessionLocal()
    service = QuestionService(db)
    
    device_id = "test_device_123"
    uid = "test_user_789"
    name = "Test User"
    email = "test@example.com"
    
    try:
        # 1. Create session
        print("Creating session...")
        session_id = service.create_session(device_id)
        print(f"Session created: {session_id}")
        
        # 2. Verify session is active
        session = service.get_session(session_id)
        assert session is not None
        assert session.status == "active"
        print("Session is active.")
        
        # 3. Save some data
        from app.models.question_models import SessionData
        data = SessionData(age=30, top_concern="Energy")
        service.save_session_data(session_id, data)
        print("Session data saved.")
        
        # 4. Link session to user
        print("Linking session to user...")
        # Note: link_session_to_user involves complex async logic and 
        # external dependencies (Firebase, etc. in some paths).
        # For this test, we'll manually simulate the linking result 
        # to verify get_session logic.
        
        session = db.query(QuestionSession).filter(QuestionSession.session_id == session_id).first()
        session.status = f"linked:{uid}"
        db.commit()
        print(f"Session status updated to linked:{uid}")
        
        # 5. Verify session is still retrievable via get_session
        print("Verifying session retrieval after linking...")
        session_after = service.get_session(session_id)
        
        if session_after is not None:
            print("✅ SUCCESS: Linked session is still retrievable!")
            assert session_after.status == f"linked:{uid}"
        else:
            print("❌ FAILURE: Linked session not found!")
            sys.exit(1)
            
        # 6. Verify expiration still works
        print("Verifying expiration logic...")
        session_after.expires_at = datetime.utcnow() - timedelta(hours=1)
        db.commit()
        
        expired_session = service.get_session(session_id)
        if expired_session is None:
            print("✅ SUCCESS: Expired linked session is not retrieved.")
        else:
            print("❌ FAILURE: Expired session was retrieved!")
            sys.exit(1)
            
    finally:
        # Cleanup
        db.query(QuestionSession).filter(QuestionSession.session_id == session_id).delete()
        db.commit()
        db.close()

if __name__ == "__main__":
    test_session_lifecycle()
