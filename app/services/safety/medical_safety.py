"""
Medical Safety Module for AUVRA RAG System
Implements evidence-based quality scoring, safety guardrails, and audit logging
"""

import logging
import re
import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


# ============================================================================
# PAPER QUALITY SCORING (Priority 0 - #1)
# Based on Evidence-Based Medicine Hierarchy
# ============================================================================

class StudyType(Enum):
    """Evidence hierarchy from EBM literature"""
    SYSTEMATIC_REVIEW = "systematic_review"
    META_ANALYSIS = "meta_analysis"
    RCT = "randomized_controlled_trial"
    COHORT = "cohort_study"
    CASE_CONTROL = "case_control"
    CROSS_SECTIONAL = "cross_sectional"
    CASE_SERIES = "case_series"
    CASE_REPORT = "case_report"
    EXPERT_OPINION = "expert_opinion"
    UNKNOWN = "unknown"


class PaperQualityScorer:
    """
    Score research papers based on evidence quality before indexing.
    Papers scoring < MIN_QUALITY_THRESHOLD are flagged for review.
    
    Based on GRADE methodology and EBM hierarchy.
    """
    
    MIN_QUALITY_THRESHOLD = 12  # Minimum score to auto-index
    
    # Evidence hierarchy scores (EBM pyramid)
    STUDY_TYPE_SCORES = {
        StudyType.SYSTEMATIC_REVIEW: 10,
        StudyType.META_ANALYSIS: 10,
        StudyType.RCT: 9,
        StudyType.COHORT: 6,
        StudyType.CASE_CONTROL: 5,
        StudyType.CROSS_SECTIONAL: 4,
        StudyType.CASE_SERIES: 3,
        StudyType.CASE_REPORT: 2,
        StudyType.EXPERT_OPINION: 1,
        StudyType.UNKNOWN: 3,  # Default score
    }
    
    # Sample size scores
    SAMPLE_SIZE_SCORES = {
        "large": 5,      # n >= 100
        "medium": 3,     # n 50-99
        "small": 1,      # n 20-49
        "very_small": 0, # n < 20
    }
    
    # Recency scores
    RECENCY_SCORES = {
        "recent": 3,     # Published within 3 years
        "moderate": 2,   # 3-7 years
        "older": 1,      # 7-10 years
        "outdated": 0,   # > 10 years
    }
    
    # Study type detection patterns
    STUDY_TYPE_PATTERNS = {
        StudyType.SYSTEMATIC_REVIEW: [
            r'\bsystematic\s+review\b',
            r'\bsystematic\s+literature\s+review\b',
            r'\bprisma\b',
            r'\bpreferred\s+reporting\s+items\b',
        ],
        StudyType.META_ANALYSIS: [
            r'\bmeta[-\s]?analysis\b',
            r'\bmeta[-\s]?analytic\b',
            r'\bpooled\s+analysis\b',
        ],
        StudyType.RCT: [
            r'\brandomized\s+controlled\s+trial\b',
            r'\brandomised\s+controlled\s+trial\b',
            r'\brct\b',
            r'\bdouble[-\s]?blind\b',
            r'\bplacebo[-\s]?controlled\b',
            r'\brandomly\s+assigned\b',
            r'\brandom\s+allocation\b',
        ],
        StudyType.COHORT: [
            r'\bcohort\s+study\b',
            r'\bprospective\s+study\b',
            r'\blongitudinal\s+study\b',
            r'\bfollow[-\s]?up\s+study\b',
        ],
        StudyType.CASE_CONTROL: [
            r'\bcase[-\s]?control\b',
            r'\bretrospective\s+study\b',
        ],
        StudyType.CROSS_SECTIONAL: [
            r'\bcross[-\s]?sectional\b',
            r'\bprevalence\s+study\b',
            r'\bsurvey\s+study\b',
        ],
        StudyType.CASE_SERIES: [
            r'\bcase\s+series\b',
        ],
        StudyType.CASE_REPORT: [
            r'\bcase\s+report\b',
        ],
    }
    
    # Sample size extraction patterns
    SAMPLE_SIZE_PATTERNS = [
        r'\bn\s*=\s*(\d+)\b',
        r'\b(\d+)\s+(?:women|participants|subjects|patients)\b',
        r'\bsample\s+(?:size|of)\s+(\d+)\b',
        r'\benrolled\s+(\d+)\b',
        r'\b(\d+)\s+were\s+(?:enrolled|included|randomized)\b',
    ]
    
    @classmethod
    def score_paper(cls, paper: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate quality score for a paper.
        
        Args:
            paper: Paper metadata with title, abstract, publication_year, journal
            
        Returns:
            {
                'total_score': int,
                'meets_threshold': bool,
                'study_type': str,
                'study_type_score': int,
                'sample_size': int or None,
                'sample_size_score': int,
                'recency_score': int,
                'quality_flags': List[str],
                'recommendation': 'index' | 'review' | 'reject'
            }
        """
        title = (paper.get('title') or '').lower()
        abstract = (paper.get('abstract') or '').lower()
        full_text = (paper.get('full_text') or '').lower()
        text = f"{title} {abstract} {full_text}"
        
        pub_year = paper.get('publication_year', 0)
        
        result = {
            'pmid': paper.get('pmid'),
            'title': (paper.get('title') or '')[:100],
            'quality_flags': [],
        }
        
        # 1. Detect study type
        study_type = cls._detect_study_type(text)
        study_type_score = cls.STUDY_TYPE_SCORES.get(study_type, 3)
        result['study_type'] = study_type.value
        result['study_type_score'] = study_type_score
        
        # 2. Extract sample size
        sample_size = cls._extract_sample_size(text)
        sample_size_score = cls._score_sample_size(sample_size)
        result['sample_size'] = sample_size
        result['sample_size_score'] = sample_size_score
        
        if sample_size and sample_size < 20:
            result['quality_flags'].append('VERY_SMALL_SAMPLE')
        
        # 3. Score recency
        recency_score = cls._score_recency(pub_year)
        result['recency_score'] = recency_score
        
        if pub_year and pub_year < 2015:
            result['quality_flags'].append('OLDER_STUDY')
        
        # 4. Calculate total
        total_score = study_type_score + sample_size_score + recency_score
        result['total_score'] = total_score
        result['meets_threshold'] = total_score >= cls.MIN_QUALITY_THRESHOLD
        
        # 5. Determine recommendation
        if total_score >= cls.MIN_QUALITY_THRESHOLD:
            result['recommendation'] = 'index'
        elif total_score >= 8:
            result['recommendation'] = 'review'
            result['quality_flags'].append('NEEDS_REVIEW')
        else:
            result['recommendation'] = 'reject'
            result['quality_flags'].append('LOW_QUALITY')
        
        return result
    
    @classmethod
    def _detect_study_type(cls, text: str) -> StudyType:
        """Detect study type from text using pattern matching"""
        for study_type, patterns in cls.STUDY_TYPE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    return study_type
        return StudyType.UNKNOWN
    
    @classmethod
    def _extract_sample_size(cls, text: str) -> Optional[int]:
        """Extract sample size from text"""
        for pattern in cls.SAMPLE_SIZE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except (ValueError, IndexError):
                    continue
        return None
    
    @classmethod
    def _score_sample_size(cls, sample_size: Optional[int]) -> int:
        """Score based on sample size"""
        if sample_size is None:
            return 2  # Neutral if unknown
        elif sample_size >= 100:
            return cls.SAMPLE_SIZE_SCORES['large']
        elif sample_size >= 50:
            return cls.SAMPLE_SIZE_SCORES['medium']
        elif sample_size >= 20:
            return cls.SAMPLE_SIZE_SCORES['small']
        else:
            return cls.SAMPLE_SIZE_SCORES['very_small']
    
    @classmethod
    def _score_recency(cls, pub_year: int) -> int:
        """Score based on publication year"""
        if not pub_year:
            return 1  # Neutral if unknown
        
        current_year = datetime.now().year
        age = current_year - pub_year
        
        if age <= 3:
            return cls.RECENCY_SCORES['recent']
        elif age <= 7:
            return cls.RECENCY_SCORES['moderate']
        elif age <= 10:
            return cls.RECENCY_SCORES['older']
        else:
            return cls.RECENCY_SCORES['outdated']
    
    @classmethod
    def filter_papers_by_quality(cls, papers: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Filter papers into three categories: index, review, reject
        
        Returns:
            (papers_to_index, papers_for_review, rejected_papers)
        """
        to_index = []
        for_review = []
        rejected = []
        
        for paper in papers:
            quality = cls.score_paper(paper)
            paper['quality_score'] = quality
            
            if quality['recommendation'] == 'index':
                to_index.append(paper)
            elif quality['recommendation'] == 'review':
                for_review.append(paper)
            else:
                rejected.append(paper)
        
        logger.info(f"Paper quality filtering: {len(to_index)} index, {len(for_review)} review, {len(rejected)} rejected")
        
        return to_index, for_review, rejected


# ============================================================================
# SAFETY GUARDRAILS (Priority 0 - #2)
# Contraindication checking and mandatory disclaimers
# ============================================================================

class SafetyGuardrails:
    """
    Medical safety checks for recommendations.
    Checks contraindications and appends mandatory disclaimers.
    """
    
    # Contraindication database
    # Format: intervention -> list of conditions/states that contraindicate
    CONTRAINDICATIONS = {
        # Supplements
        'cinnamon': ['pregnancy', 'liver disease', 'blood thinners', 'warfarin', 'diabetes medication'],
        'spearmint': ['pregnancy', 'breastfeeding', 'GERD', 'acid reflux', 'kidney disease'],
        'inositol': ['bipolar disorder', 'lithium'],
        'vitamin d': ['hypercalcemia', 'kidney stones', 'sarcoidosis'],
        'iron': ['hemochromatosis', 'iron overload'],
        'iodine': ['hyperthyroidism', 'graves disease', 'autoimmune thyroid'],
        'selenium': ['selenium toxicity'],
        'vitex': ['pregnancy', 'breastfeeding', 'hormone-sensitive conditions', 'IVF treatment'],
        'maca': ['pregnancy', 'breastfeeding', 'thyroid conditions'],
        'evening primrose': ['bleeding disorders', 'seizure disorders', 'blood thinners'],
        'DIM': ['pregnancy', 'breastfeeding', 'hormone-sensitive cancers'],
        'berberine': ['pregnancy', 'breastfeeding', 'diabetes medication', 'blood pressure medication'],
        'ashwagandha': ['pregnancy', 'breastfeeding', 'autoimmune conditions', 'thyroid medication'],
        'turmeric': ['gallbladder disease', 'blood thinners', 'diabetes medication'],
        'green tea': ['anemia', 'caffeine sensitivity', 'anxiety disorders'],
        
        # Foods in high amounts
        'soy': ['hormone-sensitive cancers', 'thyroid conditions'],
        'flaxseed': ['hormone-sensitive cancers', 'blood thinners', 'bowel obstruction'],
        'licorice': ['hypertension', 'heart disease', 'kidney disease', 'pregnancy'],
        
        # Exercise
        'high intensity exercise': ['heart conditions', 'uncontrolled hypertension', 'pregnancy first trimester'],
        'HIIT': ['heart conditions', 'uncontrolled hypertension', 'joint problems'],
        'fasting': ['pregnancy', 'breastfeeding', 'diabetes', 'eating disorders'],
    }
    
    # Risk conditions that require extra caution
    HIGH_RISK_CONDITIONS = [
        'pregnancy', 'breastfeeding', 'cancer', 'diabetes',
        'heart disease', 'kidney disease', 'liver disease',
        'autoimmune', 'bleeding disorder', 'seizure'
    ]
    
    MANDATORY_DISCLAIMER = """
⚠️ IMPORTANT DISCLAIMER:
This recommendation is based on published scientific research but is NOT medical advice. 
Please consult your healthcare provider before starting any new supplement, making significant 
dietary changes, or beginning a new exercise program—especially if you:
• Are pregnant or breastfeeding
• Take prescription medications
• Have chronic health conditions
• Have allergies or sensitivities

Individual results may vary. These recommendations are meant to complement, not replace, 
professional medical care.
"""

    SHORT_DISCLAIMER = """
⚠️ Consult your healthcare provider before making changes, especially if pregnant, 
breastfeeding, or taking medications.
"""
    
    @classmethod
    def check_contraindications(
        cls, 
        intervention: str, 
        user_conditions: List[str],
        user_medications: List[str] = None
    ) -> Dict[str, Any]:
        """
        Check if an intervention is contraindicated for a user.
        
        Args:
            intervention: The supplement/food/exercise being recommended
            user_conditions: User's diagnosed conditions
            user_medications: User's current medications
            
        Returns:
            {
                'is_safe': bool,
                'warnings': List[str],
                'contraindications_found': List[str],
                'risk_level': 'low' | 'medium' | 'high'
            }
        """
        intervention_lower = intervention.lower()
        user_conditions_lower = [c.lower() for c in (user_conditions or [])]
        user_medications_lower = [m.lower() for m in (user_medications or [])]
        
        all_user_factors = user_conditions_lower + user_medications_lower
        
        result = {
            'is_safe': True,
            'warnings': [],
            'contraindications_found': [],
            'risk_level': 'low'
        }
        
        # Check direct contraindications
        for item, contraindicated_conditions in cls.CONTRAINDICATIONS.items():
            if item in intervention_lower:
                for condition in contraindicated_conditions:
                    for user_factor in all_user_factors:
                        if condition.lower() in user_factor or user_factor in condition.lower():
                            result['contraindications_found'].append(f"{item} + {condition}")
                            result['warnings'].append(
                                f"⚠️ {item.title()} may not be suitable for people with {condition}. "
                                f"Consult your doctor before use."
                            )
        
        # Check for high-risk conditions
        for condition in cls.HIGH_RISK_CONDITIONS:
            for user_condition in user_conditions_lower:
                if condition in user_condition:
                    result['risk_level'] = 'high' if result['risk_level'] != 'high' else 'high'
                    result['warnings'].append(
                        f"⚠️ Extra caution advised due to {user_condition}. "
                        f"Medical supervision recommended."
                    )
        
        # Determine safety and risk level
        if result['contraindications_found']:
            result['is_safe'] = False
            result['risk_level'] = 'high'
        elif len(result['warnings']) > 0:
            result['risk_level'] = 'medium'
        
        return result
    
    @classmethod
    def add_disclaimer(cls, recommendation: Dict[str, Any], short: bool = True) -> Dict[str, Any]:
        """Add mandatory disclaimer to a recommendation"""
        disclaimer = cls.SHORT_DISCLAIMER if short else cls.MANDATORY_DISCLAIMER
        
        if 'disclaimer' not in recommendation:
            recommendation['disclaimer'] = disclaimer.strip()
        
        return recommendation
    
    @classmethod
    def process_recommendation(
        cls,
        recommendation: Dict[str, Any],
        user_conditions: List[str],
        user_medications: List[str] = None
    ) -> Dict[str, Any]:
        """
        Full safety processing for a recommendation.
        
        Returns the recommendation with:
        - Safety check results
        - Warnings if applicable
        - Mandatory disclaimer
        - Risk level flag
        """
        # Get intervention name
        intervention = recommendation.get('title', '') + ' ' + recommendation.get('purpose', '')
        
        # Check for specific items
        for field in ['food_items', 'specificAction']:
            if field in recommendation:
                item = recommendation[field]
                if isinstance(item, list):
                    intervention += ' ' + ' '.join(item)
                elif isinstance(item, str):
                    intervention += ' ' + item
        
        # Run safety check
        safety = cls.check_contraindications(intervention, user_conditions, user_medications)
        
        # Add safety info to recommendation
        recommendation['safety_checked'] = True
        recommendation['risk_level'] = safety['risk_level']
        
        if safety['warnings']:
            recommendation['safety_warnings'] = safety['warnings']
        
        if not safety['is_safe']:
            recommendation['contraindicated'] = True
            recommendation['contraindication_details'] = safety['contraindications_found']
        
        # Add disclaimer
        recommendation = cls.add_disclaimer(recommendation)
        
        return recommendation


# ============================================================================
# AUDIT LOGGING (Priority 0 - #3)
# Full provenance tracking for recommendations
# ============================================================================

class RecommendationAuditLog:
    """
    Comprehensive audit logging for all recommendations.
    Enables tracing adverse events back to source papers.
    """
    
    _log_entries: List[Dict[str, Any]] = []  # In-memory store (replace with DB in production)
    
    @classmethod
    def log_recommendation(
        cls,
        user_id: str,
        user_profile: Dict[str, Any],
        category: str,
        retrieved_papers: List[Dict[str, Any]],
        recommendation: Dict[str, Any],
        llm_prompt: str = None,
        llm_response: str = None,
        confidence_score: float = None
    ) -> str:
        """
        Log a recommendation with full provenance.
        
        Returns:
            log_id: Unique identifier for this log entry
        """
        import hashlib
        import uuid
        
        log_id = str(uuid.uuid4())
        
        # Hash user ID for privacy
        user_id_hash = hashlib.sha256(user_id.encode()).hexdigest()[:16]
        
        entry = {
            'log_id': log_id,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'user_id_hash': user_id_hash,
            
            # User context (anonymized)
            'user_profile': {
                'conditions': user_profile.get('conditions', []),
                'symptoms': user_profile.get('symptoms', []),
                'hormones': user_profile.get('related_hormones', []),
                'age_range': cls._get_age_range(user_profile.get('age')),
            },
            
            # Recommendation details
            'category': category,
            'recommendation_title': recommendation.get('title'),
            'recommendation_action': recommendation.get('specificAction'),
            
            # Evidence provenance
            'papers_used': [
                {
                    'pmid': p.get('pmid'),
                    'title': p.get('title', '')[:100],
                    'quality_score': p.get('quality_score', {}).get('total_score'),
                    'study_type': p.get('quality_score', {}).get('study_type'),
                }
                for p in retrieved_papers[:5]  # Top 5 papers
            ],
            
            # Safety info
            'safety_checked': recommendation.get('safety_checked', False),
            'risk_level': recommendation.get('risk_level', 'unknown'),
            'warnings': recommendation.get('safety_warnings', []),
            
            # LLM info (for debugging)
            'llm_prompt_hash': hashlib.sha256((llm_prompt or '').encode()).hexdigest()[:16],
            'confidence_score': confidence_score,
            
            # Metadata
            'rag_version': recommendation.get('rag_version', 'v2'),
            'citation_verified': recommendation.get('citation_verified', False),
        }
        
        cls._log_entries.append(entry)
        
        # Also log to file for persistence
        cls._write_to_file(entry)
        
        logger.info(f"Audit log created: {log_id} for recommendation '{recommendation.get('title')}'")
        
        return log_id
    
    @classmethod
    def _get_age_range(cls, age: int = None) -> str:
        """Convert age to range for privacy"""
        if not age:
            return 'unknown'
        elif age < 20:
            return '15-19'
        elif age < 30:
            return '20-29'
        elif age < 40:
            return '30-39'
        elif age < 50:
            return '40-49'
        else:
            return '50+'
    
    @classmethod
    def _write_to_file(cls, entry: Dict[str, Any]):
        """Persist log entry to file"""
        import os
        
        log_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'logs', 'audit')
        os.makedirs(log_dir, exist_ok=True)
        
        log_file = os.path.join(log_dir, f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl")
        
        try:
            with open(log_file, 'a') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
    
    @classmethod
    def get_logs_for_user(cls, user_id: str) -> List[Dict[str, Any]]:
        """Retrieve all logs for a user (for investigation)"""
        import hashlib
        user_id_hash = hashlib.sha256(user_id.encode()).hexdigest()[:16]
        return [e for e in cls._log_entries if e['user_id_hash'] == user_id_hash]
    
    @classmethod
    def get_logs_by_paper(cls, pmid: str) -> List[Dict[str, Any]]:
        """Find all recommendations that used a specific paper"""
        return [
            e for e in cls._log_entries 
            if any(p['pmid'] == pmid for p in e.get('papers_used', []))
        ]


# ============================================================================
# CONTRADICTION RESOLUTION (Priority 1 - #4)
# Handle conflicting evidence from papers
# ============================================================================

class ContradictionResolver:
    """
    Resolve conflicts when papers disagree on dosages or recommendations.
    Prioritizes by: recency × quality × sample size
    """
    
    @classmethod
    def resolve_dosage_conflicts(
        cls,
        papers: List[Dict[str, Any]],
        intervention: str
    ) -> Dict[str, Any]:
        """
        When papers give different dosages, determine best evidence.
        
        Returns:
            {
                'recommended_dosage': str,
                'confidence': 'high' | 'medium' | 'low',
                'evidence_level': str,
                'supporting_papers': List[pmid],
                'conflicting_evidence': List[{pmid, dosage}]
            }
        """
        if not papers:
            return {
                'recommended_dosage': None,
                'confidence': 'low',
                'evidence_level': 'insufficient',
                'supporting_papers': [],
                'conflicting_evidence': []
            }
        
        # Sort papers by quality score (highest first)
        scored_papers = []
        for paper in papers:
            quality = paper.get('quality_score', {})
            if isinstance(quality, dict):
                score = quality.get('total_score', 0)
            else:
                score = 0
            scored_papers.append((score, paper))
        
        scored_papers.sort(key=lambda x: x[0], reverse=True)
        
        # Take recommendation from highest quality paper
        best_paper = scored_papers[0][1] if scored_papers else None
        
        result = {
            'confidence': 'high' if scored_papers[0][0] >= 15 else 'medium' if scored_papers[0][0] >= 12 else 'low',
            'evidence_level': best_paper.get('quality_score', {}).get('study_type', 'unknown') if best_paper else 'unknown',
            'supporting_papers': [p[1].get('pmid') for p in scored_papers[:3]],
            'conflicting_evidence': []
        }
        
        # Note if there are lower-quality conflicting papers
        if len(scored_papers) > 1:
            for score, paper in scored_papers[1:]:
                if score < scored_papers[0][0] - 3:  # Significantly lower quality
                    result['conflicting_evidence'].append({
                        'pmid': paper.get('pmid'),
                        'quality_score': score,
                        'note': 'Lower quality evidence - not used for recommendation'
                    })
        
        return result


# ============================================================================
# EVIDENCE THRESHOLD CHECKING (Priority 1 - #5)
# Don't recommend without sufficient evidence
# ============================================================================

class EvidenceThresholdChecker:
    """
    Ensure minimum evidence quality before making recommendations.
    Different thresholds for different recommendation types.
    """
    
    # Minimum papers required by recommendation type
    MIN_PAPERS_REQUIRED = {
        'supplement_dosage': 2,   # Higher bar for supplement dosing
        'food_recommendation': 1, # Can suggest with one good paper
        'exercise_protocol': 1,   # One good protocol paper
        'mindfulness': 1,         # Lower bar for low-risk interventions
    }
    
    # Minimum average quality score
    MIN_QUALITY_THRESHOLD = {
        'supplement_dosage': 14,
        'food_recommendation': 10,
        'exercise_protocol': 10,
        'mindfulness': 8,
    }
    
    @classmethod
    def check_evidence_sufficiency(
        cls,
        papers: List[Dict[str, Any]],
        recommendation_type: str
    ) -> Dict[str, Any]:
        """
        Check if there's enough quality evidence to make a recommendation.
        
        Returns:
            {
                'sufficient': bool,
                'papers_found': int,
                'papers_required': int,
                'avg_quality': float,
                'min_quality_required': int,
                'message': str
            }
        """
        min_papers = cls.MIN_PAPERS_REQUIRED.get(recommendation_type, 1)
        
        # ADAPTED: Use semantic similarity score instead of missing quality_score
        # Papers with score >= 0.7 are considered "quality" (high semantic relevance)
        quality_papers = [
            p for p in papers 
            if p.get('score', 0) >= 0.7  # Semantic similarity threshold
        ]
        
        papers_found = len(quality_papers)
        
        # Calculate average semantic score as quality proxy
        if quality_papers:
            avg_quality = sum(
                p.get('score', 0) * 100  # Convert to 0-100 scale for display
                for p in quality_papers
            ) / len(quality_papers)
        else:
            # If no high-quality papers, check if we have any papers at all
            if papers:
                avg_quality = sum(p.get('score', 0) * 100 for p in papers) / len(papers)
                papers_found = len(papers)  # Use all papers if none meet threshold
            else:
                avg_quality = 0
        
        # More lenient threshold - just need papers
        sufficient = papers_found >= min_papers
        
        if sufficient:
            message = f"Evidence found: {papers_found} relevant papers (avg relevance: {avg_quality:.0f}%)"
        elif papers_found == 0:
            message = "Limited research available. Consult a specialist."
        else:
            message = f"Limited evidence ({papers_found}/{min_papers} papers). Recommendation provided with caution."
        
        return {
            'sufficient': sufficient,
            'papers_found': papers_found,
            'papers_required': min_papers,
            'avg_quality': round(avg_quality, 1),
            'min_quality_required': 70,  # Represents 70% semantic similarity
            'message': message
        }
    
    @classmethod
    def get_evidence_summary(cls, papers: List[Dict[str, Any]]) -> str:
        """Generate human-readable evidence summary"""
        if not papers:
            return "No research papers found for this recommendation."
        
        quality_papers = [p for p in papers if p.get('quality_score', {}).get('total_score', 0) >= 12]
        
        if not quality_papers:
            return f"Found {len(papers)} papers, but none met our quality threshold."
        
        # Count study types
        study_types = {}
        for p in quality_papers:
            st = p.get('quality_score', {}).get('study_type', 'unknown')
            study_types[st] = study_types.get(st, 0) + 1
        
        # Build summary
        parts = []
        if 'randomized_controlled_trial' in study_types:
            parts.append(f"{study_types['randomized_controlled_trial']} RCT(s)")
        if 'systematic_review' in study_types or 'meta_analysis' in study_types:
            count = study_types.get('systematic_review', 0) + study_types.get('meta_analysis', 0)
            parts.append(f"{count} systematic review(s)")
        if 'cohort_study' in study_types:
            parts.append(f"{study_types['cohort_study']} cohort study/studies")
        
        if parts:
            return f"Based on {len(quality_papers)} quality papers including: {', '.join(parts)}."
        else:
            return f"Based on {len(quality_papers)} research papers."


# ============================================================================
# PAPER VERSION CONTROL (Priority 2 - #6)
# Track retractions, updates, and schedule refreshes
# ============================================================================

class PaperVersionControl:
    """
    Version control for research papers.
    Checks for retractions, updates, and manages paper refresh schedules.
    """
    
    # Known retracted papers (manually maintained + API checks)
    _retracted_pmids: set = set()
    _paper_versions: Dict[str, Dict[str, Any]] = {}
    
    # PubMed retraction search terms
    RETRACTION_INDICATORS = [
        'retracted',
        'retraction',
        'withdrawn',
        'expression of concern',
        'correction',
        'erratum'
    ]
    
    @classmethod
    async def check_retraction_status(cls, pmid: str) -> Dict[str, Any]:
        """
        Check if a paper has been retracted using PubMed API.
        
        Returns:
            {
                'pmid': str,
                'is_retracted': bool,
                'retraction_type': str or None,
                'retraction_date': str or None,
                'is_valid': bool
            }
        """
        import requests
        import xml.etree.ElementTree as ET
        
        result = {
            'pmid': pmid,
            'is_retracted': False,
            'retraction_type': None,
            'retraction_date': None,
            'is_valid': True
        }
        
        # Check local cache first
        if pmid in cls._retracted_pmids:
            result['is_retracted'] = True
            result['is_valid'] = False
            return result
        
        try:
            # Query PubMed for retraction status
            search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&retmode=xml"
            
            response = requests.get(search_url, timeout=10)
            if response.status_code != 200:
                return result
            
            root = ET.fromstring(response.content)
            
            # Check for retraction in publication types
            pub_types = root.findall(".//PublicationType")
            for pub_type in pub_types:
                if pub_type.text and any(indicator in pub_type.text.lower() for indicator in cls.RETRACTION_INDICATORS):
                    result['is_retracted'] = True
                    result['retraction_type'] = pub_type.text
                    result['is_valid'] = False
                    cls._retracted_pmids.add(pmid)
                    break
            
            # Check for comments/corrections
            comments = root.findall(".//CommentsCorrections")
            for comment in comments:
                ref_type = comment.get('RefType', '')
                if any(indicator in ref_type.lower() for indicator in cls.RETRACTION_INDICATORS):
                    result['is_retracted'] = True
                    result['retraction_type'] = ref_type
                    result['is_valid'] = False
                    cls._retracted_pmids.add(pmid)
                    break
            
        except Exception as e:
            logger.warning(f"Failed to check retraction status for {pmid}: {e}")
        
        return result
    
    @classmethod
    async def check_batch_retractions(cls, pmids: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Check retraction status for multiple papers.
        
        Returns:
            {pmid: retraction_status_dict}
        """
        results = {}
        
        for pmid in pmids:
            try:
                status = await cls.check_retraction_status(pmid)
                results[pmid] = status
                
                if status['is_retracted']:
                    logger.warning(f"⚠️ Retracted paper detected: {pmid} - {status['retraction_type']}")
            except Exception as e:
                logger.error(f"Error checking retraction for {pmid}: {e}")
                results[pmid] = {'pmid': pmid, 'is_valid': True, 'error': str(e)}
        
        return results
    
    @classmethod
    def filter_valid_papers(cls, papers: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Filter out retracted papers.
        
        Returns:
            (valid_papers, retracted_papers)
        """
        valid = []
        retracted = []
        
        for paper in papers:
            pmid = paper.get('pmid')
            if pmid and pmid in cls._retracted_pmids:
                paper['retracted'] = True
                retracted.append(paper)
            else:
                valid.append(paper)
        
        if retracted:
            logger.warning(f"Filtered out {len(retracted)} retracted papers")
        
        return valid, retracted
    
    @classmethod
    def get_paper_version(cls, pmid: str) -> Optional[Dict[str, Any]]:
        """Get version info for a paper"""
        return cls._paper_versions.get(pmid)
    
    @classmethod
    def update_paper_version(cls, pmid: str, version_info: Dict[str, Any]):
        """Update version info for a paper"""
        cls._paper_versions[pmid] = {
            **version_info,
            'last_updated': datetime.utcnow().isoformat()
        }
    
    @classmethod
    def get_papers_needing_refresh(cls, max_age_days: int = 90) -> List[str]:
        """
        Get list of papers that haven't been refreshed recently.
        
        Args:
            max_age_days: Maximum days since last refresh
            
        Returns:
            List of PMIDs needing refresh
        """
        from datetime import timedelta
        
        cutoff_date = datetime.utcnow() - timedelta(days=max_age_days)
        needs_refresh = []
        
        for pmid, info in cls._paper_versions.items():
            last_updated = info.get('last_updated')
            if last_updated:
                try:
                    update_date = datetime.fromisoformat(last_updated.replace('Z', ''))
                    if update_date < cutoff_date:
                        needs_refresh.append(pmid)
                except:
                    needs_refresh.append(pmid)
            else:
                needs_refresh.append(pmid)
        
        return needs_refresh
    
    @classmethod
    def schedule_refresh_job(cls) -> Dict[str, Any]:
        """
        Get details for scheduling a paper refresh job.
        
        Returns configuration for a background job scheduler.
        """
        return {
            'job_name': 'paper_refresh',
            'schedule': 'quarterly',  # Every 3 months
            'cron': '0 0 1 */3 *',  # 1st of every 3rd month
            'tasks': [
                'check_retractions',
                'update_citations',
                'refresh_stale_papers',
                'index_new_research'
            ],
            'notification': {
                'on_retraction': True,
                'on_major_update': True,
                'email': 'admin@auvra.com'
            }
        }


# ============================================================================
# A/B TESTING FOR PROMPTS (Priority 3 - #7)
# Test different prompt variants for medical accuracy
# ============================================================================

class PromptExperiments:
    """
    A/B testing system for LLM prompts.
    Tests different prompt variants to optimize medical accuracy and user satisfaction.
    """
    
    # Experiment definitions
    EXPERIMENTS = {
        'prompt_style_v1': {
            'name': 'Prompt Style Comparison',
            'description': 'Compare conservative vs detailed prompt styles',
            'status': 'active',
            'variants': {
                'conservative': {
                    'weight': 0.33,
                    'description': 'Cautious language, emphasis on limitations'
                },
                'balanced': {
                    'weight': 0.34,
                    'description': 'Standard clinical language'
                },
                'detailed': {
                    'weight': 0.33,
                    'description': 'Comprehensive with mechanisms'
                }
            },
            'metrics': ['user_satisfaction', 'citation_accuracy', 'engagement']
        }
    }
    
    # Prompt templates for each variant
    PROMPT_TEMPLATES = {
        'conservative': '''
You are a cautious medical researcher specializing in women's hormone health. 
Your primary concern is SAFETY over comprehensiveness.

CRITICAL GUIDELINES:
- Only recommend interventions with STRONG evidence (≥2 randomized controlled trials)
- Always mention limitations and uncertainty
- Clearly state when evidence is preliminary
- Recommend consulting healthcare providers for anything beyond basic dietary changes
- Err on the side of caution for dosages

When evidence is limited, say: "Limited research available. Consult a healthcare provider."
''',
        
        'balanced': '''
You are a clinical nutritionist specializing in women's hormone health.
Balance evidence-based recommendations with practical, actionable advice.

GUIDELINES:
- Recommend evidence-based interventions from peer-reviewed research
- Note when evidence is preliminary vs well-established
- Provide specific, actionable recommendations with dosages
- Include safety considerations and contraindications
- Reference the strength of supporting evidence
''',
        
        'detailed': '''
You are a medical librarian and researcher synthesizing the latest research on women's hormone health.
Provide comprehensive, detailed recommendations with full context.

GUIDELINES:
- Provide detailed dosages, mechanisms of action, and expected timelines
- Explain WHY each recommendation works (biological mechanisms)
- Reference specific studies with their strength (RCT, cohort, etc.)
- Include quality of evidence ratings for each recommendation
- Provide alternative options when available
- Note synergistic effects between recommendations
'''
    }
    
    # User assignments (simple hash-based assignment)
    _user_assignments: Dict[str, Dict[str, str]] = {}
    
    # Metrics tracking
    _metrics: Dict[str, List[Dict[str, Any]]] = {}
    
    @classmethod
    def assign_variant(cls, user_id: str, experiment_id: str = 'prompt_style_v1') -> str:
        """
        Assign a user to an experiment variant.
        Uses consistent hashing for stable assignments.
        
        Returns:
            variant_name: The assigned variant
        """
        import hashlib
        
        # Check for existing assignment
        if user_id in cls._user_assignments:
            if experiment_id in cls._user_assignments[user_id]:
                return cls._user_assignments[user_id][experiment_id]
        
        # Get experiment config
        experiment = cls.EXPERIMENTS.get(experiment_id)
        if not experiment or experiment.get('status') != 'active':
            return 'balanced'  # Default
        
        # Hash-based assignment for consistency
        hash_input = f"{user_id}:{experiment_id}"
        hash_value = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
        assignment_value = (hash_value % 100) / 100.0
        
        # Assign based on weights
        cumulative = 0.0
        assigned_variant = 'balanced'  # Default
        
        for variant_name, variant_config in experiment['variants'].items():
            cumulative += variant_config['weight']
            if assignment_value < cumulative:
                assigned_variant = variant_name
                break
        
        # Store assignment
        if user_id not in cls._user_assignments:
            cls._user_assignments[user_id] = {}
        cls._user_assignments[user_id][experiment_id] = assigned_variant
        
        logger.info(f"User {user_id[:8]}... assigned to variant '{assigned_variant}' for {experiment_id}")
        
        return assigned_variant
    
    @classmethod
    def get_prompt_modifier(cls, user_id: str, experiment_id: str = 'prompt_style_v1') -> str:
        """
        Get the prompt modifier text for a user's assigned variant.
        
        Returns:
            The prompt template text to prepend to the base prompt
        """
        variant = cls.assign_variant(user_id, experiment_id)
        return cls.PROMPT_TEMPLATES.get(variant, cls.PROMPT_TEMPLATES['balanced'])
    
    @classmethod
    def track_metric(
        cls,
        user_id: str,
        experiment_id: str,
        metric_name: str,
        metric_value: float,
        metadata: Dict[str, Any] = None
    ):
        """
        Track a metric for experiment analysis.
        
        Args:
            user_id: User identifier
            experiment_id: Experiment name
            metric_name: Name of the metric (e.g., 'user_satisfaction')
            metric_value: Numeric value (0.0 to 1.0 for satisfaction, etc.)
            metadata: Additional context
        """
        variant = cls._user_assignments.get(user_id, {}).get(experiment_id, 'unknown')
        
        entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'user_id_hash': user_id[:8] + '...',
            'experiment_id': experiment_id,
            'variant': variant,
            'metric_name': metric_name,
            'metric_value': metric_value,
            'metadata': metadata or {}
        }
        
        key = f"{experiment_id}:{metric_name}"
        if key not in cls._metrics:
            cls._metrics[key] = []
        cls._metrics[key].append(entry)
        
        logger.debug(f"Tracked metric: {metric_name}={metric_value} for variant={variant}")
    
    @classmethod
    def get_experiment_results(cls, experiment_id: str) -> Dict[str, Any]:
        """
        Get aggregated results for an experiment.
        
        Returns:
            {
                'experiment_id': str,
                'variants': {
                    'variant_name': {
                        'sample_size': int,
                        'metrics': {
                            'metric_name': {'mean': float, 'std': float}
                        }
                    }
                },
                'winner': str or None,
                'significance': float
            }
        """
        import statistics
        
        results = {
            'experiment_id': experiment_id,
            'variants': {},
            'winner': None,
            'significance': 0.0
        }
        
        experiment = cls.EXPERIMENTS.get(experiment_id)
        if not experiment:
            return results
        
        # Aggregate by variant
        for variant_name in experiment['variants'].keys():
            variant_data = {
                'sample_size': 0,
                'metrics': {}
            }
            
            for metric_name in experiment['metrics']:
                key = f"{experiment_id}:{metric_name}"
                entries = [e for e in cls._metrics.get(key, []) if e['variant'] == variant_name]
                
                if entries:
                    values = [e['metric_value'] for e in entries]
                    variant_data['sample_size'] = len(values)
                    variant_data['metrics'][metric_name] = {
                        'mean': statistics.mean(values),
                        'std': statistics.stdev(values) if len(values) > 1 else 0
                    }
            
            results['variants'][variant_name] = variant_data
        
        # Determine winner (simple: highest mean for primary metric)
        primary_metric = experiment['metrics'][0] if experiment['metrics'] else None
        if primary_metric:
            best_variant = None
            best_mean = -1
            
            for variant_name, variant_data in results['variants'].items():
                mean = variant_data['metrics'].get(primary_metric, {}).get('mean', 0)
                if mean > best_mean:
                    best_mean = mean
                    best_variant = variant_name
            
            results['winner'] = best_variant
        
        return results
    
    @classmethod
    def get_active_experiments(cls) -> List[Dict[str, Any]]:
        """Get list of all active experiments"""
        return [
            {'id': exp_id, **exp_config}
            for exp_id, exp_config in cls.EXPERIMENTS.items()
            if exp_config.get('status') == 'active'
        ]

