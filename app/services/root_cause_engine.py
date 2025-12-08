from typing import Dict, List, Tuple, Optional
import google.generativeai as genai
import json
import os
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

# Load environment variables from .env file
load_dotenv()

# Configure Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ENABLE_LLM_OTHERS = os.getenv("ENABLE_LLM_OTHERS", "false").lower() in ("1", "true", "yes", "on")
LLM_OTHERS_TIMEOUT = int(os.getenv("LLM_OTHERS_TIMEOUT", "30"))  # seconds (increased from 20 → 30 by request)

# Debug logging to verify env vars are loaded
print(f"🔑 GEMINI_API_KEY loaded: {'Yes' if GEMINI_API_KEY else 'No'}")
print(f"🔑 ENABLE_LLM_OTHERS: {ENABLE_LLM_OTHERS}")


class RootCauseEngine:
    """
    Hormone imbalance root cause analysis engine
    Evidence-based clinical scoring system with LLM integration
    """
    
    @staticmethod
    def process_others_with_llm(symptom_others: Optional[str], family_others: Optional[str]) -> Dict[str, int]:
        """
        Process free-text "Others" input using Gemini API
        
        Args:
            symptom_others: User's free-text symptoms from "Others" field
            family_others: User's free-text family history from "Others" field
            
        Returns:
            Dict with hormone scores (0-3) for all 8 hormones
        """
        # If both are empty, or LLM is disabled/misconfigured, return zeros immediately
        if not symptom_others and not family_others:
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
        # Skip calling the LLM unless explicitly enabled and API key is present
        if not ENABLE_LLM_OTHERS or not GEMINI_API_KEY:
            # Optional: log once to indicate skip in development environments
            print("ℹ️ Skipping LLM processing for 'Others' text (disabled or missing GEMINI_API_KEY)")
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
        
        try:
            import time as _time
            start_ts = _time.time()
            print(f"\n{'='*70}")
            print(f"🤖 LLM PROCESSING STARTED")
            print(f"{'='*70}")
            # Clarified log labels: only free-text "Others" content (not structured symptom selections)
            print(f"📝 Free-text 'Others' symptoms passed to LLM: {symptom_others if symptom_others else 'None'}")
            print(f"👨‍👩‍👧‍👦 Free-text 'Others' family history passed to LLM: {family_others if family_others else 'None'}")
            
            # Configure API key
            genai.configure(api_key=GEMINI_API_KEY)
            print(f"✅ Gemini API configured")
            
            # Initialize Gemini model
            model = genai.GenerativeModel('gemini-2.5-flash')
            print(f"✅ Model initialized: gemini-2.5-flash")
            
            # Build prompt
            symptoms_text = symptom_others if symptom_others else "None"
            family_text = family_others if family_others else "None"
            
            prompt = f"""You are a clinical AI analyzing hormone imbalance symptoms and family history.

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
            
            print(f"🧪 FULL PROMPT SENT TO LLM:\n{prompt}\n--- END PROMPT ---")
            print(f"📤 Sending request to Gemini API...")
            
            # Call Gemini API (no generation_config - causes empty responses)
            # Execute the LLM call with a hard timeout using a separate thread
            def _call_llm():
                return model.generate_content(prompt)

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_call_llm)
                try:
                    response = future.result(timeout=LLM_OTHERS_TIMEOUT)
                except FuturesTimeoutError:
                    elapsed = _time.time() - start_ts
                    print(f"⏱️ LLM timed out after {elapsed:.2f}s (limit {LLM_OTHERS_TIMEOUT}s). Falling back to zeros.")
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
            elapsed = _time.time() - start_ts
            print(f"⏱️ LLM round-trip time (completed before timeout): {elapsed:.2f}s (limit {LLM_OTHERS_TIMEOUT}s)")
            
            print(f"📥 Received response from Gemini API")
            
            # Check if response was generated
            if not response.parts:
                raise ValueError("Empty response from LLM")
            
            # Parse JSON response
            response_text = response.text.strip()
            print("� RAW LLM RESPONSE (UNALTERED):")
            print(response.text)
            print("--- END RAW RESPONSE ---")
            print("📄 CLEANED RESPONSE FOR PARSING:")
            print(response_text)
            print("--- END CLEANED RESPONSE ---")
            
            # Clean up markdown formatting
            # Method 1: Remove ```json ... ``` blocks
            if "```json" in response_text:
                # Extract content between ```json and ```
                start_marker = "```json"
                end_marker = "```"
                start_idx = response_text.find(start_marker) + len(start_marker)
                end_idx = response_text.find(end_marker, start_idx)
                if end_idx != -1:
                    response_text = response_text[start_idx:end_idx].strip()
            # Method 2: Remove generic ``` blocks
            elif response_text.startswith("```") and response_text.endswith("```"):
                lines = response_text.split('\n')
                # Remove first and last line (the ``` markers)
                response_text = '\n'.join(lines[1:-1]).strip()
            
            # Try to parse JSON
            scores = json.loads(response_text)
            print(f"✅ Parsed JSON scores: {scores}")
            
            # Validate scores are in 0-3 range
            for hormone, score in scores.items():
                if not isinstance(score, int) or score < 0 or score > 3:
                    scores[hormone] = 0  # Reset invalid scores
            
            print(f"🎯 Final validated LLM scores: {scores}")
            return scores
            
        except Exception as e:
            print(f"❌ LLM error: {e}")
            # Fallback: Return zeros if API fails
            print(f"⚠️ LLM processing failed: {str(e)}")
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
            print(f"🔍 Extracted Others text:")
            print(f"   Symptom sources: {symptom_others_texts}")
            print(f"   Family sources: {family_others_texts}")
        
        # Call LLM if we have any "Others" text
        if symptom_others or family_others:
            llm_scores = RootCauseEngine.process_others_with_llm(symptom_others, family_others)
            # Add LLM scores to totals
            for hormone, score in llm_scores.items():
                scores[hormone] += score
        
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
                print(f"📌 Top concern is custom 'Others:' text: {top_concern}")
                # Note: LLM scores were already added in section 10

        
        # 12. IDENTIFY PRIMARY & SECONDARY
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
        
        # Secondary: scores >= 50% of primary AND >= 3
        threshold = primary_score * 0.5
        secondary_imbalances = []
        secondary_levels = []
        
        for hormone_key, score in sorted_scores[1:]:
            if score >= threshold and score >= 3:
                if "_" in hormone_key:
                    parts = hormone_key.rsplit("_", 1)
                    h_name = parts[0]
                    h_level = parts[1]
                else:
                    h_name = hormone_key
                    h_level = "unknown"
                
                secondary_imbalances.append(h_name)
                secondary_levels.append(h_level)
        
        print(f"🧬 Hormone Analysis Complete:")
        print(f"   Primary: {primary_hormone} ({primary_level}) - Score: {primary_score}")
        print(f"   Secondary: {list(zip(secondary_imbalances, secondary_levels))}")
        print(f"   All Scores: {sorted_scores[:5]}")  # Top 5 scores
        
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
