"""
Test V3 Mobile App Integration
==============================

Tests the complete mobile app flow with V3 engine:
questions.py -> RecommendationService -> AIService -> V3 Engine -> format conversion
"""

import asyncio
import sys
import os

import pytest

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Integration test disabled by default (set RUN_INTEGRATION_TESTS=1 to enable)",
)
async def test_mobile_integration():
    """Test V3 through the AIService path (same as mobile app)"""
    from app.models.ai_models import UserProfile
    from app.services.ai_service import AIService
    
    print("=" * 70)
    print("🧪 V3 MOBILE APP INTEGRATION TEST")
    print("=" * 70)
    
    # Create a test user profile matching what RecommendationService passes
    # The UserProfile pydantic model has limited fields, so we use what's available
    test_profile = UserProfile(
        primaryImbalance="insulin_resistance",
        secondaryImbalances=["androgen_high", "inflammation"],
        conditions=["pcos"],
        symptoms=["heavy_bleeding", "painful_cramps", "weight_gain", "fatigue", "acne", "anxiety", "mood_swings"],
        cyclePhase="follicular",
        birthControlStatus="No",
        age=28,
    )
    
    categories = ["food", "movement", "mindfulness"]
    all_results = {}
    
    for category in categories:
        print(f"\n{'='*50}")
        print(f"📂 Testing category: {category.upper()}")
        print("="*50)
        
        try:
            recommendations = await AIService.generate_session_recommendations(
                test_profile, 
                category
            )
            
            if recommendations:
                all_results[category] = recommendations
                print(f"\n✅ SUCCESS: Got {len(recommendations)} recommendations for {category}")
                
                for i, rec in enumerate(recommendations[:2], 1):  # Show first 2
                    print(f"\n--- Recommendation {i} ---")
                    print(f"  Title: {rec.get('title', 'N/A')}")
                    print(f"  Purpose: {rec.get('purpose', 'N/A')[:100]}...")
                    print(f"  Specific Action: {rec.get('specificAction', 'N/A')[:100]}...")
                    print(f"  Priority: {rec.get('priority', 'N/A')}")
                    print(f"  Citation Verified: {rec.get('citation_verified', False)}")
                    print(f"  RAG Version: {rec.get('rag_version', 'N/A')}")
                    
                    # Check researchBacking (critical for mobile app)
                    research = rec.get('researchBacking', {})
                    if research:
                        print(f"  📚 Research Backing:")
                        print(f"     Summary: {research.get('summary', 'N/A')[:100]}...")
                        studies = research.get('studies', [])
                        print(f"     Studies: {len(studies)} citations")
                        for study in studies[:2]:
                            print(f"       - PMID {study.get('pmid')}: {study.get('title', '')[:50]}...")
                    else:
                        print(f"  ⚠️ No researchBacking found!")
                    
                    # Check category-specific fields
                    if category == "food":
                        print(f"  Food Items: {rec.get('food_items', [])}")
                    elif category == "movement":
                        print(f"  Exercise Types: {rec.get('exercise_types', [])}")
                    elif category == "mindfulness":
                        print(f"  Techniques: {rec.get('mindfulness_techniques', [])}")
            else:
                print(f"\n⚠️ No recommendations returned for {category}")
                
        except Exception as e:
            print(f"\n❌ FAILED for {category}: {e}")
            import traceback
            traceback.print_exc()
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 INTEGRATION TEST SUMMARY")
    print("=" * 70)
    
    total_recs = sum(len(recs) for recs in all_results.values())
    total_verified = sum(
        sum(1 for r in recs if r.get('citation_verified', False))
        for recs in all_results.values()
    )
    
    print(f"Categories tested: {list(all_results.keys())}")
    print(f"Total recommendations: {total_recs}")
    print(f"Verified citations: {total_verified}/{total_recs}")
    
    # Check mobile app format compliance
    print("\n📱 Mobile App Format Compliance:")
    format_issues = []
    
    required_fields = ['title', 'purpose', 'specificAction', 'priority', 'researchBacking']
    for category, recs in all_results.items():
        for i, rec in enumerate(recs):
            for field in required_fields:
                if field not in rec or rec[field] is None:
                    format_issues.append(f"{category}[{i}] missing '{field}'")
    
    if format_issues:
        print(f"  ⚠️ Format issues found:")
        for issue in format_issues[:5]:
            print(f"     - {issue}")
    else:
        print(f"  ✅ All recommendations have required fields!")
    
    print("\n" + "=" * 70)
    print("🎉 V3 MOBILE APP INTEGRATION TEST COMPLETE!")
    print("=" * 70)
    
    return all_results


if __name__ == "__main__":
    results = asyncio.run(test_mobile_integration())
