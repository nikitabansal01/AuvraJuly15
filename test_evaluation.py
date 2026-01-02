#!/usr/bin/env python3
"""
Test the evaluation service scoring.
"""
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

async def test_evaluation():
    print("=== EVALUATION SERVICE TEST ===")
    print()
    
    from app.services.evaluation_service import get_action_plan_evaluator
    
    evaluator = get_action_plan_evaluator()
    print(f"Evaluator initialized: {evaluator.GPT_MODEL}")
    print()
    
    # Mock actions (simplified)
    mock_actions = [
        {
            "title": "Test Action 1",
            "category": "food",
            "target_hormone": "cortisol",
            "specific_action": "Eat oatmeal for breakfast",
            "purpose": "Reduce stress",
            "research_studies": [
                {"title": "Oats and Cortisol", "journal": "J Nutrition", "year": 2023, "pmid": "12345678", "finding": "Oats reduce cortisol"}
            ]
        }
    ]
    
    # Mock user context
    mock_context = {
        "age": 30,
        "cycle_day": 14,
        "cycle_phase": "ovulatory",
        "primary_hormone": "estrogen",
        "top_concern": "stress",
        "diagnosed_conditions": ["PCOS"],
        "diet_preference": "vegetarian",
        "food_allergies": "none",
        "feedback_summary": "No summary yet"
    }
    
    print("Calculating scores (this calls GPT-4o-mini)...")
    try:
        scores, cost, citation_validity = await evaluator.calculate_scores(
            mock_actions, mock_context, "No previous feedback"
        )
        print(f"Cost: ${cost:.6f}")
        print(f"Citation Validity: {citation_validity}")
        print(f"Scores: {scores}")
        
        condition = scores.get("condition_appropriateness")
        print()
        if condition is not None:
            if condition < 70:
                print(f"FALLBACK WOULD TRIGGER: condition_appropriateness={condition} < 70")
            else:
                print(f"Quality OK: condition_appropriateness={condition} >= 70")
        else:
            print("WARNING: condition_appropriateness is None")
            
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    
    print()
    print("=== TEST COMPLETE ===")

if __name__ == "__main__":
    asyncio.run(test_evaluation())
