from sqlalchemy.orm import Session
from app.core.database import RecommendationAdvice, RecommendationRecord
from app.models.ai_models import RecommendationCard
from app.services.ai_service import AIService
from typing import List, Dict, Any
import logging
import json

logger = logging.getLogger(__name__)

class AdviceService:
    def __init__(self, db: Session):
        self.db = db
    
    async def generate_and_save_advices(self, recommendation_id: int, uid: str, rec: RecommendationCard, category: str) -> bool:
        """
        Generate and save advice for recommendations
        """
        try:
            logger.info(f"Advice generation started: recommendation_id={recommendation_id}, category={category}")
            
            # Create recommendation context
            recommendation_context = self._create_recommendation_context(rec)
            
            # Generate advice by category
            if category == "food":
                advices = await self._generate_food_advices(rec, recommendation_context)
            elif category == "movement":
                advices = await self._generate_movement_advices(rec, recommendation_context)
            elif category == "mindfulness":
                advices = await self._generate_mindfulness_advices(rec, recommendation_context)
            else:
                logger.error(f"Unsupported category: {category}")
                return False
            
            # Save advice to DB (Fix #2 - Sequential to prevent SQLAlchemy session conflicts)
            # REMOVED: asyncio.to_thread causes SAWarning when multiple threads share same Session
            saved_count = 0
            for advice in advices:
                if self._save_advice(recommendation_id, uid, advice, category, recommendation_context):
                    saved_count += 1
            
            # Commit to DB
            if saved_count > 0:
                self.db.commit()
                logger.info(f"Advice generation completed: {saved_count} saved")
                return True
            else:
                logger.warning("No advice saved due to AI advice generation failure")
                return False
            
        except Exception as e:
            logger.error(f"Advice generation failed: {str(e)}")
            return False
    
    async def generate_and_save_session_advices(self, recommendation_id: int, session_id: str, recommendation_data: Dict[str, Any], category: str) -> bool:
        """
        Generate and save session advice (for temporary sessions)
        """
        try:
            logger.info(f"Session advice generation started: recommendation_id={recommendation_id}, session_id={session_id}, category={category}")
            
            # Create recommendation context
            recommendation_context = self._create_session_recommendation_context(recommendation_data)
            
            # Generate advice by category
            if category == "food":
                advices = await self._generate_food_advices_from_dict(recommendation_data, recommendation_context)
            elif category == "movement":
                advices = await self._generate_movement_advices_from_dict(recommendation_data, recommendation_context)
            elif category == "mindfulness":
                advices = await self._generate_mindfulness_advices_from_dict(recommendation_data, recommendation_context)
            else:
                logger.error(f"Unsupported category: {category}")
                return False
            
            # Save advice to DB (Fix #2 - Sequential to prevent SQLAlchemy session conflicts)
            # REMOVED: asyncio.to_thread causes SAWarning when multiple threads share same Session
            saved_count = 0
            for advice in advices:
                if self._save_session_advice(recommendation_id, session_id, advice, category, recommendation_context):
                    saved_count += 1
            
            # Commit to DB
            if saved_count > 0:
                self.db.commit()
                logger.info(f"Session advice generation completed: {saved_count} saved")
                return True
            else:
                logger.warning("No advice saved due to AI advice generation failure")
                return False
            
        except Exception as e:
            logger.error(f"Session advice generation failed: {str(e)}")
            return False
    
    def _create_recommendation_context(self, rec: RecommendationCard) -> Dict[str, Any]:
        """
        Create recommendation context
        """
        return {
            "title": rec.title,
            "specific_action": rec.specificAction,
            "food_amounts": rec.food_amounts,
            "food_items": rec.food_items,
            "exercise_durations": rec.exercise_durations,
            "exercise_types": rec.exercise_types,
            "exercise_intensities": rec.exercise_intensities,
            "mindfulness_durations": rec.mindfulness_durations,
            "mindfulness_techniques": rec.mindfulness_techniques,
            "frequency_detail": rec.frequency_detail,
            "duration_weeks": rec.duration_weeks,
            "optimal_times": rec.optimal_times,
            "conditions": rec.conditions,
            "symptoms": rec.symptoms,
            "hormones": rec.hormones
        }
    
    def _create_session_recommendation_context(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create session recommendation context (using array fields)
        """
        return {
            "title": rec.get('title'),
            "specific_action": rec.get('specificAction'),
            "food_amounts": rec.get('food_amounts', []),
            "food_items": rec.get('food_items', []),
            "exercise_durations": rec.get('exercise_durations', []),
            "exercise_types": rec.get('exercise_types', []),
            "exercise_intensities": rec.get('exercise_intensities', []),
            "mindfulness_durations": rec.get('mindfulness_durations', []),
            "mindfulness_techniques": rec.get('mindfulness_techniques', []),
            "frequency_detail": rec.get('frequency_detail'),
            "duration_weeks": rec.get('duration_weeks'),
            "optimal_times": rec.get('optimal_times', []),
            "conditions": rec.get('conditions', []),
            "symptoms": rec.get('symptoms', []),
            "hormones": rec.get('hormones', [])
        }
    
    async def _generate_food_advices(self, rec: RecommendationCard, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Generate advice for food recommendations (easy, tasty, healthy)
        """
        prompt = f"""
You are a nutrition expert helping women with hormone health. Based on the following food recommendation, generate 3 practical advice items:

RECOMMENDATION CONTEXT:
{json.dumps(context, indent=2)}

Generate 3 advice items in the following categories:
1. EASY: Simple and quick way to incorporate this food/nutrient
2. TASTY: Delicious recipe or preparation method
3. HEALTHY: Healthiest way to consume this food/nutrient

Each advice should include:
- title: Simple title describing the method (e.g., "Smoothie Add-in", "Roasted with Honey", "Raw with Salad")
- description: Detailed explanation (2-3 sentences)

Return as JSON array:
[
    {{"advice_type": "easy", "title": "...", "description": "..."}},
    {{"advice_type": "tasty", "title": "...", "description": "..."}},
    {{"advice_type": "healthy", "title": "...", "description": "..."}}
]
"""
        
        try:
            response, actual_model = await AIService.call_ai_model(prompt)
            parsed_advices = self._parse_advice_response(response)
            if parsed_advices:
                return parsed_advices
            else:
                logger.warning("AI response parsing failed")
                return []
        except Exception as e:
            logger.error(f"Food advice generation failed: {str(e)}")
            return []
    
    async def _generate_movement_advices(self, rec: RecommendationCard, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Generate advice for movement recommendations (3 easy methods)
        """
        prompt = f"""
You are a fitness expert helping women with hormone health. Based on the following movement recommendation, generate 3 practical tips for easy implementation:

RECOMMENDATION CONTEXT:
{json.dumps(context, indent=2)}

Generate 3 practical tips that make this exercise/movement easy to incorporate into daily life:
1. TIP1: Simple way to start or integrate into daily routine
2. TIP2: Alternative or modified version for beginners
3. TIP3: How to make it enjoyable and sustainable

Focus on:
- Daily life integration (e.g., walking to grocery store, taking stairs)
- Beginner-friendly modifications
- Making it enjoyable and sustainable

Each tip should include:
- title: Simple title describing the approach (e.g., "Start with 5 Minutes", "Add Music", "Morning Routine")
- description: Detailed explanation (2-3 sentences)

Return as JSON array:
[
    {{"advice_type": "tip1", "title": "...", "description": "..."}},
    {{"advice_type": "tip2", "title": "...", "description": "..."}},
    {{"advice_type": "tip3", "title": "...", "description": "..."}}
]
"""
        
        try:
            response, actual_model = await AIService.call_ai_model(prompt)
            parsed_advices = self._parse_advice_response(response)
            if parsed_advices:
                return parsed_advices
            else:
                logger.warning("AI response parsing failed")
                return []
        except Exception as e:
            logger.error(f"Movement advice generation failed: {str(e)}")
            return []
    
    async def _generate_mindfulness_advices(self, rec: RecommendationCard, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Generate advice for mindfulness recommendations (3 easy methods)
        """
        prompt = f"""
You are a mindfulness expert helping women with hormone health. Based on the following mindfulness recommendation, generate 3 practical tips for easy implementation:

RECOMMENDATION CONTEXT:
{json.dumps(context, indent=2)}

Generate 3 practical tips that make this mindfulness practice easy to incorporate into daily life:
1. TIP1: Simple way to start or integrate into daily routine
2. TIP2: Alternative or modified version for beginners
3. TIP3: How to make it enjoyable and sustainable

Focus on:
- Daily life integration (e.g., mindful breathing while waiting, mindful walking)
- Beginner-friendly modifications
- Making it enjoyable and sustainable

Each tip should include:
- title: Simple title describing the approach (e.g., "While Waiting", "With Music", "Before Bed")
- description: Detailed explanation (2-3 sentences)

Return as JSON array:
[
    {{"advice_type": "tip1", "title": "...", "description": "..."}},
    {{"advice_type": "tip2", "title": "...", "description": "..."}},
    {{"advice_type": "tip3", "title": "...", "description": "..."}}
]
"""
        
        try:
            response, actual_model = await AIService.call_ai_model(prompt)
            parsed_advices = self._parse_advice_response(response)
            if parsed_advices:
                return parsed_advices
            else:
                logger.warning("AI response parsing failed")
                return []
        except Exception as e:
            logger.error(f"Mindfulness advice generation failed: {str(e)}")
            return []
    
    async def _generate_food_advices_from_dict(self, rec: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Generate advice for food recommendations (dict-based)
        """
        prompt = f"""
You are a nutrition expert helping women with hormone health. Based on the following food recommendation, generate 3 practical advice items.

RECOMMENDATION CONTEXT:
{json.dumps(context, indent=2)}

IMPORTANT: You must respond with ONLY a valid JSON array. No additional text, explanations, or markdown formatting.

Generate exactly 3 advice items in the following categories:
1. EASY: Simple and quick way to incorporate this food/nutrient
2. TASTY: Delicious recipe or preparation method
3. HEALTHY: Healthiest way to consume this food/nutrient

Each advice must include:
- advice_type: "easy", "tasty", or "healthy"
- title: Simple title describing the method (e.g., "Smoothie Add-in", "Roasted with Herbs", "Raw Snack")
- description: Detailed explanation (2-3 sentences, string)

RESPONSE FORMAT (JSON array only):
[
  {{"advice_type": "easy", "title": "Smoothie Add-in", "description": "Add this food to your morning smoothie or yogurt for an easy hormone-balancing start to your day."}},
  {{"advice_type": "tasty", "title": "Roasted with Herbs", "description": "Try roasting this food with olive oil and herbs for a flavorful side dish that supports hormone health."}},
  {{"advice_type": "healthy", "title": "Raw Snack", "description": "For maximum benefits, consume this food raw or lightly steamed to preserve its hormone-balancing nutrients."}}
]

Remember: Return ONLY the JSON array, no other text.
"""
        
        try:
            response, actual_model = await AIService.call_ai_model(prompt)
            
            if response and response.strip():
                try:
                    # Try JSON parsing
                    advices = json.loads(response)
                    if isinstance(advices, list):
                        return advices
                    else:
                        logger.warning(f"AI response is not a list: {type(advices)}")
                        return []
                except json.JSONDecodeError as e:
                    logger.warning(f"AI response JSON parsing failed: {str(e)}, response: {response[:200]}")
                    return []
            else:
                logger.warning("AI advice generation failed - empty response")
                return []
                
        except Exception as e:
            logger.error(f"Food advice generation failed: {str(e)}")
            return []
    
    async def _generate_movement_advices_from_dict(self, rec: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Generate advice for movement recommendations (dict-based)
        """
        prompt = f"""
You are a fitness expert helping women with hormone health. Based on the following movement recommendation, generate 3 practical advice items.

RECOMMENDATION CONTEXT:
{json.dumps(context, indent=2)}

IMPORTANT: You must respond with ONLY a valid JSON array. No additional text, explanations, or markdown formatting.

Generate exactly 3 advice items in the following categories:
1. TIP1: Beginner-friendly modification or preparation
2. TIP2: Advanced variation or progression
3. TIP3: Safety and form guidance

Each advice must include:
- advice_type: "tip1", "tip2", or "tip3"
- title: Simple title describing the approach (e.g., "Start with 5 Minutes", "Add Resistance", "Focus on Form")
- description: Detailed explanation (2-3 sentences, string)

RESPONSE FORMAT (JSON array only):
[
  {{"advice_type": "tip1", "title": "Start with 5 Minutes", "description": "Start with just 5-10 minutes of this exercise to build confidence and gradually increase duration."}},
  {{"advice_type": "tip2", "title": "Add Resistance", "description": "Once comfortable, add resistance or increase intensity to challenge your body and improve hormone balance."}},
  {{"advice_type": "tip3", "title": "Focus on Form", "description": "Focus on proper form and breathing to prevent injury and maximize the hormone-balancing benefits."}}
]

Remember: Return ONLY the JSON array, no other text.
"""
        
        try:
            response, actual_model = await AIService.call_ai_model(prompt)
            
            if response and response.strip():
                try:
                    # Try JSON parsing
                    advices = json.loads(response)
                    if isinstance(advices, list):
                        return advices
                    else:
                        logger.warning(f"AI response is not a list: {type(advices)}")
                        return []
                except json.JSONDecodeError as e:
                    logger.warning(f"AI response JSON parsing failed: {str(e)}, response: {response[:200]}")
                    return []
            else:
                logger.warning("AI advice generation failed - empty response")
                return []
                
        except Exception as e:
            logger.error(f"Movement advice generation failed: {str(e)}")
            return []
    
    async def _generate_mindfulness_advices_from_dict(self, rec: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Generate advice for mindfulness recommendations (dict-based)
        """
        prompt = f"""
You are a mindfulness expert helping women with hormone health. Based on the following mindfulness recommendation, generate 3 practical advice items.

RECOMMENDATION CONTEXT:
{json.dumps(context, indent=2)}

IMPORTANT: You must respond with ONLY a valid JSON array. No additional text, explanations, or markdown formatting.

Generate exactly 3 advice items in the following categories:
1. TIP1: Beginner-friendly approach or setup
2. TIP2: Advanced technique or variation
3. TIP3: Integration into daily routine

Each advice must include:
- advice_type: "tip1", "tip2", or "tip3"
- title: Simple title describing the approach (e.g., "Find Quiet Space", "Extend Duration", "Before Bed")
- description: Detailed explanation (2-3 sentences, string)

RESPONSE FORMAT (JSON array only):
[
  {{"advice_type": "tip1", "title": "Find Quiet Space", "description": "Find a quiet space and sit comfortably with your back straight to begin this mindfulness practice."}},
  {{"advice_type": "tip2", "title": "Extend Duration", "description": "Once comfortable, try extending the duration or adding guided imagery to deepen your practice."}},
  {{"advice_type": "tip3", "title": "Before Bed", "description": "Incorporate this practice into your morning routine or before bed for consistent hormone-balancing benefits."}}
]

Remember: Return ONLY the JSON array, no other text.
"""
        
        try:
            response, actual_model = await AIService.call_ai_model(prompt)
            
            if response and response.strip():
                try:
                    # Try JSON parsing
                    advices = json.loads(response)
                    if isinstance(advices, list):
                        return advices
                    else:
                        logger.warning(f"AI response is not a list: {type(advices)}")
                        return []
                except json.JSONDecodeError as e:
                    logger.warning(f"AI response JSON parsing failed: {str(e)}, response: {response[:200]}")
                    return []
            else:
                logger.warning("AI advice generation failed - empty response")
                return []
                
        except Exception as e:
            logger.error(f"Mindfulness advice generation failed: {str(e)}")
            return []
    
    def _parse_advice_response(self, response: str) -> List[Dict[str, Any]]:
        """
        Parse AI response to convert to advice list
        """
        try:
            import re
            
            # 1. Remove markdown code blocks
            response = response.strip()
            if response.startswith('```'):
                # Remove ```json\n[...]\n``` format
                response = re.sub(r'^```(?:json)?\n', '', response)
                response = re.sub(r'\n```$', '', response)
            
            # 2. Find JSON array
            match = re.search(r'\[.*\]', response, re.DOTALL)
            if match:
                json_str = match.group(0)
                return json.loads(json_str)
            
            # 3. If entire response is JSON array
            try:
                return json.loads(response)
            except:
                pass
                
            return []
        except Exception as e:
            logger.error(f"Advice response parsing failed: {str(e)}, response: {response[:200]}...")
            return []
    
    def _save_advice(self, recommendation_id: int, uid: str, advice: Dict[str, Any], 
                    category: str, recommendation_context: Dict[str, Any]) -> bool:
        """
        Save individual advice to DB
        """
        try:
            db_advice = RecommendationAdvice(
                recommendation_id=recommendation_id,
                uid=uid,
                advice_type=advice.get("advice_type"),
                category=category,
                title=advice.get("title"),
                description=advice.get("description"),
                recommendation_context=recommendation_context
            )
            
            self.db.add(db_advice)
            return True
            
        except Exception as e:
            logger.error(f"Advice saving failed: {str(e)}")
            return False
    
    def _save_session_advice(self, recommendation_id: int, session_id: str, advice: Dict[str, Any], category: str, recommendation_context: Dict[str, Any]) -> bool:
        """
        Save session advice to DB (for temporary sessions)
        """
        try:
            db_advice = RecommendationAdvice(
                recommendation_id=recommendation_id,
                session_id=session_id,
                uid=None,  # NULL for temporary sessions
                advice_type=advice.get('advice_type'),
                category=category,
                title=advice.get('title'),
                description=advice.get('description'),
                recommendation_context=recommendation_context
            )
            
            self.db.add(db_advice)
            return True
            
        except Exception as e:
            logger.error(f"Session advice saving failed: {str(e)}")
            return False
    
    def delete_advices_by_recommendation(self, recommendation_id: int) -> bool:
        """
        Delete related advice when recommendation is deleted
        """
        try:
            self.db.query(RecommendationAdvice)\
                .filter(RecommendationAdvice.recommendation_id == recommendation_id)\
                .delete()
            return True
        except Exception as e:
            logger.error(f"Advice deletion failed: {str(e)}")
            return False
    
    def get_advices_by_recommendation(self, recommendation_id: int) -> List[Dict[str, Any]]:
        """
        Get advice for a specific recommendation
        """
        try:
            advices = self.db.query(RecommendationAdvice)\
                .filter(RecommendationAdvice.recommendation_id == recommendation_id)\
                .order_by(RecommendationAdvice.advice_type)\
                .all()
            
            return [self._advice_to_dict(advice) for advice in advices]
        except Exception as e:
            logger.error(f"Advice retrieval failed: {str(e)}")
            return []
    
    def _advice_to_dict(self, advice: RecommendationAdvice) -> Dict[str, Any]:
        """
        Convert DB advice object to dictionary
        """
        return {
            "id": advice.id,
            "recommendation_id": advice.recommendation_id,
            "uid": advice.uid,
            "advice_type": advice.advice_type,
            "category": advice.category,
            "title": advice.title,
            "description": advice.description,
            "recommendation_context": advice.recommendation_context,
            "created_at": advice.created_at.isoformat() if advice.created_at else None,
            "updated_at": advice.updated_at.isoformat() if advice.updated_at else None
        }
    

