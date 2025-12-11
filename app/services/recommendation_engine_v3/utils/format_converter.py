"""
V3 Format Converter
==================

Converts V3 recommendation format to mobile app expected format.

V3 Output Fields:
- title, purpose, action, priority, contraindications
- evidence_sources (with PMIDs, titles, relevance scores)
- root_causes, food_items, timeline, frequency

Mobile App Expected Fields:
- title, purpose, specificAction, priority, contraindications
- conditions, symptoms, hormones (tags)
- food_amounts, food_items, exercise_durations, etc.
- researchBacking: {summary, studies}
- citation_verified, rag_version
"""

from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)


def convert_v3_to_mobile_format(
    v3_recommendations: List[Dict[str, Any]],
    category: str
) -> List[Dict[str, Any]]:
    """
    Convert V3 recommendation format to mobile app expected format.
    
    Args:
        v3_recommendations: List of V3 format recommendations
        category: Category (food/movement/mindfulness)
    
    Returns:
        List of recommendations in mobile app format
    """
    converted = []
    
    for rec in v3_recommendations:
        try:
            mobile_rec = _convert_single_recommendation(rec, category)
            converted.append(mobile_rec)
        except Exception as e:
            logger.error(f"Failed to convert recommendation: {e}")
            # Skip failed conversions but continue processing
            continue
    
    return converted


def _convert_single_recommendation(
    rec: Dict[str, Any],
    category: str
) -> Dict[str, Any]:
    """Convert a single V3 recommendation to mobile format"""
    
    # Extract evidence sources for research backing
    evidence_sources = rec.get('evidence_sources', [])
    citations = rec.get('citations', [])
    
    # Build research backing from evidence sources or citations
    research_studies = []
    
    # Try evidence_sources first
    for source in evidence_sources:
        study = {
            'pmid': source.get('pmid'),
            'title': source.get('title', ''),
            'relevance': source.get('relevance_score', 0.7),
            'journal': source.get('journal', ''),
            'year': source.get('year', '')
        }
        if study['pmid']:
            research_studies.append(study)
    
    # Fall back to citations if evidence_sources is empty
    if not research_studies:
        for cit in citations:
            study = {
                'pmid': cit.get('pmid'),
                'title': cit.get('title', ''),
                'relevance': cit.get('relevance_score', 0.7),
                'journal': cit.get('journal', ''),
                'year': cit.get('year', '')
            }
            if study['pmid']:
                research_studies.append(study)
    
    # Generate research summary
    research_summary = _generate_research_summary(rec, research_studies)
    
    # Map root_causes to conditions
    root_causes = rec.get('root_causes', [])
    conditions = _map_root_causes_to_conditions(root_causes)
    
    # Extract symptoms and hormones
    symptoms = rec.get('symptoms', [])
    hormones = rec.get('hormones', [])
    
    # If no specific symptoms/hormones, derive from root_causes
    if not hormones and root_causes:
        hormones = _derive_hormones_from_root_causes(root_causes)
    
    # Build mobile format recommendation
    mobile_rec = {
        # Core fields
        'title': rec.get('title', ''),
        'purpose': rec.get('purpose', ''),
        'specificAction': rec.get('action') or rec.get('specific_action') or rec.get('specificAction', ''),
        'priority': rec.get('priority', 'medium'),
        'contraindications': rec.get('contraindications', []),
        
        # Tags
        'conditions': conditions,
        'symptoms': symptoms,
        'hormones': hormones,
        
        # Category-specific fields
        **_get_category_specific_fields(rec, category),
        
        # Timing
        'frequency_detail': rec.get('frequency', rec.get('frequency_detail', '')),
        'duration_weeks': _parse_duration_weeks(rec.get('timeline', '')),
        'optimal_times': rec.get('optimal_times', []),
        
        # Research backing (key for mobile app)
        'researchBacking': {
            'summary': research_summary,
            'studies': research_studies
        },
        
        # V3 markers
        'citation_verified': len(research_studies) > 0,
        'rag_version': 'v3_engine',
        'evidence_grade': rec.get('evidence_grade', rec.get('evidence_strength', 'moderate')),
        'relevance_score': rec.get('relevance_score', 0.7),
    }
    
    return mobile_rec


def _generate_research_summary(rec: Dict, studies: List[Dict]) -> str:
    """Generate a research summary from recommendation and studies"""
    
    # If recommendation already has evidence summary
    if rec.get('evidence_summary'):
        return rec['evidence_summary']
    
    study_count = len(studies)
    if study_count == 0:
        return f"Recommendation based on clinical guidelines for {rec.get('title', 'this intervention')}."
    
    # Build summary from PMIDs
    pmids = [str(s.get('pmid')) for s in studies if s.get('pmid')]
    evidence_strength = rec.get('evidence_strength', rec.get('evidence_grade', 'moderate'))
    
    summary_parts = []
    summary_parts.append(f"Based on {study_count} research {'study' if study_count == 1 else 'studies'}.")
    
    if evidence_strength:
        summary_parts.append(f"Evidence strength: {evidence_strength}.")
    
    if pmids:
        if len(pmids) <= 3:
            summary_parts.append(f"Key studies: PMIDs {', '.join(pmids)}.")
        else:
            summary_parts.append(f"Key studies: PMIDs {', '.join(pmids[:3])}, and {len(pmids) - 3} more.")
    
    return ' '.join(summary_parts)


def _map_root_causes_to_conditions(root_causes: List[str]) -> List[str]:
    """Map V3 root causes to mobile app conditions format"""
    
    mapping = {
        'insulin_resistance': 'Insulin Resistance',
        'blood_sugar_instability': 'Blood Sugar Issues',
        'androgen_high': 'High Androgens',
        'hirsutism': 'Hirsutism',
        'acne': 'Acne',
        'inflammation_chronic': 'Chronic Inflammation',
        'inflammation': 'Inflammation',
        'cortisol_high': 'High Cortisol',
        'cortisol_dysregulation': 'Cortisol Imbalance',
        'stress': 'Stress-related',
        'weight_gain': 'Weight Management',
        'fatigue': 'Fatigue',
        'irregular_periods': 'Irregular Periods',
        'pcos': 'PCOS',
    }
    
    conditions = []
    for cause in root_causes:
        if cause.lower() in mapping:
            conditions.append(mapping[cause.lower()])
        else:
            # Clean up and capitalize
            conditions.append(cause.replace('_', ' ').title())
    
    return conditions


def _derive_hormones_from_root_causes(root_causes: List[str]) -> List[str]:
    """Derive affected hormones from root causes"""
    
    hormone_mapping = {
        'insulin_resistance': ['Insulin'],
        'androgen_high': ['Testosterone', 'DHEA-S'],
        'androgens_high': ['Testosterone', 'DHEA-S'],
        'cortisol_high': ['Cortisol'],
        'cortisol_dysregulation': ['Cortisol'],
        'estrogen_dominance': ['Estrogen', 'Progesterone'],
        'estrogen_high': ['Estrogen'],
        'estrogen_low': ['Estrogen'],
        'progesterone_low': ['Progesterone'],
        'thyroid': ['TSH', 'T3', 'T4'],
        'thyroid_low': ['TSH', 'T3', 'T4'],
        # New mappings for common root causes
        'hormone_balance': ['Progesterone', 'Estrogen', 'Cortisol'],
        'general_wellness': ['Cortisol', 'Insulin'],
        'stress': ['Cortisol'],
        'inflammation': ['Cortisol', 'Insulin'],
        'weight_management': ['Insulin', 'Cortisol'],
        'fatigue': ['Cortisol', 'Thyroid'],
        'mood': ['Progesterone', 'Estrogen', 'Cortisol'],
        'sleep': ['Cortisol', 'Melatonin'],
        'acne': ['Androgens', 'Testosterone'],
        'hirsutism': ['Androgens', 'Testosterone'],
    }
    
    hormones = set()
    for cause in root_causes:
        if cause.lower() in hormone_mapping:
            hormones.update(hormone_mapping[cause.lower()])
    
    return list(hormones)


def _get_category_specific_fields(rec: Dict, category: str) -> Dict[str, Any]:
    """Extract category-specific fields"""
    
    if category == 'food':
        return {
            'food_amounts': rec.get('food_amounts', rec.get('amounts', [])),
            'food_items': rec.get('food_items', rec.get('foods', [])),
        }
    elif category == 'movement':
        return {
            'exercise_durations': rec.get('exercise_durations', rec.get('durations', [])),
            'exercise_types': rec.get('exercise_types', rec.get('types', [])),
            'exercise_intensities': rec.get('exercise_intensities', rec.get('intensities', [])),
        }
    elif category == 'mindfulness':
        return {
            'mindfulness_durations': rec.get('mindfulness_durations', rec.get('durations', [])),
            'mindfulness_techniques': rec.get('mindfulness_techniques', rec.get('techniques', [])),
        }
    
    return {}


def _parse_duration_weeks(timeline: str) -> int:
    """Parse timeline string to duration in weeks"""
    
    if not timeline:
        return 8  # Default
    
    timeline_lower = timeline.lower()
    
    # Handle "8-12 weeks" format
    if 'week' in timeline_lower:
        import re
        numbers = re.findall(r'\d+', timeline_lower)
        if numbers:
            # Return the middle/higher value for ranges
            nums = [int(n) for n in numbers]
            return max(nums)
    
    # Handle "2-3 months" format
    if 'month' in timeline_lower:
        import re
        numbers = re.findall(r'\d+', timeline_lower)
        if numbers:
            months = max(int(n) for n in numbers)
            return months * 4  # Convert to weeks
    
    return 8  # Default


def convert_v3_response_to_mobile_format(
    v3_response: Any,
    category: str
) -> List[Dict[str, Any]]:
    """
    Convert full V3RecommendationResponse to mobile format.
    
    This is the main entry point for converting V3 output.
    
    Args:
        v3_response: V3RecommendationResponse dataclass or dict
        category: Target category (food/movement/mindfulness)
    
    Returns:
        List of recommendations in mobile app format
    """
    
    # Handle dataclass
    if hasattr(v3_response, '__dataclass_fields__'):
        v3_response = {
            'nutrition_recommendations': v3_response.nutrition_recommendations,
            'movement_recommendations': v3_response.movement_recommendations,
            'mindfulness_recommendations': v3_response.mindfulness_recommendations,
        }
    
    # Map category to V3 field name
    category_mapping = {
        'food': 'nutrition_recommendations',
        'movement': 'movement_recommendations',
        'mindfulness': 'mindfulness_recommendations',
    }
    
    v3_field = category_mapping.get(category, f'{category}_recommendations')
    v3_recs = v3_response.get(v3_field, [])
    
    if not v3_recs:
        logger.warning(f"No recommendations found for category '{category}' in V3 response")
        return []
    
    return convert_v3_to_mobile_format(v3_recs, category)
