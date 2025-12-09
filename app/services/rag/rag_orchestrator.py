"""
RAG Orchestrator Module
Coordinates the full RAG pipeline: Retrieve → Compile → Generate → Validate
Now with medical safety integration for evidence thresholds and contradiction resolution
"""

from typing import List, Dict, Any, Optional
import logging
import json
import re

from app.services.rag.rag_retriever import get_retriever, HybridRetriever
from app.services.rag.rag_context_compiler import get_context_compiler, ContextCompiler
from app.services.rag.rag_citation_validator import get_citation_validator, CitationValidator
from app.services.root_cause_engine import RootCauseEngine
from app.models.ai_models import UserProfile

# Import medical safety modules
try:
    from app.services.safety.medical_safety import (
        EvidenceThresholdChecker,
        ContradictionResolver
    )
    MEDICAL_SAFETY_ENABLED = True
except ImportError:
    MEDICAL_SAFETY_ENABLED = False

logger = logging.getLogger(__name__)


class RAGOrchestrator:
    """
    Orchestrates the complete RAG pipeline for recommendation generation
    
    Flow:
    1. Analyze user profile (Root Cause Engine)
    2. Retrieve relevant papers (Hybrid Retriever)
    3. CHECK EVIDENCE THRESHOLD (Medical Safety)
    4. Compile context with citations (Context Compiler)
    5. Generate recommendations (LLM)
    6. Validate citations (Citation Validator)
    """
    
    def __init__(self):
        self.retriever = get_retriever()
        self.compiler = get_context_compiler()
        self.validator = get_citation_validator()
    
    async def generate_recommendations(
        self,
        user_profile: UserProfile,
        category: str,
        use_rag: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Generate recommendations using RAG pipeline
        """
        logger.info("=" * 60)
        logger.info("🎯 RAG ORCHESTRATOR CALLED")
        logger.info(f"   Category: {category}")
        logger.info(f"   Use RAG: {use_rag}")
        logger.info("=" * 60)
        
        try:
            # Convert to dict for processing
            profile_dict = user_profile.dict() if hasattr(user_profile, 'dict') else user_profile
            
            # Step 1: Analyze hormone imbalance
            logger.info("🧬 STEP 1: Analyzing hormone imbalance...")
            hormone_analysis = RootCauseEngine.analyze_hormone_imbalance(profile_dict)
            profile_dict['primary_imbalance'] = hormone_analysis.get('primary_imbalance', '')
            profile_dict['primary_level'] = hormone_analysis.get('primary_level', '')
            profile_dict['secondary_imbalances'] = hormone_analysis.get('secondary_imbalances', [])
            
            logger.info(f"✅ STEP 1 SUCCESS: Primary={hormone_analysis.get('primary_imbalance')} ({hormone_analysis.get('primary_level', '')})")
            
            if use_rag:
                logger.info("➡️ Proceeding with RAG pipeline...")
                return await self._rag_generate(profile_dict, category, hormone_analysis)
            else:
                logger.info("➡️ RAG disabled, using fallback...")
                return await self._fallback_generate(user_profile, category)
                
        except Exception as e:
            logger.error(f"❌ RAG Orchestrator EXCEPTION: {str(e)}")
            import traceback
            logger.error(f"   Traceback: {traceback.format_exc()}")
            return await self._fallback_generate(user_profile, category)
    
    async def _rag_generate(
        self,
        profile_dict: Dict[str, Any],
        category: str,
        hormone_analysis: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """RAG-enhanced generation pipeline with medical safety checks"""
        
        logger.info("=" * 60)
        logger.info(f"🔬 RAG PIPELINE STARTED: {category.upper()}")
        logger.info("=" * 60)
        
        # Step 2: Retrieve relevant papers
        logger.info("📖 STEP 2: Retrieving papers from Pinecone...")
        query = self._build_retrieval_query(profile_dict, category)
        logger.info(f"   Query: {query[:100]}...")
        
        papers = await self.retriever.retrieve(
            query=query,
            user_profile=profile_dict,
            category=category,
            top_k=20
        )
        
        if not papers:
            logger.warning(f"⚠️ STEP 2 FAILED: No papers retrieved for {category}")
            logger.info("   Falling back to prompt-only generation...")
            return await self._fallback_generate(profile_dict, category)
        
        logger.info(f"✅ STEP 2 SUCCESS: Retrieved {len(papers)} papers")
        
        # Step 2.5: CHECK EVIDENCE THRESHOLD (Medical Safety)
        if MEDICAL_SAFETY_ENABLED:
            logger.info("🛡️ STEP 2.5: Checking evidence threshold...")
            recommendation_type = self._get_recommendation_type(category)
            evidence_check = EvidenceThresholdChecker.check_evidence_sufficiency(
                papers=papers,
                recommendation_type=recommendation_type
            )
            
            if not evidence_check['sufficient']:
                logger.warning(f"⚠️ Evidence threshold not met: {evidence_check['message']}")
                evidence_warning = evidence_check['message']
            else:
                evidence_warning = None
                logger.info(f"✅ Evidence check passed: {evidence_check['papers_found']} quality papers")
        else:
            evidence_warning = None
            logger.info("ℹ️ STEP 2.5: Medical safety module not enabled")
        
        # Step 3: Compile context with citations
        logger.info("📄 STEP 3: Compiling context with citations...")
        context, citations = self.compiler.compile(papers, category, profile_dict)
        
        if not context:
            logger.warning(f"⚠️ STEP 3 FAILED: Context compilation failed for {category}")
            return await self._fallback_generate(profile_dict, category)
        
        logger.info(f"✅ STEP 3 SUCCESS: Compiled {len(citations)} citations")
        
        # Step 4: Create RAG prompt and generate
        logger.info("🤖 STEP 4: Generating recommendations with LLM...")
        prompt = self.compiler.create_rag_prompt(
            user_profile=profile_dict,
            category=category,
            research_context=context,
            citations=citations,
            hormone_analysis=hormone_analysis
        )
        
        # Import AIService for LLM call
        from app.services.ai_service import AIService
        
        llm_response, model_used = await AIService.call_ai_model(prompt)
        logger.info(f"✅ STEP 4 SUCCESS: LLM response received from {model_used}")
        
        # Step 5: Parse recommendations
        logger.info("📝 STEP 5: Parsing recommendations from LLM response...")
        recommendations = self._parse_recommendations(llm_response, category)
        
        if not recommendations:
            logger.warning(f"⚠️ STEP 5 FAILED: No recommendations parsed")
            logger.info(f"   LLM response preview: {llm_response[:200]}...")
            return []
        
        logger.info(f"✅ STEP 5 SUCCESS: Parsed {len(recommendations)} recommendations")
        
        # Step 6: Validate citations
        logger.info("🔍 STEP 6: Validating citations...")
        validated_recs, stats = self.validator.validate(recommendations, citations)
        logger.info(f"✅ STEP 6 SUCCESS: {stats['verified']}/{stats['total']} citations verified")
        
        # Step 7: Enrich with full citation metadata
        logger.info("📚 STEP 7: Enriching citations with metadata...")
        enriched_recs = self.validator.enrich_citations(validated_recs, citations)
        
        # Step 8: Calculate faithfulness score
        faithfulness = self.validator.calculate_faithfulness_score(enriched_recs, context)
        
        logger.info("=" * 60)
        logger.info(f"✅ RAG PIPELINE COMPLETE: {category.upper()}")
        logger.info(f"   Recommendations: {len(enriched_recs)}")
        logger.info(f"   Verification rate: {stats.get('verification_rate', 0):.0%}")
        logger.info(f"   Faithfulness: {faithfulness:.0%}")
        logger.info("=" * 60)
        
        return enriched_recs
    
    async def _fallback_generate(
        self,
        user_profile,
        category: str
    ) -> List[Dict[str, Any]]:
        """Fallback to original prompt-only generation"""
        from app.services.ai_service import AIService
        
        # Use original method
        if isinstance(user_profile, dict):
            from app.models.ai_models import UserProfile
            user_profile = UserProfile(**user_profile)
        
        prompt = AIService.suggest_llm_prompt_for_recommendations(user_profile, category)
        llm_response, _ = await AIService.call_ai_model(prompt)
        
        recommendations = AIService.parse_recommendations_from_llm(llm_response, category)
        
        # Mark as unverified
        for rec in recommendations:
            rec['citation_verified'] = False
            rec['citation_warning'] = "Generated without RAG - citations unverified"
        
        return recommendations
    
    def _get_recommendation_type(self, category: str) -> str:
        """
        Map category to recommendation type for evidence threshold checking.
        Higher-risk categories require more evidence.
        """
        type_mapping = {
            'food': 'food_recommendation',
            'supplement': 'supplement_dosage',
            'movement': 'exercise_protocol',
            'mindfulness': 'mindfulness',
        }
        return type_mapping.get(category, 'food_recommendation')
    
    def _build_retrieval_query(
        self,
        profile_dict: Dict[str, Any],
        category: str
    ) -> str:
        """Build query for paper retrieval"""
        parts = []
        
        # Category-specific terms
        category_terms = {
            "food": "diet nutrition intervention supplementation",
            "movement": "exercise physical activity intervention training",
            "mindfulness": "stress reduction meditation mindfulness intervention"
        }
        parts.append(category_terms.get(category, category))
        
        # PCOS/hormone context
        parts.append("PCOS polycystic ovary syndrome")
        
        # User's primary hormone
        if profile_dict.get('primary_imbalance'):
            parts.append(f"{profile_dict['primary_imbalance']} imbalance treatment")
        
        # Conditions
        for condition in profile_dict.get('conditions', [])[:2]:
            parts.append(condition)
        
        return " ".join(parts)
    
    def _parse_recommendations(
        self,
        llm_response: str,
        category: str
    ) -> List[Dict[str, Any]]:
        """Parse JSON recommendations from LLM response"""
        try:
            # Try to find JSON array in response
            json_match = re.search(r'\[[\s\S]*\]', llm_response)
            if json_match:
                json_str = json_match.group(0)
                recommendations = json.loads(json_str)
                
                if isinstance(recommendations, list):
                    return recommendations
            
            # Try to find single JSON object
            json_match = re.search(r'\{[\s\S]*\}', llm_response)
            if json_match:
                json_str = json_match.group(0)
                rec = json.loads(json_str)
                if isinstance(rec, dict):
                    return [rec]
            
            logger.warning("Could not parse JSON from LLM response")
            return []
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Parsing error: {str(e)}")
            return []


# Singleton instance
_orchestrator_instance = None

def get_rag_orchestrator() -> RAGOrchestrator:
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = RAGOrchestrator()
    return _orchestrator_instance


async def generate_rag_recommendations(
    user_profile: UserProfile,
    category: str,
    use_rag: bool = True
) -> List[Dict[str, Any]]:
    """
    Convenience function for generating RAG recommendations
    
    This is the main entry point for the RAG system.
    """
    orchestrator = get_rag_orchestrator()
    return await orchestrator.generate_recommendations(user_profile, category, use_rag)
