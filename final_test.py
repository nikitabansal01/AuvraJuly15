#!/usr/bin/env python3
"""AUVRA Comprehensive Test Suite - Final Version.

Note: This file is designed to be run as a script. It must NOT execute at import
time, otherwise pytest will crash during test collection.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    # Run relative to this repository folder (avoid hard-coded absolute paths).
    repo_root = Path(__file__).resolve().parent
    os.chdir(repo_root)

    from dotenv import load_dotenv

    load_dotenv(repo_root / ".env")

    import requests

    print("=" * 70)
    print("🧪 AUVRA COMPREHENSIVE TEST SUITE")
    print("=" * 70)

    passed = 0
    failed = 0

    def test(name: str, condition: bool, error: str = "") -> None:
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  ✅ {name}")
        else:
            failed += 1
            print(f"  ❌ {name}: {error}")

    # TEST 1: Environment
    print("\n📋 TEST 1: Environment Variables")
    print("-" * 50)
    test("OPENAI_API_KEY", bool(os.getenv("OPENAI_API_KEY")))
    test("GROQ_API_KEY", bool(os.getenv("GROQ_API_KEY")))
    test("DATABASE_URL", bool(os.getenv("DATABASE_URL")))
    test(
        "GROQ_FALLBACK_MODEL=openai/gpt-oss-120b",
        os.getenv("GROQ_FALLBACK_MODEL") == "openai/gpt-oss-120b",
    )
    print(f"\n  🔧 Configured Fallback: {os.getenv('GROQ_FALLBACK_MODEL')}")

    # TEST 2: Imports
    print("\n📋 TEST 2: Service Imports")
    print("-" * 50)
    try:
        from app.services.action_plan_generator import ActionPlanGenerator, HORMONE_PERSONAS

        test("ActionPlanGenerator", True)
        test("HORMONE_PERSONAS (6 hormones)", len(HORMONE_PERSONAS) >= 6)
    except Exception as e:
        test("ActionPlanGenerator", False, str(e)[:50])

    try:
        from app.services.image_library_service import ImageLibraryService

        test("ImageLibraryService", True)
    except Exception as e:
        test("ImageLibraryService", False, str(e)[:50])

    try:
        from app.services.evaluation_service import ActionPlanEvaluator

        test("EvaluationService", True)
    except Exception as e:
        test("EvaluationService", False, str(e)[:50])

    # TEST 3: Pydantic Models
    print("\n📋 TEST 3: Pydantic Model Validation")
    print("-" * 50)
    from app.services.action_plan_generator import ActionItemModel

    try:
        _ = ActionItemModel(
            title="Test Food",
            category="food",
            time_slot="morning",
            specific_action="Test",
            purpose="Test",
            target_hormone="cortisol",
            hormone_persona_intro="Test",
            image_prompt="test",
            research_studies=[],
            variants=[],
            food_items=["oats"],
            food_amounts=["1 cup"],
            exercise_types=[],
            exercise_durations=[],
            exercise_intensities=[],
            mindfulness_techniques=[],
            mindfulness_durations=[],
            symptoms=[],
            conditions=[],
        )
        test("Valid FOOD action", True)
    except Exception as e:
        test("Valid FOOD action", False, str(e)[:50])

    try:
        _ = ActionItemModel(
            title="Test",
            category="food",
            time_slot="lunch",  # Invalid
            specific_action="Test",
            purpose="Test",
            target_hormone="cortisol",
            hormone_persona_intro="Test",
            image_prompt="test",
            research_studies=[],
            variants=[],
            food_items=[],
            food_amounts=[],
            exercise_types=[],
            exercise_durations=[],
            exercise_intensities=[],
            mindfulness_techniques=[],
            mindfulness_durations=[],
            symptoms=[],
            conditions=[],
        )
        test("Invalid time_slot rejected", False, "Should have failed")
    except Exception:
        test("Invalid time_slot rejected", True)

    # TEST 4: API Connectivity
    print("\n📋 TEST 4: API Connectivity")
    print("-" * 50)
    try:
        r = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"},
            timeout=10,
        )
        test("OpenAI API", r.status_code == 200)
    except Exception as e:
        test("OpenAI API", False, str(e)[:50])

    try:
        r = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"},
            timeout=10,
        )
        test("Groq API", r.status_code == 200)
        if r.status_code == 200:
            models = [m["id"] for m in r.json().get("data", [])]
            fallback = os.getenv("GROQ_FALLBACK_MODEL")
            test(f"Fallback '{fallback}' available", bool(fallback) and fallback in models)
    except Exception as e:
        test("Groq API", False, str(e)[:50])

    # TEST 5: Groq gpt-oss-120b Direct Call
    print("\n📋 TEST 5: Groq openai/gpt-oss-120b Direct Call")
    print("-" * 50)
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-oss-120b",
                "messages": [{"role": "user", "content": "Say hello in one word"}],
                "max_tokens": 20,
            },
            timeout=30,
        )
        test("gpt-oss-120b responds", r.status_code == 200, f"Status {r.status_code}")
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"]
            print(f"      Response: {content[:50]}")
    except Exception as e:
        test("gpt-oss-120b responds", False, str(e)[:50])

    # TEST 6: Hormone Personas
    print("\n📋 TEST 6: Hormone Personas")
    print("-" * 50)
    from app.services.action_plan_generator import DEFAULT_PERSONA, HORMONE_PERSONAS

    for h in ["cortisol", "progesterone", "estrogen", "testosterone", "insulin", "thyroid"]:
        test(f"Persona: {h}", h in HORMONE_PERSONAS and "phase_behavior" in HORMONE_PERSONAS[h])
    test("DEFAULT_PERSONA", "name" in DEFAULT_PERSONA)

    # TEST 7: ImageLibraryService
    print("\n📋 TEST 7: Image Library Service")
    print("-" * 50)
    from app.services.image_library_service import get_image_library_service

    svc = get_image_library_service()
    test("Singleton instance", svc is not None)
    test("SIMILARITY_THRESHOLD=0.95", svc.SIMILARITY_THRESHOLD == 0.95)
    test("COST_PER_IMAGE=0.0006", svc.COST_PER_IMAGE == 0.0006)

    enhanced = svc._enhance_prompt("bowl of oatmeal", "food")
    test("_enhance_prompt returns tuple", isinstance(enhanced, tuple) and len(enhanced) == 2)

    # SUMMARY
    print("\n" + "=" * 70)
    print("📊 FINAL SUMMARY")
    print("=" * 70)
    print(f"\n  ✅ Passed: {passed}")
    print(f"  ❌ Failed: {failed}")
    total = passed + failed
    print(f"  📈 Success Rate: {passed / total * 100:.1f}%" if total else "  📈 Success Rate: N/A")
    print("\n" + "=" * 70)
    print("🏁 TEST SUITE COMPLETE")
    print("=" * 70)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
