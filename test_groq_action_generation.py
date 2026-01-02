"""
Test if Groq GPT-OSS-120B can generate action plans with the SAME structure as OpenAI.
This simulates what happens during fallback.
"""
import os
import asyncio
import json
import httpx
from dotenv import load_dotenv

load_dotenv()

# The exact JSON schema used for OpenAI Structured Outputs
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "category": {"type": "string", "enum": ["food", "movement", "mindfulness"]},
                    "time_slot": {"type": "string", "enum": ["morning", "afternoon", "evening"]},
                    "specific_action": {"type": "string"},
                    "hormone_persona_intro": {"type": "string"},
                    "food_items": {"type": "array", "items": {"type": "string"}},
                    "research_studies": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "journal": {"type": "string"},
                                "year": {"type": "integer"},
                                "participants": {"type": "integer"},
                                "finding": {"type": "string"},
                                "pmid": {"type": "string"},
                                "verification_link": {"type": "string"}
                            }
                        }
                    }
                },
                "required": ["title", "category", "time_slot", "specific_action"]
            }
        }
    },
    "required": ["actions"]
}

async def test_groq_generation():
    groq_key = os.getenv("GROQ_API_KEY")
    fallback_model = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")
    
    print("=" * 60)
    print("TESTING GROQ ACTION PLAN GENERATION")
    print("=" * 60)
    print(f"\nModel: {fallback_model}")
    print(f"API Key: {groq_key[:15]}..." if groq_key else "NOT SET")
    
    if not groq_key:
        print("\n❌ GROQ_API_KEY not set!")
        return
    
    # Check if it's a reasoning model
    is_reasoning = "gpt-oss" in fallback_model.lower()
    print(f"Is reasoning model: {is_reasoning}")
    
    # Simplified prompt (like what the generator uses)
    system_prompt = """You are AUVRA's hormone health expert. Generate personalized health actions.

OUTPUT FORMAT: You MUST return ONLY valid JSON, no markdown, no thinking, no preamble.

The JSON must have this structure:
{
    "actions": [
        {
            "title": "Action title",
            "category": "food" or "movement" or "mindfulness",
            "time_slot": "morning" or "afternoon" or "evening",
            "specific_action": "Detailed instruction",
            "hormone_persona_intro": "I'm Estrogen, and during your follicular phase...",
            "food_items": ["item1", "item2"] or [] if not food,
            "research_studies": [
                {
                    "title": "Study title",
                    "journal": "Journal name",
                    "year": 2023,
                    "participants": 50,
                    "finding": "Key finding",
                    "pmid": "12345678",
                    "verification_link": "https://pubmed.ncbi.nlm.nih.gov/12345678/"
                }
            ]
        }
    ]
}

IMPORTANT: Output ONLY valid JSON. No markdown, no thinking, no preamble."""

    user_prompt = """Generate 2 health actions for a woman in her follicular phase.
- Primary hormone: Estrogen (rising)
- Lifestyle focus: eat
- Health conditions: None

Return exactly 2 actions in the JSON format specified."""

    payload = {
        "model": fallback_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 4000
    }
    
    # For non-reasoning Groq models, use json_object mode
    if not is_reasoning:
        payload["response_format"] = {"type": "json_object"}
    
    print(f"\n📤 Sending request to Groq...")
    print(f"   Payload size: ~{len(json.dumps(payload))} chars")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=60.0
            )
            
            if response.status_code != 200:
                print(f"\n❌ API Error: {response.status_code}")
                print(response.text)
                return
            
            data = response.json()
            
            # Cost calculation
            input_tokens = data.get("usage", {}).get("prompt_tokens", 0)
            output_tokens = data.get("usage", {}).get("completion_tokens", 0)
            print(f"\n📊 Usage: {input_tokens} input + {output_tokens} output tokens")
            
            content = data["choices"][0]["message"]["content"]
            print(f"\n📥 Raw response length: {len(content)} chars")
            
            # Clean response (for reasoning models that might add markdown)
            cleaned = content.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            
            # Try to parse JSON
            try:
                result = json.loads(cleaned)
                print("\n✅ JSON PARSING SUCCESSFUL!")
                
                if "actions" in result:
                    actions = result["actions"]
                    print(f"\n📋 Generated {len(actions)} actions:")
                    for i, action in enumerate(actions, 1):
                        print(f"\n   Action {i}:")
                        print(f"   - Title: {action.get('title', 'MISSING')}")
                        print(f"   - Category: {action.get('category', 'MISSING')}")
                        print(f"   - Time Slot: {action.get('time_slot', 'MISSING')}")
                        print(f"   - Specific Action: {action.get('specific_action', 'MISSING')[:50]}...")
                        if action.get('research_studies'):
                            print(f"   - Research: {len(action['research_studies'])} studies")
                else:
                    print("\n⚠️ Response doesn't have 'actions' key")
                    print(f"Keys found: {result.keys()}")
                
                print("\n" + "=" * 60)
                print("CONCLUSION: Groq CAN generate action plans!")
                print("=" * 60)
                
            except json.JSONDecodeError as e:
                print(f"\n❌ JSON PARSING FAILED: {e}")
                print(f"First 500 chars of response:\n{cleaned[:500]}")
                
        except httpx.HTTPError as e:
            print(f"\n❌ HTTP Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_groq_generation())
