import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionProcessingStatus, SessionLocal

logger = logging.getLogger(__name__)

class ProcessingStatusService:
    """Session processing status management service"""
    
    def __init__(self, db: Session):
        self.db = db

    def _get_session(self) -> Tuple[Session, bool]:
        """Return a synchronous session, creating one if the provided db is async.

        Returns a tuple of (session, created) where `created` indicates whether
        we opened a new session that should be closed by the caller.
        """
        if isinstance(self.db, AsyncSession):
            # When an AsyncSession is passed, use a short-lived sync session
            # for lightweight status updates to avoid attribute errors.
            return SessionLocal(), True
        return self.db, False
    
    def create_processing_status(self, session_id: str, request_payload: Dict[str, Any]) -> SessionProcessingStatus:
        """Create processing status record"""
        db_session, created = self._get_session()
        # Idempotent create: if record already exists, return existing instead of raising IntegrityError
        existing = db_session.query(SessionProcessingStatus).filter(
            SessionProcessingStatus.session_id == session_id
        ).first()
        if existing:
            logger.info(f"Processing status already exists (idempotent return): {session_id}, status={existing.processing_status}")
            if created:
                db_session.close()
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
            db_session.add(processing_status)
            db_session.commit()
            db_session.refresh(processing_status)
            logger.info(f"Processing status created: {session_id}, status=queued")
            return processing_status
        except Exception as e:
            db_session.rollback()
            # Race condition fallback: try fetch again (another thread may have inserted)
            existing_after = db_session.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.session_id == session_id
            ).first()
            if existing_after:
                logger.warning(f"Processing status create race resolved by returning existing: {session_id}")
                if created:
                    db_session.close()
                return existing_after
            logger.error(f"Failed to create processing status: {session_id}, error={str(e)}")
            raise
        finally:
            if created:
                db_session.close()
    
    def update_processing_started(self, session_id: str) -> bool:
        """Update to processing started status"""
        db_session, created = self._get_session()
        try:
            processing_status = db_session.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.session_id == session_id
            ).first()
            
            if processing_status:
                processing_status.processing_status = "in_progress"
                processing_status.phase = "Initializing"
                processing_status.progress = 5
                processing_status.message = "AI recommendation generation started"
                processing_status.started_at = datetime.utcnow()
                processing_status.heartbeat_at = datetime.utcnow()
                
                db_session.commit()
                logger.info(f"Processing started: {session_id}, status=in_progress")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update processing started: {session_id}, error={str(e)}")
            db_session.rollback()
            return False
        finally:
            if created:
                db_session.close()
    
    def update_category_status(self, session_id: str, category: str, status: str, phase: str = None, progress: int = None) -> bool:
        """Update processing status by category"""
        db_session, created = self._get_session()
        try:
            processing_status = db_session.query(SessionProcessingStatus).filter(
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
                
                db_session.commit()
                logger.info(f"Category status updated: {session_id}, {category}={status}, progress={progress}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update category status: {session_id}, {category}, error={str(e)}")
            db_session.rollback()
            return False
        finally:
            if created:
                db_session.close()
    
    def update_processing_completed(self, session_id: str, result: Dict[str, Any] = None) -> bool:
        """Update to processing completed status"""
        db_session, created = self._get_session()
        try:
            processing_status = db_session.query(SessionProcessingStatus).filter(
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
                
                db_session.commit()
                logger.info(f"Processing completed: {session_id}, status=completed")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update processing completed: {session_id}, error={str(e)}")
            db_session.rollback()
            return False
        finally:
            if created:
                db_session.close()
    
    def update_processing_failed(self, session_id: str, error: Dict[str, Any] = None) -> bool:
        """Update to processing failed status"""
        db_session, created = self._get_session()
        try:
            processing_status = db_session.query(SessionProcessingStatus).filter(
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
                
                db_session.commit()
                logger.error(f"Processing failed: {session_id}, status=failed")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update processing failed: {session_id}, error={str(e)}")
            db_session.rollback()
            return False
        finally:
            if created:
                db_session.close()
    
    def update_heartbeat(self, session_id: str) -> bool:
        """Update heartbeat"""
        db_session, created = self._get_session()
        try:
            processing_status = db_session.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.session_id == session_id
            ).first()
            
            if processing_status:
                processing_status.heartbeat_at = datetime.utcnow()
                db_session.commit()
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update heartbeat: {session_id}, error={str(e)}")
            db_session.rollback()
            return False
        finally:
            if created:
                db_session.close()
    
    def get_processing_status(self, session_id: str) -> Optional[SessionProcessingStatus]:
        """Get processing status"""
        db_session, created = self._get_session()
        try:
            return db_session.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.session_id == session_id
            ).first()
        finally:
            if created:
                db_session.close()
    
    def cleanup_stalled_processing(self, timeout_minutes: int = 30) -> int:
        """Clean up stalled processing status"""
        db_session, created = self._get_session()
        try:
            from datetime import timedelta
            cutoff_time = datetime.utcnow() - timedelta(minutes=timeout_minutes)
            
            stalled_count = db_session.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.processing_status == "in_progress",
                SessionProcessingStatus.heartbeat_at < cutoff_time
            ).update({
                "processing_status": "stalled",
                "phase": "Stalled",
                "message": "Processing timeout"
            })
            
            db_session.commit()
            logger.info(f"Stalled processing status cleaned up: {stalled_count} records")
            return stalled_count
        except Exception as e:
            logger.error(f"Failed to cleanup stalled processing: {str(e)}")
            db_session.rollback()
            return 0
        finally:
            if created:
                db_session.close()
