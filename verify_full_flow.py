import asyncio
import os
import logging
import json
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import date, datetime

# Configure logging to show only what we want
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("app")
logger.setLevel(logging.INFO)

# Mock environment variables
os.environ["OPENAI_API_KEY"] = "mock-key"
# We need the real Groq key if we want to hit the real API, 
# assuming it's in the environment or .env file. 
# If not, the test might fail or we need to mock the response too.
# Based on previous turns, the system seems to have access to keys via the environment or .env loading.

from dotenv import load_dotenv
load_dotenv()

# Import the service
# We need to make sure we can import from app
import sys
sys.path.append(os.getcwd())

from app.services.action_plan_generator import ActionPlanGenerator
from app.core.database import AIModelUsageLog

async def verify_full_flow():
    print("\n🚀 STARTING END-TO-END VERIFICATION OF ACTION PLAN GENERATION")
    print("===========================================================")
    print("Objective: Verify Model Switching, Response Structure, and Logging\n")
    
    # 1. Initialize Generator
    generator = ActionPlanGenerator()
    
    # 2. Mock User Context
    print("👤 Step 1: Loading User Context (Mocked)")
    mock_user_context = {
        "cycle_day": 22,
        "cycle_phase": "luteal",
        "primary_hormone": "cortisol",
        "secondary_hormone": "progesterone",
        "age": 34,
        "top_concern": "weight gain and fatigue",
        "diagnosed_conditions": ["PCOS", "Insulin Resistance"],
        "lifestyle_focus": ["eat", "move", "pause"],
        "diet_preference": "low carb",
        "food_allergies": ["peanuts"],
        "feedback_memory": "User likes quick breakfasts.",
        "chatbot_context": "User is feeling stressed today."
    }
    print(f"   - User: 34yo female, Luteal Phase")
    print(f"   - Conditions: {mock_user_context['diagnosed_conditions']}")
    print(f"   - Top Concern: {mock_user_context['top_concern']}")
    
    # 3. Mock DB Session
    mock_db = AsyncMock()
    
    # 4. Mock Internal Methods
    generator._load_user_context = AsyncMock(return_value=mock_user_context)
    generator._get_existing_plan = AsyncMock(return_value=None)
    generator._check_and_carryforward_frozen_plan = AsyncMock(return_value=None)
    
    # Fix: Return the actions passed to image generation so they flow to _store_plan
    async def mock_generate_images(actions, *args):
        return actions, 0.0
    generator._generate_all_images = AsyncMock(side_effect=mock_generate_images)
    
    mock_plan = MagicMock()
    mock_plan.id = 12345
    generator._store_plan = AsyncMock(return_value=mock_plan)
    
    # We want to see the real formatted response structure
    # So we won't mock _format_plan_response entirely, but we need to be careful 
    # because it might depend on DB objects. 
    # Actually, let's mock it to just return the raw plan data so we can inspect it.
    # Fix: Accept any arguments (*args) to avoid TypeError
    generator._format_plan_response = AsyncMock(side_effect=lambda *args, **kwargs: {"success": True, "plan": args[0]})

    # 5. Mock Evaluator to FORCE Low Score
    print("\n📉 Step 2: Simulating Low Evaluation Score")
    with patch('app.services.evaluation_service.get_action_plan_evaluator') as mock_get_evaluator:
        mock_evaluator = AsyncMock()
        mock_get_evaluator.return_value = mock_evaluator
        mock_evaluator._get_recent_feedback.return_value = []
        
        # Return a score < 70 to trigger fallback
        mock_evaluator.calculate_scores.return_value = (
            {
                "condition_appropriateness": 55, 
                "personalization_score": 60,
                "feedback_alignment_score": 80,
                "citation_relevance_score": 90
            },
            0.01, 100
        )
        print("   - Condition Appropriateness: 55/100 (Threshold: 70)")
        print("   - Status: ⚠️ FAIL -> Should trigger fallback")

        # 6. Mock the OpenAI call (First Attempt)
        original_generate = generator._generate_actions_via_gpt
        
        async def side_effect_generate(user_context, db, model_override=None):
            if model_override is None:
                print("\n🤖 Step 3: Attempt 1 - OpenAI (Primary Model)")
                print("   - Simulating 'Generic/Bad' response...")
                return ([
                    {
                        "title": "Generic Toast",
                        "category": "food",
                        "target_hormone": "cortisol",
                        "food_items": ["bread"],
                        "food_amounts": ["1 slice"],
                        "variants": [],
                        "research_studies": []
                    },
                    {
                        "title": "Generic Walk",
                        "category": "movement",
                        "target_hormone": "cortisol",
                        "exercise_types": ["walking"],
                        "exercise_durations": ["10 min"],
                        "exercise_intensities": ["low"],
                        "variants": [],
                        "research_studies": []
                    },
                    {
                        "title": "Generic Breath",
                        "category": "mindfulness",
                        "target_hormone": "progesterone",
                        "mindfulness_techniques": ["breathing"],
                        "mindfulness_durations": ["5 min"],
                        "variants": [],
                        "research_studies": []
                    },
                    {
                        "title": "Generic Sleep",
                        "category": "mindfulness",
                        "target_hormone": "progesterone",
                        "mindfulness_techniques": ["sleeping"],
                        "mindfulness_durations": ["8 hours"],
                        "variants": [],
                        "research_studies": []
                    }
                ], 0.001)
            else:
                print(f"\n🚀 Step 4: Attempt 2 - FALLBACK TRIGGERED")
                print(f"   - Switching to Model: {model_override}")
                print("   - Sending request to Groq API...")
                # Call the REAL method for the fallback
                return await original_generate(user_context, db, model_override)

        generator._generate_actions_via_gpt = AsyncMock(side_effect=side_effect_generate)
        
        # 7. Run Generation
        result = await generator.generate_new_plan(
            user_id="test_user_123",
            plan_date=date.today(),
            user_timezone="UTC",
            db=mock_db
        )
        
        # 8. Inspect the Result (The Plan)
        # The result from generate_new_plan is what _format_plan_response returns.
        # We mocked _format_plan_response to return {"success": True, "plan": plan_object}
        # But wait, _store_plan returns a mock object. 
        # The REAL data comes from the second call to _generate_actions_via_gpt.
        # We need to capture that return value to show the user.
        
        # Let's grab the arguments passed to _store_plan to see the actual data
        # _store_plan is called with KWARGS in the actual code
        store_call_args = generator._store_plan.call_args
        
        # Handle both positional and keyword args
        if store_call_args.kwargs and 'actions' in store_call_args.kwargs:
            final_actions = store_call_args.kwargs['actions']
        elif len(store_call_args.args) > 3:
            final_actions = store_call_args.args[3]
        else:
            print("❌ Could not find 'actions' in _store_plan arguments")
            final_actions = []
        
        print("\n📦 Step 5: Final Response Verification")
        print("   (This is the JSON that will be sent to the frontend)")
        print("   ---------------------------------------------------")
        
        for i, action in enumerate(final_actions):
            print(f"\n   🔹 Action {i+1}: {action.get('title')}")
            print(f"      Category: {action.get('category').upper()}")
            print(f"      Target Hormone: {action.get('target_hormone')}")
            specific_action = action.get('specific_action')
            if specific_action:
                print(f"      Specific Action: {specific_action[:100]}...")
            else:
                print("      Specific Action: [MISSING]")
            
            # Verify Category Specific Fields
            if action.get('category') == 'food':
                print(f"      🍽️  Food: {action.get('food_items')} ({action.get('food_amounts')})")
            elif action.get('category') == 'movement':
                print(f"      🏃‍♀️ Movement: {action.get('exercise_types')} ({action.get('exercise_durations')})")
            elif action.get('category') == 'mindfulness':
                print(f"      🧘 Mindfulness: {action.get('mindfulness_techniques')} ({action.get('mindfulness_durations')})")
                
            # Verify Research
            studies = action.get('research_studies', [])
            if studies:
                study = studies[0]
                print(f"      📚 Citation: {study.get('title')}")
                print(f"         Link: {study.get('verification_link')}")
            else:
                print("      ❌ NO CITATION FOUND")

    # 9. Verify DB Log
    print("\n📝 Step 6: Database Log Verification")
    log_entry = None
    for call in mock_db.add.call_args_list:
        arg = call[0][0]
        if isinstance(arg, AIModelUsageLog):
            log_entry = arg
            break
            
    if log_entry:
        print(f"   ✅ Log Entry Created:")
        print(f"      - Primary: {log_entry.primary_model}")
        print(f"      - Fallback: {log_entry.fallback_model}")
        print(f"      - Reason: {log_entry.switch_reason}")
        
        if log_entry.fallback_model == "llama-3.3-70b-versatile":
            print("   ✅ SUCCESS: Correct fallback model logged.")
        else:
            print(f"   ❌ FAILURE: Wrong model logged ({log_entry.fallback_model})")
    else:
        print("   ❌ FAILURE: No log entry found.")

    print("\n✅ End-to-End Verification Complete.")

if __name__ == "__main__":
    asyncio.run(verify_full_flow())
