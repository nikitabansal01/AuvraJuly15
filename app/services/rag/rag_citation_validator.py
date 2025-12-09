"""
RAG Citation Validator Module
Validates generated citations against retrieved papers
"""

from typing import List, Dict, Any, Tuple
import logging
import re

logger = logging.getLogger(__name__)


class CitationValidator:
    """
    Validates that generated recommendations cite real papers from retrieval
    and filters out hallucinated citations
    """
    
    def __init__(self):
        pass
    
    def validate(
        self,
        recommendations: List[Dict[str, Any]],
        valid_citations: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Validate recommendations against retrieved papers
        
        Args:
            recommendations: List of generated recommendations
            valid_citations: List of citation metadata from retrieval
            
        Returns:
            Tuple of (validated_recommendations, validation_stats)
        """
        if not recommendations:
            return [], {"total": 0, "verified": 0, "filtered": 0}
        
        # Build lookup sets
        valid_pmids = {str(c.get("pmid", "")).strip() for c in valid_citations if c.get("pmid")}
        valid_refs = {c.get("reference_id", "") for c in valid_citations}
        valid_titles = {c.get("title", "").lower().strip() for c in valid_citations if c.get("title")}
        
        logger.info(f"🔍 Citation Validator: Checking against {len(valid_pmids)} valid PMIDs")
        
        validated = []
        filtered_count = 0
        
        for rec in recommendations:
            citation = rec.get("citation", {})
            
            # Try to verify by PMID first
            pmid = str(citation.get("pmid", "")).strip()
            ref = citation.get("reference", "")
            
            is_verified = False
            verification_method = None
            
            # Method 1: PMID match
            if pmid and pmid in valid_pmids:
                is_verified = True
                verification_method = "pmid"
            
            # Method 2: Reference ID match
            elif ref and ref in valid_refs:
                is_verified = True
                verification_method = "reference"
            
            # Method 3: Fuzzy title match (fallback)
            else:
                study_finding = citation.get("study_finding", "").lower()
                for valid_title in valid_titles:
                    if valid_title and len(valid_title) > 20:
                        # Check if significant part of title appears in finding
                        title_words = valid_title.split()[:5]
                        if all(word in study_finding for word in title_words if len(word) > 4):
                            is_verified = True
                            verification_method = "title_match"
                            break
            
            if is_verified:
                rec["citation_verified"] = True
                rec["citation_verification_method"] = verification_method
                validated.append(rec)
                logger.debug(f"✅ Citation verified via {verification_method}: {pmid or ref}")
            else:
                rec["citation_verified"] = False
                rec["citation_warning"] = "Citation could not be verified against retrieved papers"
                filtered_count += 1
                logger.warning(f"⚠️ Unverified citation filtered: PMID={pmid}, Ref={ref}")
                # Optionally include with warning for transparency
                # validated.append(rec)
        
        stats = {
            "total": len(recommendations),
            "verified": len(validated),
            "filtered": filtered_count,
            "verification_rate": len(validated) / len(recommendations) if recommendations else 0
        }
        
        logger.info(f"📊 Citation Validation: {stats['verified']}/{stats['total']} verified, {stats['filtered']} filtered")
        
        return validated, stats
    
    def enrich_citations(
        self,
        recommendations: List[Dict[str, Any]],
        valid_citations: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Enrich recommendations with full citation metadata from retrieval
        
        Args:
            recommendations: List of validated recommendations
            valid_citations: List of citation metadata
            
        Returns:
            Recommendations with enriched citation data
        """
        # Build PMID to citation lookup
        citation_lookup = {}
        for citation in valid_citations:
            pmid = str(citation.get("pmid", "")).strip()
            if pmid:
                citation_lookup[pmid] = citation
            ref = citation.get("reference_id", "")
            if ref:
                citation_lookup[ref] = citation
        
        for rec in recommendations:
            citation = rec.get("citation", {})
            pmid = str(citation.get("pmid", "")).strip()
            ref = citation.get("reference", "")
            
            # Find matching citation metadata
            full_citation = citation_lookup.get(pmid) or citation_lookup.get(ref)
            
            if full_citation:
                # Enrich with full metadata
                rec["researchBacking"] = {
                    "summary": citation.get("study_finding", ""),
                    "studies": [{
                        "title": full_citation.get("title", ""),
                        "authors": full_citation.get("authors", []),
                        "journal": full_citation.get("journal", ""),
                        "publicationYear": full_citation.get("year", 0),
                        "participantCount": full_citation.get("participant_count", 0),
                        "results": citation.get("study_finding", ""),
                        "pmid": full_citation.get("pmid", ""),
                        "verified": True
                    }]
                }
        
        return recommendations
    
    def calculate_faithfulness_score(
        self,
        recommendations: List[Dict[str, Any]],
        context: str
    ) -> float:
        """
        Calculate a basic faithfulness score for the recommendations
        
        Args:
            recommendations: List of recommendations
            context: The research context that was provided
            
        Returns:
            Faithfulness score between 0 and 1
        """
        if not recommendations:
            return 0.0
        
        context_lower = context.lower()
        
        total_claims = 0
        supported_claims = 0
        
        for rec in recommendations:
            # Check if specific action appears in context
            specific_action = rec.get("specificAction", "").lower()
            if specific_action:
                total_claims += 1
                # Look for key numbers/amounts in context
                numbers = re.findall(r'\d+(?:\.\d+)?', specific_action)
                if any(num in context_lower for num in numbers):
                    supported_claims += 1
            
            # Check if study finding references something in context
            citation = rec.get("citation", {})
            finding = citation.get("study_finding", "").lower()
            if finding and len(finding) > 20:
                total_claims += 1
                # Check if key phrases from finding appear in context
                key_phrases = finding.split()[:10]
                matches = sum(1 for phrase in key_phrases if phrase in context_lower)
                if matches >= 3:
                    supported_claims += 1
        
        if total_claims == 0:
            return 0.0
        
        score = supported_claims / total_claims
        logger.info(f"📊 Faithfulness Score: {score:.2f} ({supported_claims}/{total_claims} claims supported)")
        
        return score


# Singleton instance
_validator_instance = None

def get_citation_validator() -> CitationValidator:
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = CitationValidator()
    return _validator_instance
