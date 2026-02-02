"""
AUVRA Action Plan Evaluation Service

Evaluates action plan quality and stores metrics for trend monitoring.
Runs asynchronously after action plan delivery to avoid blocking UX.

Metrics tracked:
- structure_valid: Pydantic validation passed (Boolean)
- personalization_score: Actions tailored to user conditions (0-100)
- condition_appropriateness: Safe for diagnosed conditions (0-100)
- feedback_alignment_score: Respects prior likes/dislikes (0-100)
- preference_compliance_score: Respects diet, allergies, cuisine preferences (0-100)
- citation_validity_score: Research PMIDs are valid (0-100)
- citation_relevance_score: Findings match recommendations (0-100)
- overall_quality_score: Weighted average (0-100)
"""

import os
import json
import logging
import time
import httpx
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)

# Groq fallback configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_FALLBACK_MODEL = "llama-3.3-70b-versatile"  # Higher rate limits (30K TPM vs 8K)

# Weights for overall score calculation
METRIC_WEIGHTS = {
    "structure_valid": 0.15,  # 15% - structural validity
    "personalization_score": 0.15,  # 15% - user personalization
    "condition_appropriateness": 0.15,  # 15% - safety for conditions
    "feedback_alignment_score": 0.15,  # 15% - respects feedback
    "preference_compliance_score": 0.15,  # 15% - respects diet/allergy/cuisine preferences
    "citation_validity_score": 0.125,  # 12.5% - valid PMIDs
    "citation_relevance_score": 0.125,  # 12.5% - citations support claims
}

# LLM Evaluation Prompt
EVALUATION_PROMPT = """You are an expert evaluator for health recommendation quality.
Evaluate the following action plan generated for a user.

═══════════════════════════════════════════════════════════════════════════════
USER PROFILE
═══════════════════════════════════════════════════════════════════════════════
- Age: {age}
- Cycle Day: {cycle_day}
- Cycle Phase: {cycle_phase}
- Primary Hormone: {primary_hormone}

HEALTH CONCERNS:
- Top Concern: {top_concern}
- Diagnosed Conditions: {diagnosed_conditions}
- Period Concerns: {period_concerns}
- Body Concerns: {body_concerns}
- Skin/Hair Concerns: {skin_hair_concerns}
- Mental Health Concerns: {mental_health_concerns}
- Family History: {family_history}

PERSONALIZATION FACTORS:
- Lifestyle Focus: {lifestyle_focus}
- Diet Preference: {diet_preference}
- Food Allergies: {food_allergies}
- Stress Level: {stress_level}
- Sleep Duration: {sleep_duration}
- Workout Intensity: {workout_intensity}
- Birth Control: {birth_control}

═══════════════════════════════════════════════════════════════════════════════
FEEDBACK MEMORY
═══════════════════════════════════════════════════════════════════════════════
HISTORICAL SUMMARY (learned patterns):
{feedback_summary}

RECENT FEEDBACK:
{feedback_history}

═══════════════════════════════════════════════════════════════════════════════
CHATBOT CONVERSATION CONTEXT
═══════════════════════════════════════════════════════════════════════════════
{chatbot_context}


═══════════════════════════════════════════════════════════════════════════════
GENERATED ACTION PLAN (4 actions)
═══════════════════════════════════════════════════════════════════════════════
{actions_json}

═══════════════════════════════════════════════════════════════════════════════
EVALUATION CRITERIA
═══════════════════════════════════════════════════════════════════════════════
Score each metric from 0-100:

1. **personalization_score**: Do the actions directly address the user's specific conditions, concerns, and preferences? 
   - 90-100: Excellent - every action is highly tailored
   - 70-89: Good - most actions are relevant
   - 50-69: Average - some generic recommendations
   - 0-49: Poor - mostly generic, not personalized

2. **condition_appropriateness**: Are the recommendations safe for the user's diagnosed conditions?
   - Check for contraindications (e.g., no high-intensity for thyroid issues, no dairy for PCOS if sensitive)
   - 90-100: All actions are safe and appropriate
   - 70-89: Minor concerns but generally safe
   - 50-69: Some potentially problematic recommendations
   - 0-49: Contains contraindicated recommendations

3. **feedback_alignment_score**: Does the plan avoid previously disliked patterns and repeat liked ones?
   - 90-100: Excellent alignment with feedback
   - 70-89: Good effort to respect preferences
   - 50-69: Partially considers feedback
   - 0-49: Ignores or contradicts feedback

4. **preference_compliance_score**: Does the plan respect user's preference settings?
   - Diet preference (vegetarian/vegan/pescatarian/none)
   - Food allergies (nuts, gluten, dairy, etc.)
   - Cuisine preferences (Mediterranean, Asian, Mexican, etc.)
   - 90-100: All actions comply with preferences
   - 70-89: Most actions comply, minor issues
   - 50-69: Some actions violate preferences
   - 0-49: Major preference violations

5. **citation_relevance_score**: Do the research citations actually support the specific recommendations?
   - Check if the study findings match the claimed benefits
   - 90-100: All citations directly support claims
   - 70-89: Most citations are relevant
   - 50-69: Some citations are tangentially related
   - 0-49: Citations don't match recommendations

Respond with ONLY valid JSON in this exact format:
{{
  "personalization_score": <0-100>,
  "condition_appropriateness": <0-100>,
  "feedback_alignment_score": <0-100>,
  "preference_compliance_score": <0-100>,
  "citation_relevance_score": <0-100>,
  "reasoning": {{
    "personalization": "<brief explanation>",
    "condition": "<brief explanation>",
    "feedback": "<brief explanation>",
    "preference": "<brief explanation>",
    "citation": "<brief explanation>"
  }}
}}"""


class ActionPlanEvaluator:
    """
    Evaluates action plan quality and stores metrics for trend monitoring.
    Called asynchronously after action plan delivery to avoid blocking UX.
    """
    
    GPT_MODEL = "gpt-4o-mini"
    GPT_TEMPERATURE = 0.1  # Low temp for consistent evaluation
    
    # GPT-4o-mini pricing (per 1M tokens)
    INPUT_COST_PER_1M = 0.15
    OUTPUT_COST_PER_1M = 0.60
    
    def __init__(self):
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.client = httpx.AsyncClient(timeout=60.0)
        logger.info("ActionPlanEvaluator initialized")
    
    async def calculate_scores(
        self,
        actions: List[Dict[str, Any]],
        user_context: Dict[str, Any],
        feedback_history: str
    ) -> Tuple[Dict[str, Any], float, int]:
        """
        Calculate evaluation scores without storing to database.
        Returns (scores_dict, cost, citation_validity_score).
        """
        # Step 1: Check citation validity (no LLM needed)
        citation_validity = self._evaluate_citation_validity(actions)
        
        # Step 2: Run LLM evaluation for relevance metrics
        llm_scores, llm_cost = await self._run_llm_evaluation(
            actions, user_context, feedback_history
        )
        
        if llm_scores is None:
            llm_scores = {
                "personalization_score": None,
                "condition_appropriateness": None,
                "feedback_alignment_score": None,
                "preference_compliance_score": None,
                "citation_relevance_score": None,
                "reasoning": {}
            }
            
        return llm_scores, llm_cost, citation_validity

    async def evaluate_plan(
        self,
        plan_id: int,
        user_id: Optional[str],
        actions: List[Dict[str, Any]],
        user_context: Dict[str, Any],
        structure_valid: bool,
        db: AsyncSession,
        session_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Run all evaluation metrics and store results.
        
        Args:
            plan_id: The action plan ID
            user_id: The user's UID (None for guests)
            actions: List of generated actions
            user_context: User profile/context data
            structure_valid: Whether Pydantic validation passed
            db: Database session
            session_id: Session ID for guest users
            
        Returns:
            Evaluation results dict or None on error
        """
        from app.core.database import ActionPlanEvaluation, ActionPlanFeedback
        
        # Handle guest users
        effective_uid = user_id
        if not effective_uid:
            if session_id:
                effective_uid = f"guest_{session_id}"
            else:
                effective_uid = "guest_unknown"
        
        start_time = time.time()
        total_cost = 0.0
        
        try:
            logger.info(f"📊 Starting evaluation for plan {plan_id}")
            
            # Step 1: Get recent feedback for context
            if user_id:
                feedback_history = await self._get_recent_feedback(user_id, db)
            else:
                feedback_history = "No previous feedback (Guest User)"
            
            # Step 2: Calculate scores
            llm_scores, llm_cost, citation_validity = await self.calculate_scores(
                actions, user_context, feedback_history
            )
            total_cost += llm_cost
            
            # Step 3: Calculate overall score
            overall_score = self._calculate_overall_score(
                structure_valid=structure_valid,
                personalization_score=llm_scores.get("personalization_score"),
                condition_appropriateness=llm_scores.get("condition_appropriateness"),
                feedback_alignment_score=llm_scores.get("feedback_alignment_score"),
                preference_compliance_score=llm_scores.get("preference_compliance_score"),
                citation_validity_score=citation_validity,
                citation_relevance_score=llm_scores.get("citation_relevance_score")
            )
            
            # Step 4: Store evaluation in database
            evaluation = ActionPlanEvaluation(
                plan_id=plan_id,
                uid=effective_uid,
                structure_valid=structure_valid,
                personalization_score=llm_scores.get("personalization_score"),
                condition_appropriateness=llm_scores.get("condition_appropriateness"),
                feedback_alignment_score=llm_scores.get("feedback_alignment_score"),
                preference_compliance_score=llm_scores.get("preference_compliance_score"),
                citation_validity_score=citation_validity,
                citation_relevance_score=llm_scores.get("citation_relevance_score"),
                overall_quality_score=overall_score,
                evaluation_cost=f"${total_cost:.6f}",
                evaluation_time_ms=int((time.time() - start_time) * 1000),
                evaluator_model=self.GPT_MODEL,
                llm_evaluation_response=llm_scores
            )
            
            db.add(evaluation)
            await db.commit()
            
            logger.info(
                f"✅ Evaluation complete for plan {plan_id}: "
                f"overall={overall_score}, cost=${total_cost:.6f}, "
                f"time={int((time.time() - start_time) * 1000)}ms"
            )
            
            return {
                "plan_id": plan_id,
                "structure_valid": structure_valid,
                "personalization_score": llm_scores.get("personalization_score"),
                "condition_appropriateness": llm_scores.get("condition_appropriateness"),
                "feedback_alignment_score": llm_scores.get("feedback_alignment_score"),
                "preference_compliance_score": llm_scores.get("preference_compliance_score"),
                "citation_validity_score": citation_validity,
                "citation_relevance_score": llm_scores.get("citation_relevance_score"),
                "overall_quality_score": overall_score,
                "cost": total_cost
            }
            
        except Exception as e:
            logger.error(f"❌ Evaluation failed for plan {plan_id}: {e}")
            return None
    
    async def _get_recent_feedback(
        self, 
        user_id: str, 
        db: AsyncSession,
        limit: int = 20
    ) -> str:
        """Get recent user feedback as formatted string for LLM context."""
        from app.core.database import ActionPlanFeedback
        
        try:
            result = await db.execute(
                select(ActionPlanFeedback)
                .where(ActionPlanFeedback.uid == user_id)
                .order_by(ActionPlanFeedback.created_at.desc())
                .limit(limit)
            )
            feedback_list = result.scalars().all()
            
            if not feedback_list:
                return "No previous feedback available."
            
            lines = []
            for fb in feedback_list:
                emoji = "👍" if fb.feedback_type == "liked" else "👎" if fb.feedback_type == "disliked" else "✅"
                reason = f" (reason: {fb.replacement_reason})" if fb.replacement_reason else ""
                lines.append(f"- {emoji} {fb.feedback_type}: {fb.action_title} ({fb.action_category}){reason}")
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.warning(f"Failed to get feedback: {e}")
            return "Unable to retrieve feedback history."
    
    def _evaluate_citation_validity(self, actions: List[Dict[str, Any]]) -> int:
        """
        Check if research PMIDs are valid format.
        Returns 0-100 score based on % of valid citations.
        """
        total_citations = 0
        valid_citations = 0
        
        for action in actions:
            studies = action.get("research_studies", [])
            for study in studies:
                total_citations += 1
                pmid = study.get("pmid", "")
                
                # Valid PMID: numeric string, 1-8 digits
                if pmid and pmid.isdigit() and 1 <= len(pmid) <= 8:
                    valid_citations += 1
        
        if total_citations == 0:
            return 0  # No citations = 0 points
        
        return int((valid_citations / total_citations) * 100)
    
    async def _run_llm_evaluation(
        self,
        actions: List[Dict[str, Any]],
        user_context: Dict[str, Any],
        feedback_history: str
    ) -> tuple[Optional[Dict], float]:
        """
        Run GPT-4o-mini to evaluate relevance metrics.
        Returns (scores_dict, cost) or (None, cost) on failure.
        """
        if not self.openai_api_key and not GROQ_API_KEY:
            logger.warning("No API keys set (OpenAI or Groq), skipping LLM evaluation")
            return None, 0.0
        
        # Format actions for evaluation (compact)
        actions_summary = []
        for i, action in enumerate(actions, 1):
            studies_summary = []
            for s in action.get("research_studies", []):
                studies_summary.append({
                    "title": s.get("title", "")[:100],
                    "finding": s.get("finding", "")[:200],
                    "pmid": s.get("pmid", "")
                })
            
            actions_summary.append({
                "num": i,
                "title": action.get("title"),
                "category": action.get("category"),
                "target_hormone": action.get("target_hormone"),
                "specific_action": action.get("specific_action", "")[:300],
                "purpose": action.get("purpose", "")[:200],
                "food_items": action.get("food_items", []),
                "exercise_types": action.get("exercise_types", []),
                "mindfulness_techniques": action.get("mindfulness_techniques", []),
                "research": studies_summary
            })
        
        prompt = EVALUATION_PROMPT.format(
            age=user_context.get("age", "Unknown"),
            cycle_day=user_context.get("cycle_day", "Unknown"),
            cycle_phase=user_context.get("cycle_phase", "Unknown"),
            primary_hormone=user_context.get("primary_hormone", "Unknown"),
            top_concern=user_context.get("top_concern", "None specified"),
            diagnosed_conditions=user_context.get("diagnosed_conditions", []),
            period_concerns=user_context.get("period_concerns", "none"),
            body_concerns=user_context.get("body_concerns", "none"),
            skin_hair_concerns=user_context.get("skin_hair_concerns", "none"),
            mental_health_concerns=user_context.get("mental_health_concerns", "none"),
            family_history=user_context.get("family_history", "none specified"),
            lifestyle_focus=user_context.get("lifestyle_focus", ["eat", "move", "pause"]),
            diet_preference=user_context.get("diet_preference", "Not specified"),
            food_allergies=user_context.get("food_allergies", "none"),
            stress_level=user_context.get("stress_level", "moderate"),
            sleep_duration=user_context.get("sleep_duration", "7-8 hours"),
            workout_intensity=user_context.get("workout_intensity", "moderate"),
            birth_control=user_context.get("birth_control", "none"),
            feedback_summary=user_context.get("feedback_summary", "No summary yet"),
            feedback_history=feedback_history,
            chatbot_context=user_context.get("chatbot_context", "No recent chatbot conversations"),
            actions_json=json.dumps(actions_summary, indent=2)
        )
        
        # Try OpenAI first, fallback to Groq
        openai_error = None
        cost = 0.0
        
        # Try OpenAI first
        if self.openai_api_key:
            try:
                response = await self.client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.openai_api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.GPT_MODEL,
                        "temperature": self.GPT_TEMPERATURE,
                        "messages": [
                            {"role": "system", "content": "You are a health recommendation quality evaluator. Respond only with valid JSON."},
                            {"role": "user", "content": prompt}
                        ]
                    }
                )
                
                if response.status_code != 200:
                    openai_error = f"OpenAI API returned {response.status_code}: {response.text[:200]}"
                    logger.warning(f"❌ {openai_error}")
                else:
                    data = response.json()
                    
                    # Calculate cost
                    usage = data.get("usage", {})
                    input_tokens = usage.get("prompt_tokens", 0)
                    output_tokens = usage.get("completion_tokens", 0)
                    cost = (input_tokens * self.INPUT_COST_PER_1M / 1_000_000) + \
                           (output_tokens * self.OUTPUT_COST_PER_1M / 1_000_000)
                    
                    # Parse response
                    content = data["choices"][0]["message"]["content"].strip()
                    
                    # Clean up markdown code blocks if present
                    if content.startswith("```"):
                        content = content.split("```")[1]
                        if content.startswith("json"):
                            content = content[4:]
                    
                    scores = json.loads(content)
                    
                    logger.info(f"✅ LLM evaluation complete via OpenAI: {scores}")
                    return scores, cost
                    
            except Exception as e:
                openai_error = str(e)
                logger.warning(f"❌ OpenAI exception: {openai_error[:200]}")
        else:
            openai_error = "No OpenAI API key"
        
        # Groq fallback
        if openai_error and GROQ_API_KEY:
            try:
                logger.info(f"🔄 Falling back to Groq ({GROQ_FALLBACK_MODEL})")
                
                # gpt-oss-120b is a reasoning model - doesn't support response_format
                is_reasoning_model = "gpt-oss" in GROQ_FALLBACK_MODEL.lower()
                enhanced_prompt = prompt + "\n\nIMPORTANT: Respond with valid JSON only. No markdown, no explanation." if is_reasoning_model else prompt
                
                response = await self.client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": GROQ_FALLBACK_MODEL,
                        "temperature": self.GPT_TEMPERATURE,
                        "messages": [
                            {"role": "system", "content": "You are a health recommendation quality evaluator. Respond only with valid JSON."},
                            {"role": "user", "content": enhanced_prompt}
                        ]
                    },
                    timeout=90.0
                )
                
                if response.status_code != 200:
                    raise Exception(f"Groq API returned {response.status_code}: {response.text[:200]}")
                
                data = response.json()
                
                # Parse response
                content = data["choices"][0]["message"]["content"].strip()
                
                # Clean up markdown code blocks if present
                if content.startswith("```json"):
                    content = content[7:]
                if content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()
                
                scores = json.loads(content)
                
                logger.info(f"✅ LLM evaluation complete via Groq fallback: {scores}")
                return scores, cost
                
            except Exception as e:
                logger.error(f"❌ Groq fallback also failed: {e}")
                return None, 0.0
        else:
            logger.error(f"❌ LLM evaluation failed: {openai_error}")
            return None, 0.0
    
    def _calculate_overall_score(
        self,
        structure_valid: bool,
        personalization_score: Optional[int],
        condition_appropriateness: Optional[int],
        feedback_alignment_score: Optional[int],
        preference_compliance_score: Optional[int],
        citation_validity_score: Optional[int],
        citation_relevance_score: Optional[int]
    ) -> int:
        """
        Calculate weighted average overall score.
        structure_valid is converted to 100 (valid) or 0 (invalid).
        Missing scores are excluded from calculation.
        """
        scores = {
            "structure_valid": 100 if structure_valid else 0,
            "personalization_score": personalization_score,
            "condition_appropriateness": condition_appropriateness,
            "feedback_alignment_score": feedback_alignment_score,
            "preference_compliance_score": preference_compliance_score,
            "citation_validity_score": citation_validity_score,
            "citation_relevance_score": citation_relevance_score,
        }
        
        total_weight = 0.0
        weighted_sum = 0.0
        
        for metric, score in scores.items():
            if score is not None:
                weight = METRIC_WEIGHTS.get(metric, 0)
                weighted_sum += score * weight
                total_weight += weight
        
        if total_weight == 0:
            return 0
        
        return int(weighted_sum / total_weight)


# Singleton instance
_evaluator_instance: Optional[ActionPlanEvaluator] = None

def get_action_plan_evaluator() -> ActionPlanEvaluator:
    """Get or create the singleton evaluator instance."""
    global _evaluator_instance
    if _evaluator_instance is None:
        _evaluator_instance = ActionPlanEvaluator()
    return _evaluator_instance
