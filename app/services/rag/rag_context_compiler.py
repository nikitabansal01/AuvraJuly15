"""
RAG Context Compiler Module
Formats retrieved papers into LLM-consumable context with explicit citations
"""

from typing import List, Dict, Any, Tuple
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class Citation:
    """Represents a citation that can be verified"""
    reference_id: str  # [1], [2], etc.
    pmid: str
    title: str
    authors: List[str]
    journal: str
    year: int
    study_type: str
    participant_count: int


class ContextCompiler:
    """
    Compiles retrieved papers into formatted context for LLM generation
    with explicit, verifiable citations
    """
    
    MAX_CONTEXT_TOKENS = 4000  # Reserve tokens for prompt + response
    MAX_PAPERS = 5  # Top papers to include
    MAX_CHUNK_LENGTH = 500  # Max characters per chunk excerpt
    
    def __init__(self):
        pass
    
    def compile(
        self,
        papers: List[Dict[str, Any]],
        category: str,
        user_profile: Dict[str, Any]
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Compile retrieved papers into formatted context
        
        Args:
            papers: List of retrieved paper chunks
            category: food, movement, or mindfulness
            user_profile: User's health profile
            
        Returns:
            Tuple of (formatted_context, citation_metadata_list)
        """
        if not papers:
            logger.warning("⚠️ Context Compiler: No papers to compile")
            return "", []
        
        # Take top papers
        top_papers = papers[:self.MAX_PAPERS]
        
        context_parts = []
        citations = []
        
        for i, paper in enumerate(top_papers):
            ref_id = f"[{i + 1}]"
            
            # Extract key information
            pmid = paper.get("pmid", "N/A")
            title = paper.get("title", "Unknown Title")
            authors = paper.get("authors", [])
            journal = paper.get("journal", "Unknown Journal")
            year = paper.get("publication_year", "N/A")
            study_type = paper.get("study_type", "study")
            participants = paper.get("participant_count", "N/A")
            
            # Format authors (first 3 + et al.)
            if isinstance(authors, list) and len(authors) > 0:
                if len(authors) > 3:
                    authors_str = f"{', '.join(authors[:3])}, et al."
                else:
                    authors_str = ", ".join(authors)
            else:
                authors_str = "Unknown Authors"
            
            # Get chunk text or abstract
            chunk_text = paper.get("text", "")
            abstract = paper.get("abstract", "")
            chunk_summary = paper.get("chunk_summary", "")
            
            # Extract key findings for this category
            key_findings = self._extract_key_findings(
                chunk_text or abstract,
                chunk_summary,
                category
            )
            
            # Format the paper entry
            context_parts.append(f"""
{ref_id} {title} ({year})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Authors: {authors_str}
• Journal: {journal}
• PMID: {pmid}
• Study Type: {study_type}
• Participants: {participants if participants else 'N/A'}

Key Findings for {category.upper()}:
{key_findings}
""")
            
            # Store citation metadata for validation
            citations.append({
                "reference_id": ref_id,
                "pmid": pmid,
                "title": title,
                "authors": authors if isinstance(authors, list) else [],
                "journal": journal,
                "year": year,
                "study_type": study_type,
                "participant_count": participants if isinstance(participants, int) else 0
            })
        
        context = "\n".join(context_parts)
        
        logger.info(f"📄 Context Compiler: Compiled {len(citations)} papers for {category}")
        
        return context, citations
    
    def _extract_key_findings(
        self,
        text: str,
        summary: str,
        category: str
    ) -> str:
        """Extract key findings relevant to the category"""
        # Use summary if available
        if summary:
            return summary[:self.MAX_CHUNK_LENGTH]
        
        if not text:
            return "No detailed findings available."
        
        # Truncate and clean text
        findings = text[:self.MAX_CHUNK_LENGTH]
        if len(text) > self.MAX_CHUNK_LENGTH:
            findings = findings.rsplit(" ", 1)[0] + "..."
        
        return findings
    
    def create_rag_prompt(
        self,
        user_profile: Dict[str, Any],
        category: str,
        research_context: str,
        citations: List[Dict[str, Any]],
        hormone_analysis: Dict[str, Any]
    ) -> str:
        """
        Create the complete RAG-enhanced prompt for recommendation generation
        
        Args:
            user_profile: User's health profile
            category: food, movement, or mindfulness
            research_context: Compiled research context
            citations: List of citation metadata
            hormone_analysis: Root cause engine output
            
        Returns:
            Complete prompt string
        """
        # Extract profile details
        conditions = ", ".join(user_profile.get("conditions", [])) or "None specified"
        symptoms = ", ".join(user_profile.get("symptoms", [])) or "None specified"
        primary_hormone = hormone_analysis.get("primary_imbalance", "unknown")
        primary_level = hormone_analysis.get("primary_level", "unknown")
        secondary_hormones = ", ".join(hormone_analysis.get("secondary_imbalances", [])) or "None"
        
        # Build citation reference list for validation instruction
        valid_pmids = [c["pmid"] for c in citations if c.get("pmid")]
        valid_refs = [c["reference_id"] for c in citations]
        
        prompt = f'''You are a medical AI generating evidence-based {category} recommendations for women's hormone health.

## USER PROFILE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Conditions: {conditions}
• Symptoms: {symptoms}
• Primary Hormone Imbalance: {primary_hormone} ({primary_level})
• Secondary Imbalances: {secondary_hormones}
• Age: {user_profile.get("age", "N/A")}

## CATEGORY: {category.upper()}

## RESEARCH EVIDENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The following are REAL research papers from PubMed. Use ONLY these sources.

{research_context}

## CRITICAL CITATION REQUIREMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. ✅ ONLY cite studies using references {', '.join(valid_refs)}
2. ✅ Use the EXACT PMIDs provided: {', '.join(valid_pmids)}
3. ❌ NEVER invent studies, PMIDs, authors, or journals not listed above
4. ❌ NEVER cite studies not in the provided evidence
5. ✅ Each recommendation MUST reference at least one study from above
6. ✅ State the SPECIFIC finding from the cited study

## OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Return a JSON array. Each recommendation must include:

```json
[
  {{
    "title": "1-2 word title",
    "purpose": "What this does and why it helps the user's specific condition",
    "specificAction": "Exact amounts/duration FROM THE RESEARCH (e.g., '30g daily')",
    "frequency": "Daily/Weekly",
    "frequency_detail": "daily:1 or weekly:3",
    "duration_weeks": 12,
    "expectedTimeline": "X weeks (from study)",
    "priority": "high/medium/low",
    "citation": {{
      "reference": "[1]",
      "pmid": "12345678",
      "study_finding": "Study found X% improvement in Y over Z weeks"
    }},
    "hormones": ["{primary_hormone}"],
    "conditions": ["PCOS"],
    "symptoms": ["relevant symptoms"],
    "contraindications": ["any from research"],
    "{'food_amounts' if category == 'food' else 'exercise_durations' if category == 'movement' else 'mindfulness_durations'}": ["specific amounts"],
    "{'food_items' if category == 'food' else 'exercise_types' if category == 'movement' else 'mindfulness_techniques'}": ["specific items"]
  }}
]
```

Generate 2-5 recommendations that are DIRECTLY supported by the evidence provided.
If insufficient evidence exists for a recommendation, DO NOT include it.

IMPORTANT: Only include hormones from user's analysis: {primary_hormone}, {secondary_hormones}
'''
        
        return prompt


# Singleton instance
_compiler_instance = None

def get_context_compiler() -> ContextCompiler:
    global _compiler_instance
    if _compiler_instance is None:
        _compiler_instance = ContextCompiler()
    return _compiler_instance
