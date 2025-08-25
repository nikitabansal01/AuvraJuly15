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
        추천에 대한 조언들을 생성하고 저장
        """
        try:
            logger.info(f"조언 생성 시작: recommendation_id={recommendation_id}, category={category}")
            
            # 추천 컨텍스트 생성
            recommendation_context = self._create_recommendation_context(rec)
            
            # 카테고리별 조언 생성
            if category == "food":
                advices = await self._generate_food_advices(rec, recommendation_context)
            elif category == "movement":
                advices = await self._generate_movement_advices(rec, recommendation_context)
            elif category == "mindfulness":
                advices = await self._generate_mindfulness_advices(rec, recommendation_context)
            else:
                logger.error(f"지원하지 않는 카테고리: {category}")
                return False
            
            # 조언들을 DB에 저장 (병렬 처리)
            import asyncio
            save_tasks = []
            for advice in advices:
                # _save_advice는 동기 함수이므로 asyncio.to_thread로 래핑
                task = asyncio.to_thread(self._save_advice, recommendation_id, uid, advice, category, recommendation_context)
                save_tasks.append(task)
            
            # 모든 조언 저장을 병렬로 처리
            save_results = await asyncio.gather(*save_tasks, return_exceptions=True)
            saved_count = sum(1 for result in save_results if result is True)
            
            # DB 커밋
            if saved_count > 0:
                self.db.commit()
                logger.info(f"조언 생성 완료: {saved_count}개 저장됨")
                return True
            else:
                logger.warning("AI 조언 생성 실패로 저장된 조언이 없습니다")
                return False
            
        except Exception as e:
            logger.error(f"조언 생성 실패: {str(e)}")
            return False
    
    async def generate_and_save_session_advices(self, recommendation_id: int, session_id: str, recommendation_data: Dict[str, Any], category: str) -> bool:
        """
        세션용 조언 생성 및 저장 (임시 세션용)
        """
        try:
            logger.info(f"세션 조언 생성 시작: recommendation_id={recommendation_id}, session_id={session_id}, category={category}")
            
            # 추천 컨텍스트 생성
            recommendation_context = self._create_session_recommendation_context(recommendation_data)
            
            # 카테고리별 조언 생성
            if category == "food":
                advices = await self._generate_food_advices_from_dict(recommendation_data, recommendation_context)
            elif category == "movement":
                advices = await self._generate_movement_advices_from_dict(recommendation_data, recommendation_context)
            elif category == "mindfulness":
                advices = await self._generate_mindfulness_advices_from_dict(recommendation_data, recommendation_context)
            else:
                logger.error(f"지원하지 않는 카테고리: {category}")
                return False
            
            # 조언들을 DB에 저장 (병렬 처리)
            import asyncio
            save_tasks = []
            for advice in advices:
                # _save_session_advice는 동기 함수이므로 asyncio.to_thread로 래핑
                task = asyncio.to_thread(self._save_session_advice, recommendation_id, session_id, advice, category, recommendation_context)
                save_tasks.append(task)
            
            # 모든 조언 저장을 병렬로 처리
            save_results = await asyncio.gather(*save_tasks, return_exceptions=True)
            saved_count = sum(1 for result in save_results if result is True)
            
            # DB 커밋
            if saved_count > 0:
                self.db.commit()
                logger.info(f"세션 조언 생성 완료: {saved_count}개 저장됨")
                return True
            else:
                logger.warning("AI 조언 생성 실패로 저장된 조언이 없습니다")
                return False
            
        except Exception as e:
            logger.error(f"세션 조언 생성 실패: {str(e)}")
            return False
    
    def _create_recommendation_context(self, rec: RecommendationCard) -> Dict[str, Any]:
        """
        추천 컨텍스트 생성
        """
        return {
            "title": rec.title,
            "specific_action": rec.specificAction,
            "food_amount": rec.food_amount,
            "food_item": rec.food_item,
            "exercise_duration": rec.exercise_duration,
            "exercise_type": rec.exercise_type,
            "exercise_intensity": rec.exercise_intensity,
            "mindfulness_duration": rec.mindfulness_duration,
            "mindfulness_technique": rec.mindfulness_technique,
            "frequency_detail": rec.frequency_detail,
            "duration_weeks": rec.duration_weeks,
            "conditions": rec.conditions,
            "symptoms": rec.symptoms,
            "hormones": rec.hormones
        }
    
    def _create_session_recommendation_context(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """
        세션 추천 컨텍스트 생성 (배열 필드 사용)
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
        음식 추천에 대한 조언 생성 (easy, tasty, healthy)
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
- title: Short, catchy title
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
                logger.warning("AI 응답 파싱 실패")
                return []
        except Exception as e:
            logger.error(f"음식 조언 생성 실패: {str(e)}")
            return []
    
    async def _generate_movement_advices(self, rec: RecommendationCard, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        운동 추천에 대한 조언 생성 (3가지 쉬운 방법)
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
- title: Short, catchy title
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
                logger.warning("AI 응답 파싱 실패")
                return []
        except Exception as e:
            logger.error(f"운동 조언 생성 실패: {str(e)}")
            return []
    
    async def _generate_mindfulness_advices(self, rec: RecommendationCard, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        마음챙김 추천에 대한 조언 생성 (3가지 쉬운 방법)
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
- title: Short, catchy title
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
                logger.warning("AI 응답 파싱 실패")
                return []
        except Exception as e:
            logger.error(f"마음챙김 조언 생성 실패: {str(e)}")
            return []
    
    async def _generate_food_advices_from_dict(self, rec: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        음식 추천에 대한 조언 생성 (dict 기반)
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
- title: Short, catchy title (string)
- description: Detailed explanation (2-3 sentences, string)

RESPONSE FORMAT (JSON array only):
[
  {{"advice_type": "easy", "title": "Quick Breakfast Boost", "description": "Add this food to your morning smoothie or yogurt for an easy hormone-balancing start to your day."}},
  {{"advice_type": "tasty", "title": "Delicious Recipe Idea", "description": "Try roasting this food with olive oil and herbs for a flavorful side dish that supports hormone health."}},
  {{"advice_type": "healthy", "title": "Optimal Preparation", "description": "For maximum benefits, consume this food raw or lightly steamed to preserve its hormone-balancing nutrients."}}
]

Remember: Return ONLY the JSON array, no other text.
"""
        
        try:
            response, actual_model = await AIService.call_ai_model(prompt)
            
            if response and response.strip():
                try:
                    # JSON 파싱 시도
                    advices = json.loads(response)
                    if isinstance(advices, list):
                        return advices
                    else:
                        logger.warning(f"AI 응답이 리스트가 아님: {type(advices)}")
                        return []
                except json.JSONDecodeError as e:
                    logger.warning(f"AI 응답 JSON 파싱 실패: {str(e)}, 응답: {response[:200]}")
                    return []
            else:
                logger.warning("AI 조언 생성 실패 - 빈 응답")
                return []
                
        except Exception as e:
            logger.error(f"음식 조언 생성 실패: {str(e)}")
            return []
    
    async def _generate_movement_advices_from_dict(self, rec: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        운동 추천에 대한 조언 생성 (dict 기반)
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
- title: Short, catchy title (string)
- description: Detailed explanation (2-3 sentences, string)

RESPONSE FORMAT (JSON array only):
[
  {{"advice_type": "tip1", "title": "Beginner-Friendly Start", "description": "Start with just 5-10 minutes of this exercise to build confidence and gradually increase duration."}},
  {{"advice_type": "tip2", "title": "Advanced Variation", "description": "Once comfortable, add resistance or increase intensity to challenge your body and improve hormone balance."}},
  {{"advice_type": "tip3", "title": "Safety First", "description": "Focus on proper form and breathing to prevent injury and maximize the hormone-balancing benefits."}}
]

Remember: Return ONLY the JSON array, no other text.
"""
        
        try:
            response, actual_model = await AIService.call_ai_model(prompt)
            
            if response and response.strip():
                try:
                    # JSON 파싱 시도
                    advices = json.loads(response)
                    if isinstance(advices, list):
                        return advices
                    else:
                        logger.warning(f"AI 응답이 리스트가 아님: {type(advices)}")
                        return []
                except json.JSONDecodeError as e:
                    logger.warning(f"AI 응답 JSON 파싱 실패: {str(e)}, 응답: {response[:200]}")
                    return []
            else:
                logger.warning("AI 조언 생성 실패 - 빈 응답")
                return []
                
        except Exception as e:
            logger.error(f"운동 조언 생성 실패: {str(e)}")
            return []
    
    async def _generate_mindfulness_advices_from_dict(self, rec: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        마음챙김 추천에 대한 조언 생성 (dict 기반)
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
- title: Short, catchy title (string)
- description: Detailed explanation (2-3 sentences, string)

RESPONSE FORMAT (JSON array only):
[
  {{"advice_type": "tip1", "title": "Easy Setup", "description": "Find a quiet space and sit comfortably with your back straight to begin this mindfulness practice."}},
  {{"advice_type": "tip2", "title": "Advanced Technique", "description": "Once comfortable, try extending the duration or adding guided imagery to deepen your practice."}},
  {{"advice_type": "tip3", "title": "Daily Integration", "description": "Incorporate this practice into your morning routine or before bed for consistent hormone-balancing benefits."}}
]

Remember: Return ONLY the JSON array, no other text.
"""
        
        try:
            response, actual_model = await AIService.call_ai_model(prompt)
            
            if response and response.strip():
                try:
                    # JSON 파싱 시도
                    advices = json.loads(response)
                    if isinstance(advices, list):
                        return advices
                    else:
                        logger.warning(f"AI 응답이 리스트가 아님: {type(advices)}")
                        return []
                except json.JSONDecodeError as e:
                    logger.warning(f"AI 응답 JSON 파싱 실패: {str(e)}, 응답: {response[:200]}")
                    return []
            else:
                logger.warning("AI 조언 생성 실패 - 빈 응답")
                return []
                
        except Exception as e:
            logger.error(f"마음챙김 조언 생성 실패: {str(e)}")
            return []
    
    def _parse_advice_response(self, response: str) -> List[Dict[str, Any]]:
        """
        AI 응답을 파싱하여 조언 리스트로 변환
        """
        try:
            import re
            
            # 1. 마크다운 코드 블록 제거
            response = response.strip()
            if response.startswith('```'):
                # ```json\n[...]\n``` 형식 제거
                response = re.sub(r'^```(?:json)?\n', '', response)
                response = re.sub(r'\n```$', '', response)
            
            # 2. JSON 배열 찾기
            match = re.search(r'\[.*\]', response, re.DOTALL)
            if match:
                json_str = match.group(0)
                return json.loads(json_str)
            
            # 3. 전체 응답이 JSON 배열인 경우
            try:
                return json.loads(response)
            except:
                pass
                
            return []
        except Exception as e:
            logger.error(f"조언 응답 파싱 실패: {str(e)}, 응답: {response[:200]}...")
            return []
    
    def _save_advice(self, recommendation_id: int, uid: str, advice: Dict[str, Any], 
                    category: str, recommendation_context: Dict[str, Any]) -> bool:
        """
        개별 조언을 DB에 저장
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
            logger.error(f"조언 저장 실패: {str(e)}")
            return False
    
    def _save_session_advice(self, recommendation_id: int, session_id: str, advice: Dict[str, Any], category: str, recommendation_context: Dict[str, Any]) -> bool:
        """
        세션 조언을 DB에 저장 (임시 세션용)
        """
        try:
            db_advice = RecommendationAdvice(
                recommendation_id=recommendation_id,
                session_id=session_id,
                uid=None,  # 임시 세션이므로 NULL
                advice_type=advice.get('advice_type'),
                category=category,
                title=advice.get('title'),
                description=advice.get('description'),
                recommendation_context=recommendation_context
            )
            
            self.db.add(db_advice)
            return True
            
        except Exception as e:
            logger.error(f"세션 조언 저장 실패: {str(e)}")
            return False
    
    def delete_advices_by_recommendation(self, recommendation_id: int) -> bool:
        """
        추천이 삭제될 때 관련 조언들도 삭제
        """
        try:
            self.db.query(RecommendationAdvice)\
                .filter(RecommendationAdvice.recommendation_id == recommendation_id)\
                .delete()
            return True
        except Exception as e:
            logger.error(f"조언 삭제 실패: {str(e)}")
            return False
    
    def get_advices_by_recommendation(self, recommendation_id: int) -> List[Dict[str, Any]]:
        """
        특정 추천의 조언들 조회
        """
        try:
            advices = self.db.query(RecommendationAdvice)\
                .filter(RecommendationAdvice.recommendation_id == recommendation_id)\
                .order_by(RecommendationAdvice.advice_type)\
                .all()
            
            return [self._advice_to_dict(advice) for advice in advices]
        except Exception as e:
            logger.error(f"조언 조회 실패: {str(e)}")
            return []
    
    def _advice_to_dict(self, advice: RecommendationAdvice) -> Dict[str, Any]:
        """
        DB 조언 객체를 딕셔너리로 변환
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
    

