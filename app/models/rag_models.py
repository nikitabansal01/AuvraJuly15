from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict

class Author(BaseModel):
    """저자 정보"""
    last_name: str
    first_name: str
    affiliation: Optional[str] = None

class PublicationDate(BaseModel):
    """출판일자 정보"""
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None

class PaperMeta(BaseModel):
    title: str
    content: str  # abstract or full text
    url: str
    date: str
    source: Optional[str] = None  # 추가: 출처 정보
    
    # 논문 식별자
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    doi: Optional[str] = None
    
    # 저자 정보
    authors: Optional[List[Author]] = []
    
    # 저널 정보
    journal: Optional[str] = None
    journal_issn: Optional[str] = None
    
    # 출판년도 정보
    publication_year: Optional[int] = None
    
    # 추가 메타데이터
    mesh_terms: Optional[List[str]] = []
    abstract: Optional[str] = None
    # 섹션 태그 정보 (원본 논문에서 전달)
    source_paper: Optional[Dict[str, Any]] = None
    # 우선순위 점수 제거 - 검색 시점에 계산

class ChunkedPaper(BaseModel):
    chunk_id: str
    text: str
    source_url: str
    title: str
    start_idx: int
    end_idx: int
    
    # 논문 식별자
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    doi: Optional[str] = None
    
    # 저자 정보
    authors: Optional[List[Author]] = []
    
    # 저널 정보
    journal: Optional[str] = None
    journal_issn: Optional[str] = None
    
    # 출판년도 정보
    publication_year: Optional[int] = None
    
    # 추가 메타데이터
    mesh_terms: Optional[List[str]] = []
    abstract: Optional[str] = None
    # 섹션 태그 정보 (원본 논문에서 전달)
    source_paper: Optional[Dict[str, Any]] = None
    
    # 섹션 정보 (청크가 여러 섹션에 걸쳐있을 때 사용)
    section_info: Optional[Dict[str, Any]] = None  # 청크가 속한 섹션 정보
    overlapping_sections: Optional[List[Dict[str, Any]]] = []  # 청크가 걸쳐있는 모든 섹션들
    
    # 우선순위 점수 제거 - 검색 시점에 계산

class TaggedChunk(BaseModel):
    chunk_id: str
    text: str
    
    # 새로운 필드들 (2차 태깅 리팩토링)
    section_type: Optional[str] = ""
    chunk_summary: Optional[str] = ""
    study_arms: Optional[List[Dict[str, Any]]] = []
    
    # Study arm에서 추출되는 필드들
    condition_disease: Optional[List[str]] = []
    target: Optional[List[str]] = []
    target_age_distribution: Optional[Dict[str, int]] = {}
    num_of_participants: Optional[int] = 0
    study_duration: Optional[str] = ""
    hormone_focus: Optional[List[str]] = []
    target_symptoms: Optional[List[str]] = []
    primary_outcome: Optional[List[str]] = []
    
    # 기존 필드들 (호환성을 위해 유지)
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
    
    # 기존 필드 (하위 호환성)
    tags: Optional[List[str]] = []
    title: Optional[str] = ""
    url: Optional[str] = ""

class EmbeddingResult(BaseModel):
    id: str
    values: List[float]
    metadata: dict

# RAG API 요청 모델
class RAGRequest(BaseModel):
    """
    RAG 요청 모델 - 단순화된 버전
    """
    # 기본값만 사용, 사용자 입력 불필요
    pass

class RAGResponse(BaseModel):
    """
    RAG 응답 모델
    """
    success: bool
    message: str
    papers_processed: int
    papers_stored: int
    processing_time: float 