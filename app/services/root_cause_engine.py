from typing import Dict, List, Tuple, Optional
import json
import os
import asyncio
import logging
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# ============================================
# PYDANTIC MODELS FOR STRUCTURED OUTPUTS
# ============================================

class HormoneScoresModel(BaseModel):
    """Strict validation for hormone analysis scores"""
    androgens_high: int = Field(ge=0, le=3)
    insulin_high: int = Field(ge=0, le=3)
    thyroid_low: int = Field(ge=0, le=3)
    estrogen_high: int = Field(ge=0, le=3)
    estrogen_low: int = Field(ge=0, le=3)
    progesterone_low: int = Field(ge=0, le=3)
    cortisol_high: int = Field(ge=0, le=3)
    cortisol_low: int = Field(ge=0, le=3)
    
    model_config = {"extra": "ignore"}

# ============================================
# OPENAI API CONFIGURATION (Migrated from Gemini)
# ============================================
# OpenAI has much higher rate limits (3,500 RPM tier 1) vs Gemini free (5 RPM)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Fallback: Still support Gemini if explicitly configured
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Groq fallback configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_FALLBACK_MODEL = "llama-3.3-70b-versatile"  # Higher rate limits (30K TPM vs 8K)

# Determine which LLM provider to use (priority: OpenAI > Groq > Gemini)
LLM_PROVIDER = "openai" if OPENAI_API_KEY else ("groq" if GROQ_API_KEY else ("gemini" if GEMINI_API_KEY else None))

# Auto-enable LLM if API key is present (unless explicitly disabled)
_enable_llm_env = os.getenv("ENABLE_LLM_OTHERS", "").lower()
if _enable_llm_env in ("0", "false", "no", "off"):
    ENABLE_LLM_OTHERS = False
else:
    ENABLE_LLM_OTHERS = bool(OPENAI_API_KEY) or bool(GROQ_API_KEY) or bool(GEMINI_API_KEY) or _enable_llm_env in ("1", "true", "yes", "on")
    
LLM_OTHERS_TIMEOUT = int(os.getenv("LLM_OTHERS_TIMEOUT", "30"))  # seconds
LLM_OTHERS_MODEL = os.getenv("LLM_OTHERS_MODEL", "gpt-4o-mini")  # Fast, good for JSON

# ============================================
# PRODUCTION-READY CACHE (Thread-safe, TTL, Size-limited)
# ============================================
# Uses centralized cache utility instead of bare dictionary
from app.utils.cache_utils import hormone_analysis_cache, generate_cache_key

# Debug logging
logger.info(f"🔑 LLM Provider: {LLM_PROVIDER or 'NONE (disabled)'}")
logger.info(f"🔑 OPENAI_API_KEY loaded: {'Yes' if OPENAI_API_KEY else 'No'}")
logger.info(f"🔑 GROQ_API_KEY loaded: {'Yes' if GROQ_API_KEY else 'No'}")
logger.info(f"🔑 ENABLE_LLM_OTHERS: {ENABLE_LLM_OTHERS}")
logger.info(f"🔑 LLM Model: {LLM_OTHERS_MODEL}")


class RootCauseEngine:
    """
    Hormone imbalance root cause analysis engine
    Evidence-based clinical scoring system with LLM integration
    
    OPTIMIZATIONS (Production-ready):
    - Migrated from Gemini (5 RPM limit) to OpenAI (3,500+ RPM)
    - Thread-safe, TTL-enabled caching via cache_utils.TTLCache
    - Async support for non-blocking operations
    - Graceful fallbacks on API failures
    """
    
    @staticmethod
    def _get_cache_key(symptom_others: Optional[str], family_others: Optional[str]) -> str:
        """Generate cache key for hormone analysis using stable hash"""
        return generate_cache_key(symptom_others or '', family_others or '')
    
    @staticmethod
    def clear_cache():
        """Clear the hormone analysis cache (call between sessions)"""
        hormone_analysis_cache.clear()
    
    @staticmethod
    def get_cache_stats() -> Dict:
        """Get hormone analysis cache statistics"""
        return hormone_analysis_cache.stats()
    
    @staticmethod
    def _get_default_scores() -> Dict[str, int]:
        """Return default zero scores for all hormones"""
        return {
            "estrogen_high": 0,
            "estrogen_low": 0,
            "progesterone_low": 0,
            "androgens_high": 0,
            "insulin_high": 0,
            "cortisol_high": 0,
            "cortisol_low": 0,
            "thyroid_low": 0
        }
    
    @staticmethod
    def _build_hormone_prompt(symptom_others: str, family_others: str) -> str:
        """Build the prompt for hormone analysis"""
        symptoms_text = symptom_others if symptom_others else "None"
        family_text = family_others if family_others else "None"
        
        return f"""You are a clinical AI analyzing hormone imbalance symptoms and family history.

Patient Symptoms: {symptoms_text}
Family Medical History: {family_text}

CRITICAL INSTRUCTIONS:
1. Rate only ONE direction per hormone (high OR low, NEVER both)
2. Use evidence-based clinical reasoning for symptom-hormone associations
3. Family history adds genetic predisposition (+1 modifier) but is not diagnostic alone
4. If a symptom could indicate multiple hormone states, choose the most clinically likely one
5. Score conservatively - only give high scores (2-3) when symptoms strongly suggest that specific imbalance

SCORING SCALE:
0 = No evidence for this hormone imbalance
1 = Mild/possible indication (1-2 relevant symptoms)
2 = Moderate indication (multiple relevant symptoms or strong single indicator)
3 = Strong indication (multiple strong symptoms or classic presentation)

Return ONLY valid JSON with these exact keys. No markdown, no explanation:
{{"androgens_high": 0, "insulin_high": 0, "thyroid_low": 0, "estrogen_high": 0, "estrogen_low": 0, "progesterone_low": 0, "cortisol_high": 0, "cortisol_low": 0}}"""
    
    @staticmethod
    def _parse_llm_response(response_text: str) -> Dict[str, int]:
        """Parse and validate LLM response JSON using Pydantic"""
        try:
            # Clean up markdown formatting
            cleaned_text = response_text.strip()
            if "```json" in cleaned_text:
                start_marker = "```json"
                end_marker = "```"
                start_idx = cleaned_text.find(start_marker) + len(start_marker)
                end_idx = cleaned_text.find(end_marker, start_idx)
                if end_idx != -1:
                    cleaned_text = cleaned_text[start_idx:end_idx].strip()
            elif cleaned_text.startswith("```") and cleaned_text.endswith("```"):
                lines = cleaned_text.split('\n')
                cleaned_text = '\n'.join(lines[1:-1]).strip()
            
            # Parse JSON
            scores_dict = json.loads(cleaned_text)
            
            # Validate with Pydantic
            logger.info("📋 Validating hormone scores with Pydantic...")
            validated_scores = HormoneScoresModel.model_validate(scores_dict)
            logger.info("✅ Pydantic validation SUCCESSFUL for hormone scores")
            
            return validated_scores.model_dump()
            
        except ValidationError as ve:
            logger.error(f"❌ Pydantic Validation Failed for hormone scores: {ve}")
            logger.error(f"   Raw content: {response_text[:200]}...")
            # Return default scores on validation failure
            return RootCauseEngine._get_default_scores()
            
        except json.JSONDecodeError as je:
            logger.error(f"❌ JSON Decode Failed: {je}")
            return RootCauseEngine._get_default_scores()
        except Exception as e:
            logger.error(f"❌ Unexpected error parsing hormone scores: {e}")
            return RootCauseEngine._get_default_scores()
    
    @staticmethod
    async def _call_openai_async(prompt: str) -> str:
        """Call OpenAI API asynchronously with Groq fallback"""
        import httpx
        
        openai_error = None
        
        # Try OpenAI first
        if OPENAI_API_KEY:
            try:
                headers = {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {OPENAI_API_KEY}'
                }
                
                body = {
                    'model': LLM_OTHERS_MODEL,
                    'messages': [
                        {"role": "system", "content": "You are a clinical AI specializing in hormone imbalance analysis. Return only valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    'temperature': 0.3,  # Lower temperature for consistent JSON output
                    'max_tokens': 200,   # JSON response is small
                    'response_format': {"type": "json_object"}  # Enforce JSON output
                }
                
                async with httpx.AsyncClient(timeout=LLM_OTHERS_TIMEOUT) as client:
                    response = await client.post(
                        'https://api.openai.com/v1/chat/completions',
                        headers=headers,
                        json=body
                    )
                    
                    if response.status_code != 200:
                        openai_error = f"OpenAI API error: {response.status_code} - {response.text[:200]}"
                        logger.warning(f"❌ {openai_error}")
                    else:
                        data = response.json()
                        logger.info("✅ Hormone analysis via OpenAI")
                        return data['choices'][0]['message']['content']
            except Exception as e:
                openai_error = str(e)
                logger.warning(f"❌ OpenAI exception: {openai_error[:200]}")
        else:
            openai_error = "No OpenAI API key"
        
        # Groq fallback
        if openai_error and GROQ_API_KEY:
            try:
                logger.info(f"🔄 Falling back to Groq ({GROQ_FALLBACK_MODEL})")
                
                # gpt-oss-120b doesn't support response_format
                is_reasoning_model = "gpt-oss" in GROQ_FALLBACK_MODEL.lower()
                enhanced_prompt = prompt + "\n\nIMPORTANT: Respond with valid JSON only. No markdown." if is_reasoning_model else prompt
                
                headers = {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {GROQ_API_KEY}'
                }
                
                body = {
                    'model': GROQ_FALLBACK_MODEL,
                    'messages': [
                        {"role": "system", "content": "You are a clinical AI specializing in hormone imbalance analysis. Return only valid JSON."},
                        {"role": "user", "content": enhanced_prompt}
                    ],
                    'temperature': 0.3,
                    'max_tokens': 200
                }
                
                if not is_reasoning_model:
                    body['response_format'] = {"type": "json_object"}
                
                async with httpx.AsyncClient(timeout=LLM_OTHERS_TIMEOUT + 30) as client:
                    response = await client.post(
                        'https://api.groq.com/openai/v1/chat/completions',
                        headers=headers,
                        json=body
                    )
                    
                    if response.status_code != 200:
                        raise Exception(f"Groq API error: {response.status_code} - {response.text[:200]}")
                    
                    data = response.json()
                    content = data['choices'][0]['message']['content']
                    
                    # Clean reasoning model output
                    if is_reasoning_model:
                        if content.startswith("```json"):
                            content = content[7:]
                        if content.startswith("```"):
                            content = content[3:]
                        if content.endswith("```"):
                            content = content[:-3]
                        content = content.strip()
                    
                    logger.info("✅ Hormone analysis via Groq fallback")
                    return content
                    
            except Exception as e:
                logger.error(f"❌ Groq fallback also failed: {e}")
                raise Exception(f"Both OpenAI and Groq failed: {openai_error}")
        elif openai_error:
            raise Exception(f"OpenAI failed and no Groq fallback: {openai_error}")
    
    @staticmethod
    def _call_openai_sync(prompt: str) -> str:
        """Call OpenAI API synchronously with Groq fallback"""
        import httpx
        
        openai_error = None
        
        # Try OpenAI first
        if OPENAI_API_KEY:
            try:
                headers = {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {OPENAI_API_KEY}'
                }
                
                body = {
                    'model': LLM_OTHERS_MODEL,
                    'messages': [
                        {"role": "system", "content": "You are a clinical AI specializing in hormone imbalance analysis. Return only valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    'temperature': 0.3,
                    'max_tokens': 200,
                    'response_format': {"type": "json_object"}
                }
                
                with httpx.Client(timeout=LLM_OTHERS_TIMEOUT) as client:
                    response = client.post(
                        'https://api.openai.com/v1/chat/completions',
                        headers=headers,
                        json=body
                    )
                    
                    if response.status_code != 200:
                        openai_error = f"OpenAI API error: {response.status_code} - {response.text[:200]}"
                        logger.warning(f"❌ {openai_error}")
                    else:
                        data = response.json()
                        logger.info("✅ Hormone analysis via OpenAI (sync)")
                        return data['choices'][0]['message']['content']
            except Exception as e:
                openai_error = str(e)
                logger.warning(f"❌ OpenAI exception: {openai_error[:200]}")
        else:
            openai_error = "No OpenAI API key"
        
        # Groq fallback
        if openai_error and GROQ_API_KEY:
            try:
                logger.info(f"🔄 Falling back to Groq ({GROQ_FALLBACK_MODEL})")
                
                is_reasoning_model = "gpt-oss" in GROQ_FALLBACK_MODEL.lower()
                enhanced_prompt = prompt + "\n\nIMPORTANT: Respond with valid JSON only. No markdown." if is_reasoning_model else prompt
                
                headers = {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {GROQ_API_KEY}'
                }
                
                body = {
                    'model': GROQ_FALLBACK_MODEL,
                    'messages': [
                        {"role": "system", "content": "You are a clinical AI specializing in hormone imbalance analysis. Return only valid JSON."},
                        {"role": "user", "content": enhanced_prompt}
                    ],
                    'temperature': 0.3,
                    'max_tokens': 200
                }
                
                if not is_reasoning_model:
                    body['response_format'] = {"type": "json_object"}
                
                with httpx.Client(timeout=LLM_OTHERS_TIMEOUT + 30) as client:
                    response = client.post(
                        'https://api.groq.com/openai/v1/chat/completions',
                        headers=headers,
                        json=body
                    )
                    
                    if response.status_code != 200:
                        raise Exception(f"Groq API error: {response.status_code} - {response.text[:200]}")
                    
                    data = response.json()
                    content = data['choices'][0]['message']['content']
                    
                    if is_reasoning_model:
                        if content.startswith("```json"):
                            content = content[7:]
                        if content.startswith("```"):
                            content = content[3:]
                        if content.endswith("```"):
                            content = content[:-3]
                        content = content.strip()
                    
                    logger.info("✅ Hormone analysis via Groq fallback (sync)")
                    return content
                    
            except Exception as e:
                logger.error(f"❌ Groq fallback also failed: {e}")
                raise Exception(f"Both OpenAI and Groq failed: {openai_error}")
        elif openai_error:
            raise Exception(f"OpenAI failed and no Groq fallback: {openai_error}")
    
    @staticmethod
    def _call_gemini_sync(prompt: str) -> str:
        """Call Gemini API synchronously (legacy fallback)"""
        import google.generativeai as genai
        
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(prompt)
        
        if not response.parts:
            raise ValueError("Empty response from Gemini")
        
        return response.text.strip()
    
    @staticmethod
    def process_others_with_llm(symptom_others: Optional[str], family_others: Optional[str]) -> Dict[str, int]:
        """
        Process free-text "Others" input using LLM (OpenAI primary, Groq fallback, Gemini last)
        
        OPTIMIZED:
        - Uses OpenAI (3,500+ RPM) instead of Gemini (5 RPM)
        - Falls back to Groq (openai/gpt-oss-120b) if OpenAI fails
        - Caches results to prevent duplicate calls within same session
        - Uses JSON mode for reliable parsing
        
        Args:
            symptom_others: User's free-text symptoms from "Others" field
            family_others: User's free-text family history from "Others" field
            
        Returns:
            Dict with hormone scores (0-3) for all 8 hormones
        """
        # Return zeros if empty
        if not symptom_others and not family_others:
            return RootCauseEngine._get_default_scores()
        
        # Check if LLM is enabled
        if not ENABLE_LLM_OTHERS or not LLM_PROVIDER:
            logger.info("ℹ️ Skipping LLM processing (disabled or no API key)")
            return RootCauseEngine._get_default_scores()
        
        # Check cache first (thread-safe, TTL-enabled)
        cache_key = RootCauseEngine._get_cache_key(symptom_others, family_others)
        cached_result = hormone_analysis_cache.get(cache_key)
        if cached_result is not None:
            logger.info(f"✅ Cache HIT for hormone analysis (saved 1 LLM call)")
            return cached_result
        
        try:
            import time as _time
            start_ts = _time.time()
            
            logger.info(f"🤖 LLM PROCESSING ({LLM_PROVIDER.upper()}, model={LLM_OTHERS_MODEL if LLM_PROVIDER == 'openai' else GROQ_FALLBACK_MODEL})")
            logger.info(f"   Symptoms: {symptom_others[:100] if symptom_others else 'None'}...")
            logger.info(f"   Family: {family_others[:100] if family_others else 'None'}...")
            
            # Build prompt
            prompt = RootCauseEngine._build_hormone_prompt(symptom_others, family_others)
            
            # Call appropriate LLM provider (OpenAI first, with Groq fallback built-in)
            if LLM_PROVIDER == "openai" or LLM_PROVIDER == "groq":
                # Both OpenAI and Groq use the same methods with built-in fallback
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Already in async context - use sync to avoid nested loop
                        response_text = RootCauseEngine._call_openai_sync(prompt)
                    else:
                        response_text = loop.run_until_complete(
                            RootCauseEngine._call_openai_async(prompt)
                        )
                except RuntimeError:
                    # No event loop - use sync
                    response_text = RootCauseEngine._call_openai_sync(prompt)
            else:
                # Gemini fallback (last resort)
                response_text = RootCauseEngine._call_gemini_sync(prompt)
            
            elapsed = _time.time() - start_ts
            logger.info(f"⏱️ LLM response in {elapsed:.2f}s")
            
            # Parse response
            scores = RootCauseEngine._parse_llm_response(response_text)
            
            # Cache result (thread-safe, auto-expires after TTL)
            hormone_analysis_cache.set(cache_key, scores)
            logger.info(f"✅ Cached hormone analysis result (key={cache_key[:8]}...)")
            logger.info(f"🎯 Final scores: {scores}")
            
            return scores
            
        except Exception as e:
            logger.error(f"❌ LLM error: {e}")
            return RootCauseEngine._get_default_scores()
    
    @staticmethod
    def analyze_hormone_imbalance(user_data: Dict) -> Dict[str, any]:
        """
        Analyze hormone imbalance based on user data using evidence-based clinical scoring
        
        Args:
            user_data: User survey data with keys matching QuestionScreen.tsx:
                - period_description: str
                - cycle_length: str
                - period_concerns: list
                - body_concerns: list
                - skin_hair_concerns: list
                - mental_health_concerns: list
                - other_concerns: list (can contain "Others: text" format)
                - top_concern: str
                - diagnosed_conditions: list (can contain "Others: text" format)
                - family_history: list (can contain "Others: text" format)
                - workout_intensity: str
                - sleep_duration: str
                - stress_level: str
                
            Note: Frontend sends "Others" text embedded in arrays like:
                ['PCOS', 'Others: My doctor mentioned insulin resistance']
                The backend extracts text after "Others:" and sends to LLM
            
        Returns:
            Dict containing:
            - primary_imbalance: Primary hormone imbalance (e.g., "androgens")
            - primary_level: Primary hormone level (e.g., "high")
            - secondary_imbalances: List of secondary hormone imbalances
            - secondary_levels: List of secondary hormone levels
            - all_scores: Dict of all hormone scores (for debugging)
        """
        logger.info("🔬 ===== HORMONE ANALYSIS STARTED =====")
        logger.info(f"🔬 Input data keys: {list(user_data.keys())}")
        logger.info(f"🔬 period_description: {user_data.get('period_description')}")
        logger.info(f"🔬 cycle_length: {user_data.get('cycle_length')}")
        logger.info(f"🔬 period_concerns: {user_data.get('period_concerns')}")
        logger.info(f"🔬 body_concerns: {user_data.get('body_concerns')}")
        logger.info(f"🔬 skin_hair_concerns: {user_data.get('skin_hair_concerns')}")
        logger.info(f"🔬 mental_health_concerns: {user_data.get('mental_health_concerns')}")
        logger.info(f"🔬 other_concerns: {user_data.get('other_concerns')}")
        logger.info(f"🔬 diagnosed_conditions: {user_data.get('diagnosed_conditions')}")
        logger.info(f"🔬 family_history: {user_data.get('family_history')}")
        logger.info(f"🔬 workout_intensity: {user_data.get('workout_intensity')}")
        logger.info(f"🔬 sleep_duration: {user_data.get('sleep_duration')}")
        logger.info(f"🔬 stress_level: {user_data.get('stress_level')}")
        
        # Initialize scores for 8 hormone states
        scores = {
            "estrogen_high": 0,
            "estrogen_low": 0,
            "progesterone_low": 0,
            "androgens_high": 0,
            "insulin_high": 0,
            "cortisol_high": 0,
            "cortisol_low": 0,
            "thyroid_low": 0
        }
        
        # 1. PERIOD DESCRIPTION (Table 1)
        period_desc = user_data.get("period_description", "")
        if period_desc == "Irregular":
            scores["androgens_high"] += 2
            scores["thyroid_low"] += 1
        elif period_desc == "Occasional Skips":
            scores["androgens_high"] += 1
            scores["progesterone_low"] += 1
        elif period_desc == "I don't get periods":
            scores["androgens_high"] += 2
            scores["estrogen_low"] += 2
        
        # 2. CYCLE LENGTH (Table 2)
        cycle_length = user_data.get("cycle_length", "")
        if cycle_length == "Less than 21 days":
            scores["progesterone_low"] += 2
        elif cycle_length == "31-35 days":
            scores["androgens_high"] += 1
        elif cycle_length == "35+ days":
            scores["androgens_high"] += 2
            scores["insulin_high"] += 1
        
        # 3. PERIOD CONCERNS (Table 3)
        period_concerns = user_data.get("period_concerns") or []
        if isinstance(period_concerns, dict):
            period_concerns = period_concerns.get("concerns", [])
        
        if "Irregular Periods" in period_concerns:
            scores["androgens_high"] += 2
            scores["thyroid_low"] += 1
        if "Painful Periods" in period_concerns:
            scores["estrogen_high"] += 2
            scores["progesterone_low"] += 1
        if "Light periods / Spotting" in period_concerns:
            scores["estrogen_low"] += 2
            scores["progesterone_low"] += 2
        if "Heavy periods" in period_concerns:
            scores["estrogen_high"] += 2
            scores["progesterone_low"] += 1
        
        # 4. BODY CONCERNS (Table 4)
        body_concerns = user_data.get("body_concerns") or []
        if isinstance(body_concerns, dict):
            body_concerns = body_concerns.get("concerns", [])
        
        if "Bloating" in body_concerns:
            scores["estrogen_high"] += 1
            scores["insulin_high"] += 1
        if "Hot Flashes" in body_concerns:
            scores["estrogen_low"] += 2
        if "Nausea" in body_concerns:
            scores["estrogen_high"] += 1
            scores["cortisol_low"] += 1
        if "Difficulty losing weight / stubborn belly fat" in body_concerns:
            scores["insulin_high"] += 2
            scores["cortisol_high"] += 1
            scores["thyroid_low"] += 1
        if "Recent weight gain" in body_concerns:
            scores["insulin_high"] += 2
            scores["thyroid_low"] += 2
            scores["cortisol_high"] += 1
        if "Menstrual headaches" in body_concerns:
            scores["estrogen_high"] += 2
            scores["progesterone_low"] += 1
        
        # 5. SKIN/HAIR CONCERNS (Table 5) - HIGHEST WEIGHTS
        skin_concerns = user_data.get("skin_hair_concerns") or []
        if isinstance(skin_concerns, dict):
            skin_concerns = skin_concerns.get("concerns", [])
        
        if "Hirsutism (hair growth on chin, nipples etc)" in skin_concerns:
            scores["androgens_high"] += 3  # TIER 1 INDICATOR
        if "Thinning of hair" in skin_concerns:
            scores["thyroid_low"] += 2
            scores["androgens_high"] += 1
        if "Adult Acne" in skin_concerns:
            scores["androgens_high"] += 2
            scores["insulin_high"] += 1
        
        # 6. MENTAL HEALTH CONCERNS (Table 6)
        mental_concerns = user_data.get("mental_health_concerns") or []
        if isinstance(mental_concerns, dict):
            mental_concerns = mental_concerns.get("concerns", [])
        
        if "Mood swings" in mental_concerns:
            scores["progesterone_low"] += 2
            scores["estrogen_high"] += 1
        if "Stress" in mental_concerns:
            scores["cortisol_high"] += 2
        if "Fatigue" in mental_concerns:
            scores["thyroid_low"] += 2
            scores["cortisol_low"] += 2
            scores["insulin_high"] += 1
        
        # 7. DIAGNOSED CONDITIONS (Table 7) - HIGHEST WEIGHTS
        diagnosed = user_data.get("diagnosed_conditions") or []
        if not isinstance(diagnosed, list):
            diagnosed = []
        
        if "PCOS" in diagnosed or "PCOD" in diagnosed:
            scores["androgens_high"] += 5
            scores["insulin_high"] += 5
        if "Endometriosis" in diagnosed:
            scores["estrogen_high"] += 5
        if "Dysmenorrhea" in diagnosed:
            scores["estrogen_high"] += 2
            scores["progesterone_low"] += 1
        if "Amenorrhea" in diagnosed:
            scores["androgens_high"] += 3
            scores["estrogen_low"] += 3
        if "Menorrhagia" in diagnosed:
            scores["estrogen_high"] += 5
            scores["progesterone_low"] += 2
        if "Metrorrhagia" in diagnosed:
            scores["estrogen_high"] += 2
            scores["progesterone_low"] += 2
        if "Cushing's Syndrome" in diagnosed:
            scores["cortisol_high"] += 10  # DEFINITIVE
        if "Premenstrual Syndrome" in diagnosed:
            scores["progesterone_low"] += 2
            scores["estrogen_high"] += 1
        if "PMDD" in diagnosed:
            scores["progesterone_low"] += 5
            scores["cortisol_high"] += 2
        if "Diabetes" in diagnosed:
            scores["insulin_high"] += 5
        if "Hashimoto's" in diagnosed or "Hypothyroidism" in diagnosed:
            scores["thyroid_low"] += 5
        
        # 8. FAMILY HISTORY (Table 8) - +1 genetic modifiers
        family = user_data.get("family_history") or []
        if not isinstance(family, list):
            family = []
        
        if "Diabetes" in family:
            scores["insulin_high"] += 1
        if "PCOS" in family or "PCOD" in family:
            scores["androgens_high"] += 1
            scores["insulin_high"] += 1
        if "Endometriosis" in family:
            scores["estrogen_high"] += 1
        if "Amenorrhea" in family:
            scores["estrogen_low"] += 1
        if "Cushing's Syndrome" in family:
            scores["cortisol_high"] += 2
        if "Hashimoto's" in family or "Hypothyroidism" in family:
            scores["thyroid_low"] += 1
        
        # 9. LIFESTYLE FACTORS (Tables 9, 10, 11)
        sleep = user_data.get("sleep_duration", "")
        if sleep == "<6 hours":
            scores["cortisol_high"] += 2
            scores["insulin_high"] += 1
        elif sleep == "6-7 hours":
            scores["cortisol_high"] += 1
        
        stress = user_data.get("stress_level", "")
        if stress == "Moderate":
            scores["cortisol_high"] += 1
        elif stress == "High":
            scores["cortisol_high"] += 2
            scores["progesterone_low"] += 1
        
        # Amplification: High stress + Poor sleep
        if stress == "High" and sleep == "<6 hours":
            scores["cortisol_high"] += 1  # Bonus
        
        workout = user_data.get("workout_intensity", "")
        if workout == "Low" or workout == "I'm yet to start":
            scores["insulin_high"] += 1
        elif workout == "High":
            scores["cortisol_high"] += 1
            scores["progesterone_low"] += 1
        
        # 10. LLM PROCESSING FOR "OTHERS"
        # Extract "Others" text from multiple sources
        symptom_others_texts = []
        family_others_texts = []
        
        # Extract from other_concerns (can be dict or list)
        other_concerns = user_data.get("other_concerns")
        if isinstance(other_concerns, dict):
            # Dict format: {text: "user input"}
            if other_concerns.get("text"):
                symptom_others_texts.append(other_concerns["text"])
        elif isinstance(other_concerns, list):
            # List format: ["option1", "Others: user input"]
            for item in other_concerns:
                if isinstance(item, str) and item.startswith("Others:"):
                    symptom_others_texts.append(item.replace("Others:", "").strip())
        
        # Extract from diagnosed_conditions (list format)
        diagnosed = user_data.get("diagnosed_conditions", [])
        if isinstance(diagnosed, list):
            for item in diagnosed:
                if isinstance(item, str) and item.startswith("Others:"):
                    symptom_others_texts.append(item.replace("Others:", "").strip())
        
        # Extract from family_history (list format)
        family = user_data.get("family_history", [])
        if isinstance(family, list):
            for item in family:
                if isinstance(item, str) and item.startswith("Others:"):
                    family_others_texts.append(item.replace("Others:", "").strip())
        
        # Also check family_history_others dict format (for backwards compatibility)
        family_history_data = user_data.get("family_history_others")
        if isinstance(family_history_data, dict):
            if family_history_data.get("text"):
                family_others_texts.append(family_history_data["text"])
        
        # ADDITIONAL: Check for familyHistoryText (frontend sends this)
        family_history_text = user_data.get("familyHistoryText") or user_data.get("family_history_text")
        if family_history_text and isinstance(family_history_text, str):
            family_others_texts.append(family_history_text.strip())
        
        # Combine texts
        symptom_others = " | ".join(symptom_others_texts) if symptom_others_texts else None
        family_others = " | ".join(family_others_texts) if family_others_texts else None
        
        # Debug logging to verify extraction
        if symptom_others or family_others:
            logger.info(f"🔍 Extracted Others text:")
            logger.info(f"   Symptom sources: {symptom_others_texts}")
            logger.info(f"   Family sources: {family_others_texts}")
        
        # Call LLM if we have any "Others" text
        if symptom_others or family_others:
            llm_scores = RootCauseEngine.process_others_with_llm(symptom_others, family_others)
            # Add LLM scores to totals
            for hormone, score in llm_scores.items():
                scores[hormone] += score
        
        # Log scores after all rule-based and LLM scoring
        logger.info(f"🔬 AFTER ALL SCORING: {scores}")
        
        # 11. TOP CONCERN MULTIPLIER (1.5x)
        # Apply 1.5x multiplier to hormones associated with user's top concern
        top_concern = user_data.get("top_concern")
        if top_concern:
            # Map ALL concerns to hormones (from every category)
            # Based on evidence-based clinical scoring tables used above
            concern_map = {
                # PERIOD CONCERNS (Table 3)
                "Irregular Periods": ["androgens_high", "thyroid_low"],
                "Painful Periods": ["estrogen_high", "progesterone_low"],
                "Light periods / Spotting": ["estrogen_low", "progesterone_low"],
                "Heavy periods": ["estrogen_high", "progesterone_low"],
                
                # BODY CONCERNS (Table 4)
                "Bloating": ["estrogen_high", "insulin_high"],
                "Hot Flashes": ["estrogen_low"],
                "Nausea": ["estrogen_high", "cortisol_low"],
                "Difficulty losing weight / stubborn belly fat": ["insulin_high", "cortisol_high", "thyroid_low"],
                "Recent weight gain": ["insulin_high", "thyroid_low", "cortisol_high"],
                "Menstrual headaches": ["estrogen_high", "progesterone_low"],
                
                # SKIN/HAIR CONCERNS (Table 5)
                "Hirsutism (hair growth on chin, nipples etc)": ["androgens_high"],
                "Thinning of hair": ["thyroid_low", "androgens_high"],
                "Adult Acne": ["androgens_high", "insulin_high"],
                
                # MENTAL HEALTH CONCERNS (Table 6)
                "Mood swings": ["progesterone_low", "cortisol_high"],
                "Stress": ["cortisol_high"],
                "Fatigue": ["thyroid_low", "cortisol_low", "insulin_high"]
            }
            
            # Check if top_concern is in the known mapping
            if top_concern in concern_map:
                for hormone in concern_map[top_concern]:
                    scores[hormone] = int(scores[hormone] * 1.5)
            elif top_concern.startswith("Others:"):
                # For custom "Others:" text as top concern,
                # The LLM already scored it above (section 10)
                # We apply a general multiplier to all non-zero LLM-scored hormones
                # This is handled by the LLM process above, no additional action needed
                logger.info(f"📌 Top concern is custom 'Others:' text: {top_concern}")
                # Note: LLM scores were already added in section 10

        
        # 12. CHECK FOR HEALTHY USER (NO SIGNIFICANT SYMPTOMS)
        # Calculate total score across all hormones
        total_score = sum(scores.values())
        
        # Minimum threshold: at least 3 points needed to show hormone analysis
        # This prevents users with only mild lifestyle factors from getting hormone results
        # (e.g., moderate stress=1 + 6-7h sleep=1 = 2 points should NOT trigger analysis)
        MINIMUM_SCORE_THRESHOLD = 3
        if total_score < MINIMUM_SCORE_THRESHOLD:
            logger.info("🩺 USER IS HEALTHY - No significant hormone indicators detected")
            logger.info(f"   Total score: {total_score} (threshold: {MINIMUM_SCORE_THRESHOLD})")
            logger.info(f"   All Scores: {scores}")
            return {
                "is_healthy": True,
                "primary_imbalance": None,
                "primary_level": None,
                "secondary_imbalances": [],
                "secondary_levels": [],
                "all_scores": scores,
                "total_score": total_score
            }
        
        # 13. IDENTIFY PRIMARY & SECONDARY (only for users with symptoms)
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        
        primary = sorted_scores[0]
        primary_hormone_key = primary[0]
        primary_score = primary[1]
        
        # Parse hormone name and direction
        # e.g., "androgens_high" → hormone="androgens", direction="high"
        if "_" in primary_hormone_key:
            parts = primary_hormone_key.rsplit("_", 1)
            primary_hormone = parts[0]
            primary_level = parts[1]
        else:
            primary_hormone = primary_hormone_key
            primary_level = "unknown"
        
        # Secondary: ALWAYS return the 2nd highest scoring hormone that is DIFFERENT from primary
        # (Skip any hormone with the same base name as primary - prevents estrogen_high + estrogen_low)
        secondary_imbalances = []
        secondary_levels = []
        
        # Find the next highest hormone that is NOT the same base hormone as primary
        for hormone_key, score in sorted_scores[1:]:  # Skip the primary (index 0)
            if "_" in hormone_key:
                parts = hormone_key.rsplit("_", 1)
                h_name = parts[0]
                h_level = parts[1]
            else:
                h_name = hormone_key
                h_level = "unknown"
            
            # Skip if same base hormone as primary (e.g., skip estrogen_low if estrogen_high is primary)
            if h_name == primary_hormone:
                logger.debug(f"Skipping {hormone_key} - same base hormone as primary ({primary_hormone})")
                continue
            
            # Found a valid secondary hormone
            secondary_imbalances.append(h_name)
            secondary_levels.append(h_level)
            break  # Only take the first valid secondary
        
        logger.info(f"🧬 Hormone Analysis Complete:")
        logger.info(f"   Primary: {primary_hormone} ({primary_level}) - Score: {primary_score}")
        logger.info(f"   Secondary: {list(zip(secondary_imbalances, secondary_levels))}")
        logger.info(f"   All Scores: {sorted_scores[:5]}")  # Top 5 scores
        
        return {
            "primary_imbalance": primary_hormone,
            "primary_level": primary_level,
            "secondary_imbalances": secondary_imbalances,
            "secondary_levels": secondary_levels,
            "all_scores": scores  # For debugging
        }
    
    @staticmethod
    def get_formatted_imbalance_text(analysis_result: Dict) -> str:
        """
        Format analysis result into text for prompts
        
        Args:
            analysis_result: Result from analyze_hormone_imbalance
            
        Returns:
            Formatted text (e.g., "progesterone (low), Secondary: testosterone (low)")
        """
        primary = f"{analysis_result['primary_imbalance']} ({analysis_result['primary_level']})"
        
        if analysis_result['secondary_imbalances']:
            secondary_parts = []
            for i, hormone in enumerate(analysis_result['secondary_imbalances']):
                level = analysis_result['secondary_levels'][i] if i < len(analysis_result['secondary_levels']) else "unknown"
                secondary_parts.append(f"{hormone} ({level})")
            secondary_text = f", Secondary: {', '.join(secondary_parts)}"
        else:
            secondary_text = ""
            
        return f"{primary}{secondary_text}"
    
    @staticmethod
    def get_related_hormones(analysis_result: Dict) -> List[str]:
        """
        Extract related hormones from analysis result
        
        Args:
            analysis_result: Result from analyze_hormone_imbalance
            
        Returns:
            List of related hormones (e.g., ["progesterone", "testosterone"])
        """
        hormones = [analysis_result['primary_imbalance']]
        hormones.extend(analysis_result['secondary_imbalances'])
        return hormones
