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
        세션용 추천 생성 및 저장 (임시 세션용)
        """
        try:
            logger.info(f"세션 추천 생성 시작: session_id={session_id}, category={category}")
            
            # UserProfile 객체 생성
            user_profile_obj = UserProfile(**user_profile)
            
            # AI 추천 생성 (세션용 최적화된 방식)
            recommendations = await AIService.generate_session_recommendations(user_profile_obj, category)
            
            if not recommendations:
                logger.warning(f"세션 추천 생성 실패: {session_id}, {category}")
                return False
            
            # 각 추천을 세션용으로 저장
            for rec in recommendations:
                await self._save_single_session_recommendation(session_id, rec, category)
            
            logger.info(f"세션 추천 생성 완료: {session_id}, {category}")
            return True
            
        except Exception as e:
            logger.error(f"세션 추천 생성 중 오류: {str(e)}", exc_info=True)
            return False
    
    async def _save_single_session_recommendation(self, session_id: str, rec: Dict[str, Any], category: str) -> bool:
        """
        단일 세션 추천을 DB에 저장 (임시)
        """
        try:
            # DB 레코드 생성 (session_id로 연결, uid는 NULL)
            db_record = RecommendationRecord(
                session_id=session_id,
                uid=None,  # 임시 세션이므로 NULL
                recommendation_type="general",
                category=category,
                confidence=None,
                generated_at=None,
                
                # 추천 카드 정보
                title=rec.get('title'),
                purpose=rec.get('purpose'),
                specific_action=rec.get('specificAction'),
                priority=rec.get('priority'),
                contraindications=rec.get('contraindications'),
                
                # 태그 정보
                conditions=rec.get('conditions', []),
                symptoms=rec.get('symptoms', []),
                hormones=rec.get('hormones', []),
                
                # 배열 필드들
                food_amounts=rec.get('food_amounts', []),
                food_items=rec.get('food_items', []),
                exercise_durations=rec.get('exercise_durations', []),
                exercise_types=rec.get('exercise_types', []),
                exercise_intensities=rec.get('exercise_intensities', []),
                mindfulness_durations=rec.get('mindfulness_durations', []),
                mindfulness_techniques=rec.get('mindfulness_techniques', []),
                frequency_detail=rec.get('frequency_detail'),
                duration_weeks=rec.get('duration_weeks'),
                optimal_times=rec.get('optimal_times', []),
                
                # 연구 근거
                research_summary=rec.get('researchBacking', {}).get('summary') if rec.get('researchBacking') else None,
                research_studies=rec.get('researchBacking', {}).get('studies') if rec.get('researchBacking') else None,
                
                # 사용자 프로필 스냅샷
                user_profile_snapshot=user_profile if 'user_profile' in locals() else None
            )
            
            self.db.add(db_record)
            self.db.flush()  # ID 생성
            
            # 조언 생성 및 저장
            advice_service = AdviceService(self.db)
            await advice_service.generate_and_save_session_advices(
                recommendation_id=db_record.id,
                session_id=session_id,
                recommendation_data=rec,
                category=category
            )
            
            return True
            
        except Exception as e:
            logger.error(f"세션 추천 저장 실패: {str(e)}", exc_info=True)
            return False
    
    async def save_recommendations(self, uid: str, result: RecommendationResult, recommendation_type: str = "general") -> bool:
        """
        추천 결과를 DB에 저장
        """
        try:
            logger.info(f"추천 결과 저장 시작: uid={uid}, type={recommendation_type}")
            
            # 각 카테고리별로 추천 저장
            categories = {
                "food": result.food,
                "movement": result.movement,
                "mindfulness": result.mindfulness
            }
            
            # 병렬로 추천들을 저장 (카테고리별로)
            import asyncio
            tasks = []
            for category, recommendations in categories.items():
                for rec in recommendations:
                    task = self._save_single_recommendation(uid, rec, category, recommendation_type, result)
                    tasks.append(task)
            
            # 모든 추천을 병렬로 처리
            results = await asyncio.gather(*tasks, return_exceptions=True)
            saved_count = sum(1 for result in results if result is True)
            
            self.db.commit()
            logger.info(f"추천 결과 저장 완료: {saved_count}개 저장됨")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"추천 결과 저장 실패: {str(e)}")
            return False
    
    async def _save_single_recommendation(self, uid: str, rec: RecommendationCard, category: str, 
                                        recommendation_type: str, result: RecommendationResult) -> bool:
        """
        단일 추천을 DB에 저장
        """
        try:
            # 연구 근거 정보 추출
            research_summary = None
            research_studies = None
            if rec.researchBacking:
                research_summary = rec.researchBacking.summary
                research_studies = rec.researchBacking.studies
            
            # 사용자 프로필 스냅샷
            user_profile_snapshot = None
            if result.userProfile:
                user_profile_snapshot = result.userProfile.dict()
            
            # DB 레코드 생성
            db_record = RecommendationRecord(
                uid=uid,
                recommendation_type=recommendation_type,
                category=category,
                confidence=result.confidence,
                generated_at=result.generatedAt,
                
                # 추천 카드 정보
                title=rec.title,
                purpose=rec.purpose,
                specific_action=rec.specificAction,
                priority=rec.priority,
                contraindications=rec.contraindications,
                
                # 태그 정보
                conditions=rec.conditions,
                symptoms=rec.symptoms,
                hormones=rec.hormones,
                
                # 카테고리별 구체적 행동 필드들 (복수형)
                food_amounts=rec.food_amounts,
                food_items=rec.food_items,
                exercise_durations=rec.exercise_durations,
                exercise_types=rec.exercise_types,
                exercise_intensities=rec.exercise_intensities,
                mindfulness_durations=rec.mindfulness_durations,
                mindfulness_techniques=rec.mindfulness_techniques,
                frequency_detail=rec.frequency_detail,
                duration_weeks=rec.duration_weeks,
                
                # 연구 근거
                research_summary=research_summary,
                research_studies=research_studies,
                
                # 사용자 프로필 스냅샷
                user_profile_snapshot=user_profile_snapshot
            )
            
            self.db.add(db_record)
            self.db.flush()  # ID 생성을 위해 flush
            
            # 조언 생성 및 저장
            advice_service = AdviceService(self.db)
            try:
                advice_success = await advice_service.generate_and_save_advices(
                    db_record.id, uid, rec, category
                )
            except Exception as e:
                logger.error(f"조언 생성 중 오류: {str(e)}")
                advice_success = False
            
            # DB 커밋
            self.db.commit()
            
            if not advice_success:
                logger.warning(f"조언 생성 실패: recommendation_id={db_record.id}")
                # 조언 생성 실패해도 추천은 저장됨 (추천과 조언은 독립적)
            
            return True
            
        except Exception as e:
            logger.error(f"단일 추천 저장 실패: {str(e)}")
            return False
    
    def get_user_recommendations(self, uid: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        사용자의 추천 이력 조회
        """
        try:
            records = self.db.query(RecommendationRecord)\
                .filter(RecommendationRecord.uid == uid)\
                .order_by(RecommendationRecord.created_at.desc())\
                .limit(limit)\
                .all()
            
            return [self._record_to_dict(record) for record in records]
            
        except Exception as e:
            logger.error(f"사용자 추천 이력 조회 실패: {str(e)}")
            return []
    
    def get_user_recommendations_by_category(self, uid: str, category: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        카테고리별 사용자 추천 이력 조회
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
            logger.error(f"카테고리별 추천 이력 조회 실패: {str(e)}")
            return []
    
    async def delete_recommendation(self, recommendation_id: int, uid: str) -> bool:
        """
        추천 삭제 (관련 조언들도 함께 삭제)
        """
        try:
            # 조언들 먼저 삭제
            advice_service = AdviceService(self.db)
            advice_service.delete_advices_by_recommendation(recommendation_id)
            
            # 추천 삭제
            self.db.query(RecommendationRecord)\
                .filter(RecommendationRecord.id == recommendation_id)\
                .filter(RecommendationRecord.uid == uid)\
                .delete()
            
            self.db.commit()
            logger.info(f"추천 삭제 완료: recommendation_id={recommendation_id}")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"추천 삭제 실패: {str(e)}")
            return False
    
    def _record_to_dict(self, record: RecommendationRecord) -> Dict[str, Any]:
        """
        DB 레코드를 딕셔너리로 변환
        """
        return {
            "id": record.id,
            "uid": record.uid,
            "recommendation_type": record.recommendation_type,
            "category": record.category,
            "confidence": record.confidence,
            "generated_at": record.generated_at.isoformat() if record.generated_at else None,
            
            # 추천 카드 정보
            "title": record.title,
            "specific_action": record.specific_action,
            "priority": record.priority,
            "contraindications": record.contraindications,
            
            # 태그 정보
            "conditions": record.conditions,
            "symptoms": record.symptoms,
            "hormones": record.hormones,
            
            # 카테고리별 구체적 행동 필드들 (프롬프트와 일치하는 복수형 사용)
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
            
            # 연구 근거
            "research_summary": record.research_summary,
            "research_studies": record.research_studies,
            
            # 사용자 프로필 스냅샷
            "user_profile_snapshot": record.user_profile_snapshot,
            
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None
        }
