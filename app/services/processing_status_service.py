import logging
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.core.database import SessionProcessingStatus

logger = logging.getLogger(__name__)

class ProcessingStatusService:
    """Session processing status management service"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_processing_status(self, session_id: str, request_payload: Dict[str, Any]) -> SessionProcessingStatus:
        """Create processing status record"""
        # Idempotent create: if record already exists, return existing instead of raising IntegrityError
        existing = self.db.query(SessionProcessingStatus).filter(
            SessionProcessingStatus.session_id == session_id
        ).first()
        if existing:
            logger.info(f"Processing status already exists (idempotent return): {session_id}, status={existing.processing_status}")
            return existing

        processing_status = SessionProcessingStatus(
            session_id=session_id,
            processing_status="queued",
            phase="Queued",
            progress=0,
            message="Waiting for recommendation generation",
            request_payload=request_payload
        )

        try:
            self.db.add(processing_status)
            self.db.commit()
            self.db.refresh(processing_status)
            logger.info(f"Processing status created: {session_id}, status=queued")
            return processing_status
        except Exception as e:
            self.db.rollback()
            # Race condition fallback: try fetch again (another thread may have inserted)
            existing_after = self.db.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.session_id == session_id
            ).first()
            if existing_after:
                logger.warning(f"Processing status create race resolved by returning existing: {session_id}")
                return existing_after
            logger.error(f"Failed to create processing status: {session_id}, error={str(e)}")
            raise
    
    def update_processing_started(self, session_id: str) -> bool:
        """Update to processing started status"""
        try:
            processing_status = self.db.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.session_id == session_id
            ).first()
            
            if processing_status:
                processing_status.processing_status = "in_progress"
                processing_status.phase = "Initializing"
                processing_status.progress = 5
                processing_status.message = "AI recommendation generation started"
                processing_status.started_at = datetime.utcnow()
                processing_status.heartbeat_at = datetime.utcnow()
                
                self.db.commit()
                logger.info(f"Processing started: {session_id}, status=in_progress")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update processing started: {session_id}, error={str(e)}")
            self.db.rollback()
            return False
    
    def update_category_status(self, session_id: str, category: str, status: str, phase: str = None, progress: int = None) -> bool:
        """Update processing status by category"""
        try:
            processing_status = self.db.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.session_id == session_id
            ).first()
            
            if processing_status:
                # Update status by category
                if category == "food":
                    processing_status.food_status = status
                elif category == "movement":
                    processing_status.movement_status = status
                elif category == "mindfulness":
                    processing_status.mindfulness_status = status
                
                # Calculate overall progress
                completed_categories = sum([
                    1 if processing_status.food_status == "completed" else 0,
                    1 if processing_status.movement_status == "completed" else 0,
                    1 if processing_status.mindfulness_status == "completed" else 0
                ])
                
                # Update progress (approximately 33% per category)
                if progress is None:
                    progress = min(100, 5 + (completed_categories * 30))
                
                processing_status.progress = progress
                processing_status.heartbeat_at = datetime.utcnow()
                
                if phase:
                    processing_status.phase = phase
                
                # Update message
                if status == "processing":
                    processing_status.message = f"{category} recommendation generation in progress..."
                elif status == "completed":
                    processing_status.message = f"{category} recommendation completed"
                elif status == "failed":
                    processing_status.message = f"{category} recommendation generation failed"
                
                self.db.commit()
                logger.info(f"Category status updated: {session_id}, {category}={status}, progress={progress}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update category status: {session_id}, {category}, error={str(e)}")
            self.db.rollback()
            return False
    
    def update_processing_completed(self, session_id: str, result: Dict[str, Any] = None) -> bool:
        """Update to processing completed status"""
        try:
            processing_status = self.db.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.session_id == session_id
            ).first()
            
            if processing_status:
                processing_status.processing_status = "completed"
                processing_status.phase = "Completed"
                processing_status.progress = 100
                processing_status.message = "All recommendations generation completed"
                processing_status.finished_at = datetime.utcnow()
                processing_status.heartbeat_at = datetime.utcnow()
                
                if result:
                    processing_status.result = result
                
                self.db.commit()
                logger.info(f"Processing completed: {session_id}, status=completed")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update processing completed: {session_id}, error={str(e)}")
            self.db.rollback()
            return False
    
    def update_processing_failed(self, session_id: str, error: Dict[str, Any] = None) -> bool:
        """Update to processing failed status"""
        try:
            processing_status = self.db.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.session_id == session_id
            ).first()
            
            if processing_status:
                processing_status.processing_status = "failed"
                processing_status.phase = "Failed"
                processing_status.message = "Error occurred during recommendation generation"
                processing_status.finished_at = datetime.utcnow()
                processing_status.heartbeat_at = datetime.utcnow()
                
                if error:
                    processing_status.error = error
                
                self.db.commit()
                logger.error(f"Processing failed: {session_id}, status=failed")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update processing failed: {session_id}, error={str(e)}")
            self.db.rollback()
            return False
    
    def update_heartbeat(self, session_id: str) -> bool:
        """Update heartbeat"""
        try:
            processing_status = self.db.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.session_id == session_id
            ).first()
            
            if processing_status:
                processing_status.heartbeat_at = datetime.utcnow()
                self.db.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update heartbeat: {session_id}, error={str(e)}")
            self.db.rollback()
            return False
    
    def get_processing_status(self, session_id: str) -> Optional[SessionProcessingStatus]:
        """Get processing status"""
        return self.db.query(SessionProcessingStatus).filter(
            SessionProcessingStatus.session_id == session_id
        ).first()
    
    def cleanup_stalled_processing(self, timeout_minutes: int = 30) -> int:
        """Clean up stalled processing status"""
        try:
            from datetime import timedelta
            cutoff_time = datetime.utcnow() - timedelta(minutes=timeout_minutes)
            
            stalled_count = self.db.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.processing_status == "in_progress",
                SessionProcessingStatus.heartbeat_at < cutoff_time
            ).update({
                "processing_status": "stalled",
                "phase": "Stalled",
                "message": "Processing timeout"
            })
            
            self.db.commit()
            logger.info(f"Stalled processing status cleaned up: {stalled_count} records")
            return stalled_count
        except Exception as e:
            logger.error(f"Failed to cleanup stalled processing: {str(e)}")
            self.db.rollback()
            return 0
