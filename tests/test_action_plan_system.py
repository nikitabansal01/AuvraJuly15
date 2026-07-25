"""
AUVRA Action Plan System Tests

Tests for the new action plan generation system:
- ActionPlanGenerator
- ImageLibraryService
- API endpoints
- Feedback and replacement
"""

import asyncio
import os
import sys
import logging
from datetime import datetime, timezone, date

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# MOCK TESTS (No database required)
# ============================================================================

def test_hormone_personas():
    """Test that hormone personas are properly defined."""
    from app.services.action_plan_generator import HORMONE_PERSONAS, DEFAULT_PERSONA
    
    print("\n🧪 Testing Hormone Personas...")
    
    # Check all expected hormones
    expected_hormones = ["cortisol", "progesterone", "estrogen", "testosterone", "insulin", "thyroid"]
    
    for hormone in expected_hormones:
        assert hormone in HORMONE_PERSONAS, f"Missing persona for {hormone}"
        persona = HORMONE_PERSONAS[hormone]
        assert "name" in persona, f"Missing name for {hormone}"
        assert "emoji" in persona, f"Missing emoji for {hormone}"
        assert "phase_behavior" in persona, f"Missing phase_behavior for {hormone}"
        print(f"  ✅ {hormone}: {persona['name']} {persona['emoji']}")
    
    # Check default persona
    assert "name" in DEFAULT_PERSONA
    print(f"  ✅ Default: {DEFAULT_PERSONA['name']} {DEFAULT_PERSONA['emoji']}")
    
    print("✅ Hormone personas test passed!")


def test_prompt_templates():
    """Test GPT prompt template formatting."""
    from app.services.action_plan_generator import ACTION_GENERATION_PROMPT, SYSTEM_PROMPT
    
    print("\n🧪 Testing Prompt Templates...")
    
    # Test that prompts can be formatted
    test_values = {
        "num_actions": 4,
        "age": 30,
        "cycle_day": 14,
        "cycle_phase": "ovulation",
        "primary_hormone": "cortisol",
        "secondary_hormone": "progesterone",
        "lifestyle_focus": "eat, move, pause",
        "top_concern": "stress",
        "diagnosed_conditions": "none",
        "period_concerns": "cramps",
        "body_concerns": "bloating",
        "skin_hair_concerns": "acne",
        "mental_health_concerns": "anxiety",
        "family_history": "none",
        "diet_preference": "omnivore",
        "food_allergies": "none",
        "cuisine_preference": "mediterranean",
        "cultural_background": "none",
        "dine_out_frequency": "rarely",
        "body_metrics": "normal",
        "cravings": "chocolate",
        "birth_control": "none",
        "hormone_phase_context": "Estrogen is rising.",
        "feedback_summary": "User likes yoga.",
        "chatbot_context": "No recent chat.",
        "stress_level": "high",
        "sleep_duration": "6-7 hours",
        "workout_intensity": "moderate",
        "feedback_memory": "No previous feedback",
        "primary_count": 2,
        "secondary_count": 2,
        "category_guidance": "Balanced mix",
        # Newer prompt placeholders (keep these lightweight so this stays a pure formatting test)
        "current_streak": 0,
        "longest_streak": 0,
        "weekly_checkin_insights": "No weekly check-in data yet",
        "daily_review_insights": "No daily review data yet",
        "care_plan_checkin_insights": "No care plan check-in data yet",
        "symptom_checkin_insights": "No symptom check-in data yet",
        "allowed_symptoms": "[]",
        "allowed_conditions": "[]",
        "recently_recommended": "(none)",
        "health_situation_summary": "No additional health context",
        "unified_memory_context": "No saved memory",
        "chat_history": "No prior chat"
    }
    
    try:
        formatted = ACTION_GENERATION_PROMPT.format(**test_values)
        assert "cortisol" in formatted
        assert "4" in formatted
        print("  ✅ ACTION_GENERATION_PROMPT formats correctly")
    except KeyError as e:
        print(f"  ❌ Missing placeholder: {e}")
        raise
    
    # Check system prompt exists and has key content
    assert "research" in SYSTEM_PROMPT.lower()
    assert "hormone" in SYSTEM_PROMPT.lower()
    print("  ✅ SYSTEM_PROMPT contains required content")
    
    print("✅ Prompt templates test passed!")


def test_category_guidance():
    """Test category guidance generation based on lifestyle focus."""
    from app.services.action_plan_generator import ActionPlanGenerator
    
    print("\n🧪 Testing Category Guidance...")
    
    generator = ActionPlanGenerator()
    
    # Test different lifestyle focus combinations
    test_cases = [
        (["eat", "move", "pause"], "Balanced"),
        (["eat", "move"], "food and movement"),
        (["eat"], "food"),
        ([], "Balanced"),
    ]
    
    for focus, expected_keyword in test_cases:
        guidance = generator._get_category_guidance(focus)
        assert expected_keyword.lower() in guidance.lower(), f"Expected '{expected_keyword}' in guidance for {focus}"
        print(f"  ✅ Focus {focus}: '{guidance[:50]}...'")
    
    print("✅ Category guidance test passed!")


def test_cycle_calculation():
    """Test cycle day and phase calculation."""
    from app.services.action_plan_generator import ActionPlanGenerator
    from datetime import timedelta
    
    print("\n🧪 Testing Cycle Calculation...")
    
    generator = ActionPlanGenerator()
    
    # Test different scenarios
    now = datetime.now(timezone.utc)
    
    # Day 1 (period just started)
    day1 = now
    cycle_day, phase = generator._calculate_cycle_info(day1, "28")
    print(f"  Today: cycle_day={cycle_day}, phase={phase}")
    
    # Day 5 (end of menstrual)
    day5 = now - timedelta(days=4)
    cycle_day, phase = generator._calculate_cycle_info(day5, "28")
    print(f"  Day 5: cycle_day={cycle_day}, phase={phase}")
    assert phase == "menstrual" or phase == "follicular"
    
    # Day 14 (ovulation)
    day14 = now - timedelta(days=13)
    cycle_day, phase = generator._calculate_cycle_info(day14, "28")
    print(f"  Day 14: cycle_day={cycle_day}, phase={phase}")
    
    # Day 21 (luteal)
    day21 = now - timedelta(days=20)
    cycle_day, phase = generator._calculate_cycle_info(day21, "28")
    print(f"  Day 21: cycle_day={cycle_day}, phase={phase}")
    
    # Test with None
    none_day, none_phase = generator._calculate_cycle_info(None, None)
    assert none_day is None
    assert none_phase is None
    print("  ✅ None input handled correctly")
    
    print("✅ Cycle calculation test passed!")


def test_image_library_service_init():
    """Test ImageLibraryService initialization."""
    from app.services.image_library_service import ImageLibraryService, get_image_library_service
    
    print("\n🧪 Testing ImageLibraryService Init...")
    
    service = get_image_library_service()
    
    assert service is not None
    assert service.SIMILARITY_THRESHOLD == 0.88
    assert service.COST_PER_IMAGE == 0.0
    print(f"  ✅ Service initialized")
    print(f"  ✅ Similarity threshold: {service.SIMILARITY_THRESHOLD}")
    print(f"  ✅ Cost per image: ${service.COST_PER_IMAGE}")
    
    # Test singleton
    service2 = get_image_library_service()
    assert service is service2
    print("  ✅ Singleton pattern works")
    
    print("✅ ImageLibraryService init test passed!")


def test_cosine_similarity():
    """Test cosine similarity calculation."""
    from app.services.image_library_service import ImageLibraryService
    
    print("\n🧪 Testing Cosine Similarity...")
    
    service = ImageLibraryService()
    
    # Test identical vectors
    vec1 = [1.0, 0.0, 0.0]
    sim = service._cosine_similarity(vec1, vec1)
    assert abs(sim - 1.0) < 0.001, f"Identical vectors should have similarity 1.0, got {sim}"
    print(f"  ✅ Identical vectors: {sim:.4f}")
    
    # Test orthogonal vectors
    vec2 = [0.0, 1.0, 0.0]
    sim = service._cosine_similarity(vec1, vec2)
    assert abs(sim) < 0.001, f"Orthogonal vectors should have similarity 0.0, got {sim}"
    print(f"  ✅ Orthogonal vectors: {sim:.4f}")
    
    # Test similar vectors
    vec3 = [0.9, 0.1, 0.0]
    sim = service._cosine_similarity(vec1, vec3)
    assert sim > 0.9, f"Similar vectors should have high similarity, got {sim}"
    print(f"  ✅ Similar vectors: {sim:.4f}")
    
    # Test zero vector
    zero = [0.0, 0.0, 0.0]
    sim = service._cosine_similarity(vec1, zero)
    assert sim == 0.0
    print(f"  ✅ Zero vector handling: {sim:.4f}")
    
    print("✅ Cosine similarity test passed!")


def test_prompt_enhancement():
    """Test image prompt enhancement."""
    from app.services.image_library_service import ImageLibraryService
    
    print("\n🧪 Testing Prompt Enhancement...")
    
    service = ImageLibraryService()
    
    test_prompt = "bowl of oatmeal with berries"
    enhanced = service._enhance_prompt(test_prompt)
    
    assert "oatmeal" in enhanced.lower()
    assert "professional" in enhanced.lower()
    assert "wellness" in enhanced.lower()
    print(f"  Original: {test_prompt}")
    print(f"  Enhanced: {enhanced[:100]}...")
    
    print("✅ Prompt enhancement test passed!")


def test_legacy_format_conversion():
    """Test conversion to legacy assignment format."""
    from app.api.v1.endpoints.action_plan import _convert_to_legacy_format
    
    print("\n🧪 Testing Legacy Format Conversion...")
    
    # Test with sample action plan result
    sample_result = {
        "success": True,
        "plan_id": 1,
        "plan_date": "2025-01-15",
        "primary_hormone": "cortisol",
        "secondary_hormones": ["progesterone"],
        "cycle_day": 14,
        "cycle_phase": "ovulation",
        "actions": [
            {
                "id": 1,
                "slot": 1,
                "time_slot": "morning",
                "category": "food",
                "title": "Magnesium-Rich Breakfast",
                "specific_action": "Start your day with oatmeal topped with almonds",
                "purpose": "Support cortisol regulation",
                "target_hormone": "cortisol",
                "hormone_persona_intro": "Hey love, Cora here...",
                "hero_image_url": "https://example.com/image.png",
                "research_studies": [],
                "is_completed": False,
                "is_replaced": False,
                "variants": [],
                "food_items": ["oatmeal", "almonds"],
                "food_amounts": ["1 cup", "1/4 cup"]
            },
            {
                "id": 2,
                "slot": 2,
                "time_slot": "afternoon",
                "category": "movement",
                "title": "Gentle Yoga",
                "specific_action": "15-minute gentle yoga flow",
                "purpose": "Reduce stress hormones",
                "target_hormone": "cortisol",
                "hero_image_url": None,
                "research_studies": [],
                "is_completed": True,
                "variants": []
            }
        ]
    }
    
    legacy = _convert_to_legacy_format(sample_result)
    
    # Check structure
    assert "assignments" in legacy
    assert "morning" in legacy["assignments"]
    assert "afternoon" in legacy["assignments"]
    assert "completed" in legacy["assignments"]
    assert legacy["total_assignments"] == 2
    assert legacy["completed_assignments"] == 1
    assert legacy["generation_source"] == "action_plan"
    
    # Check that completed action is in completed bucket
    assert len(legacy["assignments"]["completed"]) == 1
    assert legacy["assignments"]["completed"][0]["title"] == "Gentle Yoga"
    
    # Check morning action
    assert len(legacy["assignments"]["morning"]) == 1
    assert legacy["assignments"]["morning"][0]["title"] == "Magnesium-Rich Breakfast"
    
    print(f"  ✅ Total assignments: {legacy['total_assignments']}")
    print(f"  ✅ Completed: {legacy['completed_assignments']}")
    print(f"  ✅ Morning actions: {len(legacy['assignments']['morning'])}")
    print(f"  ✅ Completed actions: {len(legacy['assignments']['completed'])}")
    
    # Test with error result
    error_result = {"success": False, "error": "Test error"}
    legacy_error = _convert_to_legacy_format(error_result)
    assert legacy_error["total_assignments"] == 0
    print("  ✅ Error handling works")
    
    print("✅ Legacy format conversion test passed!")


def test_pydantic_models():
    """Test Pydantic model validation."""
    from app.models.action_plan_models import (
        ActionPlanFeedbackRequest,
        ActionReplacementRequest,
        ActionItemInfo,
        VariantInfo
    )
    
    print("\n🧪 Testing Pydantic Models...")
    
    # Test FeedbackRequest
    feedback = ActionPlanFeedbackRequest(
        item_id=1,
        feedback_type="like",
        time_shown=datetime.now(timezone.utc)
    )
    assert feedback.item_id == 1
    print("  ✅ ActionPlanFeedbackRequest validated")
    
    # Test ReplacementRequest
    replacement = ActionReplacementRequest(
        item_id=1,
        reason="Too complicated"
    )
    assert replacement.reason == "Too complicated"
    print("  ✅ ActionReplacementRequest validated")
    
    # Test VariantInfo
    variant = VariantInfo(
        variant_type="tasty",
        title="Chocolate version",
        description="With dark chocolate"
    )
    assert variant.variant_type == "tasty"
    print("  ✅ VariantInfo validated")
    
    # Test ActionItemInfo
    action = ActionItemInfo(
        id=1,
        slot=1,
        time_slot="morning",
        category="food",
        title="Test Action",
        specific_action="Do this thing",
        target_hormone="cortisol"
    )
    assert action.category == "food"
    print("  ✅ ActionItemInfo validated")
    
    print("✅ Pydantic models test passed!")


# ============================================================================
# ASYNC TESTS (Require API keys but not database)
# ============================================================================

async def test_embedding_generation():
    """Test OpenAI embedding generation (requires API key)."""
    from app.services.image_library_service import ImageLibraryService
    
    print("\n🧪 Testing Embedding Generation...")
    
    service = ImageLibraryService()
    
    if not service.openai_api_key:
        print("  ⏭️ Skipped (OPENAI_API_KEY not set)")
        return
    
    test_text = "Bowl of oatmeal with fresh berries and almonds"
    embedding = await service._get_embedding(test_text)
    
    assert embedding is not None
    assert len(embedding) == 1536  # ada-002 dimension
    print(f"  ✅ Embedding generated: {len(embedding)} dimensions")
    
    # Test caching
    embedding2 = await service._get_embedding(test_text)
    assert embedding == embedding2
    print("  ✅ Embedding caching works")
    
    print("✅ Embedding generation test passed!")


async def test_gpt_action_generation():
    """Test GPT action generation (requires API key)."""
    from app.services.action_plan_generator import ActionPlanGenerator
    
    print("\n🧪 Testing GPT Action Generation...")
    
    generator = ActionPlanGenerator()
    
    if not generator.openai_api_key:
        print("  ⏭️ Skipped (OPENAI_API_KEY not set)")
        return
    
    # Test with sample user context
    user_context = {
        "user_id": "test_user",
        "primary_hormone": "cortisol",
        "secondary_hormone": "progesterone",
        "cycle_day": 14,
        "cycle_phase": "ovulation",
        "lifestyle_focus": ["eat", "move", "pause"],
        "top_concern": "stress management",
        "diagnosed_conditions": [],
        "stress_level": "high",
        "sleep_duration": "6-7 hours",
        "workout_intensity": "moderate",
        "feedback_memory": "No previous feedback"
    }
    
    actions, cost = await generator._generate_actions_via_gpt(user_context)
    
    assert actions is not None
    assert len(actions) == 4, f"Expected 4 actions, got {len(actions)}"
    print(f"  ✅ Generated {len(actions)} actions")
    print(f"  ✅ Cost: ${cost:.4f}")
    
    # Verify action structure
    for i, action in enumerate(actions):
        assert "title" in action, f"Action {i} missing title"
        assert "category" in action, f"Action {i} missing category"
        assert "target_hormone" in action, f"Action {i} missing target_hormone"
        print(f"  ✅ Action {i+1}: {action['title']} ({action['category']}, {action['target_hormone']})")
    
    print("✅ GPT action generation test passed!")


# ============================================================================
# RUN TESTS
# ============================================================================

def run_sync_tests():
    """Run all synchronous tests."""
    print("\n" + "="*60)
    print("🚀 RUNNING SYNCHRONOUS TESTS")
    print("="*60)
    
    test_hormone_personas()
    test_prompt_templates()
    test_category_guidance()
    test_cycle_calculation()
    test_image_library_service_init()
    test_cosine_similarity()
    test_prompt_enhancement()
    test_legacy_format_conversion()
    test_pydantic_models()
    
    print("\n" + "="*60)
    print("✅ ALL SYNCHRONOUS TESTS PASSED")
    print("="*60)


async def run_async_tests():
    """Run all asynchronous tests."""
    print("\n" + "="*60)
    print("🚀 RUNNING ASYNCHRONOUS TESTS")
    print("="*60)
    
    await test_embedding_generation()
    await test_gpt_action_generation()
    
    print("\n" + "="*60)
    print("✅ ALL ASYNCHRONOUS TESTS PASSED")
    print("="*60)


if __name__ == "__main__":
    print("\n" + "="*60)
    print("🧪 AUVRA ACTION PLAN SYSTEM TESTS")
    print("="*60)
    
    # Run sync tests
    run_sync_tests()
    
    # Run async tests
    asyncio.run(run_async_tests())
    
    print("\n" + "="*60)
    print("🎉 ALL TESTS COMPLETED!")
    print("="*60)
