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
    """Schedule batch worker"""
    
    def __init__(self, batch_size: int = 500, sleep_seconds: int = 300):
        """
        Args:
            batch_size: Maximum number of schedules to process at once
            sleep_seconds: Processing interval in seconds
        """
        self.batch_size = batch_size
        self.sleep_seconds = sleep_seconds
        self.is_running = False
    
    def start(self):
        """Start the worker"""
        logger.info("Schedule worker started")
        self.is_running = True
        
        while self.is_running:
            try:
                self._process_batch()
                time.sleep(self.sleep_seconds)
                
            except KeyboardInterrupt:
                logger.info("Worker stop request received")
                break
            except Exception as e:
                logger.error(f"Error during worker execution: {str(e)}")
                time.sleep(60)  # Wait 1 minute on error
        
        logger.info("Schedule worker stopped")
    
    def stop(self):
        """Stop the worker"""
        logger.info("Worker stop request")
        self.is_running = False
    
    def _process_batch(self):
        """Process batch of schedules"""
        db = SessionLocal()
        try:
            service = NewSchedulingService(db)
            
            # Get schedules due for execution
            due_schedules = service.get_due_schedules(self.batch_size)
            
            if not due_schedules:
                logger.debug("No schedules to process")
                return
            
            logger.info(f"Batch processing started: {len(due_schedules)} schedules")
            
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
                    logger.error(f"Schedule processing failed (ID: {schedule.id}): {str(e)}")
                    error_count += 1
            
            logger.info(f"Batch processing completed: success={success_count}, failed={error_count}")
            
        except Exception as e:
            logger.error(f"Error during batch processing: {str(e)}")
        finally:
            db.close()

def run_worker():
    """Worker execution function"""
    worker = ScheduleWorker()
    worker.start()

if __name__ == "__main__":
    run_worker()

