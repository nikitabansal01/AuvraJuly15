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
    Generate general recommendations API.
    Prompt engineering based recommendations.
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
        # Fallback: Use fallback model if confidence is low or no recommendations
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
                logger.error(f"Unsupported fallback model provider: {fallback_config['provider']}")
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
    
    # Save recommendation results to database
    recommendation_service = RecommendationService(db)
    uid = current_user.get("user_id")
    if uid:
        save_success = await recommendation_service.save_recommendations(uid, result, "general")
        if not save_success:
            # Return recommendation results even if save fails
            pass

    return result

@router.post("/recommendations/rag", response_model=RecommendationResult)
async def generate_rag_recommendations(
    request: RecommendationRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_active_user)
):
    """
    Generate RAG-based recommendations API.
    Uses RAG v2 with quality scoring, safety checks, and citation validation.
    """
    user_profile_obj = request.userProfile
    
    try:
        # Import RAG v2 orchestrator
        from app.services.rag.rag_orchestrator import generate_rag_recommendations as rag_v2_generate
        
        # Generate RAG-based recommendations for all categories
        categories = ["food", "movement", "mindfulness"]
        rag_results = {}
        confidences = []
        
        for category in categories:
            try:
                recommendations = await rag_v2_generate(
                    user_profile=user_profile_obj,
                    category=category,
                    use_rag=True
                )
                rag_results[category] = recommendations
                
                # Calculate confidence based on citation verification
                if recommendations:
                    verified = sum(1 for r in recommendations if r.get('citation_verified', False))
                    conf = 70 + (verified / len(recommendations)) * 30 if recommendations else 70
                    confidences.append(conf)
                else:
                    confidences.append(40)
            except Exception as cat_error:
                rag_results[category] = []
                confidences.append(30)
        
        confidence = min(max(confidences), 100) if confidences else 30
        
        result = RecommendationResult(
            food=rag_results.get("food", []),
            movement=rag_results.get("movement", []),
            mindfulness=rag_results.get("mindfulness", []),
            userProfile=user_profile_obj,
            generatedAt=None,
            confidence=confidence,
            rawLLMResponse="RAG v2: Quality-scored papers with citation validation"
        )
        
        # Save recommendation results to database
        recommendation_service = RecommendationService(db)
        uid = current_user.get("user_id")
        if uid:
            save_success = await recommendation_service.save_recommendations(uid, result, "rag_v2")
            if not save_success:
                # Return recommendation results even if save fails
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
    """Get user's recommendation history."""
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
    """Get advices for a specific recommendation."""
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
    """Delete recommendation (including related advices)."""
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
    """Get user's recommendation history by category."""
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