import logging
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.core.database import SessionProcessingStatus

logger = logging.getLogger(__name__)

class ProcessingStatusService:
    """세션 처리 상태 관리 서비스"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_processing_status(self, session_id: str, request_payload: Dict[str, Any]) -> SessionProcessingStatus:
        """처리 상태 레코드 생성"""
        processing_status = SessionProcessingStatus(
            session_id=session_id,
            processing_status="queued",
            phase="Queued",
            progress=0,
            message="Waiting for recommendation generation",
            request_payload=request_payload
        )
        
        self.db.add(processing_status)
        self.db.commit()
        self.db.refresh(processing_status)
        
        logger.info(f"처리 상태 생성: {session_id}, status=queued")
        return processing_status
    
    def update_processing_started(self, session_id: str) -> bool:
        """처리 시작 상태로 업데이트"""
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
                logger.info(f"처리 시작: {session_id}, status=in_progress")
                return True
            return False
        except Exception as e:
            logger.error(f"처리 시작 업데이트 실패: {session_id}, error={str(e)}")
            self.db.rollback()
            return False
    
    def update_category_status(self, session_id: str, category: str, status: str, phase: str = None, progress: int = None) -> bool:
        """카테고리별 처리 상태 업데이트"""
        try:
            processing_status = self.db.query(SessionProcessingStatus).filter(
                SessionProcessingStatus.session_id == session_id
            ).first()
            
            if processing_status:
                # 카테고리별 상태 업데이트
                if category == "food":
                    processing_status.food_status = status
                elif category == "movement":
                    processing_status.movement_status = status
                elif category == "mindfulness":
                    processing_status.mindfulness_status = status
                
                # 전체 진행률 계산
                completed_categories = sum([
                    1 if processing_status.food_status == "completed" else 0,
                    1 if processing_status.movement_status == "completed" else 0,
                    1 if processing_status.mindfulness_status == "completed" else 0
                ])
                
                # 진행률 업데이트 (각 카테고리당 약 33%)
                if progress is None:
                    progress = min(100, 5 + (completed_categories * 30))
                
                processing_status.progress = progress
                processing_status.heartbeat_at = datetime.utcnow()
                
                if phase:
                    processing_status.phase = phase
                
                # 메시지 업데이트
                if status == "processing":
                    processing_status.message = f"{category} recommendation generation in progress..."
                elif status == "completed":
                    processing_status.message = f"{category} recommendation completed"
                elif status == "failed":
                    processing_status.message = f"{category} recommendation generation failed"
                
                self.db.commit()
                logger.info(f"카테고리 상태 업데이트: {session_id}, {category}={status}, progress={progress}")
                return True
            return False
        except Exception as e:
            logger.error(f"카테고리 상태 업데이트 실패: {session_id}, {category}, error={str(e)}")
            self.db.rollback()
            return False
    
    def update_processing_completed(self, session_id: str, result: Dict[str, Any] = None) -> bool:
        """처리 완료 상태로 업데이트"""
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
                logger.info(f"처리 완료: {session_id}, status=completed")
                return True
            return False
        except Exception as e:
            logger.error(f"처리 완료 업데이트 실패: {session_id}, error={str(e)}")
            self.db.rollback()
            return False
    
    def update_processing_failed(self, session_id: str, error: Dict[str, Any] = None) -> bool:
        """처리 실패 상태로 업데이트"""
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
                logger.error(f"처리 실패: {session_id}, status=failed")
                return True
            return False
        except Exception as e:
            logger.error(f"처리 실패 업데이트 실패: {session_id}, error={str(e)}")
            self.db.rollback()
            return False
    
    def update_heartbeat(self, session_id: str) -> bool:
        """하트비트 업데이트"""
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
            logger.error(f"하트비트 업데이트 실패: {session_id}, error={str(e)}")
            self.db.rollback()
            return False
    
    def get_processing_status(self, session_id: str) -> Optional[SessionProcessingStatus]:
        """처리 상태 조회"""
        return self.db.query(SessionProcessingStatus).filter(
            SessionProcessingStatus.session_id == session_id
        ).first()
    
    def cleanup_stalled_processing(self, timeout_minutes: int = 30) -> int:
        """정체된 처리 상태 정리"""
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
            logger.info(f"정체된 처리 상태 정리: {stalled_count}개")
            return stalled_count
        except Exception as e:
            logger.error(f"정체된 처리 상태 정리 실패: {str(e)}")
            self.db.rollback()
            return 0
