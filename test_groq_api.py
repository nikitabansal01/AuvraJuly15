#!/usr/bin/env python3
"""
Test if Groq API is reachable and working.
"""
import os
import httpx
import asyncio
from dotenv import load_dotenv

load_dotenv()

async def test_groq():
    groq_key = os.getenv("GROQ_API_KEY")
    fallback_model = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")
    
    print("=== GROQ API TEST ===")
    print(f"API Key: {groq_key[:15]}..." if groq_key else "NOT SET")
    print(f"Fallback Model: {fallback_model}")
    print()
    
    if not groq_key:
        print("ERROR: GROQ_API_KEY not set")
        return
    
    # Test 1: List models
    print("1. Testing models endpoint...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {groq_key}"}
            )
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                model_ids = [m["id"] for m in models]
                print(f"   Available models: {len(model_ids)}")
                # Check if fallback model is available
                if fallback_model in model_ids:
                    print(f"   Fallback model '{fallback_model}' is available")
                else:
                    print(f"   WARNING: Fallback model '{fallback_model}' NOT in available models")
                    print(f"   Available: {model_ids[:5]}...")
            else:
                print(f"   ERROR: Status {resp.status_code}")
                print(f"   Response: {resp.text[:200]}")
        except Exception as e:
            print(f"   ERROR: {e}")
    print()
    
    # Test 2: Simple completion
    print("2. Testing chat completion...")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # Use a working model for test
            test_model = "llama-3.3-70b-versatile"  # Known working model
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": test_model,
                    "messages": [
                        {"role": "user", "content": "Say 'hello' in JSON format: {\"greeting\": \"hello\"}"}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 50,
                    "response_format": {"type": "json_object"}
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                print(f"   Model: {test_model}")
                print(f"   Response: {content}")
                print(f"   Tokens: {usage.get('total_tokens', 'N/A')}")
            elif resp.status_code == 429:
                print(f"   Rate limited (429) - API works but quota exceeded")
            else:
                print(f"   ERROR: Status {resp.status_code}")
                print(f"   Response: {resp.text[:300]}")
        except Exception as e:
            print(f"   ERROR: {e}")
    print()
    
    # Test 3: Test configured fallback model
    print(f"3. Testing configured fallback model: {fallback_model}...")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            payload = {
                "model": fallback_model,
                "messages": [
                    {"role": "user", "content": "Respond with exactly: {\"status\": \"ok\"}"}
                ],
                "temperature": 0.1,
                "max_tokens": 50
            }
            # Only add response_format for non-reasoning models
            if "gpt-oss" not in fallback_model.lower():
                payload["response_format"] = {"type": "json_object"}
                
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json"
                },
                json=payload
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                print(f"   Response: {content[:100]}...")
            elif resp.status_code == 429:
                print(f"   Rate limited (429) - model exists but quota exceeded")
            else:
                print(f"   ERROR: Status {resp.status_code}")
                print(f"   Response: {resp.text[:300]}")
        except Exception as e:
            print(f"   ERROR: {e}")
    
    print()
    print("=== TEST COMPLETE ===")

if __name__ == "__main__":
    asyncio.run(test_groq())
