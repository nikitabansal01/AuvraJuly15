import os
import httpx
import json
import logging
from typing import Any, Dict, List, Optional
from app.models.ai_models import UserProfile
from app.services.root_cause_engine import RootCauseEngine

# Import medical safety modules
try:
    from app.services.safety.medical_safety import SafetyGuardrails, RecommendationAuditLog, EvidenceThresholdChecker
    MEDICAL_SAFETY_ENABLED = True
except ImportError:
    MEDICAL_SAFETY_ENABLED = False

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

class AIService:
    @staticmethod
    def determine_provider_from_model_name(model_name: str) -> str:
        """
        Automatically determine provider from model name (extensible)
        """
        model_name_lower = model_name.lower()
        
        # OpenAI models
        if model_name_lower.startswith(("gpt-", "text-embedding-", "dall-e-", "whisper-")):
            return "openai"
        
        # Groq models
        elif model_name_lower.startswith(("llama-", "mixtral-", "gemma-", "qwen-", "codellama-")):
            return "groq"
        
        # Perplexity models
        elif model_name_lower.startswith(("llama-3.1-", "mixtral-8x7b-", "codellama-", "mistral-7b-", "gemma-2b-", "gemma-7b-")):
            return "perplexity"
        
        # Anthropic models
        elif model_name_lower.startswith(("claude-", "sonnet-", "opus-", "haiku-")):
            return "anthropic"
        
        # Default
        else:
            return "unknown"
    
    @staticmethod
    def get_current_model_config() -> Dict[str, str]:
        """
        Return model configuration to use based on current environment variables (extensible)
        """
        model_version = os.getenv("CURRENT_MODEL", "gpt-4o")
        provider = AIService.determine_provider_from_model_name(model_version)
        
        # Default configuration by provider
        provider_configs = {
            "openai": {
                "provider": "openai",
                "model": model_version,
                "api_key_env": "OPENAI_API_KEY",
                "base_url": "https://api.openai.com/v1"
            },
            "groq": {
                "provider": "groq", 
                "model": model_version,
                "api_key_env": "GROQ_API_KEY",
                "base_url": "https://api.groq.com/openai/v1"
            },
            "anthropic": {
                "provider": "anthropic",
                "model": model_version,
                "api_key_env": "ANTHROPIC_API_KEY",
                "base_url": "https://api.anthropic.com"
            },
            "perplexity": {
                "provider": "perplexity",
                "model": model_version,
                "api_key_env": "PERPLEXITY_API_KEY",
                "base_url": "https://api.perplexity.ai"
            }
        }
        
        return provider_configs.get(provider, provider_configs["openai"])
    
    @staticmethod
    def get_fallback_model_config() -> Dict[str, str]:
        """
        Return fallback model configuration (extensible)
        """
        fallback_model = os.getenv("FALLBACK_MODEL", "llama-3.3-70b")
        provider = AIService.determine_provider_from_model_name(fallback_model)
        
        # Default configuration by provider
        provider_configs = {
            "openai": {
                "provider": "openai",
                "model": fallback_model,
                "api_key_env": "OPENAI_API_KEY",
                "base_url": "https://api.openai.com/v1"
            },
            "groq": {
                "provider": "groq", 
                "model": fallback_model,
                "api_key_env": "GROQ_API_KEY",
                "base_url": "https://api.groq.com/openai/v1"
            },
            "anthropic": {
                "provider": "anthropic",
                "model": fallback_model,
                "api_key_env": "ANTHROPIC_API_KEY",
                "base_url": "https://api.anthropic.com"
            },
            "perplexity": {
                "provider": "perplexity",
                "model": fallback_model,
                "api_key_env": "PERPLEXITY_API_KEY",
                "base_url": "https://api.perplexity.ai"
            }
        }
        
        return provider_configs.get(provider, provider_configs["groq"])
    
    @staticmethod
    async def call_ai_model(prompt: str) -> tuple[str, str]:
        """
        Call appropriate AI model based on environment variables (extensible)
        """
        model_config = AIService.get_current_model_config()
        
        if model_config["provider"] == "openai":
            response = await AIService.call_openai(prompt, model_config["model"])
            return response, model_config["model"]
        elif model_config["provider"] == "groq":
            response = await AIService.call_groq(prompt, model_config["model"])
            return response, model_config["model"]
        elif model_config["provider"] == "anthropic":
            response = await AIService.call_anthropic(prompt, model_config["model"])
            return response, model_config["model"]
        elif model_config["provider"] == "perplexity":
            response = await AIService.call_perplexity(prompt, model_config["model"])
            return response, model_config["model"]
        else:
            logger.error(f"Unsupported model provider: {model_config['provider']}")
            return '', ''
    
    @staticmethod
    async def call_openai(prompt: str, model_name: str = None) -> str:
        """
        OpenAI API call (using official library)
        """
        if not prompt:
            return ''
        
        # Get model name from environment variables if not provided
        if model_name is None:
            model_config = AIService.get_current_model_config()
            model_name = model_config["model"]
        
        try:
            from openai import AsyncOpenAI
            
            client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            
            # Retry logic
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = await client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": "You are a helpful assistant specializing in women's hormone health and wellness."},
                            {"role": "user", "content": prompt}
                        ],
                        max_tokens=1800,
                        temperature=0.7
                    )
                    
                    return response.choices[0].message.content
                    
                except Exception as e:
                    if attempt < max_retries - 1:
                        import asyncio
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    else:
                        logger.error(f"OpenAI API call failed (attempt {attempt + 1}/{max_retries}): {e}")
                        raise e
                        
        except Exception as e:
            logger.error(f"OpenAI API initialization failed: {e}")
            return ''

    @staticmethod
    async def call_groq(prompt: str, model_name: str = None) -> str:
        if not prompt:
            return ''
        
        # Get model name from environment variables if not provided
        if model_name is None:
            model_config = AIService.get_current_model_config()
            model_name = model_config["model"]
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {GROQ_API_KEY}'
        }
        body = {
            'model': model_name,
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.7,
            'max_tokens': 1800
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post('https://api.groq.com/openai/v1/chat/completions', headers=headers, json=body)
            if response.status_code != 200:
                return ''
            data = response.json()
            return data.get('choices', [{}])[0].get('message', {}).get('content', '')

    @staticmethod
    async def call_anthropic(prompt: str, model_name: str = None) -> str:
        """
        Anthropic Claude API call
        """
        if not prompt:
            return ''
        
        # Get model name from environment variables if not provided
        if model_name is None:
            model_config = AIService.get_current_model_config()
            model_name = model_config["model"]
        
        ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
        if not ANTHROPIC_API_KEY:
            logger.error("ANTHROPIC_API_KEY environment variable is not set.")
            return ''
        
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': ANTHROPIC_API_KEY,
            'anthropic-version': '2023-06-01'
        }
        
        body = {
            'model': model_name,
            'max_tokens': 1800,
            'messages': [{'role': 'user', 'content': prompt}]
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    'https://api.anthropic.com/v1/messages',
                    headers=headers,
                    json=body
                )
                if response.status_code != 200:
                    logger.error(f"Anthropic API call failed: {response.status_code}")
                    return ''
                data = response.json()
                return data.get('content', [{}])[0].get('text', '')
        except Exception as e:
            logger.error(f"Error during Anthropic API call: {e}")
            return ''

    @staticmethod
    async def call_perplexity(prompt: str, model_name: str = None) -> str:
        """
        Perplexity API call
        """
        if not prompt:
            return ''
        
        # Get model name from environment variables if not provided
        if model_name is None:
            model_config = AIService.get_current_model_config()
            model_name = model_config["model"]
        
        PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
        if not PERPLEXITY_API_KEY:
            logger.error("PERPLEXITY_API_KEY environment variable is not set.")
            return ''
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {PERPLEXITY_API_KEY}'
        }
        
        body = {
            'model': model_name,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 1800,
            'temperature': 0.7
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    'https://api.perplexity.ai/chat/completions',
                    headers=headers,
                    json=body
                )
                if response.status_code != 200:
                    logger.error(f"Perplexity API call failed: {response.status_code}")
                    return ''
                data = response.json()
                return data.get('choices', [{}])[0].get('message', {}).get('content', '')
        except Exception as e:
            logger.error(f"Error during Perplexity API call: {e}")
            return ''

    @staticmethod
    def parse_recommendations_from_llm(llm_response: str, category: str) -> List[Dict[str, Any]]:
        try:
            import re
            
            # 1. Find JSON array format [...]
            match = re.search(r'\[.*\]', llm_response, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    # Ensure default values (consistent processing)
                    for rec in parsed:
                        # Set default values for required fields
                        rec.setdefault('researchBacking', {'summary': 'Based on current research', 'studies': []})
                        rec.setdefault('contraindications', [])
                        rec.setdefault('frequency', 'Daily')
                        rec.setdefault('expectedTimeline', '4-6 weeks')
                        rec.setdefault('priority', 'medium')
                        rec.setdefault('conditions', [])
                        rec.setdefault('symptoms', [])
                        rec.setdefault('hormones', [])
                        rec.setdefault('frequency_detail', None)
                        rec.setdefault('duration_weeks', None)
                        rec.setdefault('purpose', None)
                        # optimal_times is explicitly set to "omit entirely" in the prompt, so don't set default value
                        
                        # Process category-specific fields and convert arrays (using function parameters)
                        AIService._process_category_specific_fields(rec, category)
                    
                    # Normalize tags
                    normalized_parsed = AIService.normalize_tags(parsed)
                    return normalized_parsed
            
            # 2. Find single JSON object format {...}
            match = re.search(r'\{.*\}', llm_response, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    # Convert single object to array
                    rec = parsed
                    # Set default values for required fields (consistent processing)
                    rec.setdefault('researchBacking', {'summary': 'Based on current research', 'studies': []})
                    rec.setdefault('contraindications', [])
                    rec.setdefault('frequency', 'Daily')
                    rec.setdefault('expectedTimeline', '4-6 weeks')
                    rec.setdefault('priority', 'medium')
                    rec.setdefault('conditions', [])
                    rec.setdefault('symptoms', [])
                    rec.setdefault('hormones', [])
                    rec.setdefault('frequency_detail', None)
                    rec.setdefault('duration_weeks', None)
                    rec.setdefault('purpose', None)
                    # optimal_times is explicitly set to "omit entirely" in the prompt, so don't set default value
                    
                    # Process category-specific fields and convert arrays (using function parameters)
                    AIService._process_category_specific_fields(rec, category)
                    
                    # Normalize tags
                    normalized_parsed = AIService.normalize_tags([rec])
                    return normalized_parsed
            
            return []
        except Exception as e:
            logger.error(f"Recommendation parsing failed: {str(e)}, response: {llm_response[:200]}...")
            return []


    
    @staticmethod
    def _process_category_specific_fields(rec: Dict[str, Any], category: str) -> None:
        """
        Supplement category-specific fields (don't modify AI response, only supplement missing fields)
        """
        category_lower = category.lower()
        
        # Don't modify AI response, only supplement missing fields with default values
        if category_lower == 'food':
            # Supplement with empty arrays if food-related fields are missing
            if 'food_amounts' not in rec:
                rec['food_amounts'] = []
            if 'food_items' not in rec:
                rec['food_items'] = []
            
        elif category_lower == 'movement':
            # Supplement with empty arrays if exercise-related fields are missing
            if 'exercise_durations' not in rec:
                rec['exercise_durations'] = []
            if 'exercise_types' not in rec:
                rec['exercise_types'] = []
            if 'exercise_intensities' not in rec:
                rec['exercise_intensities'] = []
            
        elif category_lower == 'mindfulness':
            # Supplement with empty arrays if mindfulness-related fields are missing
            if 'mindfulness_durations' not in rec:
                rec['mindfulness_durations'] = []
            if 'mindfulness_techniques' not in rec:
                rec['mindfulness_techniques'] = []
    
    @staticmethod
    def normalize_tags(recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Function to normalize tags in recommendations
        """
        # Tag mapping for normalization
        condition_mapping = {
            'pcos': 'PCOS',
            'polycystic ovary syndrome': 'PCOS',
            'pcod': 'PCOD',
            'polycystic ovarian disease': 'PCOD',
            'endometriosis': 'Endometriosis',
            'dysmenorrhea': 'Dysmenorrhea',
            'amenorrhea': 'Amenorrhea',
            "cushing's syndrome": "Cushing's syndrome",
            'cushing syndrome': "Cushing's syndrome",
            'menorrhagia': 'Menorrhagia',
            'metrorrhagia': 'Metrorrhagia',
            'pms': 'PMS',
            'premenstrual syndrome': 'PMS',
            'diabetes': 'Diabetes',
            'pmdd': 'PMDD',
            'premenstrual dysphoric disorder': 'PMDD',
            'perimenopause': 'Perimenopause',
            'menopause': 'Menopause',
            'postmenopausal': 'Postmenopausal'
        }
        
        hormone_mapping = {
            'androgens': 'androgens',
            'androgen': 'androgens',
            'progesterone': 'progesterone',
            'estrogen': 'estrogen',
            'thyroid': 'thyroid',
            'insulin': 'insulin',
            'cortisol': 'cortisol',
            'fsh': 'FSH',
            'follicle stimulating hormone': 'FSH',
            'lh': 'LH',
            'luteinizing hormone': 'LH',
            'prolactin': 'prolactin',
            'ghrelin': 'ghrelin',
            'hunger hormone': 'ghrelin'
        }
        
        symptom_mapping = {
            'irregular periods': 'irregular periods',
            'irregular menstruation': 'irregular periods',
            'painful periods': 'painful periods',
            'dysmenorrhea': 'painful periods',
            'light periods': 'light periods',
            'spotting': 'spotting',
            'heavy periods': 'heavy periods',
            'menorrhagia': 'heavy periods',
            'bloating': 'bloating',
            'hot flashes': 'hot flashes (during the day)',
            'hot flash': 'hot flashes (during the day)',
            'nausea': 'nausea',
            'difficulty losing weight': 'difficulty losing weight',
            'weight loss difficulty': 'difficulty losing weight',
            'stubborn belly fat': 'stubborn belly fat',
            'belly fat': 'stubborn belly fat',
            'weight gain': 'weight gain',
            'menstrual headaches': 'menstrual headaches',
            'hirsutism': 'hirsutism',
            'excessive hair growth': 'hirsutism',
            'thinning of hair': 'thinning of hair/ hairloss',
            'hairloss': 'thinning of hair/ hairloss',
            'hair loss': 'thinning of hair/ hairloss',
            'adult acne': 'adult acne',
            'acne': 'adult acne',
            'mood swings': 'mood swings',
            'stress': 'stress',
            'fatigue': 'fatigue',
            'night sweats': 'night sweats'
        }
        
        normalized_recommendations = []
        
        for rec in recommendations:
            normalized_rec = rec.copy()
            
            # Normalize conditions
            if 'conditions' in normalized_rec:
                normalized_conditions = []
                for condition in normalized_rec['conditions']:
                    condition_lower = condition.lower().strip()
                    normalized_condition = condition_mapping.get(condition_lower, condition)
                    if normalized_condition not in normalized_conditions:
                        normalized_conditions.append(normalized_condition)
                normalized_rec['conditions'] = normalized_conditions
            
            # Normalize hormones
            if 'hormones' in normalized_rec:
                normalized_hormones = []
                for hormone in normalized_rec['hormones']:
                    hormone_lower = hormone.lower().strip()
                    normalized_hormone = hormone_mapping.get(hormone_lower, hormone)
                    if normalized_hormone not in normalized_hormones:
                        normalized_hormones.append(normalized_hormone)
                normalized_rec['hormones'] = normalized_hormones
            
            # Normalize symptoms
            if 'symptoms' in normalized_rec:
                normalized_symptoms = []
                for symptom in normalized_rec['symptoms']:
                    symptom_lower = symptom.lower().strip()
                    normalized_symptom = symptom_mapping.get(symptom_lower, symptom)
                    if normalized_symptom not in normalized_symptoms:
                        normalized_symptoms.append(normalized_symptom)
                normalized_rec['symptoms'] = normalized_symptoms
            
            normalized_recommendations.append(normalized_rec)
        
        return normalized_recommendations

    @staticmethod
    def evaluate_llm_confidence(llm_response: str) -> int:
        import re
        if not llm_response or llm_response.strip() == '':
            return 0
        confidence_match = re.search(r'confidence:\s*(\d+)', llm_response, re.IGNORECASE)
        if confidence_match:
            return int(confidence_match.group(1))
        try:
            match = re.search(r'\[.*\]', llm_response, re.DOTALL)
            if not match:
                return 30
            parsed = json.loads(match.group(0))
            if not isinstance(parsed, list) or not parsed:
                return 40
            quality_score = 0
            for rec in parsed:
                if rec.get('title') and rec.get('specificAction') and rec.get('researchBacking'):
                    quality_score += 20
                if rec.get('researchBacking', {}).get('studies'):
                    if len(rec['researchBacking']['studies']) > 0:
                        quality_score += 10
            return min(quality_score, 100)
        except Exception:
            return 20

    @staticmethod
    def process_recommendations_with_safety(
        recommendations: List[Dict[str, Any]],
        user_profile: UserProfile,
        category: str,
        retrieved_papers: List[Dict[str, Any]] = None,
        user_id: str = None,
        llm_prompt: str = None
    ) -> List[Dict[str, Any]]:
        """
        Process recommendations with medical safety checks and audit logging.
        
        Adds:
        - Contraindication checking
        - Safety warnings
        - Mandatory disclaimers
        - Audit logging for provenance
        """
        if not MEDICAL_SAFETY_ENABLED:
            logger.warning("Medical safety module not available - skipping safety checks")
            return recommendations
        
        if not recommendations:
            return recommendations
        
        processed_recommendations = []
        
        # Get user conditions for contraindication checking
        user_conditions = user_profile.conditions or []
        
        for rec in recommendations:
            try:
                # Apply safety guardrails
                processed_rec = SafetyGuardrails.process_recommendation(
                    recommendation=rec,
                    user_conditions=user_conditions,
                    user_medications=None  # Could add medications to UserProfile in future
                )
                
                # Log each recommendation for audit trail
                if user_id:
                    try:
                        RecommendationAuditLog.log_recommendation(
                            user_id=user_id,
                            user_profile=user_profile.dict() if hasattr(user_profile, 'dict') else {},
                            category=category,
                            retrieved_papers=retrieved_papers or [],
                            recommendation=processed_rec,
                            llm_prompt=llm_prompt
                        )
                    except Exception as audit_error:
                        logger.warning(f"Audit logging failed: {audit_error}")
                
                processed_recommendations.append(processed_rec)
                
            except Exception as safety_error:
                logger.error(f"Safety processing failed for recommendation: {safety_error}")
                # Still include the recommendation but mark it
                rec['safety_error'] = str(safety_error)
                processed_recommendations.append(rec)
        
        # Log high-risk recommendations for manual review
        high_risk_count = sum(1 for r in processed_recommendations if r.get('risk_level') == 'high')
        if high_risk_count > 0:
            logger.warning(f"⚠️ {high_risk_count} high-risk recommendations flagged for review in {category}")
        
        return processed_recommendations

    @staticmethod
    def suggest_llm_prompt_for_recommendations(user_profile: UserProfile, category: str) -> str:
        up = user_profile
        
        # Use root cause engine to analyze hormone imbalance
        root_cause_analysis = RootCauseEngine.analyze_hormone_imbalance(user_profile.dict())
        imbalance_text = RootCauseEngine.get_formatted_imbalance_text(root_cause_analysis)
        related_hormones = RootCauseEngine.get_related_hormones(root_cause_analysis)
        
        # Extract Primary/Secondary hormone information
        primary_hormone = root_cause_analysis.get("primary_imbalance", "")
        secondary_hormones = root_cause_analysis.get("secondary_imbalances", [])
        
        user_health_profile = ', '.join(filter(None, [
            f"Age: {up.age}" if up.age else None,
            f"Ethnicity: {up.ethnicity}" if up.ethnicity else None,
            f"Cycle phase: {up.cyclePhase}" if up.cyclePhase and up.cyclePhase != 'unknown' else None,
            f"Birth control: {up.birthControlStatus}" if up.birthControlStatus else None,
            f"Diagnosis: {', '.join(up.conditions)}" if up.conditions else None,
            f"Symptoms: {', '.join(up.symptoms)}" if up.symptoms else None
        ]))
        
        # Pre-define JSON examples
        example_study = {
            "title": "Cinnamon Supplementation Improves Insulin Sensitivity in Women with PCOS",
            "authors": ["Lee J", "Kim S", "Park M"],
            "journal": "Diabetes Research",
            "publicationYear": 2023,
            "participantCount": 130,
            "results": "Improved insulin sensitivity by 25% and reduced fasting glucose"
        }
        
        example_recommendation = {
            "title": "Cinnamon Supplementation",
            "purpose": "Cinnamon helps improve insulin sensitivity and reduce blood sugar levels",
            "specificAction": "Take 1.5g of cinnamon powder daily",
            "frequency": "Daily",
            "intensity": "Moderate",
            "expectedTimeline": "12 weeks",
            "priority": "high",
            "contraindications": ["Not recommended during pregnancy"],
            "conditions": ["PCOS"],
            "symptoms": ["weight gain"],
            "hormones": ["insulin"],
            "food_amounts": ["1.5g"],
            "food_items": ["cinnamon powder"],
            "frequency_detail": "daily:1",
            "duration_weeks": 12,
            "researchBacking": {
                "summary": "Based on 2023 study with 130 women showing Improved insulin sensitivity by 25% and reduced fasting glucose",
                "studies": [example_study]
            }
        }
        
        # researchBacking structure example
        research_backing_structure = {
            "summary": "Based on [YEAR] study with [NUMBER] women showing [SPECIFIC RESULTS]",
            "studies": [
                {
                    "title": "Study Title",
                    "authors": ["Author1", "Author2"],
                    "journal": "Journal Name",
                    "publicationYear": 2023,
                    "participantCount": 130,
                    "results": "Specific results description"
                }
            ]
        }
        
        prompt = f'''
You are a medical AI assistant specializing in women's hormone health. Your task is to generate HIGHLY SPECIFIC, SCIENTIFICALLY-BASED recommendations with exact amounts, durations, and frequencies.

Category: {category}
Root cause (hormones out of balance): {imbalance_text}
User health profile: {user_health_profile}

CRITICAL HORMONE FOCUS REQUIREMENT:
- You MUST focus ONLY on recommendations related to the identified hormone imbalances: {', '.join(related_hormones)}
- All recommendations must directly address these specific hormone imbalances
- Do NOT include recommendations for other hormones not identified in the root cause analysis
- The hormones field in each recommendation must ONLY contain hormones from the root cause analysis: {', '.join(related_hormones)}

PRIMARY/SECONDARY HORMONE BALANCE REQUIREMENT:
- Primary hormone ({primary_hormone}): You MUST include at least 1 recommendation specifically targeting this hormone
- Secondary hormones ({', '.join(secondary_hormones)}): You MUST include at least 1 recommendation specifically targeting these hormones
- Ensure balanced coverage of both primary and secondary hormone imbalances
- If you have limited recommendations, prioritize primary hormone first, then secondary hormones

SCIENTIFIC REQUIREMENTS:
- Use ONLY research studies from the last 10 years on women's hormonal health
- Medical accuracy is CRITICAL - every recommendation must be based on actual clinical studies
- Match research to user's specific health profile (hormones, conditions, symptoms)
- Medical factors (symptoms, diagnosis) carry more weight than demographic factors
- STRONGLY prefer human clinical trials over animal studies
- If research mentions specific supplements/nutrients, you may reference additional studies for food sources and amounts
- ALL recommendations must be actionable with specific amounts, durations, and frequencies

CRITICAL REQUIREMENTS FOR SPECIFIC ACTIONS:
- FOOD: Specify exact amounts (grams, cups, servings) and frequency. Example: "Consume 2 tablespoons of ground flaxseed daily" or "Eat 100g of salmon 3 times per week"
- MOVEMENT: Specify exact duration, intensity, and frequency. Example: "Perform 30-minute moderate-intensity yoga sessions 4 times per week" or "Walk briskly for 45 minutes daily"
- MINDFULNESS: Specify exact duration, technique, and frequency. Example: "Practice 15-minute daily meditation" or "Perform 20-minute deep breathing exercises twice daily"
- ALL recommendations must include: exact amounts/times and frequency (daily/weekly)
- Base ALL recommendations on actual research studies from the last 10 years
- If research mentions specific supplements/nutrients, you may reference additional studies for food sources and amounts

TIME UNIT STANDARDIZATION FOR DURATION ARRAYS:
- exercise_durations: Use "min" instead of "minutes" or "minute" (e.g., ["30 min", "45 min"] not ["30 minutes", "45 minutes"])
- mindfulness_durations: Use "min" instead of "minutes" or "minute" (e.g., ["15 min", "20 min"] not ["15 minutes", "20 minutes"])
- For hours, use "h" instead of "hours" or "hour" (e.g., ["1.5 h", "2 h"] not ["1.5 hours", "2 hours"])
- This standardization applies ONLY to the exercise_durations and mindfulness_durations array fields

FREQUENCY DETAIL FORMAT (for scheduling):
- Use structured format: "frequency_detail": "daily:1" or "frequency_detail": "weekly:3" or "frequency_detail": "daily:2"
- Format: "frequency_detail": "[type]:[times]"
- Types: "daily", "weekly", "monthly"
- Times: number of times per period
- Examples: "daily:1" (once daily), "daily:2" (twice daily), "weekly:3" (3 times per week), "weekly:5" (5 times per week), "monthly:1" (once per month), "monthly:2" (twice per month)

OPTIMAL TIMING REQUIREMENTS:
- ONLY include optimal_times if the research study specifically mentions timing (e.g., "morning", "afternoon", "night", "before meals", "after exercise")
- If research mentions timing, include ONLY the single best time from the research
- If research doesn't mention timing, DO NOT include optimal_times field at all
- NEVER guess or assume timing - only use what's explicitly stated in research
- NEVER include multiple times unless research specifically compares and recommends multiple times

RESEARCH BACKING FORMAT:
- Summary: "Based on [YEAR] study with [NUMBER] women showing [SPECIFIC RESULTS]"
- Example: "Based on 2023 study with 130 women showing Improved insulin sensitivity by 25% and reduced fasting glucose"
- Studies must include: title, authors (array), journal, publicationYear, participantCount, results
- Example study: {json.dumps(example_study, ensure_ascii=False)}

TAGGING REQUIREMENTS:
- Each recommendation must include tags for related conditions, symptoms, and hormones
- Use ONLY the exact standardized terms listed below:

CONDITIONS/DISEASES (use exact terms):
- PCOD, PCOS, Endometriosis, Dysmenorrhea, Amenorrhea, Cushing's syndrome, Menorrhagia, Metrorrhagia, PMS, Diabetes, PMDD, Perimenopause, Menopause, Postmenopausal

HORMONES/BIOMARKERS (use exact terms):
- androgens, progesterone, estrogen, thyroid, insulin, cortisol, FSH, LH, prolactin, ghrelin

TARGET SYMPTOMS (use exact terms):
- irregular periods, painful periods, light periods, spotting, heavy periods, bloating, hot flashes (during the day), nausea, difficulty losing weight, stubborn belly fat, weight gain, menstrual headaches, hirsutism, thinning of hair/ hairloss, adult acne, mood swings, stress, fatigue, night sweats

- conditions: Array of related medical conditions (e.g., ["PCOS", "Endometriosis"])
- symptoms: Array of related symptoms (e.g., ["irregular periods", "weight gain", "adult acne"])
- hormones: Array of related hormones (e.g., ["insulin", "androgens", "cortisol"])
  IMPORTANT: Only include hormones from the root cause analysis: {', '.join(related_hormones)}
  Do NOT include other hormones not identified in the root cause analysis

TITLE AND PURPOSE FORMAT:
- title: 1-2 words describing the specific method/technique (e.g., "Cinnamon Supplementation", "Yoga Practice", "Meditation")
- purpose: Natural, descriptive sentence explaining what the recommendation does and its benefits (e.g., "Cinnamon helps improve insulin sensitivity and reduce blood sugar levels", "Yoga practice helps reduce stress and balance cortisol levels", "Meditation helps calm the mind and reduce anxiety")

CRITICAL OUTPUT FORMAT REQUIREMENTS:
Return a JSON array of recommendation cards. Each card must include EXACTLY these fields:

REQUIRED FIELDS (all categories):
- title: 1-2 words describing the specific method/technique
- purpose: Natural, descriptive sentence explaining what the recommendation does and its benefits
- specificAction: Exact action with amounts/duration (e.g., "Take 1.5g of cinnamon powder daily")
- frequency: "Daily", "Weekly", etc.
- intensity: "Low", "Moderate", "High"
- expectedTimeline: "12 weeks", "8 weeks", etc.
- priority: "high", "medium", or "low"
- contraindications: Array of contraindications (e.g., ["Not recommended during pregnancy"])
- conditions: Array of related medical conditions (e.g., ["PCOS", "Endometriosis"])
- symptoms: Array of related symptoms (e.g., ["irregular periods", "weight gain"])
- hormones: Array of related hormones (e.g., ["insulin", "androgens"])
  IMPORTANT: Only include hormones from the root cause analysis: {', '.join(related_hormones)}
  Do NOT include other hormones not identified in the root cause analysis
- frequency_detail: Structured format (e.g., "daily:1", "weekly:3")
- duration_weeks: Number only (e.g., 12, 8, 16)
- researchBacking: Object with EXACTLY this structure:
{json.dumps(research_backing_structure, ensure_ascii=False, indent=2)}

CATEGORY-SPECIFIC FIELDS (use EXACT field names):

FOOD recommendations (MUST include):
- food_amounts: Array of exact amounts (e.g., ["150g", "100g", "2 tablespoons"]) - DO NOT use "food_amount"
- food_items: Array of food items (e.g., ["oats", "lentils", "flaxseed"]) - DO NOT use "food_item"
- optimal_times: Array with single time (e.g., ["morning"]) - ONLY if research mentions timing, otherwise OMIT entirely

MOVEMENT recommendations (MUST include):
- exercise_durations: Array of durations (e.g., ["30 min", "45 min"]) - DO NOT use "exercise_duration"
- exercise_types: Array of exercise types (e.g., ["yoga", "walking"]) - DO NOT use "exercise_type"
- exercise_intensities: Array of intensities (e.g., ["moderate", "low"]) - DO NOT use "exercise_intensity"
- optimal_times: Array with single time (e.g., ["morning"]) - ONLY if research mentions timing, otherwise OMIT entirely

MINDFULNESS recommendations (MUST include):
- mindfulness_durations: Array of durations (e.g., ["15 min", "20 min"]) - DO NOT use "mindfulness_duration"
- mindfulness_techniques: Array of techniques (e.g., ["meditation", "deep breathing"]) - DO NOT use "mindfulness_technique"
- optimal_times: Array with single time (e.g., ["morning"]) - ONLY if research mentions timing, otherwise OMIT entirely

Generate as many relevant cards as possible.

Example structure: {json.dumps([example_recommendation], ensure_ascii=False)}

Note: The example above does NOT include "optimal_times" because the research doesn't specifically mention timing. Only include "optimal_times" if research explicitly mentions timing.

CONFIDENCE ASSESSMENT:
- If you are highly confident in your recommendations (based on strong research evidence), include "confidence: 90" in your response
- If you are moderately confident (some research support but limited), include "confidence: 70" in your response  
- If you are less confident (limited research or extrapolation), include "confidence: 50" in your response
- If you cannot provide evidence-based recommendations, include "confidence: 30" and explain why
- Always base confidence on the quality and relevance of available research for this specific user profile
'''
        return prompt

    # NOTE: Legacy RAG functions removed - now using RAG v2 from app.services.rag/
    # Removed: create_rag_enhanced_prompt, generate_rag_recommendations, 
    #          search_relevant_research_by_category, create_category_search_query,
    #          create_category_filter, filter_symptoms_by_category, extract_research_texts

    @staticmethod
    def parse_frequency_detail(frequency_detail: str) -> dict:
        """
        Parse structured frequency_detail into schedulable format
        Format: "type:times" (e.g., "daily:1", "weekly:3", "monthly:1")
        """
        if not frequency_detail:
            return {"type": "unknown", "times": 0, "description": "No frequency specified"}
        
        try:
            if ":" not in frequency_detail:
                # Fallback for existing format compatibility
                return {"type": "custom", "times": 0, "description": frequency_detail}
            
            freq_type, times_str = frequency_detail.split(":", 1)
            times = int(times_str)
            
            return {
                "type": freq_type.lower(),  # "daily", "weekly", "monthly"
                "times": times,             # 1, 2, 3, etc.
                "description": frequency_detail
            }
            
        except (ValueError, AttributeError) as e:
            logger.warning(f"frequency_detail parsing failed: {frequency_detail}, error: {str(e)}")
            return {"type": "custom", "times": 0, "description": frequency_detail}

    @staticmethod
    async def generate_session_recommendations(user_profile: UserProfile, category: str) -> List[Dict[str, Any]]:
        """
        Generate session recommendations (for background processing)
        
        NEW RAG ARCHITECTURE:
        Uses RAGOrchestrator for full pipeline: Retrieve → Compile → Generate → Validate
        Falls back to prompt-only if RAG fails
        """
        try:
            # ========================================
            # STEP 1: Try NEW RAG Orchestrator (with citation validation)
            # ========================================
            try:
                from app.services.rag.rag_orchestrator import generate_rag_recommendations
                
                logger.info(f"🔬 RAG v2: Starting orchestrated RAG pipeline for category={category}")
                
                recommendations = await generate_rag_recommendations(
                    user_profile=user_profile,
                    category=category,
                    use_rag=True
                )
                
                if recommendations:
                    # Count verified citations
                    verified_count = sum(1 for r in recommendations if r.get('citation_verified', False))
                    logger.info(f"✅ RAG v2: Generated {len(recommendations)} recommendations, "
                               f"{verified_count} with verified citations for {category}")
                    return recommendations
                else:
                    logger.warning(f"⚠️ RAG v2: No recommendations from orchestrator, trying legacy RAG")
                    
            except ImportError as import_error:
                logger.warning(f"⚠️ RAG v2 modules not available: {import_error}, using prompt-only")
            except Exception as rag_v2_error:
                logger.warning(f"⚠️ RAG v2 failed for {category}: {str(rag_v2_error)}, using prompt-only")


            # ========================================
            # STEP 3: Fallback to prompt-only (no research)
            # ========================================
            logger.info(f"📝 Using prompt-only generation for category={category}")
            
            # Create prompt (without real research)
            prompt = AIService.suggest_llm_prompt_for_recommendations(user_profile, category)
            logger.info(f"Session recommendation prompt creation completed: category={category}")
            
            # Call OpenAI API
            llm_response, actual_model = await AIService.call_ai_model(prompt)
            logger.info(f"AI model call completed: category={category}, model={actual_model}")
            
            # Evaluate confidence
            confidence = AIService.evaluate_llm_confidence(llm_response)
            logger.info(f"Confidence evaluation completed: category={category}, confidence={confidence}")
            
            # Parse response
            recommendations = AIService.parse_recommendations_from_llm(llm_response, category)
            logger.info(f"Response parsing completed: category={category}, recommendations_count={len(recommendations) if recommendations else 0}")
            
            # Mark as prompt-only (unverified citations)
            for rec in recommendations if recommendations else []:
                rec['citation_verified'] = False
                rec['rag_version'] = 'prompt_only'
                rec['citation_warning'] = 'Generated without RAG - citations may be hallucinated'
            
            # Fallback: low confidence or no recommendations → use fallback model
            if confidence < 60 or not recommendations:
                fallback_config = AIService.get_fallback_model_config()
                if fallback_config["provider"] == "openai":
                    fallback_response = await AIService.call_openai(prompt, fallback_config["model"])
                elif fallback_config["provider"] == "groq":
                    fallback_response = await AIService.call_groq(prompt, fallback_config["model"])
                elif fallback_config["provider"] == "anthropic":
                    fallback_response = await AIService.call_anthropic(prompt, fallback_config["model"])
                elif fallback_config["provider"] == "perplexity":
                    fallback_response = await AIService.call_perplexity(prompt, fallback_config["model"])
                else:
                    logger.error(f"Unsupported fallback model provider: {fallback_config['provider']}")
                    return recommendations
                
                fallback_confidence = AIService.evaluate_llm_confidence(fallback_response)
                fallback_recommendations = AIService.parse_recommendations_from_llm(fallback_response, category)
                if fallback_recommendations and fallback_confidence > confidence:
                    # Mark fallback recommendations
                    for rec in fallback_recommendations:
                        rec['citation_verified'] = False
                        rec['rag_version'] = 'prompt_only_fallback'
                    recommendations = fallback_recommendations
            
            return recommendations if recommendations else []
            
        except Exception as e:
            logger.error(f"Session recommendation generation failed (category={category}): {str(e)}")
            return [] 