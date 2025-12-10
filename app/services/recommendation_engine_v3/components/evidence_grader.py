"""
Evidence Grader - Research Quality Scoring
==========================================

Reusable component for grading the quality and relevance
of scientific evidence.
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum
import logging
import re

logger = logging.getLogger(__name__)


class StudyType(Enum):
    """Types of scientific studies ranked by evidence strength"""
    SYSTEMATIC_REVIEW = 10
    META_ANALYSIS = 9
    RCT = 8  # Randomized Controlled Trial
    COHORT = 6
    CASE_CONTROL = 5
    CROSS_SECTIONAL = 4
    CASE_REPORT = 2
    EXPERT_OPINION = 1
    UNKNOWN = 0


@dataclass
class EvidenceGrade:
    """Result of evidence grading"""
    overall_score: float  # 0.0 - 1.0
    study_quality: float
    relevance_score: float
    recency_score: float
    sample_size_score: float
    explanation: str
    grade_letter: str  # A, B, C, D, F
    confidence: str  # high, medium, low
    strengths: List[str]
    limitations: List[str]


class EvidenceGrader:
    """
    Grades scientific evidence quality for recommendation support.
    
    This component evaluates research papers based on:
    - Study type (RCT > observational > case study)
    - Sample size
    - Publication recency
    - Relevance to user's condition
    - Citation count (if available)
    """
    
    def __init__(self):
        self.study_type_patterns = {
            StudyType.SYSTEMATIC_REVIEW: [
                r'systematic review', r'systematic\s+literature\s+review'
            ],
            StudyType.META_ANALYSIS: [
                r'meta-analysis', r'meta\s+analysis', r'pooled analysis'
            ],
            StudyType.RCT: [
                r'randomized controlled', r'randomised controlled',
                r'rct', r'double-blind', r'placebo-controlled'
            ],
            StudyType.COHORT: [
                r'cohort study', r'prospective study', r'longitudinal'
            ],
            StudyType.CASE_CONTROL: [
                r'case-control', r'case control', r'retrospective'
            ],
            StudyType.CROSS_SECTIONAL: [
                r'cross-sectional', r'cross sectional', r'survey'
            ],
            StudyType.CASE_REPORT: [
                r'case report', r'case series', r'case study'
            ],
        }
        
    def grade_evidence(
        self,
        document: Dict[str, Any],
        user_context: Dict[str, Any] = None,
        target_conditions: List[str] = None
    ) -> EvidenceGrade:
        """
        Grade a single piece of evidence.
        
        Args:
            document: Paper/study metadata and content
            user_context: User's health profile for relevance scoring
            target_conditions: Conditions to check relevance against
        
        Returns:
            EvidenceGrade with detailed scoring
        """
        # Extract document info
        title = document.get('title', '').lower()
        abstract = document.get('abstract', document.get('text', '')).lower()
        year = document.get('year', document.get('publication_year', 0))
        sample_size = self._extract_sample_size(abstract)
        
        # Calculate component scores
        study_type = self._classify_study_type(title, abstract)
        study_quality = study_type.value / 10.0
        
        relevance_score = self._calculate_relevance(
            document, target_conditions or []
        )
        
        recency_score = self._calculate_recency(year)
        
        sample_size_score = self._score_sample_size(sample_size)
        
        # Weighted overall score
        overall_score = (
            study_quality * 0.35 +
            relevance_score * 0.30 +
            recency_score * 0.15 +
            sample_size_score * 0.20
        )
        
        # Determine grade letter
        grade_letter = self._score_to_letter(overall_score)
        
        # Determine confidence
        confidence = self._determine_confidence(
            study_type, sample_size, year
        )
        
        # Identify strengths and limitations
        strengths = []
        limitations = []
        
        if study_quality >= 0.8:
            strengths.append(f"High-quality {study_type.name} study design")
        elif study_quality < 0.5:
            limitations.append("Limited study design strength")
            
        if sample_size and sample_size > 100:
            strengths.append(f"Good sample size (n={sample_size})")
        elif sample_size and sample_size < 30:
            limitations.append(f"Small sample size (n={sample_size})")
            
        if recency_score > 0.8:
            strengths.append("Recent publication")
        elif recency_score < 0.4:
            limitations.append("Older study - may not reflect current evidence")
            
        if relevance_score > 0.7:
            strengths.append("Highly relevant to user's condition")
        elif relevance_score < 0.4:
            limitations.append("Limited relevance to specific condition")
        
        explanation = self._generate_explanation(
            study_type, overall_score, strengths, limitations
        )
        
        return EvidenceGrade(
            overall_score=round(overall_score, 2),
            study_quality=round(study_quality, 2),
            relevance_score=round(relevance_score, 2),
            recency_score=round(recency_score, 2),
            sample_size_score=round(sample_size_score, 2),
            explanation=explanation,
            grade_letter=grade_letter,
            confidence=confidence,
            strengths=strengths,
            limitations=limitations
        )
    
    def grade_multiple(
        self,
        documents: List[Dict[str, Any]],
        target_conditions: List[str] = None
    ) -> Dict[str, Any]:
        """
        Grade multiple documents and provide aggregate analysis.
        """
        grades = []
        for doc in documents:
            grade = self.grade_evidence(
                doc, target_conditions=target_conditions
            )
            grades.append({
                'document': doc,
                'grade': grade
            })
        
        # Sort by overall score
        grades.sort(
            key=lambda x: x['grade'].overall_score, 
            reverse=True
        )
        
        # Calculate aggregate metrics
        if grades:
            avg_score = sum(g['grade'].overall_score for g in grades) / len(grades)
            high_quality_count = sum(
                1 for g in grades if g['grade'].grade_letter in ['A', 'B']
            )
            rct_count = sum(
                1 for g in grades 
                if g['grade'].study_quality >= 0.8
            )
        else:
            avg_score = 0
            high_quality_count = 0
            rct_count = 0
        
        return {
            'graded_documents': grades,
            'aggregate': {
                'average_score': round(avg_score, 2),
                'high_quality_count': high_quality_count,
                'rct_count': rct_count,
                'total_documents': len(documents),
                'evidence_strength': self._aggregate_strength(grades)
            }
        }
    
    def _classify_study_type(self, title: str, abstract: str) -> StudyType:
        """Classify the study type based on text patterns"""
        text = f"{title} {abstract}"
        
        for study_type, patterns in self.study_type_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    return study_type
        
        return StudyType.UNKNOWN
    
    def _extract_sample_size(self, text: str) -> Optional[int]:
        """Extract sample size from study text"""
        patterns = [
            r'n\s*=\s*(\d+)',
            r'(\d+)\s*participants',
            r'(\d+)\s*subjects',
            r'(\d+)\s*patients',
            r'(\d+)\s*women',
            r'sample\s*size\s*(?:of\s*)?(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        
        return None
    
    def _calculate_relevance(
        self, 
        document: Dict[str, Any],
        target_conditions: List[str]
    ) -> float:
        """Calculate relevance to target conditions"""
        if not target_conditions:
            return 0.5  # Default moderate relevance
        
        text = f"{document.get('title', '')} {document.get('abstract', '')}".lower()
        
        matches = sum(
            1 for condition in target_conditions
            if condition.lower() in text
        )
        
        return min(matches / max(len(target_conditions), 1), 1.0)
    
    def _calculate_recency(self, year: int) -> float:
        """Score based on publication year"""
        from datetime import datetime
        current_year = datetime.now().year
        
        if not year or year < 1990:
            return 0.2
        
        age = current_year - year
        
        if age <= 2:
            return 1.0
        elif age <= 5:
            return 0.8
        elif age <= 10:
            return 0.6
        elif age <= 15:
            return 0.4
        else:
            return 0.2
    
    def _score_sample_size(self, sample_size: Optional[int]) -> float:
        """Score based on sample size"""
        if not sample_size:
            return 0.5  # Unknown
        
        if sample_size >= 1000:
            return 1.0
        elif sample_size >= 500:
            return 0.9
        elif sample_size >= 100:
            return 0.7
        elif sample_size >= 50:
            return 0.5
        elif sample_size >= 20:
            return 0.3
        else:
            return 0.1
    
    def _score_to_letter(self, score: float) -> str:
        """Convert numeric score to letter grade"""
        if score >= 0.9:
            return 'A'
        elif score >= 0.75:
            return 'B'
        elif score >= 0.6:
            return 'C'
        elif score >= 0.4:
            return 'D'
        else:
            return 'F'
    
    def _determine_confidence(
        self, 
        study_type: StudyType,
        sample_size: Optional[int],
        year: int
    ) -> str:
        """Determine overall confidence level"""
        score = 0
        
        if study_type in [StudyType.SYSTEMATIC_REVIEW, StudyType.META_ANALYSIS, StudyType.RCT]:
            score += 2
        elif study_type in [StudyType.COHORT, StudyType.CASE_CONTROL]:
            score += 1
        
        if sample_size and sample_size > 100:
            score += 1
        
        from datetime import datetime
        if year and (datetime.now().year - year) <= 5:
            score += 1
        
        if score >= 3:
            return 'high'
        elif score >= 2:
            return 'medium'
        else:
            return 'low'
    
    def _generate_explanation(
        self,
        study_type: StudyType,
        score: float,
        strengths: List[str],
        limitations: List[str]
    ) -> str:
        """Generate human-readable explanation"""
        quality_desc = 'high' if score >= 0.7 else 'moderate' if score >= 0.5 else 'limited'
        
        explanation = f"This {study_type.name.replace('_', ' ').lower()} provides {quality_desc}-quality evidence."
        
        if strengths:
            explanation += f" Strengths: {'; '.join(strengths[:2])}."
        
        if limitations:
            explanation += f" Note: {'; '.join(limitations[:2])}."
        
        return explanation
    
    def _aggregate_strength(self, grades: List[Dict]) -> str:
        """Determine overall evidence strength"""
        if not grades:
            return 'insufficient'
        
        high_count = sum(
            1 for g in grades 
            if g['grade'].grade_letter in ['A', 'B']
        )
        
        if high_count >= 3:
            return 'strong'
        elif high_count >= 1:
            return 'moderate'
        elif len(grades) >= 3:
            return 'weak'
        else:
            return 'insufficient'
