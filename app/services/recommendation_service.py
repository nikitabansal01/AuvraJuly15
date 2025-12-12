from sqlalchemy.orm import Session
from app.core.database import RecommendationRecord
from app.models.ai_models import RecommendationResult, RecommendationCard, UserProfile
from app.services.advice_service import AdviceService
from app.services.ai_service import AIService
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class RecommendationService:
    def __init__(self, db: Session):
        self.db = db
    
    async def generate_and_save_session_recommendations(self, session_id: str, user_profile: Dict[str, Any], category: str) -> bool:
        """
        Generate and save session recommendations (for temporary sessions)
        """
        try:
            print("=" * 60)
            print(f"🚀 [REC SERVICE] Starting {category.upper()}")
            print(f"   Session: {session_id}")
            print("=" * 60)
            logger.info("=" * 60)
            logger.info(f"🚀 RECOMMENDATION SERVICE: Starting {category.upper()}")
            logger.info(f"   Session: {session_id}")
            logger.info("=" * 60)
            
            # Create UserProfile object
            user_profile_obj = UserProfile(**user_profile)
            print(f"✅ [REC SERVICE] UserProfile created, calling AIService.generate_session_recommendations")
            logger.info(f"✅ UserProfile created, calling AIService.generate_session_recommendations")
            
            # Extract user's hormone info for fallback
            user_hormones = {
                'primaryImbalance': user_profile_obj.primaryImbalance,
                'secondaryImbalances': user_profile_obj.secondaryImbalances or []
            }
            
            # Generate AI recommendations (optimized for sessions)
            recommendations = await AIService.generate_session_recommendations(user_profile_obj, category)
            
            if not recommendations:
                print(f"⚠️ [REC SERVICE] No recommendations returned for {category}")
                logger.warning(f"⚠️ RECOMMENDATION SERVICE: No recommendations returned for {category}")
                return False
            
            print(f"✅ [REC SERVICE] Got {len(recommendations)} recommendations for {category}")
            logger.info(f"✅ RECOMMENDATION SERVICE: Got {len(recommendations)} recommendations for {category}")
            
            # Save each recommendation for session with user's hormone info
            for rec in recommendations:
                await self._save_single_session_recommendation(session_id, rec, category, user_hormones)
            
            logger.info(f"Session recommendation generation completed: {session_id}, {category}")
            return True
            
        except Exception as e:
            logger.error(f"Error during session recommendation generation: {str(e)}", exc_info=True)
            return False
    
    async def _save_single_session_recommendation(self, session_id: str, rec: Dict[str, Any], category: str, user_hormones: Dict[str, Any] = None) -> bool:
        """
        Save single session recommendation to DB (temporary)
        
        Args:
            session_id: Session ID
            rec: Recommendation data
            category: food/movement/mindfulness
            user_hormones: User's hormone info (primary_hormone, secondary_hormones)
        """
        try:
            # Get user's allowed hormones (max 2: primary + first secondary)
            allowed_hormones = []
            if user_hormones:
                primary = user_hormones.get('primary_hormone') or user_hormones.get('primaryImbalance')
                secondary = user_hormones.get('secondary_hormones') or user_hormones.get('secondaryImbalances') or []
                if primary:
                    allowed_hormones.append(primary.lower())
                    if secondary and len(secondary) > 0:
                        allowed_hormones.append(secondary[0].lower())  # Max 1 secondary
            
            # Filter hormones to ONLY user's allowed hormones
            rec_hormones = rec.get('hormones', [])
            if rec_hormones and allowed_hormones:
                # Only keep hormones that are in user's allowed list
                hormones = [h for h in rec_hormones if h.lower() in allowed_hormones]
            else:
                hormones = []
            
            # If no matching hormones, use user's hormones directly
            if not hormones:
                if allowed_hormones:
                    hormones = [h.title() for h in allowed_hormones]  # Capitalize
                else:
                    hormones = ['progesterone']  # Minimal fallback
            
            # Ensure optimal_times is set - use defaults if not provided
            optimal_times = rec.get('optimal_times', [])
            if not optimal_times:
                # Default optimal times based on category
                category_default_times = {
                    'food': ['morning'],
                    'movement': ['afternoon'],
                    'mindfulness': ['evening'],
                }
                optimal_times = category_default_times.get(category.lower(), ['anytime'])
            
            # Create DB record (linked by session_id, uid is NULL)
            db_record = RecommendationRecord(
                session_id=session_id,
                uid=None,  # NULL for temporary sessions
                recommendation_type="general",
                category=category,
                confidence=None,
                generated_at=None,
                
                # Recommendation card information
                title=rec.get('title'),
                purpose=rec.get('purpose'),
                specific_action=rec.get('specificAction'),
                priority=rec.get('priority'),
                contraindications=rec.get('contraindications'),
                
                # Tag information
                conditions=rec.get('conditions', []),
                symptoms=rec.get('symptoms', []),
                hormones=hormones,  # Use our ensured hormones
                
                # Array fields
                food_amounts=rec.get('food_amounts', []),
                food_items=rec.get('food_items', []),
                exercise_durations=rec.get('exercise_durations', []),
                exercise_types=rec.get('exercise_types', []),
                exercise_intensities=rec.get('exercise_intensities', []),
                mindfulness_durations=rec.get('mindfulness_durations', []),
                mindfulness_techniques=rec.get('mindfulness_techniques', []),
                frequency_detail=rec.get('frequency_detail', 'daily:1'),
                duration_weeks=rec.get('duration_weeks', 8),
                optimal_times=optimal_times,  # Use our ensured optimal_times
                
                # Research backing
                research_summary=rec.get('researchBacking', {}).get('summary') if rec.get('researchBacking') else None,
                research_studies=rec.get('researchBacking', {}).get('studies') if rec.get('researchBacking') else None,
                
                # User profile snapshot
                user_profile_snapshot=None
            )
            
            self.db.add(db_record)
            self.db.flush()  # Generate ID
            
            # Generate and save advice
            advice_service = AdviceService(self.db)
            await advice_service.generate_and_save_session_advices(
                recommendation_id=db_record.id,
                session_id=session_id,
                recommendation_data=rec,
                category=category
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save session recommendation: {str(e)}", exc_info=True)
            return False
    
    async def save_recommendations(self, uid: str, result: RecommendationResult, recommendation_type: str = "general") -> bool:
        """
        Save recommendation results to DB
        """
        try:
            logger.info(f"Recommendation result saving started: uid={uid}, type={recommendation_type}")
            
            # Save recommendations by category
            categories = {
                "food": result.food,
                "movement": result.movement,
                "mindfulness": result.mindfulness
            }
            
            # Save recommendations in parallel (by category)
            import asyncio
            tasks = []
            for category, recommendations in categories.items():
                for rec in recommendations:
                    task = self._save_single_recommendation(uid, rec, category, recommendation_type, result)
                    tasks.append(task)
            
            # Process all recommendations in parallel
            results = await asyncio.gather(*tasks, return_exceptions=True)
            saved_count = sum(1 for result in results if result is True)
            
            self.db.commit()
            logger.info(f"Recommendation result saving completed: {saved_count} saved")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to save recommendation results: {str(e)}")
            return False
    
    async def _save_single_recommendation(self, uid: str, rec: RecommendationCard, category: str, 
                                        recommendation_type: str, result: RecommendationResult) -> bool:
        """
        Save single recommendation to DB
        """
        try:
            # Extract research backing information
            research_summary = None
            research_studies = None
            if rec.researchBacking:
                research_summary = rec.researchBacking.summary
                research_studies = rec.researchBacking.studies
            
            # User profile snapshot
            user_profile_snapshot = None
            if result.userProfile:
                user_profile_snapshot = result.userProfile.dict()
            
            # Get user's allowed hormones (max 2: primary + first secondary)
            allowed_hormones = []
            if result.userProfile:
                primary = result.userProfile.primaryImbalance
                secondary = result.userProfile.secondaryImbalances or []
                if primary:
                    allowed_hormones.append(primary.lower())
                    if secondary and len(secondary) > 0:
                        allowed_hormones.append(secondary[0].lower())  # Max 1 secondary
            
            # Filter hormones to ONLY user's allowed hormones
            rec_hormones = rec.hormones or []
            if rec_hormones and allowed_hormones:
                # Only keep hormones that are in user's allowed list
                hormones = [h for h in rec_hormones if h.lower() in allowed_hormones]
            else:
                hormones = []
            
            # If no matching hormones, use user's hormones directly
            if not hormones:
                if allowed_hormones:
                    hormones = [h.title() for h in allowed_hormones]  # Capitalize
                else:
                    hormones = ['progesterone']  # Minimal fallback
            
            # Ensure optimal_times is set - use defaults if not provided
            optimal_times = getattr(rec, 'optimal_times', None)
            if not optimal_times:
                # Default optimal times based on category
                category_default_times = {
                    'food': ['morning'],
                    'movement': ['afternoon'],
                    'mindfulness': ['evening'],
                }
                optimal_times = category_default_times.get(category.lower(), ['anytime'])
            
            # Create DB record
            db_record = RecommendationRecord(
                uid=uid,
                recommendation_type=recommendation_type,
                category=category,
                confidence=result.confidence,
                generated_at=result.generatedAt,
                
                # Recommendation card information
                title=rec.title,
                purpose=rec.purpose,
                specific_action=rec.specificAction,
                priority=rec.priority,
                contraindications=rec.contraindications,
                
                # Tag information
                conditions=rec.conditions,
                symptoms=rec.symptoms,
                hormones=hormones,  # Use our ensured hormones
                
                # Category-specific action fields (plural)
                food_amounts=rec.food_amounts,
                food_items=rec.food_items,
                exercise_durations=rec.exercise_durations,
                exercise_types=rec.exercise_types,
                exercise_intensities=rec.exercise_intensities,
                mindfulness_durations=rec.mindfulness_durations,
                mindfulness_techniques=rec.mindfulness_techniques,
                frequency_detail=rec.frequency_detail,
                duration_weeks=rec.duration_weeks,
                
                # CRITICAL: Include optimal_times for proper time-based scheduling
                optimal_times=optimal_times,
                
                # Research backing
                research_summary=research_summary,
                research_studies=research_studies,
                
                # User profile snapshot
                user_profile_snapshot=user_profile_snapshot
            )
            
            self.db.add(db_record)
            self.db.flush()  # Flush for ID generation
            
            # Generate and save advice
            advice_service = AdviceService(self.db)
            try:
                advice_success = await advice_service.generate_and_save_advices(
                    db_record.id, uid, rec, category
                )
            except Exception as e:
                logger.error(f"Error during advice generation: {str(e)}")
                advice_success = False
            
            # Commit to DB
            self.db.commit()
            
            if not advice_success:
                logger.warning(f"Advice generation failed: recommendation_id={db_record.id}")
                # Recommendation is still saved even if advice generation fails (recommendations and advice are independent)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save single recommendation: {str(e)}")
            return False
    
    def get_user_recommendations(self, uid: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get user's recommendation history
        """
        try:
            records = self.db.query(RecommendationRecord)\
                .filter(RecommendationRecord.uid == uid)\
                .order_by(RecommendationRecord.created_at.desc())\
                .limit(limit)\
                .all()
            
            return [self._record_to_dict(record) for record in records]
            
        except Exception as e:
            logger.error(f"Failed to get user recommendation history: {str(e)}")
            return []
    
    def get_user_recommendations_by_category(self, uid: str, category: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get user's recommendation history by category
        """
        try:
            records = self.db.query(RecommendationRecord)\
                .filter(RecommendationRecord.uid == uid)\
                .filter(RecommendationRecord.category == category)\
                .order_by(RecommendationRecord.created_at.desc())\
                .limit(limit)\
                .all()
            
            return [self._record_to_dict(record) for record in records]
            
        except Exception as e:
            logger.error(f"Failed to get category-specific recommendation history: {str(e)}")
            return []
    
    async def delete_recommendation(self, recommendation_id: int, uid: str) -> bool:
        """
        Delete recommendation (including related advice)
        """
        try:
            # Delete advice first
            advice_service = AdviceService(self.db)
            advice_service.delete_advices_by_recommendation(recommendation_id)
            
            # Delete recommendation
            self.db.query(RecommendationRecord)\
                .filter(RecommendationRecord.id == recommendation_id)\
                .filter(RecommendationRecord.uid == uid)\
                .delete()
            
            self.db.commit()
            logger.info(f"Recommendation deletion completed: recommendation_id={recommendation_id}")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to delete recommendation: {str(e)}")
            return False
    
    def _record_to_dict(self, record: RecommendationRecord) -> Dict[str, Any]:
        """
        Convert DB record to dictionary
        """
        return {
            "id": record.id,
            "uid": record.uid,
            "recommendation_type": record.recommendation_type,
            "category": record.category,
            "confidence": record.confidence,
            "generated_at": record.generated_at.isoformat() if record.generated_at else None,
            
            # Recommendation card information
            "title": record.title,
            "specific_action": record.specific_action,
            "priority": record.priority,
            "contraindications": record.contraindications,
            
            # Tag information
            "conditions": record.conditions,
            "symptoms": record.symptoms,
            "hormones": record.hormones,
            
            # Category-specific action fields (using plural forms matching prompts)
            "food_amounts": record.food_amounts,
            "food_items": record.food_items,
            "exercise_durations": record.exercise_durations,
            "exercise_types": record.exercise_types,
            "exercise_intensities": record.exercise_intensities,
            "mindfulness_durations": record.mindfulness_durations,
            "mindfulness_techniques": record.mindfulness_techniques,
            "frequency_detail": record.frequency_detail,
            "duration_weeks": record.duration_weeks,
            "optimal_times": record.optimal_times,
            
            # Research backing
            "research_summary": record.research_summary,
            "research_studies": record.research_studies,
            
            # User profile snapshot
            "user_profile_snapshot": record.user_profile_snapshot,
            
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None
        }
