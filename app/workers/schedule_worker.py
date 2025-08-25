import logging
import time
import asyncio
from datetime import datetime
from typing import List
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.services.new_scheduling_service import NewSchedulingService
from app.core.database import RecommendationSchedule

logger = logging.getLogger(__name__)

class ScheduleWorker:
    """스케줄 배치 워커"""
    
    def __init__(self, batch_size: int = 500, sleep_seconds: int = 300):
        """
        Args:
            batch_size: 한 번에 처리할 최대 스케줄 수
            sleep_seconds: 처리 간격 (초)
        """
        self.batch_size = batch_size
        self.sleep_seconds = sleep_seconds
        self.is_running = False
    
    def start(self):
        """워커 시작"""
        logger.info("스케줄 워커 시작")
        self.is_running = True
        
        while self.is_running:
            try:
                self._process_batch()
                time.sleep(self.sleep_seconds)
                
            except KeyboardInterrupt:
                logger.info("워커 중단 요청 받음")
                break
            except Exception as e:
                logger.error(f"워커 실행 중 오류: {str(e)}")
                time.sleep(60)  # 오류 시 1분 대기
        
        logger.info("스케줄 워커 종료")
    
    def stop(self):
        """워커 중지"""
        logger.info("워커 중지 요청")
        self.is_running = False
    
    def _process_batch(self):
        """배치 처리"""
        db = SessionLocal()
        try:
            service = NewSchedulingService(db)
            
            # 실행 예정인 스케줄들 조회
            due_schedules = service.get_due_schedules(self.batch_size)
            
            if not due_schedules:
                logger.debug("처리할 스케줄 없음")
                return
            
            logger.info(f"배치 처리 시작: {len(due_schedules)}개 스케줄")
            
            success_count = 0
            error_count = 0
            
            for schedule in due_schedules:
                try:
                    success = service.process_schedule(schedule)
                    if success:
                        success_count += 1
                    else:
                        error_count += 1
                        
                except Exception as e:
                    logger.error(f"스케줄 처리 실패 (ID: {schedule.id}): {str(e)}")
                    error_count += 1
            
            logger.info(f"배치 처리 완료: 성공={success_count}, 실패={error_count}")
            
        except Exception as e:
            logger.error(f"배치 처리 중 오류: {str(e)}")
        finally:
            db.close()

def run_worker():
    """워커 실행 함수"""
    worker = ScheduleWorker()
    worker.start()

if __name__ == "__main__":
    run_worker()

