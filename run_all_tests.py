#!/usr/bin/env python3
"""
AUVRA Comprehensive Test Suite
Tests all components of the backend system
"""

import os
import sys
import asyncio
import json

# Set working directory
os.chdir('/Users/mohanganesh/AUVRA/AuvraJuly15')

# Load environment
from dotenv import load_dotenv
load_dotenv('.env')

import httpx

# Test Results
results = {
    "passed": 0,
    "failed": 0,
    "tests": []
}

def record(name, passed, details=""):
    results["tests"].append({"name": name, "passed": passed, "details": details})
    if passed:
        results["passed"] += 1
        print(f"  ✅ {name}")
    else:
        results["failed"] += 1
        print(f"  ❌ {name}: {details}")

# ============================================================================
# TEST 1: Environment Variables
# ============================================================================
print("\n" + "="*70)
print("📋 TEST 1: Environment Variables")
print("="*70)

env_vars = {
    "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
    "GROQ_API_KEY": os.getenv("GROQ_API_KEY"),
    "DATABASE_URL": os.getenv("DATABASE_URL"),
    "GROQ_FALLBACK_MODEL": os.getenv("GROQ_FALLBACK_MODEL"),
    "PINECONE_API_KEY": os.getenv("PINECONE_API_KEY"),
    "RUNPOD_API_KEY": os.getenv("RUNPOD_API_KEY"),
}

for var, val in env_vars.items():
    record(f"ENV: {var}", val is not None and len(val) > 5)

fallback = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")
print(f"\n  🔧 Fallback Model: {fallback}")

# ============================================================================
# TEST 2: Service Imports
# ============================================================================
print("\n" + "="*70)
print("📋 TEST 2: Service Imports")
print("="*70)

try:
    from app.services.action_plan_generator import ActionPlanGenerator, HORMONE_PERSONAS
    record("Import ActionPlanGenerator", True)
    record("HORMONE_PERSONAS defined", len(HORMONE_PERSONAS) >= 6)
except Exception as e:
    record("Import ActionPlanGenerator", False, str(e))

try:
    from app.services.image_library_service import ImageLibraryService
    record("Import ImageLibraryService", True)
except Exception as e:
    record("Import ImageLibraryService", False, str(e))

try:
    from app.services.pubmed_service import execute_pubmed_tool
    record("Import PubMed Service", True)
except Exception as e:
    record("Import PubMed Service", False, str(e))

try:
    from app.services.evaluation_service import ActionPlanEvaluator
    record("Import Evaluation Service", True)
except Exception as e:
    record("Import Evaluation Service", False, str(e))

# ============================================================================
# TEST 3: Pydantic Model Validation
# ============================================================================
print("\n" + "="*70)
print("📋 TEST 3: Pydantic Model Validation")
print("="*70)

from app.services.action_plan_generator import ActionItemModel

# Valid food action
try:
    action = ActionItemModel(
        title="Omega-3 Breakfast Bowl",
        category="food",
        time_slot="morning",
        specific_action="A bowl of chia pudding with walnuts",
        purpose="Omega-3 fatty acids reduce inflammation",
        target_hormone="cortisol",
        hormone_persona_intro="I am Cortisol",
        image_prompt="chia pudding bowl, walnuts, berries",
        research_studies=[],
        variants=[],
        food_items=["chia seeds", "walnuts", "berries"],
        food_amounts=["2 tbsp", "1/4 cup", "1/2 cup"],
        exercise_types=[],
        exercise_durations=[],
        exercise_intensities=[],
        mindfulness_techniques=[],
        mindfulness_durations=[],
        symptoms=["fatigue", "stress"],
        conditions=["PCOS"]
    )
    record("Valid FOOD action passes", True)
except Exception as e:
    record("Valid FOOD action passes", False, str(e))

# Valid movement action
try:
    action = ActionItemModel(
        title="Morning Yoga Flow",
        category="movement",
        time_slot="morning",
        specific_action="Gentle yoga session",
        purpose="Yoga activates parasympathetic nervous system",
        target_hormone="cortisol",
        hormone_persona_intro="I am Cortisol",
        image_prompt="woman doing yoga",
        research_studies=[],
        variants=[],
        food_items=[],
        food_amounts=[],
        exercise_types=["yoga"],
        exercise_durations=["20 min"],
        exercise_intensities=["low"],
        mindfulness_techniques=[],
        mindfulness_durations=[],
        symptoms=[],
        conditions=[]
    )
    record("Valid MOVEMENT action passes", True)
except Exception as e:
    record("Valid MOVEMENT action passes", False, str(e))

# Valid mindfulness action
try:
    action = ActionItemModel(
        title="Evening Meditation",
        category="mindfulness",
        time_slot="evening",
        specific_action="Guided breathing meditation",
        purpose="Deep breathing reduces cortisol",
        target_hormone="cortisol",
        hormone_persona_intro="I am Cortisol",
        image_prompt="peaceful meditation scene",
        research_studies=[],
        variants=[],
        food_items=[],
        food_amounts=[],
        exercise_types=[],
        exercise_durations=[],
        exercise_intensities=[],
        mindfulness_techniques=["box breathing"],
        mindfulness_durations=["10 min"],
        symptoms=[],
        conditions=[]
    )
    record("Valid MINDFULNESS action passes", True)
except Exception as e:
    record("Valid MINDFULNESS action passes", False, str(e))

# Invalid time_slot should fail
try:
    action = ActionItemModel(
        title="Test",
        category="food",
        time_slot="lunch",  # INVALID
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
        conditions=[]
    )
    record("Invalid time_slot rejected", False, "Should have raised error")
except Exception:
    record("Invalid time_slot rejected", True)

# Invalid category should fail
try:
    action = ActionItemModel(
        title="Test",
        category="sleep",  # INVALID
        time_slot="evening",
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
        conditions=[]
    )
    record("Invalid category rejected", False, "Should have raised error")
except Exception:
    record("Invalid category rejected", True)

# ============================================================================
# TEST 4: API Connectivity
# ============================================================================
print("\n" + "="*70)
print("📋 TEST 4: API Connectivity")
print("="*70)

async def test_apis():
    # OpenAI API
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"}
            )
            record("OpenAI API reachable", resp.status_code == 200)
    except Exception as e:
        record("OpenAI API reachable", False, str(e))

    # Groq API
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"}
            )
            if resp.status_code == 200:
                record("Groq API reachable", True)
                models = resp.json().get("data", [])
                model_ids = [m["id"] for m in models]
                fallback = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")
                record(f"Fallback model '{fallback}' available", fallback in model_ids)
            else:
                record("Groq API reachable", False, f"Status {resp.status_code}")
    except Exception as e:
        record("Groq API reachable", False, str(e))

    # PubMed API
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=test&retmax=1&retmode=json"
            )
            record("PubMed API reachable", resp.status_code == 200)
    except Exception as e:
        record("PubMed API reachable", False, str(e))

asyncio.run(test_apis())

# ============================================================================
# TEST 5: Groq Fallback Model Direct Test
# ============================================================================
print("\n" + "="*70)
print("📋 TEST 5: Groq Fallback Model (openai/gpt-oss-120b)")
print("="*70)

async def test_groq_fallback():
    fallback = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-120b")
    
    payload = {
        "model": fallback,
        "messages": [
            {"role": "system", "content": "You are a JSON generator. Only output valid JSON."},
            {"role": "user", "content": 'Return exactly: {"status": "ok", "model": "gpt-oss"}'}
        ],
        "temperature": 0.3,
        "max_tokens": 50
    }
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
                    "Content-Type": "application/json"
                },
                json=payload
            )
            
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                record(f"Groq {fallback} responds", True)
                print(f"      Response: {content[:80]}")
                
                # Try to parse JSON
                try:
                    parsed = json.loads(content)
                    record("Response is valid JSON", True)
                except:
                    record("Response is valid JSON", False, "Could not parse")
            elif resp.status_code == 429:
                record(f"Groq {fallback} responds", False, "Rate limited (429)")
            else:
                record(f"Groq {fallback} responds", False, f"Status {resp.status_code}")
    except Exception as e:
        record(f"Groq {fallback} responds", False, str(e))

asyncio.run(test_groq_fallback())

# ============================================================================
# TEST 6: PubMed Citation Search
# ============================================================================
print("\n" + "="*70)
print("📋 TEST 6: PubMed Citation Search")
print("="*70)

async def test_pubmed_search():
    query = "PCOS+insulin+resistance+diet+women"
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Search
            resp = await client.get(
                f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={query}&retmax=3&retmode=json"
            )
            data = resp.json()
            ids = data.get("esearchresult", {}).get("idlist", [])
            
            if ids:
                record("PubMed search returns papers", True)
                print(f"      Found PMIDs: {ids}")
                
                # Fetch first paper
                resp = await client.get(
                    f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={ids[0]}&retmode=xml"
                )
                
                import re
                title_match = re.search(r"<ArticleTitle>(.+?)</ArticleTitle>", resp.text)
                if title_match:
                    record("PubMed fetch returns title", True)
                    print(f"      Title: {title_match.group(1)[:60]}...")
                else:
                    record("PubMed fetch returns title", False, "No title found")
            else:
                record("PubMed search returns papers", False, "No results")
    except Exception as e:
        record("PubMed search returns papers", False, str(e))

asyncio.run(test_pubmed_search())

# ============================================================================
# TEST 7: Database Connection
# ============================================================================
print("\n" + "="*70)
print("📋 TEST 7: Database Connection")
print("="*70)

async def test_database():
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    
    db_url = os.getenv("DATABASE_URL", "")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    
    try:
        engine = create_async_engine(db_url, echo=False)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            record("Database connection", True)
            
            # Check tables
            result = await conn.execute(text("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name IN ('users', 'action_plans', 'action_items', 'ai_model_usage_logs')
            """))
            tables = [row[0] for row in result.fetchall()]
            
            for table in ["users", "action_plans", "action_items"]:
                record(f"Table '{table}' exists", table in tables)
            
            record("Table 'ai_model_usage_logs' exists", "ai_model_usage_logs" in tables)
            
        await engine.dispose()
    except Exception as e:
        record("Database connection", False, str(e))

asyncio.run(test_database())

# ============================================================================
# TEST 8: Hormone Personas
# ============================================================================
print("\n" + "="*70)
print("📋 TEST 8: Hormone Personas")
print("="*70)

from app.services.action_plan_generator import HORMONE_PERSONAS, DEFAULT_PERSONA

expected_hormones = ["cortisol", "progesterone", "estrogen", "testosterone", "insulin", "thyroid"]

for hormone in expected_hormones:
    if hormone in HORMONE_PERSONAS:
        persona = HORMONE_PERSONAS[hormone]
        has_required = all(k in persona for k in ["name", "emoji", "phase_behavior", "focus"])
        record(f"Persona: {hormone}", has_required)
    else:
        record(f"Persona: {hormone}", False, "Missing")

record("DEFAULT_PERSONA defined", "name" in DEFAULT_PERSONA)

# ============================================================================
# TEST 9: Image Library Service
# ============================================================================
print("\n" + "="*70)
print("📋 TEST 9: Image Library Service")
print("="*70)

from app.services.image_library_service import ImageLibraryService, get_image_library_service

service = get_image_library_service()
record("ImageLibraryService singleton", service is not None)
record("SIMILARITY_THRESHOLD = 0.95", service.SIMILARITY_THRESHOLD == 0.95)
record("COST_PER_IMAGE = 0.0006", service.COST_PER_IMAGE == 0.0006)

# Test prompt enhancement
enhanced = service._enhance_prompt("bowl of oatmeal with berries", "food")
record("_enhance_prompt returns tuple", isinstance(enhanced, tuple) and len(enhanced) == 2)
record("Enhanced prompt contains original", "oatmeal" in enhanced[0])

# ============================================================================
# FINAL SUMMARY
# ============================================================================
print("\n" + "="*70)
print("📊 FINAL SUMMARY")
print("="*70)

print(f"\n  ✅ Passed: {results['passed']}")
print(f"  ❌ Failed: {results['failed']}")
print(f"  📈 Success Rate: {results['passed'] / (results['passed'] + results['failed']) * 100:.1f}%")

if results["failed"] > 0:
    print("\n  Failed Tests:")
    for test in results["tests"]:
        if not test["passed"]:
            print(f"    - {test['name']}: {test['details']}")

print("\n" + "="*70)
print("🏁 TEST SUITE COMPLETE")
print("="*70)

sys.exit(0 if results["failed"] == 0 else 1)
