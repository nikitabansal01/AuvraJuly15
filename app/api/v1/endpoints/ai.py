from fastapi import APIRouter, HTTPException, status, Request, Depends
from sqlalchemy.orm import Session
from app.models.ai_models import UserProfile, RecommendationResult
from app.services.ai_service import AIService
from app.services.recommendation_service import RecommendationService
from app.services.advice_service import AdviceService
from app.core.database import get_db
from app.core.security import get_current_active_user
from pydantic import BaseModel

router = APIRouter()

class RecommendationRequest(BaseModel):
    userProfile: UserProfile

@router.post("/recommendations", response_model=RecommendationResult)
async def generate_recommendations(
    request: RecommendationRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """
    일반 추천 생성 API
    프롬프트 엔지니어링 기반 추천
    """
    user_profile_obj = request.userProfile
    categories = ["food", "movement", "mindfulness"]
    result_dict = {"food": [], "movement": [], "mindfulness": []}
    confidences = []
    raw_llm_responses = []

    for category in categories:
        prompt = AIService.suggest_llm_prompt_for_recommendations(user_profile_obj, category)
        llm_response, actual_model = await AIService.call_ai_model(prompt)
        confidence = AIService.evaluate_llm_confidence(llm_response)
        recommendations = AIService.parse_recommendations_from_llm(llm_response, category)
        # Fallback: 신뢰도 낮거나 추천 없음 → Fallback 모델 사용
        if confidence < 60 or not recommendations:
            fallback_config = AIService.get_fallback_model_config()
            if fallback_config["provider"] == "openai":
                fallback_response = await AIService.call_openai(prompt, fallback_config["model"])
            elif fallback_config["provider"] == "groq":
                fallback_response = await AIService.call_groq(prompt, fallback_config["model"])
            elif fallback_config["provider"] == "anthropic":
                fallback_response = await AIService.call_anthropic(prompt, fallback_config["model"])
            elif fallback_config["provider"] == "perplexity":
                fallback_response = await AIService.call_perplexity(prompt, fallback_config["model"])
            else:
                logger.error(f"지원하지 않는 fallback 모델 프로바이더: {fallback_config['provider']}")
                continue
            
            fallback_confidence = AIService.evaluate_llm_confidence(fallback_response)
            fallback_recommendations = AIService.parse_recommendations_from_llm(fallback_response, category)
            if fallback_recommendations and fallback_confidence > confidence:
                llm_response = fallback_response
                confidence = fallback_confidence
                recommendations = fallback_recommendations
        result_dict[category] = recommendations
        confidences.append(confidence)
        raw_llm_responses.append(llm_response)

    result = RecommendationResult(
        food=result_dict["food"],
        movement=result_dict["movement"],
        mindfulness=result_dict["mindfulness"],
        userProfile=user_profile_obj,
        generatedAt=None,
        confidence=min(max(confidences), 100) if confidences else None,
        rawLLMResponse="\n---\n".join(raw_llm_responses)
    )
    
    # 추천 결과를 DB에 저장
    recommendation_service = RecommendationService(db)
    uid = current_user.get("user_id")
    if uid:
        save_success = await recommendation_service.save_recommendations(uid, result, "general")
        if not save_success:
            # 저장 실패해도 추천 결과는 반환
            pass

    return result

@router.post("/recommendations/rag", response_model=RecommendationResult)
async def generate_rag_recommendations(
    request: RecommendationRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """
    RAG 기반 추천 생성 API
    실제 PCOS 연구 데이터를 기반으로 한 추천
    """
    user_profile_obj = request.userProfile
    
    try:
        # RAG 기반 추천 생성
        rag_results = await AIService.generate_rag_recommendations(user_profile_obj)
        
        # 신뢰도 평가 (RAG 결과 기반)
        confidences = []
        for category in ["food", "movement", "mindfulness"]:
            recommendations = rag_results.get(category, [])
            if recommendations:
                # 추천이 있으면 높은 신뢰도
                confidences.append(85)
            else:
                # 추천이 없으면 낮은 신뢰도
                confidences.append(30)
        
        confidence = min(max(confidences), 100) if confidences else 30
        
        result = RecommendationResult(
            food=rag_results.get("food", []),
            movement=rag_results.get("movement", []),
            mindfulness=rag_results.get("mindfulness", []),
            userProfile=user_profile_obj,
            generatedAt=None,
            confidence=confidence,
            rawLLMResponse="RAG-based recommendations generated from actual PCOS research data"
        )
        
        # 추천 결과를 DB에 저장
        recommendation_service = RecommendationService(db)
        uid = current_user.get("user_id")
        if uid:
            save_success = await recommendation_service.save_recommendations(uid, result, "rag")
            if not save_success:
                # 저장 실패해도 추천 결과는 반환
                pass
    
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG recommendation generation failed: {str(e)}")

@router.get("/recommendations/history")
async def get_recommendation_history(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """
    사용자의 추천 이력 조회
    """
    try:
        uid = current_user.get("user_id")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        recommendation_service = RecommendationService(db)
        history = recommendation_service.get_user_recommendations(uid, limit)
        
        return {
            "success": True,
            "data": history,
            "count": len(history)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get recommendation history: {str(e)}")

@router.get("/recommendations/{recommendation_id}/advices")
async def get_recommendation_advices(
    recommendation_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """
    특정 추천의 조언들 조회
    """
    try:
        uid = current_user.get("user_id")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        advice_service = AdviceService(db)
        advices = advice_service.get_advices_by_recommendation(recommendation_id)
        
        return {
            "success": True,
            "data": advices,
            "count": len(advices),
            "recommendation_id": recommendation_id
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get recommendation advices: {str(e)}")

@router.delete("/recommendations/{recommendation_id}")
async def delete_recommendation(
    recommendation_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """
    추천 삭제 (관련 조언들도 함께 삭제)
    """
    try:
        uid = current_user.get("user_id")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        recommendation_service = RecommendationService(db)
        delete_success = await recommendation_service.delete_recommendation(recommendation_id, uid)
        
        if not delete_success:
            raise HTTPException(status_code=404, detail="Recommendation not found or cannot be deleted")
        
        return {
            "success": True,
            "message": "Recommendation and related advices deleted successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete recommendation: {str(e)}")

@router.get("/recommendations/history/{category}")
async def get_recommendation_history_by_category(
    category: str,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """
    카테고리별 사용자 추천 이력 조회
    """
    try:
        uid = current_user.get("user_id")
        if not uid:
            raise HTTPException(status_code=400, detail="User ID not found")
        
        if category not in ["food", "movement", "mindfulness"]:
            raise HTTPException(status_code=400, detail="Invalid category")
        
        recommendation_service = RecommendationService(db)
        history = recommendation_service.get_user_recommendations_by_category(uid, category, limit)
        
        return {
            "success": True,
            "data": history,
            "count": len(history),
            "category": category
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get recommendation history: {str(e)}") 