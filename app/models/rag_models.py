from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from datetime import datetime

class Author(BaseModel):
    """Author information"""
    last_name: str
    first_name: str
    affiliation: Optional[str] = None

class PublicationDate(BaseModel):
    """Publication date information"""
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None

class StudyArm(BaseModel):
    """Study arm information"""
    arm_name: Optional[str] = ""
    intervention_type: Optional[List[str]] = []
    target_symptoms: Optional[List[str]] = []
    hormone_focus: Optional[List[str]] = []
    participant_count: Optional[int] = 0
    duration: Optional[str] = ""
    description: Optional[str] = ""

class ChunkStudyArms(BaseModel):
    """Model for storing chunk study arms information in PostgreSQL"""
    chunk_id: str
    paper_id: str  # pmid or pmcid
    section_type: Optional[str] = ""
    chunk_summary: Optional[str] = ""
    study_arms: List[StudyArm] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class PaperMeta(BaseModel):
    title: str
    content: str  # abstract or full text
    url: str
    date: str
    source: Optional[str] = None  # Added: source information
    
    # Paper identifiers
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    doi: Optional[str] = None
    
    # Author information
    authors: Optional[List[Author]] = []
    
    # Journal information
    journal: Optional[str] = None
    journal_issn: Optional[str] = None
    
    # Publication year information
    publication_year: Optional[int] = None
    
    # Additional metadata
    mesh_terms: Optional[List[str]] = []
    abstract: Optional[str] = None
    # Section tag information (passed from original paper)
    source_paper: Optional[Dict[str, Any]] = None
    # Priority score removed - calculated at search time

class ChunkedPaper(BaseModel):
    chunk_id: str
    text: str
    source_url: str
    title: str
    start_idx: int
    end_idx: int
    
    # Paper identifiers
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    doi: Optional[str] = None
    
    # Author information
    authors: Optional[List[Author]] = []
    
    # Journal information
    journal: Optional[str] = None
    journal_issn: Optional[str] = None
    
    # Publication year information
    publication_year: Optional[int] = None
    
    # Additional metadata
    mesh_terms: Optional[List[str]] = []
    abstract: Optional[str] = None
    # Section tag information (passed from original paper)
    source_paper: Optional[Dict[str, Any]] = None
    
    # Section information (used when chunk spans multiple sections)
    section_info: Optional[Dict[str, Any]] = None  # Section information where chunk belongs
    overlapping_sections: Optional[List[Dict[str, Any]]] = []  # All sections the chunk spans
    
    # Priority score removed - calculated at search time

class TaggedChunk(BaseModel):
    chunk_id: str
    text: str
    
    # New fields (2nd tagging refactoring)
    section_type: Optional[str] = ""
    chunk_summary: Optional[str] = ""
    study_arms: Optional[List[Dict[str, Any]]] = []
    
    # Fields extracted from Study arm
    condition_disease: Optional[List[str]] = []
    target: Optional[List[str]] = []
    target_age_distribution: Optional[Dict[str, int]] = {}
    num_of_participants: Optional[int] = 0
    study_duration: Optional[str] = ""
    hormone_focus: Optional[List[str]] = []
    target_symptoms: Optional[List[str]] = []
    primary_outcome: Optional[List[str]] = []
    
    # Existing fields (maintained for compatibility)
    study_type: Optional[str] = ""
    is_human_study: Optional[bool] = False
    participant_count: Optional[int] = 0
    symptoms_focus: Optional[List[str]] = []
    relevance_score: Optional[int] = 0
    intervention_type: Optional[List[str]] = []
    risk_of_bias: Optional[str] = ""
    citation_count: Optional[int] = 0
    menstrual_phase: Optional[str] = ""
    primary_outcome_text: Optional[str] = ""
    
    # Existing field (backward compatibility)
    tags: Optional[List[str]] = []
    title: Optional[str] = ""
    url: Optional[str] = ""

class EmbeddingResult(BaseModel):
    id: str
    values: List[float]
    metadata: dict

# RAG API request models
class RAGRequest(BaseModel):
    """
    RAG request model - simplified version
    """
    # Use default values only, no user input required
    pass

class RAGResponse(BaseModel):
    """
    RAG response model
    """
    success: bool
    message: str
    papers_processed: int
    papers_stored: int
    processing_time: float 