import os
import httpx
import json
from typing import Any, Dict, List, Optional
from app.models.ai_models import UserProfile

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

class AIService:
    @staticmethod
    async def call_openai(prompt: str) -> str:
        if not prompt:
            return ''
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {OPENAI_API_KEY}'
        }
        body = {
            'model': 'gpt-4o',
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.7,
            'max_tokens': 1800
        }
        
        # 재시도 로직 추가
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:  # 타임아웃 60초로 증가
                    response = await client.post('https://api.openai.com/v1/chat/completions', headers=headers, json=body)
                    if response.status_code != 200:
                        if attempt < max_retries - 1:
                            import asyncio
                            await asyncio.sleep(2 ** attempt)  # 지수 백오프
                            continue
                        return ''
                    data = response.json()
                    return data.get('choices', [{}])[0].get('message', {}).get('content', '')
            except Exception as e:
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)  # 지수 백오프
                    continue
                print(f"OpenAI API 호출 실패: {e}")
                return ''

    @staticmethod
    async def call_groq(prompt: str) -> str:
        if not prompt:
            return ''
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {GROQ_API_KEY}'
        }
        body = {
            'model': 'llama-3.3-70b-versatile',
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
    def parse_recommendations_from_llm(llm_response: str) -> List[Dict[str, Any]]:
        try:
            import re
            match = re.search(r'\[.*\]', llm_response, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                # 기본값 보장
                for rec in parsed:
                    rec.setdefault('researchBacking', {'summary': 'Based on current research', 'studies': []})
                    rec.setdefault('contraindications', [])
                    rec.setdefault('frequency', 'Daily')
                    rec.setdefault('expectedTimeline', '4-6 weeks')
                    rec.setdefault('priority', 'medium')
                return parsed
            return []
        except Exception:
            return []

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
- ALL recommendations must include: exact duration (weeks/months), frequency (daily/weekly), and specific amounts/times
- Base ALL recommendations on actual research studies from the last 10 years
- If research mentions specific supplements/nutrients, you may reference additional studies for food sources and amounts

RESEARCH BACKING FORMAT:
- Summary: "Based on [YEAR] study with [NUMBER] women showing [SPECIFIC RESULTS]"
- Example: "Based on 2023 study with 130 women showing Improved insulin sensitivity by 25% and reduced fasting glucose"
- Studies must include: title, authors (array), journal, publicationYear, participantCount, results
- Example study: {{"title": "Cinnamon Supplementation Improves Insulin Sensitivity in Women with PCOS", "authors": ["Lee J", "Kim S", "Park M"], "journal": "Diabetes Research", "publicationYear": 2023, "participantCount": 130, "results": "Improved insulin sensitivity by 25% and reduced fasting glucose"}}

Output format: Return a JSON array of recommendation cards. Each card must include: title, specificAction (with exact amounts/duration), frequency, intensity, expectedTimeline, priority (high/medium/low), contraindications (array), and researchBacking object with: summary (string) and studies (array of objects with: title, authors (array), journal, publicationYear, participantCount, results). Generate as many relevant cards as possible.

Example structure: [{{"title": "Cinnamon Supplementation for Insulin Sensitivity", "specificAction": "Take 1.5g of cinnamon powder daily for 12 weeks", "frequency": "Daily", "intensity": "Moderate", "expectedTimeline": "12 weeks", "priority": "high", "contraindications": ["Not recommended during pregnancy"], "researchBacking": {{"summary": "Based on 2023 study with 130 women showing Improved insulin sensitivity by 25% and reduced fasting glucose", "studies": [{{"title": "Cinnamon Supplementation Improves Insulin Sensitivity in Women with PCOS", "authors": ["Lee J", "Kim S", "Park M"], "journal": "Diabetes Research", "publicationYear": 2023, "participantCount": 130, "results": "Improved insulin sensitivity by 25% and reduced fasting glucose"}}]}}}}]

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
- ALL recommendations must include: exact duration (weeks/months), frequency (daily/weekly), and specific amounts/times

RESEARCH BACKING FORMAT:
- Summary: "Based on [YEAR] study with [NUMBER] women showing [SPECIFIC RESULTS]"
- Example: "Based on 2023 study with 130 women showing Improved insulin sensitivity by 25% and reduced fasting glucose"
- Studies must include: title, authors (array), journal, publicationYear, participantCount, results
- Example study: {{"title": "Cinnamon Supplementation Improves Insulin Sensitivity in Women with PCOS", "authors": ["Lee J", "Kim S", "Park M"], "journal": "Diabetes Research", "publicationYear": 2023, "participantCount": 130, "results": "Improved insulin sensitivity by 25% and reduced fasting glucose"}}

Output format: Return a JSON array of recommendation cards. Each card must include: title, specificAction (with exact amounts/duration), frequency, intensity, expectedTimeline, priority (high/medium/low), contraindications (array), and researchBacking object with: summary (string) and studies (array of objects with: title, authors (array), journal, publicationYear, participantCount, results). Generate as many relevant cards as possible.

Example structure: [{{"title": "Cinnamon Supplementation for Insulin Sensitivity", "specificAction": "Take 1.5g of cinnamon powder daily for 12 weeks", "frequency": "Daily", "intensity": "Moderate", "expectedTimeline": "12 weeks", "priority": "high", "contraindications": ["Not recommended during pregnancy"], "researchBacking": {{"summary": "Based on 2023 study with 130 women showing Improved insulin sensitivity by 25% and reduced fasting glucose", "studies": [{{"title": "Cinnamon Supplementation Improves Insulin Sensitivity in Women with PCOS", "authors": ["Lee J", "Kim S", "Park M"], "journal": "Diabetes Research", "publicationYear": 2023, "participantCount": 130, "results": "Improved insulin sensitivity by 25% and reduced fasting glucose"}}]}}}}]

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
                llm_response = await AIService.call_openai(enhanced_prompt)
                
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