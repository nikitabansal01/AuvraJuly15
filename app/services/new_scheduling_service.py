import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from app.core.database import (
    RecommendationSchedule, ScheduleRedistribution, DailyAssignment,
    RecommendationRecord, RecommendationAdvice, UserProfile, UserResponse
)
from app.utils.timezone_utils import (
    compute_next_fire_at_utc, get_local_date, should_emit_for_date,
    convert_frequency_detail_to_rrule
)
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

class NewSchedulingService:
    """New timezone-based scheduling service"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create_schedule_from_recommendation(self, recommendation, tzid="Asia/Seoul"):
        """
        Create schedule from recommendation
        
        Args:
            recommendation: RecommendationRecord object
            tzid: Timezone ID (default: Asia/Seoul, priority from UserProfile)
        
        Returns:
            Created RecommendationSchedule object
        """
        try:
            logger.info(f"Schedule creation started: recommendation_id={recommendation.id}, timezone={tzid}")
            
            # Get current_timezone from UserProfile first
            user_profile = self.db.query(UserProfile).filter(UserProfile.uid == recommendation.uid).first()
            if user_profile and user_profile.current_timezone:
                tzid = user_profile.current_timezone
                logger.info(f"Using timezone from UserProfile: {tzid}")
            else:
                logger.info(f"No UserProfile timezone, using default: {tzid}")
            
            # Set start/end dates in UTC
            start_date_utc = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
            end_date_utc = start_date_utc + timedelta(weeks=recommendation.duration_weeks)
            
            # Create RRULE
            rrule_str = self._create_rrule_from_frequency(recommendation.frequency_detail, tzid)
            
            # Calculate next execution time in UTC
            next_fire_at_utc = compute_next_fire_at_utc(tzid, 0, 0)
            
            # Create schedule
            schedule = RecommendationSchedule(
                uid=recommendation.uid,
                recommendation_id=recommendation.id,
                start_date_utc=start_date_utc,
                end_date_utc=end_date_utc,
                next_fire_at_utc=next_fire_at_utc,
                rrule=rrule_str,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            self.db.add(schedule)
            self.db.flush()  # Flush only for ID generation
            
            logger.info(f"Schedule creation completed: schedule_id={schedule.id}, timezone={tzid}")
            return schedule
            
        except Exception as e:
            logger.error(f"Schedule creation failed: {str(e)}")
            raise Exception(f"Schedule creation failed: {str(e)}")
    
    def get_due_schedules(self, limit: int = 500) -> List[RecommendationSchedule]:
        """
        Get schedules due for execution (for batch processing)
        
        Args:
            limit: Maximum number to retrieve
        
        Returns:
            Schedules due for execution
        """
        try:
            now_utc = datetime.utcnow()
            
            schedules = self.db.query(RecommendationSchedule).filter(
                RecommendationSchedule.next_fire_at_utc <= now_utc
            ).order_by(RecommendationSchedule.next_fire_at_utc).limit(limit).all()
            
            return schedules
            
        except Exception as e:
            logger.error(f"Failed to retrieve due schedules: {str(e)}")
            return []
    
    def process_schedule(self, schedule: RecommendationSchedule) -> bool:
        """
        Process individual schedule (for batch worker)
        
        Args:
            schedule: Schedule to process
        
        Returns:
            Processing success status
        """
        try:
            # Get timezone information from UserProfile
            user_profile = self.db.query(UserProfile).filter(UserProfile.uid == schedule.uid).first()
            tzid = user_profile.current_timezone if user_profile and user_profile.current_timezone else "Asia/Seoul"
            
            # Calculate today's local date
            today_local = get_local_date(tzid)
            
            # Idempotency check: Check if already published today (check by DailyAssignment existence)
            existing_assignment = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.schedule_id == schedule.id,
                    DailyAssignment.assignment_date == today_local
                )
            ).first()
            
            if existing_assignment:
                logger.info(f"Already published today: schedule_id={schedule.id}, date={today_local}")
                # Update only next execution time
                self._update_next_fire_time(schedule)
                return True
            
            # Check if should publish today
            should_emit = should_emit_for_date(
                schedule.id, today_local, schedule.rrule, 
                schedule.start_date_utc.date(), schedule.end_date_utc.date() if schedule.end_date_utc else None, self.db
            )
            
            if not should_emit:
                logger.info(f"Not today's target: schedule_id={schedule.id}, date={today_local}")
                # Update only next execution time
                self._update_next_fire_time(schedule)
                return True
            
            # Create daily assignments
            self._create_daily_assignments(schedule, today_local)
            
            # Update next execution time
            self._update_next_fire_time(schedule)
            
            self.db.commit()
            logger.info(f"Schedule processing completed: schedule_id={schedule.id}, date={today_local}")
            return True
            
        except Exception as e:
            logger.error(f"Schedule processing failed: {str(e)}")
            self.db.rollback()
            return False
    
    def _create_daily_assignments(self, schedule: RecommendationSchedule, assignment_date: date):
        """
        Create daily assignments
        
        Args:
            schedule: Schedule
            assignment_date: Assignment date
        """
        try:
            # Get recommendation information
            recommendation = self.db.query(RecommendationRecord).filter(
                RecommendationRecord.id == schedule.recommendation_id
            ).first()
            
            if not recommendation:
                logger.error(f"No recommendation information: recommendation_id={schedule.recommendation_id}")
                return
            
            # Check if already exists (one assignment per recommendation)
            existing = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.schedule_id == schedule.id,
                    DailyAssignment.assignment_date == assignment_date
                )
            ).first()
            
            if existing:
                logger.info(f"Assignment already exists: schedule_id={schedule.id}, date={assignment_date}")
                return
            
            # Determine the best time group based on optimal_times and category
            time_group = self._determine_time_group(recommendation)
            
            # Create new assignment (one assignment per recommendation)
            assignment = DailyAssignment(
                uid=schedule.uid,
                schedule_id=schedule.id,
                recommendation_id=recommendation.id,
                assignment_date=assignment_date,
                time_group=time_group,
                is_completed=False
            )
            
            self.db.add(assignment)
            logger.info(f"Daily assignment creation completed: schedule_id={schedule.id}, date={assignment_date}, time_group={time_group}")
            
        except Exception as e:
            logger.error(f"Daily assignment creation failed: {str(e)}")
            raise
    
    def _determine_time_group(self, recommendation: RecommendationRecord) -> str:
        """
        Determine the best time group for an assignment based on recommendation data.
        
        Priority:
        1. Use optimal_times from recommendation if available
        2. Use category-based defaults
        3. Use title-based inference
        
        Args:
            recommendation: The recommendation record
            
        Returns:
            Time group string: 'morning', 'afternoon', 'evening', or 'anytime'
        """
        # First check optimal_times from recommendation
        optimal_times = recommendation.optimal_times
        if optimal_times and len(optimal_times) > 0:
            # Use the first specified optimal time
            time = optimal_times[0].lower()
            # Normalize time slot names
            time_mapping = {
                'morning': 'morning',
                'afternoon': 'afternoon', 
                'evening': 'evening',
                'night': 'evening',  # Map 'night' to 'evening' for consistency
                'anytime': 'anytime',
            }
            return time_mapping.get(time, 'anytime')
        
        # Infer from title if no optimal_times specified
        title_lower = (recommendation.title or '').lower()
        category = (recommendation.category or '').lower()
        
        # Morning indicators
        morning_keywords = ['breakfast', 'morning', 'seed', 'flaxseed', 'chia', 'oatmeal', 
                           'smoothie', 'juice', 'tea', 'wake', 'start day']
        if any(kw in title_lower for kw in morning_keywords):
            return 'morning'
        
        # Afternoon indicators  
        afternoon_keywords = ['lunch', 'afternoon', 'walk', 'cardio', 'yoga', 'stretch', 
                             'mid-day', 'midday', 'exercise']
        if any(kw in title_lower for kw in afternoon_keywords):
            return 'afternoon'
        
        # Evening indicators
        evening_keywords = ['dinner', 'evening', 'night', 'sleep', 'meditation', 'relax',
                           'wind down', 'bedtime', 'strength', 'resistance']
        if any(kw in title_lower for kw in evening_keywords):
            return 'evening'
        
        # Category-based defaults
        category_defaults = {
            'food': 'morning',       # Most food recommendations work well in morning
            'movement': 'afternoon',  # Exercise typically in afternoon
            'mindfulness': 'evening', # Mindfulness/meditation often in evening
        }
        
        return category_defaults.get(category, 'anytime')
    
    def _update_next_fire_time(self, schedule: RecommendationSchedule):
        """
        Update next execution time
        
        Args:
            schedule: Schedule to update
        """
        try:
            # tzid field removed, need to get timezone information from UserProfile
            user_profile = self.db.query(UserProfile).filter(UserProfile.uid == schedule.uid).first()
            tzid = user_profile.current_timezone if user_profile and user_profile.current_timezone else "Asia/Seoul"
            
            schedule.next_fire_at_utc = compute_next_fire_at_utc(tzid, 0, 0)
            schedule.updated_at = datetime.utcnow()
            
        except Exception as e:
            logger.error(f"Next execution time update failed: {str(e)}")
            raise
    
    def get_user_assignments_for_date(self, uid: str, target_date: date, 
                                    tzid: str = "Asia/Seoul") -> Dict[str, Any]:
        """
        Get user's assignments for specific date (for API)
        
        Args:
            uid: User ID
            target_date: Date to query
            tzid: User timezone
        
        Returns:
            Assignment information
        """
        try:
            # 1. Handle uncompleted assignments from yesterday
            self._handle_uncompleted_assignments(uid, target_date)
            
            # 2. Check and correct active schedules
            self._ensure_schedules_emitted_for_date(uid, target_date, tzid)
            
            # 3. Clean up existing assignments and apply new selection logic
            self._cleanup_and_reselect_assignments(uid, target_date, tzid)
            
            # 3. Query assignments for the date
            assignments = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == uid,
                    DailyAssignment.assignment_date == target_date
                )
            ).all()
            
            # 3. Group by time and separate completed assignments
            # Include both 'evening' and 'night' for frontend compatibility
            time_groups = {
                "morning": [],
                "afternoon": [],
                "evening": [],  # Added for frontend compatibility
                "anytime": []
            }
            
            # Time slot normalization: map 'night' to 'evening' for consistency
            def normalize_time_group(time_group: str) -> str:
                """Normalize time group names for frontend compatibility."""
                normalized = time_group.lower() if time_group else 'anytime'
                if normalized == 'night':
                    return 'evening'
                if normalized not in time_groups:
                    return 'anytime'
                return normalized
            
            completed_group = []  # Store completed assignments separately
            completed_count = 0
            
            for assignment in assignments:
                # Get recommendation information
                recommendation = self.db.query(RecommendationRecord).filter(
                    RecommendationRecord.id == assignment.recommendation_id
                ).first()
                
                if not recommendation:
                    continue
                
                # Debug: Check recommendation data
                logger.debug(f"Recommendation data check: id={recommendation.id}, specific_action={recommendation.specific_action}, research_summary={recommendation.research_summary}")
                
                # Get advice information
                advices = self.db.query(RecommendationAdvice).filter(
                    RecommendationAdvice.recommendation_id == recommendation.id
                ).all()
                
                # Compose assignment information
                assignment_info = {
                    "id": assignment.id,
                    "recommendation_id": recommendation.id,
                    "title": recommendation.title,
                    "purpose": recommendation.purpose,
                    "specific_action": recommendation.specific_action or "",
                    "category": recommendation.category,
                    "conditions": recommendation.conditions or [],
                    "symptoms": recommendation.symptoms or [],
                    "hormones": recommendation.hormones or [],
                    "research_summary": recommendation.research_summary or "",
                    "research_studies": recommendation.research_studies or [],
                    "is_completed": assignment.is_completed,
                    "completed_at": assignment.completed_at.isoformat() if assignment.completed_at else None,
                    "advices": [
                        {
                            "type": advice.advice_type,
                            "category": advice.category,
                            "title": advice.title,
                            "description": advice.description
                        }
                        for advice in advices
                    ]
                }
                
                # Log completion status (for debugging)
                if assignment.is_completed:
                    logger.debug(f"Completed assignment retrieved: assignment_id={assignment.id}, completed_at={assignment.completed_at}")
                
                # Add category-specific details
                if recommendation.category == "food":
                    assignment_info.update({
                        "food_amounts": recommendation.food_amounts or [],
                        "food_items": recommendation.food_items or []
                    })
                elif recommendation.category == "movement":
                    assignment_info.update({
                        "exercise_durations": recommendation.exercise_durations or [],
                        "exercise_types": recommendation.exercise_types or [],
                        "exercise_intensities": recommendation.exercise_intensities or []
                    })
                elif recommendation.category == "mindfulness":
                    assignment_info.update({
                        "mindfulness_durations": recommendation.mindfulness_durations or [],
                        "mindfulness_techniques": recommendation.mindfulness_techniques or []
                    })
                
                # Normalize the time group and add to appropriate group
                normalized_time_group = normalize_time_group(assignment.time_group)
                time_groups[normalized_time_group].append(assignment_info)
                
                if assignment.is_completed:
                    completed_count += 1
            
            # 4. Separate completed assignments into separate section
            completed_group, reorganized_time_groups = self._reorganize_assignments_with_completed_group(time_groups)
            
            # 5. Calculate hormone statistics (pass uid for first-time user support)
            hormone_stats = self._calculate_hormone_stats(uid, assignments)
            
            # Place completed section at the top within assignments
            assignments_with_completed = {
                "completed": completed_group,
                **reorganized_time_groups
            }
            
            return {
                "date": target_date.isoformat(),
                "assignments": assignments_with_completed,
                "total_assignments": len(assignments),
                "completed_assignments": completed_count,
                "completion_rate": (completed_count / len(assignments) * 100) if assignments else 0,
                "hormone_stats": hormone_stats
            }
            
        except Exception as e:
            logger.error(f"User assignment retrieval failed: {str(e)}")
            return {
                "date": target_date.isoformat(),
                "assignments": {
                    "completed": [],
                    "morning": [],
                    "afternoon": [],
                    "evening": [],  # Use 'evening' for frontend compatibility
                    "anytime": []
                },
                "total_assignments": 0,
                "completed_assignments": 0,
                "completion_rate": 0,
                "hormone_stats": {}
            }
    
    def _ensure_schedules_emitted_for_date(self, uid: str, target_date: date, tzid: str):
        """
        Ensure schedules are emitted for specific date (for API correction)
        Select only 3-4 assignments per day
        
        Args:
            uid: User ID
            target_date: Target date
            tzid: User timezone
        """
        try:
            # Query active schedules
            schedules = self.db.query(RecommendationSchedule).filter(
                RecommendationSchedule.uid == uid
            ).all()
            
            # Filter schedules that should emit for the date (considering redistribution)
            eligible_schedules = []
            for schedule in schedules:
                should_emit = self._should_emit_for_date_with_redistribution(
                    schedule, target_date
                )
                
                if should_emit:
                    # Check if assignment already exists
                    existing_assignment = self.db.query(DailyAssignment).filter(
                        and_(
                            DailyAssignment.schedule_id == schedule.id,
                            DailyAssignment.assignment_date == target_date
                        )
                    ).first()
                    
                    if not existing_assignment:
                        eligible_schedules.append(schedule)
            
            # Select only 3-4 and create assignments
            if eligible_schedules:
                # Sort by priority (priority, created_at, etc.)
                selected_schedules = self._select_daily_assignments(eligible_schedules, target_date)
                
                for schedule in selected_schedules:
                    self._create_daily_assignments(schedule, target_date)
                    schedule.updated_at = datetime.utcnow()
                    logger.info(f"Selected assignment created: schedule_id={schedule.id}, date={target_date}")
            
            self.db.commit()
            
        except Exception as e:
            logger.error(f"Schedule emission guarantee failed: {str(e)}")
            self.db.rollback()
    
    def _cleanup_duplicate_assignments(self, uid: str, target_date: date):
        """
        Clean up duplicate assignments (keep only one assignment per schedule)
        
        Args:
            uid: User ID
            target_date: Target date
        """
        try:
            # Query all assignments for the date
            assignments = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == uid,
                    DailyAssignment.assignment_date == target_date
                )
            ).all()
            
            # Group by schedule
            schedule_assignments = {}
            for assignment in assignments:
                if assignment.schedule_id not in schedule_assignments:
                    schedule_assignments[assignment.schedule_id] = []
                schedule_assignments[assignment.schedule_id].append(assignment)
            
            # Keep only first assignment for each schedule and delete the rest
            for schedule_id, assignment_list in schedule_assignments.items():
                if len(assignment_list) > 1:
                    # Keep first assignment, delete the rest
                    for assignment in assignment_list[1:]:
                        self.db.delete(assignment)
                        logger.info(f"Duplicate assignment deleted: assignment_id={assignment.id}, schedule_id={schedule_id}")
            
            self.db.commit()
            
        except Exception as e:
            logger.error(f"Duplicate assignment cleanup failed: {str(e)}")
            self.db.rollback()
    
    def _cleanup_and_reselect_assignments(self, uid: str, target_date: date, tzid: str):
        """
        Clean up existing assignments and apply new selection logic
        
        Args:
            uid: User ID
            target_date: Target date
            tzid: User timezone
        """
        try:
            # 1. Query existing assignments
            existing_assignments = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == uid,
                    DailyAssignment.assignment_date == target_date
                )
            ).all()
            
            # 2. If more than 4 assignments, delete existing assignments
            if len(existing_assignments) > 4:
                logger.info(f"Deleting {len(existing_assignments)} existing assignments for reselection")
                for assignment in existing_assignments:
                    self.db.delete(assignment)
                self.db.commit()
                
                # 3. Recreate assignments with new selection logic (prevent recursion)
                self._create_selected_assignments(uid, target_date, tzid)
            
            # 4. Clean up duplicate assignments (one assignment per schedule only)
            self._cleanup_duplicate_assignments(uid, target_date)
            
        except Exception as e:
            logger.error(f"Assignment cleanup and reselection failed: {str(e)}")
            self.db.rollback()
    
    def _select_daily_assignments(self, eligible_schedules: List[RecommendationSchedule], target_date: date) -> List[RecommendationSchedule]:
        """
        Select assignments to display for the day (3-4)
        Primary/Secondary hormone-based even selection, odd days primary priority
        
        Args:
            eligible_schedules: Schedules to select from
            target_date: Target date
        
        Returns:
            Selected schedules (3-4)
        """
        try:
            if not eligible_schedules:
                return []
            
            # Check current number of assignments
            current_assignments = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == eligible_schedules[0].uid,
                    DailyAssignment.assignment_date == target_date
                )
            ).count()
            
            # Do not add more if already enough
            if current_assignments >= 4:
                logger.info(f"Enough assignments already exist: {current_assignments} assignments")
                return []
            
            # Number of assignments that can be added
            max_new_assignments = 4 - current_assignments
            
            # Get primary/secondary hormone information from UserResponse
            uid = eligible_schedules[0].uid
            user_response = self.db.query(UserResponse).filter(UserResponse.uid == uid).first()
            
            if user_response and user_response.primary_hormone:
                # Primary/Secondary hormone-based even selection
                selected_schedules = self._select_balanced_hormone_assignments(
                    eligible_schedules, target_date, max_new_assignments, 
                    user_response.primary_hormone, user_response.secondary_hormones
                )
            else:
                # Fallback to existing priority method
                sorted_schedules = self._prioritize_schedules(eligible_schedules, target_date)
                selected_schedules = sorted_schedules[:max_new_assignments]
            
            logger.info(f"Assignment selection completed: {len(selected_schedules)} assignments selected (existing {current_assignments} + new {len(selected_schedules)} assignments)")
            return selected_schedules
            
        except Exception as e:
            logger.error(f"Assignment selection failed: {str(e)}")
            return eligible_schedules[:3]  # Default value on error
    
    def _prioritize_schedules(self, schedules: List[RecommendationSchedule], target_date: date) -> List[RecommendationSchedule]:
        """
        Sort schedules by priority
        
        Args:
            schedules: Schedules to sort
            target_date: Target date
        
        Returns:
            Schedules sorted by priority
        """
        try:
            # Compose schedule information with recommendation
            schedule_info = []
            for schedule in schedules:
                recommendation = self.db.query(RecommendationRecord).filter(
                    RecommendationRecord.id == schedule.recommendation_id
                ).first()
                
                if recommendation:
                    # Calculate priority score
                    priority_score = self._calculate_priority_score(recommendation, schedule, target_date)
                    schedule_info.append((schedule, priority_score))
            
            # Sort by priority score (higher score first)
            schedule_info.sort(key=lambda x: x[1], reverse=True)
            
            return [schedule for schedule, score in schedule_info]
            
        except Exception as e:
            logger.error(f"Schedule priority sorting failed: {str(e)}")
            return schedules
    
    def _calculate_priority_score(self, recommendation: RecommendationRecord, schedule: RecommendationSchedule, target_date: date) -> float:
        """
        Calculate priority score (Primary/Secondary hormone-based + lifestyle_focus category weighting)
        
        Args:
            recommendation: Recommendation information
            schedule: Schedule information
            target_date: Target date
        
        Returns:
            Priority score (higher is higher priority)
        """
        try:
            score = 0.0
            
            # 1. Priority (high > medium > low)
            priority_map = {"high": 100, "medium": 50, "low": 10}
            score += priority_map.get(recommendation.priority, 25)
            
            # 2. Recent recommendation priority (freshness)
            days_since_creation = (target_date - recommendation.created_at.date()).days
            freshness_score = max(0, 30 - days_since_creation)  # Max 30 points
            score += freshness_score
            
            # 3. Lifestyle focus category weighting (eat/move/pause preference)
            # Mapping: eat -> food, move -> movement, pause -> mindfulness
            user_profile = self.db.query(UserProfile).filter(
                UserProfile.uid == schedule.uid
            ).first()
            
            if user_profile and user_profile.lifestyle_focus:
                lifestyle_to_category = {
                    'eat': 'food',
                    'move': 'movement', 
                    'pause': 'mindfulness'
                }
                preferred_categories = [lifestyle_to_category.get(lf.lower(), lf.lower()) for lf in user_profile.lifestyle_focus]
                
                rec_category = (recommendation.category or '').lower()
                if rec_category in preferred_categories:
                    # Boost score significantly for user's preferred categories (60 points)
                    score += 60
                    logger.debug(f"Category boost applied: {rec_category} is in preferred {preferred_categories}")
            
            # 4. Hormone importance - ONLY score user's primary and first secondary hormone
            user_response = self.db.query(UserResponse).filter(
                UserResponse.uid == schedule.uid
            ).first()
            
            if user_response and recommendation.hormones:
                # Get user's allowed hormones (max 2: primary + first secondary)
                allowed_hormones = set()
                if user_response.primary_hormone:
                    allowed_hormones.add(user_response.primary_hormone.lower())
                if user_response.secondary_hormones and len(user_response.secondary_hormones) > 0:
                    allowed_hormones.add(user_response.secondary_hormones[0].lower())
                
                for hormone in recommendation.hormones:
                    hormone_lower = hormone.lower()
                    # Only score if hormone is in user's allowed hormones
                    if hormone_lower not in allowed_hormones:
                        continue  # Skip hormones not in user's profile
                    
                    # Primary hormone gets the highest score (50 points)
                    if user_response.primary_hormone and hormone_lower == user_response.primary_hormone.lower():
                        score += 50
                    # Secondary hormone gets medium score (30 points)
                    elif hormone_lower in allowed_hormones:
                        score += 30
            
            return score
            
        except Exception as e:
            logger.error(f"Priority score calculation failed: {str(e)}")
            return 25.0  # Default value
    
    def _select_balanced_hormone_assignments(self, schedules: List[RecommendationSchedule], target_date: date, 
                                           max_count: int, primary_hormone: str, secondary_hormones: List[str]) -> List[RecommendationSchedule]:
        """
        Primary/Secondary hormone-based even selection
        Only considers first secondary hormone (max 2 hormones total)
        Also applies lifestyle_focus category weighting
        
        Args:
            schedules: Schedules to select from
            target_date: Target date  
            max_count: Maximum number of selections
            primary_hormone: Primary hormone
            secondary_hormones: Secondary hormones (only first one used)
        
        Returns:
            Selected schedules
        """
        try:
            # Get lifestyle_focus for category weighting
            uid = schedules[0].uid if schedules else None
            lifestyle_focus = []
            if uid:
                user_profile = self.db.query(UserProfile).filter(UserProfile.uid == uid).first()
                if user_profile and user_profile.lifestyle_focus:
                    lifestyle_focus = user_profile.lifestyle_focus
            
            # Mapping: eat -> food, move -> movement, pause -> mindfulness
            lifestyle_to_category = {'eat': 'food', 'move': 'movement', 'pause': 'mindfulness'}
            preferred_categories = [lifestyle_to_category.get(lf.lower(), lf.lower()) for lf in lifestyle_focus]
            
            # Only use first secondary hormone (max 2 hormones total)
            first_secondary = secondary_hormones[0] if secondary_hormones else None
            
            # Classify recommendations by hormone
            primary_schedules = []
            secondary_schedules = []
            
            for schedule in schedules:
                recommendation = self.db.query(RecommendationRecord).filter(
                    RecommendationRecord.id == schedule.recommendation_id
                ).first()
                
                if not recommendation or not recommendation.hormones:
                    continue  # Skip recommendations without hormones (only user's hormones matter)
                
                # Convert hormone names to lowercase for comparison
                rec_hormones = [h.lower() for h in recommendation.hormones]
                
                # Check if it's a primary hormone-related recommendation
                if primary_hormone.lower() in rec_hormones:
                    primary_schedules.append(schedule)
                # Check if it's the first secondary hormone-related recommendation
                elif first_secondary and first_secondary.lower() in rec_hormones:
                    secondary_schedules.append(schedule)
            
            # Sort each category by priority (lifestyle_focus weighting applied in _prioritize_schedules -> _calculate_priority_score)
            primary_schedules = self._prioritize_schedules(primary_schedules, target_date)
            secondary_schedules = self._prioritize_schedules(secondary_schedules, target_date)
            
            # Apply lifestyle_focus category preference within each hormone group
            # Move preferred category recommendations to front
            if preferred_categories:
                def sort_by_category_preference(scheds):
                    preferred = []
                    others = []
                    for s in scheds:
                        rec = self.db.query(RecommendationRecord).filter(RecommendationRecord.id == s.recommendation_id).first()
                        if rec and rec.category and rec.category.lower() in preferred_categories:
                            preferred.append(s)
                        else:
                            others.append(s)
                    return preferred + others
                
                primary_schedules = sort_by_category_preference(primary_schedules)
                secondary_schedules = sort_by_category_preference(secondary_schedules)
            
            # Even selection logic - ONLY from primary and secondary, no "other" hormones
            selected = []
            
            if max_count <= 0:
                return selected
            
            # Ensure primary and secondary hormone-related recommendations are each included at least once
            if primary_schedules and len(selected) < max_count:
                selected.append(primary_schedules[0])
                primary_schedules = primary_schedules[1:]
            
            if secondary_schedules and len(selected) < max_count:
                selected.append(secondary_schedules[0])
                secondary_schedules = secondary_schedules[1:]
            
            # Distribute the remaining slots between primary and secondary only
            # If an odd number remains, prioritize primary
            remaining_count = max_count - len(selected)
            
            while remaining_count > 0:
                added_in_round = False
                
                # Add primary (prioritize)
                if primary_schedules and remaining_count > 0:
                    selected.append(primary_schedules[0])
                    primary_schedules = primary_schedules[1:]
                    remaining_count -= 1
                    added_in_round = True
                
                # Add secondary
                if secondary_schedules and remaining_count > 0:
                    selected.append(secondary_schedules[0])
                    secondary_schedules = secondary_schedules[1:]
                    remaining_count -= 1
                    added_in_round = True
                
                # If no more recommendations to add, end (no other hormones used)
                if not added_in_round:
                    break
            
            logger.info(f"Balanced selection completed: primary={len([s for s in selected if self._is_primary_hormone_schedule(s, primary_hormone)])}, " +
                       f"secondary={len([s for s in selected if self._is_secondary_hormone_schedule(s, [first_secondary] if first_secondary else [])])}, " +
                       f"preferred_categories={preferred_categories}, total={len(selected)}")
            
            return selected
            
        except Exception as e:
            logger.error(f"Even selection failed: {str(e)}")
            # Fallback to default priority method on error
            sorted_schedules = self._prioritize_schedules(schedules, target_date)
            return sorted_schedules[:max_count]
    
    def _is_primary_hormone_schedule(self, schedule: RecommendationSchedule, primary_hormone: str) -> bool:
        """Check if schedule is related to primary hormone"""
        try:
            recommendation = self.db.query(RecommendationRecord).filter(
                RecommendationRecord.id == schedule.recommendation_id
            ).first()
            
            if not recommendation or not recommendation.hormones:
                return False
                
            return primary_hormone.lower() in [h.lower() for h in recommendation.hormones]
        except:
            return False
    
    def _is_secondary_hormone_schedule(self, schedule: RecommendationSchedule, secondary_hormones: List[str]) -> bool:
        """Check if schedule is related to secondary hormone"""
        try:
            if not secondary_hormones:
                return False
                
            recommendation = self.db.query(RecommendationRecord).filter(
                RecommendationRecord.id == schedule.recommendation_id
            ).first()
            
            if not recommendation or not recommendation.hormones:
                return False
                
            rec_hormones = [h.lower() for h in recommendation.hormones]
            return any(sh.lower() in rec_hormones for sh in secondary_hormones)
        except:
            return False
    
    def _create_selected_assignments(self, uid: str, target_date: date, tzid: str):
        """
        Create only selected assignments (prevent recursion)
        
        Args:
            uid: User ID
            target_date: Target date
            tzid: User timezone
        """
        try:
            # Query active schedules
            schedules = self.db.query(RecommendationSchedule).filter(
                RecommendationSchedule.uid == uid
            ).all()
            
            # Filter schedules that should emit for the date
            eligible_schedules = []
            for schedule in schedules:
                should_emit = should_emit_for_date(
                    schedule.id, target_date, schedule.rrule,
                    schedule.start_date_utc.date(), schedule.end_date_utc.date() if schedule.end_date_utc else None, self.db
                )
                
                if should_emit:
                    eligible_schedules.append(schedule)
            
            # Select only 3-4 and create assignments
            if eligible_schedules:
                selected_schedules = self._select_daily_assignments(eligible_schedules, target_date)
                
                for schedule in selected_schedules:
                    self._create_daily_assignments(schedule, target_date)
                    schedule.updated_at = datetime.utcnow()
                    logger.info(f"Re-selected assignment created: schedule_id={schedule.id}, date={target_date}")
            
            self.db.commit()
            
        except Exception as e:
            logger.error(f"Selected assignment creation failed: {str(e)}")
            self.db.rollback()
    
    def _calculate_hormone_stats(self, uid: str, assignments: List[DailyAssignment]) -> Dict[str, Any]:
        """
        Calculate hormone statistics for the Hormone Quests display.
        
        IMPORTANT: This function calculates the data shown in the "Your Hormone Quests" 
        section on the home screen. It needs to properly show the user's targeted hormones
        with their completion progress.
        
        Args:
            uid: User ID (needed for first-time users with no assignments)
            assignments: List of assignments
        
        Returns:
            Hormone statistics dict: {"hormone_name": {"total": N, "completed": M}, ...}
        """
        try:
            hormone_stats = {}
            
            # Get user's primary and secondary hormones from UserResponse
            # This works even for first-time users with no assignments
            user_hormones = None
            user_response = self.db.query(UserResponse).filter(UserResponse.uid == uid).first()
            if user_response:
                user_hormones = {
                    'primary': user_response.primary_hormone,
                    'secondary': user_response.secondary_hormones or []
                }
            
            # Use user's hormones only - single hormone per recommendation
            def get_default_hormone(category: str, recommendation_index: int = 0) -> List[str]:
                """Get default hormone based on user's hormone profile.
                
                FIXED: Returns only ONE hormone per recommendation.
                Alternates between primary and secondary to distribute assignments.
                """
                if user_hormones and user_hormones['primary']:
                    primary = user_hormones['primary']
                    secondary_list = user_hormones['secondary'] or []
                    
                    # If we have secondary hormones, alternate between primary and secondary
                    if secondary_list:
                        # Use index to distribute: even indices get primary, odd get secondary
                        if recommendation_index % 2 == 0:
                            return [primary]
                        else:
                            return [secondary_list[0]]
                    else:
                        # Only primary hormone
                        return [primary]
                
                # Minimal fallback - single hormone
                return ['progesterone']
            
            for idx, assignment in enumerate(assignments):
                recommendation = self.db.query(RecommendationRecord).filter(
                    RecommendationRecord.id == assignment.recommendation_id
                ).first()
                
                if not recommendation:
                    continue
                
                # Get hormones from recommendation, or use smart defaults
                hormones = recommendation.hormones
                if not hormones:
                    cat = recommendation.category or 'food'
                    hormones = get_default_hormone(cat, idx)  # FIXED: Use index for alternation
                    logger.debug(f"Using smart default hormone for recommendation {recommendation.id}: {hormones}")
                
                # FILTER: Only count hormones that match user's primary/secondary
                # This prevents showing unrelated hormones in the UI
                allowed_hormones = set()
                if user_hormones:
                    if user_hormones['primary']:
                        allowed_hormones.add(user_hormones['primary'].lower())
                    for sh in (user_hormones['secondary'] or []):
                        allowed_hormones.add(sh.lower())
                
                for hormone in hormones:
                    # Normalize hormone name (lowercase for consistency)
                    hormone_normalized = hormone.lower()
                    
                    # Only count if it's in user's allowed hormones, or if no user hormones defined
                    if allowed_hormones and hormone_normalized not in allowed_hormones:
                        continue
                    
                    if hormone_normalized not in hormone_stats:
                        hormone_stats[hormone_normalized] = {"total": 0, "completed": 0}
                    
                    hormone_stats[hormone_normalized]["total"] += 1
                    if assignment.is_completed:
                        hormone_stats[hormone_normalized]["completed"] += 1
            
            # Ensure we always have at least the user's primary/secondary hormones shown
            if user_hormones:
                if user_hormones['primary'] and user_hormones['primary'].lower() not in hormone_stats:
                    hormone_stats[user_hormones['primary'].lower()] = {"total": 0, "completed": 0}
                for sh in (user_hormones['secondary'] or []):
                    if sh.lower() not in hormone_stats:
                        hormone_stats[sh.lower()] = {"total": 0, "completed": 0}
            
            logger.info(f"Calculated hormone stats: {hormone_stats}")
            return hormone_stats
            
        except Exception as e:
            logger.error(f"Hormone statistics calculation failed: {str(e)}")
            return {}
    
    def _reorganize_assignments_with_completed_group(self, time_groups: Dict[str, List[Dict[str, Any]]]) -> tuple:
        """
        Reorganize assignments to separate completed ones into a separate group
        - Completed assignments within a time group are kept at the front
        - Assignments where the previous time group was incomplete and the next one is completed are moved to the completed section
        
        Args:
            time_groups: Time group assignments
            
        Returns:
            (completed_group, reorganized_time_groups)
        """
        try:
            completed_group = []
            reorganized_time_groups = {
                "morning": [],
                "afternoon": [], 
                "evening": [],  # Use 'evening' for frontend compatibility
                "anytime": []
            }
            
            # Define time order - use evening (which maps from night)
            time_order = ['morning', 'afternoon', 'evening', 'anytime']
            
            # Check if any incomplete assignments exist in previous time groups
            has_incomplete_before = {}  # Check if incomplete assignments exist in previous time groups
            current_has_incomplete = False
            
            for time_group in time_order:
                items = time_groups.get(time_group, [])
                has_incomplete_before[time_group] = current_has_incomplete
                
                # Check if any incomplete assignments exist in the current time group
                has_incomplete_in_current = any(not item["is_completed"] for item in items)
                current_has_incomplete = current_has_incomplete or has_incomplete_in_current
            
            # Reorganize assignments
            for time_group in time_order:
                items = time_groups.get(time_group, [])
                
                for item in items:
                    if item["is_completed"]:
                        # If previous time group had incomplete assignments and current one is completed, move to completed section
                        if has_incomplete_before[time_group]:
                            completed_group.append(item)
                        else:
                            # Otherwise, keep in original time group (sorted at the front)
                            reorganized_time_groups[time_group].append(item)
                    else:
                        # Incomplete assignments are added to original time group
                        reorganized_time_groups[time_group].append(item)
            
            return completed_group, reorganized_time_groups
            
        except Exception as e:
            logger.error(f"Assignment reorganization failed: {str(e)}")
            return [], time_groups
    
    def mark_assignment_completed(self, assignment_id: int, uid: str, 
                                notes: Optional[str] = None) -> bool:
        """
        Mark assignment as completed
        
        Args:
            assignment_id: Assignment ID
            uid: User ID
            notes: Notes
        
        Returns:
            Success status
        """
        try:
            assignment = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.id == assignment_id,
                    DailyAssignment.uid == uid
                )
            ).first()
            
            if not assignment:
                logger.error(f"Assignment not found: assignment_id={assignment_id}, uid={uid}")
                return False
            
            assignment.is_completed = True
            assignment.completed_at = datetime.utcnow()
            assignment.notes = notes
            assignment.updated_at = datetime.utcnow()
            
            self.db.commit()
            logger.info(f"Assignment marked as completed: assignment_id={assignment_id}, uid={uid}, is_completed={assignment.is_completed}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to mark assignment as completed: {str(e)}")
            self.db.rollback()
            return False
    
    def create_redistribution(self, schedule_id: int, original_date: date, 
                            override_date: date, reason: str, 
                            source: str = "system") -> bool:
        """
        Create redistribution information
        
        Args:
            schedule_id: Schedule ID
            original_date: Original date
            override_date: Redistribution date
            reason: Redistribution reason
            source: Redistribution source
        
        Returns:
            Success status
        """
        try:
            redistribution = ScheduleRedistribution(
                schedule_id=schedule_id,
                original_date=original_date,
                override_date=override_date,
                reason=reason,
                source=source
            )
            
            self.db.add(redistribution)
            self.db.commit()
            
            logger.info(f"Redistribution information created: schedule_id={schedule_id}, original={original_date}, override={override_date}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create redistribution information: {str(e)}")
            self.db.rollback()
            return False

    def _create_rrule_from_frequency(self, frequency_detail: str, tzid: str) -> str:
        """
        Convert frequency_detail to RRULE
        
        Args:
            frequency_detail: Frequency detail (e.g., "daily:1", "weekly:3")
            tzid: Timezone ID
        
        Returns:
            RRULE string
        """
        try:
            # Parse frequency_detail
            if ":" not in frequency_detail:
                logger.warning(f"Invalid frequency_detail format: {frequency_detail}")
                return "FREQ=DAILY;INTERVAL=1"
            
            freq_type, times = frequency_detail.split(":")
            times = int(times)
            
            # Create RRULE
            if freq_type.lower() == "daily":
                return f"FREQ=DAILY;INTERVAL={max(1, 7 // times)}"
            elif freq_type.lower() == "weekly":
                return f"FREQ=WEEKLY;INTERVAL={max(1, 4 // times)}"
            elif freq_type.lower() == "monthly":
                return f"FREQ=MONTHLY;INTERVAL={max(1, 12 // times)}"
            else:
                logger.warning(f"Unknown frequency type: {freq_type}")
                return "FREQ=DAILY;INTERVAL=1"
                
        except Exception as e:
            logger.error(f"RRULE creation failed: {str(e)}")
            return "FREQ=DAILY;INTERVAL=1"
    
    def _handle_uncompleted_assignments(self, uid: str, target_date: date):
        """
        Redistribute uncompleted assignments from yesterday
        
        Args:
            uid: User ID
            target_date: Target date (today)
        """
        try:
            # Calculate yesterday's date
            yesterday = target_date - timedelta(days=1)
            
            # Find uncompleted assignments from yesterday
            yesterday_assignments = self.db.query(DailyAssignment).filter(
                and_(
                    DailyAssignment.uid == uid,
                    DailyAssignment.assignment_date == yesterday,
                    DailyAssignment.is_completed == False
                )
            ).all()
            
            if not yesterday_assignments:
                return  # No uncompleted assignments from yesterday
            
            logger.info(f"Found {len(yesterday_assignments)} uncompleted assignments from yesterday: uid={uid}, date={yesterday}")
            
            # Apply redistribution logic for each uncompleted assignment
            for assignment in yesterday_assignments:
                self._redistribute_uncompleted_assignment(assignment, target_date)
                
        except Exception as e:
            logger.error(f"Failed to redistribute uncompleted assignments: {str(e)}")
    
    def _redistribute_uncompleted_assignment(self, assignment: DailyAssignment, target_date: date):
        """
        Redistribute a specific uncompleted assignment
        
        Args:
            assignment: Uncompleted assignment
            target_date: Target date for redistribution
        """
        try:
            # Get recommendation information
            recommendation = self.db.query(RecommendationRecord).filter(
                RecommendationRecord.id == assignment.recommendation_id
            ).first()
            
            if not recommendation:
                logger.warning(f"No recommendation information: recommendation_id={assignment.recommendation_id}")
                return
            
            # Parse frequency_detail
            frequency_info = self._parse_frequency_detail(recommendation.frequency_detail)
            if not frequency_info:
                logger.warning(f"Failed to parse frequency_detail: {recommendation.frequency_detail}")
                return
            
            # Do not redistribute daily assignments (they appear daily)
            if frequency_info.get('type') == 'daily':
                logger.info(f"Daily assignment not redistributed: assignment_id={assignment.id}")
                return
            
            # Calculate remaining duration
            remaining_days = self._calculate_remaining_days(recommendation, target_date)
            if remaining_days <= 0:
                logger.info(f"Assignment duration expired: assignment_id={assignment.id}")
                return
            
            # Calculate redistributed dates
            redistributed_dates = self._calculate_redistributed_dates(
                recommendation, frequency_info, target_date, remaining_days
            )
            
            if not redistributed_dates:
                logger.warning(f"Failed to calculate redistributed dates: assignment_id={assignment.id}")
                return
            
            # Save redistribution information
            self._save_redistribution_info(assignment, redistributed_dates)
            
            logger.info(f"Assignment redistribution completed: assignment_id={assignment.id}, dates={[d.isoformat() for d in redistributed_dates]}")
            
        except Exception as e:
            logger.error(f"Assignment redistribution failed: assignment_id={assignment.id}, error={str(e)}")
    
    def _parse_frequency_detail(self, frequency_detail: str) -> Optional[Dict[str, Any]]:
        """
        Parse frequency_detail
        
        Args:
            frequency_detail: Frequency detail (e.g., "daily:1", "weekly:3")
            
        Returns:
            Parsed frequency information
        """
        if not frequency_detail or ':' not in frequency_detail:
            return None
        
        try:
            freq_type, times_str = frequency_detail.split(':', 1)
            times = int(times_str)
            
            return {
                'type': freq_type.lower(),
                'times': times,
                'description': frequency_detail
            }
        except (ValueError, AttributeError):
            return None
    
    def _calculate_remaining_days(self, recommendation: RecommendationRecord, target_date: date) -> int:
        """
        Calculate remaining duration of recommendation
        
        Args:
            recommendation: Recommendation information
            target_date: Baseline date
            
        Returns:
            Remaining days
        """
        try:
            if not recommendation.duration_weeks:
                return 365  # Assume 1 year if duration is not available
                
            created_date = recommendation.created_at.date()
            end_date = created_date + timedelta(weeks=recommendation.duration_weeks)
            
            # If target_date exceeds end_date, return 0
            if target_date >= end_date:
                return 0
            
            return (end_date - target_date).days
            
        except Exception as e:
            logger.error(f"Remaining days calculation failed: {str(e)}")
            return 0
    
    def _calculate_redistributed_dates(self, recommendation: RecommendationRecord, frequency_info: Dict[str, Any], 
                                     target_date: date, remaining_days: int) -> List[date]:
        """
        Calculate redistributed dates
        
        Args:
            recommendation: Recommendation information
            frequency_info: Frequency information
            target_date: Start date
            remaining_days: Remaining days
            
        Returns:
            List of redistributed dates
        """
        try:
            freq_type = frequency_info['type']
            times = frequency_info['times']
            
            if freq_type == 'weekly':
                # Weekly unit: Distribute remaining days evenly into times
                if remaining_days < times:
                    # If remaining days are less than times, distribute daily
                    return [target_date + timedelta(days=i) for i in range(remaining_days)]
                else:
                    # Distribute evenly
                    interval = remaining_days // times
                    dates = []
                    for i in range(times):
                        day_offset = i * interval
                        if day_offset < remaining_days:
                            dates.append(target_date + timedelta(days=day_offset))
                    return dates
                    
            elif freq_type == 'monthly':
                # Monthly unit: Distribute remaining days evenly into times
                if remaining_days < times:
                    # If remaining days are less than times, distribute daily
                    return [target_date + timedelta(days=i) for i in range(remaining_days)]
                else:
                    # Distribute evenly
                    interval = remaining_days // times
                    dates = []
                    for i in range(times):
                        day_offset = i * interval
                        if day_offset < remaining_days:
                            dates.append(target_date + timedelta(days=day_offset))
                    return dates
            
            return []
            
        except Exception as e:
            logger.error(f"Redistributed date calculation failed: {str(e)}")
            return []
    
    def _save_redistribution_info(self, assignment: DailyAssignment, redistributed_dates: List[date]):
        """
        Save redistribution information
        
        Args:
            assignment: Original assignment
            redistributed_dates: Redistributed dates
        """
        try:
            # Create ScheduleRedistribution record for each redistributed date
            for redistributed_date in redistributed_dates:
                redistribution = ScheduleRedistribution(
                    schedule_id=assignment.schedule_id,
                    original_date=assignment.assignment_date,
                    override_date=redistributed_date,
                    reason="uncompleted",
                    source="system"
                )
                
                self.db.add(redistribution)
            
            self.db.commit()
            logger.info(f"Redistribution information saved: schedule_id={assignment.schedule_id}, dates={len(redistributed_dates)}")
            
        except Exception as e:
            logger.error(f"Failed to save redistribution information: {str(e)}")
            self.db.rollback()
    
    def _should_emit_for_date_with_redistribution(self, schedule: RecommendationSchedule, target_date: date) -> bool:
        """
        Check if a schedule should be executed for a specific date, considering redistribution
        
        Args:
            schedule: Schedule information
            target_date: Date to check
            
        Returns:
            Execution status
        """
        try:
            # 1. Basic RRULE check
            should_emit_original = should_emit_for_date(
                schedule.id, target_date, schedule.rrule,
                schedule.start_date_utc.date(), 
                schedule.end_date_utc.date() if schedule.end_date_utc else None, 
                self.db
            )
            
            # 2. Check redistribution information
            redistribution = self.db.query(ScheduleRedistribution).filter(
                and_(
                    ScheduleRedistribution.schedule_id == schedule.id,
                    ScheduleRedistribution.override_date == target_date
                )
            ).first()
            
            # If redistribution information exists, execute on that date
            if redistribution:
                logger.info(f"Redistributed assignment found: schedule_id={schedule.id}, date={target_date}, original_date={redistribution.original_date}")
                return True
            
            # If it was redistributed from today's date, do not execute on today's date
            if should_emit_original:
                redistributed_from_today = self.db.query(ScheduleRedistribution).filter(
                    and_(
                        ScheduleRedistribution.schedule_id == schedule.id,
                        ScheduleRedistribution.original_date == target_date
                    )
                ).first()
                
                if redistributed_from_today:
                    logger.info(f"Assignment redistributed from today's date: schedule_id={schedule.id}, date={target_date}")
                    return False
            
            return should_emit_original
            
        except Exception as e:
            logger.error(f"Failed to check execution status considering redistribution: {str(e)}")
            # Fallback to default RRULE on error
            return should_emit_for_date(
                schedule.id, target_date, schedule.rrule,
                schedule.start_date_utc.date(), 
                schedule.end_date_utc.date() if schedule.end_date_utc else None, 
                self.db
            )

