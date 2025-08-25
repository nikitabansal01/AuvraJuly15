import os
import httpx
import json
import logging
from typing import Any, Dict, List, Optional
from app.models.ai_models import UserProfile

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

class AIService:
    @staticmethod
    def determine_provider_from_model_name(model_name: str) -> str:
        """
        모델명에서 provider 자동 결정 (확장 가능)
        """
        model_name_lower = model_name.lower()
        
        # OpenAI 모델들
        if model_name_lower.startswith(("gpt-", "text-embedding-", "dall-e-", "whisper-")):
            return "openai"
        
        # Groq 모델들
        elif model_name_lower.startswith(("llama-", "mixtral-", "gemma-", "qwen-", "codellama-")):
            return "groq"
        
        # Perplexity 모델들
        elif model_name_lower.startswith(("llama-3.1-", "mixtral-8x7b-", "codellama-", "mistral-7b-", "gemma-2b-", "gemma-7b-")):
            return "perplexity"
        
        # Anthropic 모델들
        elif model_name_lower.startswith(("claude-", "sonnet-", "opus-", "haiku-")):
            return "anthropic"
        
        # 기본값
        else:
            return "unknown"
    
    @staticmethod
    def get_current_model_config() -> Dict[str, str]:
        """
        현재 환경변수에 따라 사용할 모델 설정을 반환 (확장 가능)
        """
        model_version = os.getenv("CURRENT_MODEL", "gpt-4o")
        provider = AIService.determine_provider_from_model_name(model_version)
        
        # Provider별 기본 설정
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
        Fallback 모델 설정을 반환 (확장 가능)
        """
        fallback_model = os.getenv("FALLBACK_MODEL", "llama-3.3-70b")
        provider = AIService.determine_provider_from_model_name(fallback_model)
        
        # Provider별 기본 설정
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
        환경변수에 따라 적절한 AI 모델을 호출 (확장 가능)
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
            logger.error(f"지원하지 않는 모델 프로바이더: {model_config['provider']}")
            return '', ''
    
    @staticmethod
    async def call_openai(prompt: str, model_name: str = None) -> str:
        """
        OpenAI API 호출 (공식 라이브러리 사용)
        """
        if not prompt:
            return ''
        
        # 모델명이 없으면 환경변수에서 가져오기
        if model_name is None:
            model_config = AIService.get_current_model_config()
            model_name = model_config["model"]
        
        try:
            from openai import AsyncOpenAI
            
            client = AsyncOpenAI(api_key=OPENAI_API_KEY)
            
            # 재시도 로직
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
                        await asyncio.sleep(2 ** attempt)  # 지수 백오프
                        continue
                    else:
                        logger.error(f"OpenAI API 호출 실패 (시도 {attempt + 1}/{max_retries}): {e}")
                        raise e
                        
        except Exception as e:
            logger.error(f"OpenAI API 초기화 실패: {e}")
            return ''

    @staticmethod
    async def call_groq(prompt: str, model_name: str = None) -> str:
        if not prompt:
            return ''
        
        # 모델명이 없으면 환경변수에서 가져오기
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
        Anthropic Claude API 호출
        """
        if not prompt:
            return ''
        
        # 모델명이 없으면 환경변수에서 가져오기
        if model_name is None:
            model_config = AIService.get_current_model_config()
            model_name = model_config["model"]
        
        ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
        if not ANTHROPIC_API_KEY:
            logger.error("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
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
                    logger.error(f"Anthropic API 호출 실패: {response.status_code}")
                    return ''
                data = response.json()
                return data.get('content', [{}])[0].get('text', '')
        except Exception as e:
            logger.error(f"Anthropic API 호출 중 오류: {e}")
            return ''

    @staticmethod
    async def call_perplexity(prompt: str, model_name: str = None) -> str:
        """
        Perplexity API 호출
        """
        if not prompt:
            return ''
        
        # 모델명이 없으면 환경변수에서 가져오기
        if model_name is None:
            model_config = AIService.get_current_model_config()
            model_name = model_config["model"]
        
        PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
        if not PERPLEXITY_API_KEY:
            logger.error("PERPLEXITY_API_KEY 환경변수가 설정되지 않았습니다.")
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
                    logger.error(f"Perplexity API 호출 실패: {response.status_code}")
                    return ''
                data = response.json()
                return data.get('choices', [{}])[0].get('message', {}).get('content', '')
        except Exception as e:
            logger.error(f"Perplexity API 호출 중 오류: {e}")
            return ''

    @staticmethod
    def parse_recommendations_from_llm(llm_response: str) -> List[Dict[str, Any]]:
        try:
            import re
            
            # 1. JSON 배열 형식 찾기 [...]
            match = re.search(r'\[.*\]', llm_response, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    # 기본값 보장
                    for rec in parsed:
                        rec.setdefault('researchBacking', {'summary': 'Based on current research', 'studies': []})
                        rec.setdefault('contraindications', [])
                        rec.setdefault('frequency', 'Daily')
                        rec.setdefault('expectedTimeline', '4-6 weeks')
                        rec.setdefault('priority', 'medium')
                        # 새로운 태그 필드들의 기본값
                        rec.setdefault('conditions', [])
                        rec.setdefault('symptoms', [])
                        rec.setdefault('hormones', [])
                        rec.setdefault('frequency_detail', None)
                        rec.setdefault('duration_weeks', None)
                        rec.setdefault('purpose', None)  # 목적 필드 기본값
                        # optimal_times는 연구에 언급된 경우에만 포함되므로 기본값 설정하지 않음
                        
                        # 카테고리별 필드 정리 및 배열 변환
                        AIService._process_category_specific_fields(rec)
                    
                    # 태그 정규화
                    normalized_parsed = AIService.normalize_tags(parsed)
                    return normalized_parsed
            
            # 2. 단일 JSON 객체 형식 찾기 {...}
            match = re.search(r'\{.*\}', llm_response, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    # 단일 객체를 배열로 변환
                    rec = parsed
                    rec.setdefault('researchBacking', {'summary': 'Based on current research', 'studies': []})
                    rec.setdefault('contraindications', [])
                    rec.setdefault('frequency', 'Daily')
                    rec.setdefault('expectedTimeline', '4-6 weeks')
                    rec.setdefault('priority', 'medium')
                    # 새로운 태그 필드들의 기본값
                    rec.setdefault('conditions', [])
                    rec.setdefault('symptoms', [])
                    rec.setdefault('hormones', [])
                    rec.setdefault('frequency_detail', None)
                    rec.setdefault('duration_weeks', None)
                    rec.setdefault('purpose', None)  # 목적 필드 기본값
                    
                    # 카테고리별 필드 정리 및 배열 변환
                    AIService._process_category_specific_fields(rec)
                    
                    # 태그 정규화
                    normalized_parsed = AIService.normalize_tags([rec])
                    return normalized_parsed
            
            return []
        except Exception as e:
            logger.error(f"추천 파싱 실패: {str(e)}, 응답: {llm_response[:200]}...")
            return []


    
    @staticmethod
    def _process_category_specific_fields(rec: Dict[str, Any]) -> None:
        """
        카테고리별 필드 정리 및 배열 변환
        """
        # 기존 필드들 제거
        fields_to_remove = [
            'food_amount', 'food_item', 'exercise_duration', 'exercise_type', 
            'exercise_intensity', 'mindfulness_duration', 'mindfulness_technique'
        ]
        
        for field in fields_to_remove:
            if field in rec:
                del rec[field]
        
        # 카테고리별 필드 추가
        if 'category' in rec:
            category = rec['category'].lower()
            
            if category == 'food':
                # 음식 관련 필드만 추가
                rec['food_amounts'] = []  # ["150g", "100g"]
                rec['food_items'] = []    # ["oats", "lentils"]
                
            elif category == 'movement':
                # 운동 관련 필드만 추가
                rec['exercise_durations'] = []  # ["30 minutes", "45 minutes"]
                rec['exercise_types'] = []      # ["yoga", "walking"]
                rec['exercise_intensities'] = [] # ["moderate", "low"]
                
            elif category == 'mindfulness':
                # 마음챙김 관련 필드만 추가
                rec['mindfulness_durations'] = []  # ["15 minutes", "20 minutes"]
                rec['mindfulness_techniques'] = [] # ["meditation", "deep breathing"]
    
    @staticmethod
    def normalize_tags(recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        추천의 태그들을 정규화하는 함수
        """
        # 정규화할 태그 매핑
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
            
            # conditions 정규화
            if 'conditions' in normalized_rec:
                normalized_conditions = []
                for condition in normalized_rec['conditions']:
                    condition_lower = condition.lower().strip()
                    normalized_condition = condition_mapping.get(condition_lower, condition)
                    if normalized_condition not in normalized_conditions:
                        normalized_conditions.append(normalized_condition)
                normalized_rec['conditions'] = normalized_conditions
            
            # hormones 정규화
            if 'hormones' in normalized_rec:
                normalized_hormones = []
                for hormone in normalized_rec['hormones']:
                    hormone_lower = hormone.lower().strip()
                    normalized_hormone = hormone_mapping.get(hormone_lower, hormone)
                    if normalized_hormone not in normalized_hormones:
                        normalized_hormones.append(normalized_hormone)
                normalized_rec['hormones'] = normalized_hormones
            
            # symptoms 정규화
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
    def suggest_llm_prompt_for_recommendations(user_profile: UserProfile, category: str) -> str:
        up = user_profile
        user_health_profile = ', '.join(filter(None, [
            f"Age: {up.age}" if up.age else None,
            f"Ethnicity: {up.ethnicity}" if up.ethnicity else None,
            f"Cycle phase: {up.cyclePhase}" if up.cyclePhase and up.cyclePhase != 'unknown' else None,
            f"Birth control: {up.birthControlStatus}" if up.birthControlStatus else None,
            f"Diagnosis: {', '.join(up.conditions)}" if up.conditions else None,
            f"Symptoms: {', '.join(up.symptoms)}" if up.symptoms else None
        ]))
        secondary_imbalances_text = f", Secondary: {', '.join(up.secondaryImbalances)}" if up.secondaryImbalances else ''
        prompt = f'''
You are a medical AI assistant specializing in women's hormone health. Your task is to generate HIGHLY SPECIFIC, SCIENTIFICALLY-BASED recommendations with exact amounts, durations, and frequencies.

Category: {category}
Root cause (hormones out of balance): {up.primaryImbalance}{secondary_imbalances_text}
User health profile: {user_health_profile}

SCIENTIFIC REQUIREMENTS:
- Use ONLY research studies from the last 10 years on women's hormonal health
- Medical accuracy is CRITICAL - every recommendation must be based on actual clinical studies
- Match research to user's specific health profile (hormones, conditions, symptoms)
- Medical factors (symptoms, diagnosis) carry more weight than demographic factors
- STRONGLY prefer human clinical trials over animal studies
- If research mentions specific supplements/nutrients, you may reference additional studies for food sources and amounts
- ALL recommendations must be actionable with specific amounts, durations, and frequencies

CRITICAL REQUIREMENTS FOR SPECIFIC ACTIONS:
- FOOD: Specify exact amounts (grams, cups, servings) and frequency. Example: "Consume 2 tablespoons of ground flaxseed daily for 12 weeks" or "Eat 100g of salmon 3 times per week for 8 weeks"
- MOVEMENT: Specify exact duration, intensity, and frequency. Example: "Perform 30-minute moderate-intensity yoga sessions 4 times per week for 12 weeks" or "Walk briskly for 45 minutes daily for 8 weeks"
- MINDFULNESS: Specify exact duration, technique, and frequency. Example: "Practice 15-minute daily meditation for 12 weeks" or "Perform 20-minute deep breathing exercises twice daily for 8 weeks"
- ALL recommendations must include: exact duration (weeks only, as number), frequency (daily/weekly), and specific amounts/times
- Base ALL recommendations on actual research studies from the last 10 years
- If research mentions specific supplements/nutrients, you may reference additional studies for food sources and amounts

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
- Example study: {{"title": "Cinnamon Supplementation Improves Insulin Sensitivity in Women with PCOS", "authors": ["Lee J", "Kim S", "Park M"], "journal": "Diabetes Research", "publicationYear": 2023, "participantCount": 130, "results": "Improved insulin sensitivity by 25% and reduced fasting glucose"}}

TAGGING REQUIREMENTS:
- Each recommendation must include tags for related conditions, symptoms, and hormones
- Use ONLY the exact standardized terms listed below:

CONDITIONS/DISEASES (use exact terms):
- PCOD, PCOS, Endometriosis, Dysmenorrhea, Amenorrhea, Cushing's syndrome, Menorrhagia, Metrorrhagia, PMS, Diabetes, PMDD, Perimenopause, Menopause, Postmenopausal

HORMONES/BIOMARKERS (use exact terms):
- androgens, progesterone, estrogen, thyroid, insulin, cortisol, FSH, LH, PROLACTIN, Hunger hormone (Ghrelin)

TARGET SYMPTOMS (use exact terms):
- irregular periods, painful periods, light periods, spotting, heavy periods, bloating, hot flashes (during the day), nausea, difficulty losing weight, stubborn belly fat, weight gain, menstrual headaches, hirsutism, thinning of hair/ hairloss, adult acne, mood swings, stress, fatigue, night sweats

- conditions: Array of related medical conditions (e.g., ["PCOS", "Endometriosis"])
- symptoms: Array of related symptoms (e.g., ["irregular periods", "weight gain", "adult acne"])
- hormones: Array of related hormones (e.g., ["insulin", "androgens", "cortisol"])

TITLE AND PURPOSE FORMAT:
- title: 1-2 words describing the specific method/technique (e.g., "Cinnamon Supplementation", "Yoga Practice", "Meditation")
- purpose: Natural, descriptive sentence explaining what the recommendation does and its benefits (e.g., "Cinnamon helps improve insulin sensitivity and reduce blood sugar levels", "Yoga practice helps reduce stress and balance cortisol levels", "Meditation helps calm the mind and reduce anxiety")

Output format: Return a JSON array of recommendation cards. Each card must include: title (1-2 words), purpose (target benefit), specificAction (with exact amounts/duration), frequency, intensity, expectedTimeline, priority (high/medium/low), contraindications (array), conditions (array of related conditions), symptoms (array of related symptoms), hormones (array of related hormones), and researchBacking object with: summary (string) and studies (array of objects with: title, authors (array), journal, publicationYear, participantCount, results). 

Additionally, include the following separated fields based on category:

FOOD recommendations: 
- food_amounts: Array of exact amounts (e.g., ["150g", "100g", "2 tablespoons"])
- food_items: Array of food items (e.g., ["oats", "lentils", "flaxseed"])
- frequency_detail, duration_weeks
- optimal_times: ONLY include if research specifically mentions timing. If included, use ONLY the single best time from research (e.g., ["morning"] or ["afternoon"] or ["night"]). If research doesn't mention timing, omit this field entirely.

MOVEMENT recommendations: 
- exercise_durations: Array of durations (e.g., ["30 minutes", "45 minutes"])
- exercise_types: Array of exercise types (e.g., ["yoga", "walking"])
- exercise_intensities: Array of intensities (e.g., ["moderate", "low"])
- frequency_detail, duration_weeks
- optimal_times: ONLY include if research specifically mentions timing. If included, use ONLY the single best time from research (e.g., ["morning"] or ["afternoon"] or ["night"]). If research doesn't mention timing, omit this field entirely.

MINDFULNESS recommendations: 
- mindfulness_durations: Array of durations (e.g., ["15 minutes", "20 minutes"])
- mindfulness_techniques: Array of techniques (e.g., ["meditation", "deep breathing"])
- frequency_detail, duration_weeks
- optimal_times: ONLY include if research specifically mentions timing. If included, use ONLY the single best time from research (e.g., ["morning"] or ["afternoon"] or ["night"]). If research doesn't mention timing, omit this field entirely.

Generate as many relevant cards as possible.

Example structure: [{{"title": "Cinnamon Supplementation", "purpose": "Cinnamon helps improve insulin sensitivity and reduce blood sugar levels", "specificAction": "Take 1.5g of cinnamon powder daily for 12 weeks", "frequency": "Daily", "intensity": "Moderate", "expectedTimeline": "12 weeks", "priority": "high", "contraindications": ["Not recommended during pregnancy"], "conditions": ["PCOS"], "symptoms": ["weight gain"], "hormones": ["insulin"], "food_amounts": ["1.5g"], "food_items": ["cinnamon powder"], "optimal_times": ["morning"], "frequency_detail": "daily:1", "duration_weeks": 12, "researchBacking": {{"summary": "Based on 2023 study with 130 women showing Improved insulin sensitivity by 25% and reduced fasting glucose", "studies": [{{"title": "Cinnamon Supplementation Improves Insulin Sensitivity in Women with PCOS", "authors": ["Lee J", "Kim S", "Park M"], "journal": "Diabetes Research", "publicationYear": 2023, "participantCount": 130, "results": "Improved insulin sensitivity by 25% and reduced fasting glucose"}}]}}}}]

Note: In the example above, "optimal_times": ["morning"] is included because the research specifically mentioned morning timing. If research doesn't mention timing, omit the "optimal_times" field entirely.

CONFIDENCE ASSESSMENT:
- If you are highly confident in your recommendations (based on strong research evidence), include "confidence: 90" in your response
- If you are moderately confident (some research support but limited), include "confidence: 70" in your response  
- If you are less confident (limited research or extrapolation), include "confidence: 50" in your response
- If you cannot provide evidence-based recommendations, include "confidence: 30" and explain why
- Always base confidence on the quality and relevance of available research for this specific user profile
'''
        return prompt

    @staticmethod
    def create_rag_enhanced_prompt(user_profile: UserProfile, category: str, research_texts: List[str]) -> str:
        """
        RAG 검색 결과를 포함한 강화된 프롬프트 생성
        """
        up = user_profile
        user_health_profile = ', '.join(filter(None, [
            f"Age: {up.age}" if up.age else None,
            f"Ethnicity: {up.ethnicity}" if up.ethnicity else None,
            f"Cycle phase: {up.cyclePhase}" if up.cyclePhase and up.cyclePhase != 'unknown' else None,
            f"Birth control: {up.birthControlStatus}" if up.birthControlStatus else None,
            f"Diagnosis: {', '.join(up.conditions)}" if up.conditions else None,
            f"Symptoms: {', '.join(up.symptoms)}" if up.symptoms else None
        ]))
        secondary_imbalances_text = f", Secondary: {', '.join(up.secondaryImbalances)}" if up.secondaryImbalances else ''
        
        # 카테고리별 특화 지시사항
        category_instructions = {
            "food": "Focus on dietary interventions, nutrition, and food-based treatments. Base recommendations on specific foods, nutrients, and dietary patterns.",
            "movement": "Focus on exercise interventions, physical activity, and movement-based treatments. Base recommendations on specific exercise types, duration, and intensity.",
            "mindfulness": "Focus on stress reduction, meditation, and mindfulness-based treatments. Base recommendations on specific techniques, duration, and frequency."
        }
        
        # 연구 텍스트 결합
        research_context = "\n\n".join([f"Research Text {i+1}:\n{text}" for i, text in enumerate(research_texts)])
        
        prompt = f'''
You are a medical AI assistant specializing in women's hormone health. Your task is to generate HIGHLY SPECIFIC, SCIENTIFICALLY-BASED recommendations with exact amounts, durations, and frequencies based on the provided research texts.

Category: {category}
Root cause (hormones out of balance): {up.primaryImbalance}{secondary_imbalances_text}
User health profile: {user_health_profile}

CATEGORY-SPECIFIC FOCUS:
{category_instructions.get(category, "")}

RELEVANT RESEARCH CONTEXTS:
The following research texts are specifically relevant to your profile and the {category} category:

{research_context}

CRITICAL REQUIREMENTS:
- Base your recommendations PRIMARILY on the research texts provided above
- Reference specific studies and findings from the provided research
- Ensure recommendations match the intervention types and outcomes shown in the research
- Focus on {category}-specific interventions and outcomes
- If research texts don't provide enough information for {category} recommendations, you may supplement with general knowledge
- ALL recommendations must be actionable with specific amounts, durations, and frequencies

CRITICAL REQUIREMENTS FOR SPECIFIC ACTIONS:
- FOOD: Specify exact amounts (grams, cups, servings) and frequency. Example: "Consume 2 tablespoons of ground flaxseed daily for 12 weeks" or "Eat 100g of salmon 3 times per week for 8 weeks"
- MOVEMENT: Specify exact duration, intensity, and frequency. Example: "Perform 30-minute moderate-intensity yoga sessions 4 times per week for 12 weeks" or "Walk briskly for 45 minutes daily for 8 weeks"
- MINDFULNESS: Specify exact duration, technique, and frequency. Example: "Practice 15-minute daily meditation for 12 weeks" or "Perform 20-minute deep breathing exercises twice daily for 8 weeks"
- ALL recommendations must include: exact duration (weeks only, as number), frequency (daily/weekly), and specific amounts/times

RESEARCH BACKING FORMAT:
- Summary: "Based on [YEAR] study with [NUMBER] women showing [SPECIFIC RESULTS]"
- Example: "Based on 2023 study with 130 women showing Improved insulin sensitivity by 25% and reduced fasting glucose"
- Studies must include: title, authors (array), journal, publicationYear, participantCount, results
- Example study: {{"title": "Cinnamon Supplementation Improves Insulin Sensitivity in Women with PCOS", "authors": ["Lee J", "Kim S", "Park M"], "journal": "Diabetes Research", "publicationYear": 2023, "participantCount": 130, "results": "Improved insulin sensitivity by 25% and reduced fasting glucose"}}

Output format: Return a JSON array of recommendation cards. Each card must include: title, specificAction (with exact amounts/duration), frequency, intensity, expectedTimeline, priority (high/medium/low), contraindications (array), and researchBacking object with: summary (string) and studies (array of objects with: title, authors (array), journal, publicationYear, participantCount, results). 

Additionally, include the following separated fields based on category:

FOOD recommendations: food_amount, food_item, frequency_detail, duration_weeks
MOVEMENT recommendations: exercise_duration, exercise_type, exercise_intensity, frequency_detail, duration_weeks  
MINDFULNESS recommendations: mindfulness_duration, mindfulness_technique, frequency_detail, duration_weeks

Generate as many relevant cards as possible.

Example structure: [{{"title": "Cinnamon Supplementation for Insulin Sensitivity", "specificAction": "Take 1.5g of cinnamon powder daily for 12 weeks", "frequency": "Daily", "intensity": "Moderate", "expectedTimeline": "12 weeks", "priority": "high", "contraindications": ["Not recommended during pregnancy"], "food_amount": "1.5g", "food_item": "cinnamon powder", "frequency_detail": "daily", "duration_weeks": 12, "researchBacking": {{"summary": "Based on 2023 study with 130 women showing Improved insulin sensitivity by 25% and reduced fasting glucose", "studies": [{{"title": "Cinnamon Supplementation Improves Insulin Sensitivity in Women with PCOS", "authors": ["Lee J", "Kim S", "Park M"], "journal": "Diabetes Research", "publicationYear": 2023, "participantCount": 130, "results": "Improved insulin sensitivity by 25% and reduced fasting glucose"}}]}}}}]

CONFIDENCE ASSESSMENT:
- If you are highly confident in your recommendations (based on strong research evidence), include "confidence: 90" in your response
- If you are moderately confident (some research support but limited), include "confidence: 70" in your response  
- If you are less confident (limited research or extrapolation), include "confidence: 50" in your response
- If you cannot provide evidence-based recommendations, include "confidence: 30" and explain why
- Always base confidence on the quality and relevance of available research for this specific user profile
'''
        return prompt

    @staticmethod
    async def generate_rag_recommendations(user_profile: UserProfile) -> Dict[str, List]:
        """
        RAG 기반 추천 생성
        """
        from app.services.rag_service import RAGService
        
        categories = ["food", "movement", "mindfulness"]
        results = {}
        
        for category in categories:
            try:
                # 1. RAG 검색으로 관련 연구 찾기
                search_results = await AIService.search_relevant_research_by_category(user_profile, category)
                
                # 2. 연구 텍스트 추출 (원문)
                research_texts = AIService.extract_research_texts(search_results)
                
                # 3. 강화된 프롬프트 생성
                enhanced_prompt = AIService.create_rag_enhanced_prompt(user_profile, category, research_texts)
                
                # 4. LLM 호출
                llm_response, actual_model = await AIService.call_ai_model(enhanced_prompt)
                
                # 5. 결과 파싱
                recommendations = AIService.parse_recommendations_from_llm(llm_response)
                
                results[category] = recommendations
                
            except Exception as e:
                print(f"RAG recommendation generation failed for {category}: {e}")
                results[category] = []
        
        return results

    @staticmethod
    async def search_relevant_research_by_category(user_profile: UserProfile, category: str) -> List[Dict]:
        """
        카테고리별로 관련 연구 검색
        """
        from app.services.rag_service import RAGService
        
        # 검색 쿼리 생성
        query = AIService.create_category_search_query(user_profile, category)
        
        # 카테고리별 필터 생성
        filter_conditions = AIService.create_category_filter(category)
        
        # Pinecone 검색
        search_results = await RAGService.search_and_rank_papers(
            query=query,
            user_profile=user_profile.dict(),
            top_k=10,
            filter=filter_conditions
        )
        
        return search_results

    @staticmethod
    def create_category_search_query(user_profile: UserProfile, category: str) -> str:
        """
        카테고리별 검색 쿼리 생성
        """
        query_parts = []
        
        # 1. 기본 PCOS 키워드
        query_parts.append("PCOS polycystic ovary syndrome")
        
        # 2. 카테고리별 정확한 키워드
        if category == "food":
            query_parts.append("diet nutrition food meal dietary intervention")
        elif category == "movement":
            query_parts.append("exercise workout training physical activity movement intervention")
        elif category == "mindfulness":
            query_parts.append("mindfulness meditation stress relaxation mental health intervention")
        
        # 3. 사용자 호르몬 불균형
        if user_profile.primaryImbalance:
            query_parts.append(user_profile.primaryImbalance)
        
        # 4. 사용자 증상 (카테고리와 관련된 것만)
        if user_profile.symptoms:
            relevant_symptoms = AIService.filter_symptoms_by_category(user_profile.symptoms, category)
            query_parts.extend(relevant_symptoms)
        
        return " ".join(query_parts)

    @staticmethod
    def create_category_filter(category: str) -> Dict:
        """
        카테고리별 필터 생성
        """
        if category == "food":
            return {"intervention_type": {"$in": ["food"]}}
        elif category == "movement":
            return {"intervention_type": {"$in": ["movement"]}}
        elif category == "mindfulness":
            return {"intervention_type": {"$in": ["mindfulness"]}}
        else:
            return {}

    @staticmethod
    def filter_symptoms_by_category(symptoms: List[str], category: str) -> List[str]:
        """
        사용자가 입력한 증상을 그대로 사용 (카테고리 분류 없음)
        """
        # 사용자가 입력한 증상을 그대로 반환
        # 각 증상마다 food, movement, mindfulness 해결법이 모두 존재할 수 있음
        return symptoms

    @staticmethod
    def extract_research_texts(search_results: List[Dict]) -> List[str]:
        """
        검색 결과에서 연구 텍스트 추출
        """
        research_texts = []
        
        for result in search_results:
            # 원문 텍스트 추출
            text = result.get("content", "")
            title = result.get("title", "")
            study_arms_text = result.get("study_arms_text", "")
            section_type = result.get("section_type", "")
            chunk_summary = result.get("chunk_summary", "")
            
            if text and title:
                # 제목과 내용을 결합
                research_text = f"Title: {title}\n\nContent: {text[:2000]}"  # 2000자로 제한
                
                # study_arms 정보 추가
                if study_arms_text:
                    research_text += f"\n\nStudy Arms: {study_arms_text}"
                
                # 섹션 정보 추가
                if section_type:
                    research_text += f"\n\nSection Type: {section_type}"
                
                if chunk_summary:
                    research_text += f"\n\nChunk Summary: {chunk_summary}"
                
                research_texts.append(research_text)
        
        return research_texts[:5]  # 상위 5개 연구만 사용

    @staticmethod
    def parse_frequency_detail(frequency_detail: str) -> dict:
        """
        구조화된 frequency_detail을 스케줄링 가능한 형태로 파싱
        Format: "type:times" (e.g., "daily:1", "weekly:3", "monthly:1")
        """
        if not frequency_detail:
            return {"type": "unknown", "times": 0, "description": "No frequency specified"}
        
        try:
            if ":" not in frequency_detail:
                # 기존 형식 호환성을 위한 fallback
                return {"type": "custom", "times": 0, "description": frequency_detail}
            
            freq_type, times_str = frequency_detail.split(":", 1)
            times = int(times_str)
            
            return {
                "type": freq_type.lower(),  # "daily", "weekly", "monthly"
                "times": times,             # 1, 2, 3, etc.
                "description": frequency_detail
            }
            
        except (ValueError, AttributeError) as e:
            logger.warning(f"frequency_detail 파싱 실패: {frequency_detail}, 오류: {str(e)}")
            return {"type": "custom", "times": 0, "description": frequency_detail}

    @staticmethod
    async def generate_session_recommendations(user_profile: UserProfile, category: str) -> List[Dict[str, Any]]:
        """
        세션용 추천 생성 (백그라운드 처리용)
        기존 일반 추천 생성 로직과 동일하지만 세션 플로우에 최적화
        """
        try:
            # 프롬프트 생성
            prompt = AIService.suggest_llm_prompt_for_recommendations(user_profile, category)
            logger.info(f"세션 추천 프롬프트 생성 완료: category={category}")
            
            # OpenAI API 호출
            llm_response, actual_model = await AIService.call_ai_model(prompt)
            logger.info(f"AI 모델 호출 완료: category={category}, model={actual_model}, response_length={len(llm_response) if llm_response else 0}")
            
            # 신뢰도 평가
            confidence = AIService.evaluate_llm_confidence(llm_response)
            logger.info(f"신뢰도 평가 완료: category={category}, confidence={confidence}")
            
            # 응답 파싱
            recommendations = AIService.parse_recommendations_from_llm(llm_response)
            logger.info(f"응답 파싱 완료: category={category}, recommendations_count={len(recommendations) if recommendations else 0}")
            logger.info(f"AI 응답 내용 (처음 200자): {llm_response[:200] if llm_response else 'None'}")
            
            # Fallback: 신뢰도 낮거나 추천 없음 → Fallback 모델 사용
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
                    logger.error(f"지원하지 않는 fallback 모델 프로바이더: {fallback_config['provider']}")
                    return recommendations
                
                fallback_confidence = AIService.evaluate_llm_confidence(fallback_response)
                fallback_recommendations = AIService.parse_recommendations_from_llm(fallback_response)
                if fallback_recommendations and fallback_confidence > confidence:
                    recommendations = fallback_recommendations
            
            return recommendations if recommendations else []
            
        except Exception as e:
            logger.error(f"세션 추천 생성 실패 (category={category}): {str(e)}")
            return [] 