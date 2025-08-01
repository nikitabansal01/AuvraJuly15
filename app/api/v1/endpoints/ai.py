from fastapi import APIRouter, HTTPException, status, Request
from app.models.ai_models import UserProfile, RecommendationResult
from app.services.ai_service import AIService
from pydantic import BaseModel

router = APIRouter()

class RecommendationRequest(BaseModel):
    userProfile: UserProfile

@router.post("/recommendations", response_model=RecommendationResult)
async def generate_recommendations(request: Request):
    body = await request.json()
    user_profile = body.get("userProfile")
    if not user_profile:
        raise HTTPException(status_code=400, detail="userProfile 필수")

    user_profile_obj = UserProfile(**user_profile)
    categories = ["food", "movement", "mindfulness"]
    result_dict = {"food": [], "movement": [], "mindfulness": []}
    confidences = []
    raw_llm_responses = []

    for category in categories:
        prompt = AIService.suggest_llm_prompt_for_recommendations(user_profile_obj, category)
        llm_response = await AIService.call_openai(prompt)
        confidence = AIService.evaluate_llm_confidence(llm_response)
        recommendations = AIService.parse_recommendations_from_llm(llm_response)
        # Fallback: 신뢰도 낮거나 추천 없음 → Groq 사용
        if confidence < 60 or not recommendations:
            groq_response = await AIService.call_groq(prompt)
            groq_confidence = AIService.evaluate_llm_confidence(groq_response)
            groq_recommendations = AIService.parse_recommendations_from_llm(groq_response)
            if groq_recommendations and groq_confidence > confidence:
                llm_response = groq_response
                confidence = groq_confidence
                recommendations = groq_recommendations
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
    return result

@router.post("/recommendations/rag", response_model=RecommendationResult)
async def generate_rag_recommendations(request: RecommendationRequest):
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
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RAG recommendation generation failed: {str(e)}") 