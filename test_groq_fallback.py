import asyncio
import os
import logging
import json
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import date

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock environment variables if needed (assuming .env is loaded)
from dotenv import load_dotenv
load_dotenv()

# Import the service
from app.services.action_plan_generator import ActionPlanGenerator
from app.core.database import AIModelUsageLog

async def test_groq_fallback_flow():
    print("\n🧪 STARTING GROQ FALLBACK INTEGRATION TEST")
    print("===========================================")
    
    # 1. Initialize Generator
    generator = ActionPlanGenerator()
    
    # 2. Mock User Context (Complex case to justify reasoning)
    mock_user_context = {
        "cycle_day": 22,
        "cycle_phase": "luteal",
        "primary_hormone": "cortisol",
        "secondary_hormone": "progesterone",
        "age": 34,
        "top_concern": "weight gain and fatigue",
        "diagnosed_conditions": ["PCOS", "Insulin Resistance"],
        "lifestyle_focus": ["eat", "move"],
        "diet_preference": "low carb",
        "food_allergies": ["peanuts"],
        "feedback_memory": "User likes quick breakfasts.",
        "chatbot_context": "User is feeling stressed today."
    }
    
    # 3. Mock DB Session
    mock_db = AsyncMock()
    
    # 4. Mock Internal Methods to isolate the Model Switching Logic
    
    # Mock _load_user_context to return our fake profile
    generator._load_user_context = AsyncMock(return_value=mock_user_context)
    
    # Mock _get_existing_plan to return None (force generation)
    generator._get_existing_plan = AsyncMock(return_value=None)
    
    # Mock _check_and_carryforward_frozen_plan to return None
    generator._check_and_carryforward_frozen_plan = AsyncMock(return_value=None)
    
    # Mock _generate_all_images to avoid image generation costs/time
    generator._generate_all_images = AsyncMock(return_value=([], 0.0))
    
    # Mock _store_plan to return a fake plan object with an ID
    mock_plan = MagicMock()
    mock_plan.id = 12345
    generator._store_plan = AsyncMock(return_value=mock_plan)
    
    # Mock _format_plan_response
    generator._format_plan_response = AsyncMock(return_value={"success": True, "plan": "mock_plan"})
    
    # 5. THE CRITICAL PART: Mock the Evaluator to FORCE Low Score
    # We need to patch where it's imported inside the method or class
    with patch('app.services.evaluation_service.get_action_plan_evaluator') as mock_get_evaluator:
        mock_evaluator = AsyncMock()
        mock_get_evaluator.return_value = mock_evaluator
        
        # Mock _get_recent_feedback
        mock_evaluator._get_recent_feedback.return_value = []
        
        # Mock calculate_scores to return LOW SCORE (< 70)
        # This triggers the fallback logic
        mock_evaluator.calculate_scores.return_value = (
            {
                "condition_appropriateness": 55,  # < 70 triggers switch!
                "personalization_score": 60,
                "feedback_alignment_score": 80,
                "citation_relevance_score": 90
            },
            0.01, # cost
            100   # citation validity
        )
        
        # 6. Mock the FIRST call to _generate_actions_via_gpt (OpenAI)
        # We want the first call to succeed but produce "bad" content (conceptually), 
        # but technically we just need it to return *something* so the code proceeds to evaluation.
        # However, we want the SECOND call (Groq) to be REAL.
        
        # We will wrap the real method to intercept calls
        original_generate = generator._generate_actions_via_gpt
        
        async def side_effect_generate(user_context, db, model_override=None):
            if model_override is None:
                print("   🤖 Call 1: OpenAI (Mocked) -> Returning dummy actions")
                # Return dummy actions for the first "failed" attempt
                return ([
                    {
                        "title": "Generic Toast",
                        "category": "food",
                        "target_hormone": "cortisol",
                        "food_items": ["bread"],
                        "food_amounts": ["1 slice"],
                        "variants": []
                    },
                    {
                        "title": "Generic Walk",
                        "category": "movement",
                        "target_hormone": "cortisol",
                        "exercise_types": ["walking"],
                        "exercise_durations": ["10 min"],
                        "exercise_intensities": ["low"],
                        "variants": []
                    },
                    {
                        "title": "Generic Breath",
                        "category": "mindfulness",
                        "target_hormone": "progesterone",
                        "mindfulness_techniques": ["breathing"],
                        "mindfulness_durations": ["5 min"],
                        "variants": []
                    },
                    {
                        "title": "Generic Sleep",
                        "category": "mindfulness",
                        "target_hormone": "progesterone",
                        "mindfulness_techniques": ["sleeping"],
                        "mindfulness_durations": ["8 hours"],
                        "variants": []
                    }
                ], 0.001)
            else:
                print(f"   🚀 Call 2: FALLBACK TRIGGERED -> Calling Real API: {model_override}")
                # Call the REAL method for the fallback to verify Groq integration
                return await original_generate(user_context, db, model_override)

        generator._generate_actions_via_gpt = AsyncMock(side_effect=side_effect_generate)
        
        # 7. Run the Generation
        print("\n▶️  Executing generate_new_plan...")
        result = await generator.generate_new_plan(
            user_id="test_user_123",
            plan_date=date.today(),
            user_timezone="UTC",
            db=mock_db
        )
        
        # 8. Verify Results
        print("\n✅ Execution Complete. Verifying Logic...")
        
        # Check if fallback was called
        calls = generator._generate_actions_via_gpt.call_args_list
        if len(calls) >= 2:
            print("   ✅ Fallback mechanism triggered (2 generation calls made)")
            
            # Verify the model override in the second call
            args, kwargs = calls[1]
            used_model = kwargs.get('model_override')
            print(f"   ✅ Fallback Model Requested: {used_model}")
            
            if used_model == "llama-3.3-70b-versatile":
                print("   ✅ CORRECT MODEL selected (Llama 3.3)")
            else:
                print(f"   ❌ WRONG MODEL selected: {used_model}")
        else:
            print("   ❌ Fallback NOT triggered (Only 1 call made)")
            
        # Verify DB Logging
        # We expect an add() call for AIModelUsageLog
        # We need to inspect the arguments passed to db.add()
        print("\n🔍 Checking Database Logs...")
        log_entry = None
        for call in mock_db.add.call_args_list:
            arg = call[0][0]
            if isinstance(arg, AIModelUsageLog):
                log_entry = arg
                break
        
        if log_entry:
            print("   ✅ AIModelUsageLog entry found!")
            print(f"      - Primary Model: {log_entry.primary_model}")
            print(f"      - Fallback Model: {log_entry.fallback_model}")
            print(f"      - Final Model: {log_entry.final_model_used}")
            print(f"      - Switch Reason: {log_entry.switch_reason}")
            
            if log_entry.fallback_model == "llama-3.3-70b-versatile":
                print("   ✅ Log confirms correct fallback model usage")
            else:
                print("   ❌ Log shows incorrect fallback model")
        else:
            print("   ❌ No AIModelUsageLog entry found in DB session")

    # Cleanup
    await generator.client.aclose()

if __name__ == "__main__":
    asyncio.run(test_groq_fallback_flow())
