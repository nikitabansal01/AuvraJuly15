#!/usr/bin/env python3
"""Diagnose OpenAI API issues - test inference and check rate limits."""

import os
import sys
import time
import json

# Load .env file manually
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value.strip('"\'')

from openai import OpenAI

api_key = os.getenv('OPENAI_API_KEY')
if not api_key:
    print("ERROR: OPENAI_API_KEY not found")
    sys.exit(1)

print("=" * 60)
print("OpenAI API Diagnostic Test")
print("=" * 60)
print(f"API Key: {api_key[:8]}...{api_key[-4:]}")

client = OpenAI(api_key=api_key)

# Test 1: Simple inference
print("\n📝 Test 1: Simple GPT-4o-mini inference")
print("-" * 50)

try:
    start = time.time()
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Say 'Hello' in one word."}],
        max_tokens=10
    )
    elapsed = time.time() - start
    
    print(f"✅ SUCCESS!")
    print(f"   Response: {response.choices[0].message.content}")
    print(f"   Model: {response.model}")
    print(f"   Tokens: {response.usage.prompt_tokens} in / {response.usage.completion_tokens} out")
    print(f"   Latency: {elapsed:.2f}s")
    
    # Check headers for rate limit info
    print("\n📊 Rate Limit Status (from response):")
    print(f"   ID: {response.id}")
    
except Exception as e:
    error_str = str(e)
    print(f"❌ FAILED: {type(e).__name__}")
    print(f"   Error: {error_str[:500]}")
    
    # Parse error details
    if "429" in error_str:
        print("\n🔍 429 Error Analysis:")
        if "quota" in error_str.lower():
            print("   ⚠️ QUOTA EXCEEDED - You've run out of credits!")
        elif "rate" in error_str.lower():
            print("   ⚠️ RATE LIMITED - Too many requests per minute")
        if "insufficient_quota" in error_str.lower():
            print("   💳 NO CREDITS - Need to add payment method or buy credits")
    elif "401" in error_str:
        print("\n🔍 401 Error - Invalid API key!")
    elif "403" in error_str:
        print("\n🔍 403 Error - API key doesn't have access to this model")

# Test 2: Check models access
print("\n📝 Test 2: Check model access")
print("-" * 50)

try:
    models = client.models.list()
    gpt_models = [m.id for m in models.data if 'gpt' in m.id.lower()]
    print(f"✅ Available GPT models: {len(gpt_models)}")
    for m in sorted(gpt_models)[:10]:
        print(f"   - {m}")
    if len(gpt_models) > 10:
        print(f"   ... and {len(gpt_models) - 10} more")
except Exception as e:
    print(f"❌ FAILED: {e}")

# Test 3: Rapid requests to check rate limits
print("\n📝 Test 3: Rate limit test (3 rapid requests)")
print("-" * 50)

success_count = 0
for i in range(3):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": f"Say '{i}'"}],
            max_tokens=5
        )
        success_count += 1
        print(f"   Request {i+1}: ✅ {response.choices[0].message.content.strip()}")
    except Exception as e:
        error_str = str(e)
        print(f"   Request {i+1}: ❌ {type(e).__name__}")
        if "429" in error_str:
            # Try to extract retry-after
            if "retry" in error_str.lower():
                print(f"      Retry after info in error")
            print(f"      {error_str[:200]}")
        break

print(f"\n   Result: {success_count}/3 requests succeeded")

# Test 4: Check organization/project info
print("\n📝 Test 4: Account/Organization Info")
print("-" * 50)

try:
    # Try to get organization info via a workaround
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Hi"}],
        max_tokens=1
    )
    # The response headers would have org info but we can't access them directly
    print(f"   System fingerprint: {response.system_fingerprint}")
    print(f"   Response ID prefix: {response.id[:20]}...")
except Exception as e:
    print(f"   Could not get info: {e}")

print("\n" + "=" * 60)
print("DIAGNOSIS SUMMARY")
print("=" * 60)

if success_count == 3:
    print("✅ API is working fine - no current issues!")
    print("   The 429 errors may be intermittent during peak usage.")
    print("\n   Recommendations:")
    print("   1. The Groq fallback you added should handle any 429s")
    print("   2. Consider adding request queuing to avoid bursts")
    print("   3. Check your OpenAI tier at: https://platform.openai.com/settings/organization/limits")
elif success_count > 0:
    print("⚠️ PARTIAL SUCCESS - Rate limits are being hit")
    print("   You're hitting RPM (requests per minute) limits.")
    print("\n   Solutions:")
    print("   1. ✅ Groq fallback is already implemented")
    print("   2. Add request delays between calls")
    print("   3. Upgrade OpenAI tier for higher limits")
else:
    print("❌ API CALLS FAILING")
    print("   Check the error messages above for details.")
    print("\n   If it's a quota issue:")
    print("   1. Go to: https://platform.openai.com/settings/organization/billing")
    print("   2. Add credits or set up auto-recharge")
