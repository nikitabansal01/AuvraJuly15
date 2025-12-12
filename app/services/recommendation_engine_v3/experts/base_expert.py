"""
Base Expert Module - Abstract base class for domain experts
===========================================================

All domain experts (Nutrition, Movement, Mindfulness) inherit from this base.
This ensures consistent interface and shared functionality.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import logging

from app.services.recommendation_engine_v3.core.problem_narrower import FocusedProblem

logger = logging.getLogger(__name__)


@dataclass
class ExpertRecommendation:
    """Standard recommendation structure from an expert module"""
    title: str
    purpose: str
    specific_action: str
    frequency: str
    priority: str  # high, medium, low
    evidence_strength: str  # strong, moderate, weak
    root_causes_addressed: List[str]
    citations: List[Dict[str, Any]]
    citation_verified: bool
    confidence: float
    module_source: str  # Which sub-module generated this
    
    # Optional fields
    contraindications: List[str] = None
    timeline: str = None
    intensity: str = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'title': self.title,
            'purpose': self.purpose,
            'specificAction': self.specific_action,
            'frequency': self.frequency,
            'priority': self.priority,
            'evidence_strength': self.evidence_strength,
            'root_causes_addressed': self.root_causes_addressed,
            'citations': self.citations,
            'citation_verified': self.citation_verified,
            'confidence': self.confidence,
            'module_source': self.module_source,
            'contraindications': self.contraindications or [],
            'expectedTimeline': self.timeline or '8-12 weeks',
            'intensity': self.intensity or 'moderate',
        }


class BaseDomainExpert(ABC):
    """
    Abstract base class for domain expert modules.
    
    Each expert has:
    - Sub-modules for specific hormone/condition focuses
    - Specialized retrieval queries
    - Evidence-based intervention templates
    - Access to RAG retrieval for real-time evidence
    """
    
    DOMAIN_NAME = "base"  # Override in subclass
    
    def __init__(self, retrieval_component=None):
        self.submodules = {}
        self.retrieval = retrieval_component  # Shared retrieval component
        self._initialize_submodules()
        logger.info(f"🏗️ {self.__class__.__name__} initialized with {len(self.submodules)} submodules")
    
    def set_retrieval(self, retrieval_component):
        """Set retrieval component for real RAG integration"""
        self.retrieval = retrieval_component
        # Also set for all submodules
        for submodule in self.submodules.values():
            if hasattr(submodule, 'set_retrieval'):
                submodule.set_retrieval(retrieval_component)
    
    @abstractmethod
    def _initialize_submodules(self):
        """Initialize domain-specific sub-modules"""
        pass
    
    @abstractmethod
    async def generate_recommendations(
        self,
        focused_problem: FocusedProblem,
        active_submodules: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Generate recommendations for this domain.
        
        Args:
            focused_problem: The narrowed problem definition
            active_submodules: List of sub-module names to activate
        
        Returns:
            List of recommendation dictionaries
        """
        pass
    
    def _select_submodules(
        self,
        focused_problem: FocusedProblem,
        active_submodules: List[str] = None
    ) -> List:
        """Select which sub-modules to run based on problem and config"""
        if active_submodules:
            # Use specified submodules
            return [
                self.submodules[name] 
                for name in active_submodules 
                if name in self.submodules
            ]
        
        # Auto-select based on root causes
        selected = []
        root_causes = focused_problem.get_all_root_causes()
        
        for name, module in self.submodules.items():
            if hasattr(module, 'TARGET_ROOT_CAUSES'):
                if any(cause in module.TARGET_ROOT_CAUSES for cause in root_causes):
                    selected.append(module)
        
        # Always include at least one module
        if not selected and self.submodules:
            selected = [list(self.submodules.values())[0]]
        
        return selected
    
    def _merge_recommendations(
        self,
        module_results: List[List[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        """Merge recommendations from multiple sub-modules"""
        merged = []
        for results in module_results:
            merged.extend(results)
        return merged


class BaseExpertSubModule(ABC):
    """
    Abstract base class for expert sub-modules.
    
    Each sub-module focuses on a specific hormone imbalance or condition
    within its parent domain. Now with RAG retrieval integration.
    """
    
    MODULE_NAME = "base_submodule"
    TARGET_ROOT_CAUSES = []  # Which root causes this module addresses
    
    def __init__(self):
        self.retrieval = None  # Will be set by parent expert
    
    def set_retrieval(self, retrieval_component):
        """Set retrieval component for RAG integration"""
        self.retrieval = retrieval_component
    
    # Retrieval configuration - override in subclass
    RETRIEVAL_CONFIG = {
        'primary_queries': [],
        'must_include_terms': [],
        'study_type_preference': ['meta-analysis', 'RCT', 'systematic review'],
        'min_evidence_level': 'moderate',
    }
    
    # Intervention templates - evidence-backed action templates
    INTERVENTION_TEMPLATES = {}
    
    @abstractmethod
    async def generate(
        self,
        focused_problem: FocusedProblem
    ) -> List[Dict[str, Any]]:
        """Generate recommendations for this sub-module's focus"""
        pass
    
    async def retrieve_evidence(self, topic: str, focused_problem: FocusedProblem = None, category: str = "food") -> List[Dict[str, Any]]:
        """
        Retrieve relevant research evidence from Pinecone for a topic.
        
        Args:
            topic: The topic to search for (e.g., "low glycemic diet insulin resistance")
            focused_problem: Optional context for personalization
            category: Category for search - "food", "movement", or "mindfulness"
        
        Returns:
            List of relevant documents with citations
        """
        if self.retrieval is None:
            logger.debug(f"⚠️ {self.MODULE_NAME}: No retrieval component, using templates only")
            return []
        
        try:
            # Build a focused query
            queries = self.RETRIEVAL_CONFIG.get('primary_queries', [])
            if not queries:
                queries = [topic]
            
            all_results = []
            for query in queries[:3]:  # Limit to top 3 queries
                results = await self.retrieval.retrieve(
                    query=f"{query} PCOS women",
                    top_k=5,
                    min_score=0.3,
                    category=category
                )
                all_results.extend(results)
            
            # Deduplicate by PMID/DOI
            seen = set()
            unique_results = []
            for r in all_results:
                doc_id = r.get('pmid') or r.get('doi') or r.get('id', str(hash(str(r))))
                if doc_id not in seen:
                    seen.add(doc_id)
                    unique_results.append(r)
            
            logger.info(f"📚 {self.MODULE_NAME}: Retrieved {len(unique_results)} evidence documents")
            return unique_results[:10]  # Return top 10
            
        except Exception as e:
            logger.warning(f"⚠️ {self.MODULE_NAME}: Retrieval failed: {e}")
            return []
    
    def _extract_citations(self, retrieved_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extract citation information from retrieved documents.
        
        IMPORTANT: Includes 'text' and 'mesh_terms' fields so Evidence Grader
        can analyze the actual research content for quality grading.
        """
        citations = []
        for doc in retrieved_docs[:5]:  # Top 5 citations
            # Handle both direct fields and nested metadata
            metadata = doc.get('metadata', {})
            
            # Get publication_year - handle float (from Pinecone) or int
            year_raw = doc.get('publication_year') or doc.get('year') or metadata.get('publication_year') or metadata.get('year')
            year = int(year_raw) if year_raw else None
            
            citation = {
                # Identity fields
                'pmid': doc.get('pmid') or metadata.get('pmid'),
                'doi': doc.get('doi') or metadata.get('doi'),
                'title': doc.get('title') or metadata.get('title', 'Unknown'),
                
                # Content for Evidence Grader (CRITICAL - was missing before!)
                'text': doc.get('text') or metadata.get('text', ''),
                'mesh_terms': doc.get('mesh_terms') or metadata.get('mesh_terms', []),
                
                # Metadata
                'authors': doc.get('authors') or metadata.get('authors', []),
                'year': year,
                'publication_year': year,  # Alias for Evidence Grader
                'journal': doc.get('journal') or metadata.get('journal'),
                'relevance_score': doc.get('score', 0.0),
                'study_type': doc.get('study_type') or metadata.get('study_type'),
            }
            
            # Only add if we have at least PMID or meaningful title
            if citation['pmid'] or citation['doi'] or (citation['title'] and citation['title'] != 'Unknown'):
                citations.append(citation)
        return citations
    
    def _create_recommendation(
        self,
        template_key: str,
        focused_problem: FocusedProblem,
        citations: List[Dict[str, Any]] = None,
        evidence_sources: List[Dict[str, Any]] = None,
        customizations: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Create a recommendation from a template.
        
        Templates provide evidence-based defaults, which are then
        customized for the specific user.
        
        Args:
            template_key: Key of the template to use
            focused_problem: The focused problem context
            citations: Display-friendly citation list
            evidence_sources: Full documents with 'text' field for Evidence Grader
            customizations: Any custom field overrides
        """
        template = self.INTERVENTION_TEMPLATES.get(template_key, {})
        if not template:
            return None
        
        # Determine citation verification status:
        # True only if we have citations with valid PMIDs (from real Pinecone papers)
        verified_citations = [
            c for c in (citations or []) 
            if c.get('pmid') or c.get('doi')
        ]
        has_verified_citations = len(verified_citations) > 0
        
        # Map root causes to hormones for frontend display
        root_causes = template.get('root_causes', self.TARGET_ROOT_CAUSES)
        hormones = self._map_root_causes_to_hormones(root_causes, focused_problem)
        
        rec = {
            'title': template.get('title', template_key),
            'purpose': template.get('purpose', ''),
            'specificAction': template.get('action', ''),
            'frequency': template.get('frequency', 'Daily'),
            'frequency_detail': template.get('frequency_detail', 'daily:1'),
            'priority': template.get('priority', 'medium'),
            
            # FIXED: Set to 'pending_grade' - will be set by Evidence Grader
            # Template value is just a hint, real grade comes from analysis
            'evidence_strength': 'pending_grade',
            'evidence_strength_hint': template.get('evidence_strength', 'moderate'),
            
            'root_causes_addressed': root_causes,
            'citations': citations or [],
            
            # FIXED: Store full evidence sources for Evidence Grader
            # These contain 'text' field needed for actual content analysis
            'evidence_sources': evidence_sources or citations or [],
            
            # FIXED: Only True if we have citations with valid PMIDs/DOIs
            'citation_verified': has_verified_citations,
            'verified_citation_count': len(verified_citations),
            
            'confidence': 0.7 if has_verified_citations else 0.5,
            'module_source': self.MODULE_NAME,
            'contraindications': template.get('contraindications', []),
            'expectedTimeline': template.get('timeline', '8-12 weeks'),
            'intensity': template.get('intensity', 'moderate'),
            
            # CRITICAL: Include optimal_times for time-based scheduling
            'optimal_times': template.get('optimal_times', ['anytime']),
            
            # CRITICAL: Include hormones for Hormone Quests display
            'hormones': hormones,
            
            # CRITICAL: Include conditions and symptoms from focused_problem
            'conditions': self._extract_conditions(focused_problem),
            'symptoms': self._extract_symptoms(focused_problem),
            
            # Category-specific fields from template
            'food_amounts': template.get('food_amounts', []),
            'food_items': template.get('food_items', []),
            'exercise_durations': template.get('exercise_durations', []),
            'exercise_types': template.get('exercise_types', []),
            'exercise_intensities': template.get('exercise_intensities', []),
            'mindfulness_durations': template.get('mindfulness_durations', []),
            'mindfulness_techniques': template.get('mindfulness_techniques', []),
            
            # Duration for scheduling
            'duration_weeks': template.get('duration_weeks', 8),
        }
        
        # Apply customizations
        if customizations:
            rec.update(customizations)
        
        # Personalize for user constraints
        rec = self._personalize(rec, focused_problem)
        
        return rec
    
    def _map_root_causes_to_hormones(
        self, 
        root_causes: List[str], 
        focused_problem: FocusedProblem
    ) -> List[str]:
        """
        Map recommendation to USER'S hormones (not template's root causes).
        
        CRITICAL: Each recommendation should be tagged with ONE of the user's
        actual hormones (from Root Cause Engine), NOT derived from root causes.
        
        Logic:
        1. Check if user has defined hormones (from Root Cause Engine)
        2. If yes, use those hormones directly - distribute across recommendations
        3. If no, fall back to mapping from root causes
        """
        # PRIORITY 1: Use user's actual hormones from Root Cause Engine
        if focused_problem and hasattr(focused_problem, 'user_hormones'):
            user_hormones = focused_problem.user_hormones
            primary = user_hormones.get('primary', '')
            secondary = user_hormones.get('secondary', [])
            
            if primary:
                # User has defined hormones - use them
                all_hormones = [primary]
                if secondary:
                    all_hormones.extend(secondary)
                
                # Return ONE hormone for this recommendation
                # The specific hormone is determined by the root causes
                # (to distribute recommendations across user's hormones)
                
                # Check if any root cause maps to a user's hormone
                ROOT_CAUSE_TO_HORMONE = {
                    'insulin_resistance': 'insulin',
                    'blood_sugar_instability': 'insulin',
                    'androgen_high': 'testosterone',
                    'androgens_high': 'testosterone',
                    'progesterone_low': 'progesterone',
                    'progesterone_deficiency': 'progesterone',
                    'cortisol_high': 'cortisol',
                    'cortisol_dysregulation': 'cortisol',
                    'thyroid_low': 'thyroid',
                    'thyroid_imbalance': 'thyroid',
                    'estrogen_dominance': 'estrogen',
                    'estrogen_imbalance': 'estrogen',
                }
                
                # Find which user hormone this recommendation targets
                for cause in root_causes:
                    cause_lower = cause.lower()
                    mapped_hormone = ROOT_CAUSE_TO_HORMONE.get(cause_lower, '')
                    
                    # Check if mapped hormone matches user's primary or secondary
                    if mapped_hormone and mapped_hormone in all_hormones:
                        return [mapped_hormone]
                    
                    # Check if the root cause itself is a hormone name
                    for uh in all_hormones:
                        if uh in cause_lower or cause_lower in uh:
                            return [uh]
                
                # No match found - return PRIMARY hormone only
                return [primary]
        
        # FALLBACK: Original logic if no user hormones defined
        ROOT_CAUSE_TO_HORMONE = {
            'insulin_resistance': 'insulin',
            'blood_sugar_instability': 'insulin',
            'leptin_resistance': 'insulin',
            'androgen_high': 'testosterone',
            'androgens_high': 'testosterone',
            'hirsutism': 'testosterone',
            'acne': 'testosterone',
            'hair_loss': 'testosterone',
            'estrogen_imbalance': 'estrogen',
            'estrogen_dominance': 'estrogen',
            'estrogen_balance': 'estrogen',
            'progesterone_low': 'progesterone',
            'progesterone_deficiency': 'progesterone',
            'luteal_phase_defect': 'progesterone',
            'cortisol_high': 'cortisol',
            'cortisol_dysregulation': 'cortisol',
            'stress': 'cortisol',
            'thyroid_low': 'thyroid',
            'thyroid_imbalance': 'thyroid',
            'hypothyroid': 'thyroid',
            'inflammation': 'cortisol',
            'prostaglandin_imbalance': 'progesterone',
        }
        
        hormones = set()
        
        # Map from root causes
        for cause in root_causes:
            cause_lower = cause.lower()
            if cause_lower in ROOT_CAUSE_TO_HORMONE:
                hormones.add(ROOT_CAUSE_TO_HORMONE[cause_lower])
        
        # Ensure at least one hormone
        if not hormones:
            hormones.add('progesterone')
        
        return list(hormones)
    
    def _extract_conditions(self, focused_problem: FocusedProblem) -> List[str]:
        """Extract conditions from focused problem for frontend display."""
        conditions = []
        if focused_problem:
            if hasattr(focused_problem, 'primary_concern') and focused_problem.primary_concern:
                if hasattr(focused_problem.primary_concern, 'related_conditions'):
                    conditions.extend(focused_problem.primary_concern.related_conditions or [])
            if hasattr(focused_problem, 'secondary_concerns'):
                for concern in focused_problem.secondary_concerns:
                    if hasattr(concern, 'related_conditions'):
                        conditions.extend(concern.related_conditions or [])
        return list(set(conditions))[:5]  # Limit to 5
    
    def _extract_symptoms(self, focused_problem: FocusedProblem) -> List[str]:
        """Extract symptoms from focused problem for frontend display."""
        symptoms = []
        if focused_problem:
            if hasattr(focused_problem, 'primary_concern') and focused_problem.primary_concern:
                if hasattr(focused_problem.primary_concern, 'concern_type'):
                    symptoms.append(focused_problem.primary_concern.concern_type)
            if hasattr(focused_problem, 'secondary_concerns'):
                for concern in focused_problem.secondary_concerns:
                    if hasattr(concern, 'concern_type'):
                        symptoms.append(concern.concern_type)
        return list(set(symptoms))[:5]  # Limit to 5
    
    def _personalize(
        self,
        recommendation: Dict[str, Any],
        focused_problem: FocusedProblem
    ) -> Dict[str, Any]:
        """Personalize recommendation for user's constraints"""
        # Check dietary constraints
        for constraint in focused_problem.constraints:
            if constraint.constraint_type == 'dietary':
                # Add constraint warning if relevant
                if 'dairy' in constraint.description.lower():
                    if 'dairy' in recommendation.get('specificAction', '').lower():
                        recommendation['constraint_warning'] = 'Adjust for dairy-free diet'
                if 'vegetarian' in constraint.description.lower() or 'vegan' in constraint.description.lower():
                    if any(meat in recommendation.get('specificAction', '').lower() 
                           for meat in ['fish', 'salmon', 'meat', 'chicken', 'beef']):
                        recommendation['constraint_warning'] = 'Adjust for vegetarian/vegan diet'
        
        return recommendation
