"""
BM25 + Pinecone Vector 하이브리드 검색 서비스
HQ vs LQ 모델 성능 비교 및 복잡한 가중치 시스템 지원
"""
import os
import json
import logging
import nltk
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from rank_bm25 import BM25Okapi
from datetime import datetime
import pickle
import asyncio
from functools import lru_cache

from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

# NLTK 토크나이저 다운로드 (한 번만)
try:
    nltk.download('punkt', quiet=True)
    from nltk.tokenize import word_tokenize
except:
    logger.warning("NLTK 토크나이저 사용 불가, 기본 split 사용")
    def word_tokenize(text):
        return text.lower().split()

class HybridSearchService:
    def __init__(self, data_dir: str = "data/bm25", service_type: str = "combined"):
        self.data_dir = Path(data_dir)
        self.service_type = service_type  # "combined", "hq_only", "lq_only"
        self.documents: List[Dict[str, Any]] = []
        self.bm25_indexes: Dict[str, BM25Okapi] = {}
        self.document_map: Dict[str, int] = {}  # id -> index 매핑
        self.is_loaded = False
        
        # 필드별 가중치 설정 (실험용으로 다양하게 설정 가능)
        self.field_weights = {
            # 기본 텍스트 필드
            "title": 4.0,
            "abstract": 3.0,
            "text": 1.0,  # 본문
            "section_title": 2.0,
            "chunk_summary": 2.5,
            "study_arms_text": 2.0,
            
            # 태그 기반 필드들
            "doc_summary": 2.0,
            "intervention_type_text": 3.0,  # 리스트를 텍스트로 변환
            "hormone_focus_text": 3.0,
            "symptoms_focus_text": 3.0,
            "doc_study_type_text": 2.0,
            "doc_condition_disease_text": 3.5,
            "doc_target_symptoms_text": 3.0,
            "mesh_terms_text": 1.5,
            
            # 섹션별 가중치 (methods, results, discussion 등)
            "methods_text": 0.5,
            "results_text": 1.5,
            "discussion_text": 1.3,
            "conclusion_text": 1.4,
            "introduction_text": 0.8,
        }
    
    def tokenize_text(self, text: str) -> List[str]:
        """텍스트 토크나이징 (한영 혼용 고려)"""
        if not text:
            return []
        
        try:
            # NLTK 토크나이저 사용
            tokens = word_tokenize(text.lower())
            # 한글, 영문, 숫자만 유지
            tokens = [token for token in tokens if any(c.isalnum() for c in token)]
            return tokens
        except:
            # 대체 토크나이저
            return [word.strip().lower() for word in text.split() if word.strip()]
    
    def load_data_from_json(self, json_file: str) -> bool:
        """JSON 파일에서 데이터 로드"""
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 새로운 형식 (metadata + documents) 또는 이전 형식 (documents 배열) 지원
            if isinstance(data, dict) and "documents" in data:
                self.documents = data["documents"]
                logger.info(f"메타데이터 포함 형식으로 로드: {data.get('metadata', {}).get('document_count', 0)}개 문서")
            elif isinstance(data, list):
                self.documents = data
                logger.info(f"기본 형식으로 로드: {len(data)}개 문서")
            else:
                logger.error(f"지원하지 않는 JSON 형식: {json_file}")
                return False
            
            # 문서 ID 매핑 생성
            self.document_map = {doc["id"]: i for i, doc in enumerate(self.documents)}
            
            logger.info(f"데이터 로드 완료: {len(self.documents)}개 문서")
            return True
            
        except Exception as e:
            logger.error(f"JSON 파일 로드 실패: {json_file}, 오류: {e}")
            return False
    
    def prepare_corpus_for_field(self, field_name: str) -> List[List[str]]:
        """특정 필드에 대한 코퍼스 준비"""
        corpus = []
        
        for doc in self.documents:
            # 기본 텍스트 필드
            if field_name in ["title", "abstract", "text", "section_title", "chunk_summary", "study_arms_text", "doc_summary"]:
                text = doc.get(field_name, "")
            
            # 리스트 태그를 텍스트로 변환
            elif field_name == "intervention_type_text":
                interventions = doc.get("intervention_type", []) + doc.get("doc_intervention_type", [])
                text = " ".join(interventions) if interventions else ""
                
            elif field_name == "hormone_focus_text":
                hormones = doc.get("hormone_focus", []) + doc.get("doc_hormone_focus", [])
                text = " ".join(hormones) if hormones else ""
                
            elif field_name == "symptoms_focus_text":
                symptoms = doc.get("symptoms_focus", []) + doc.get("doc_target_symptoms", [])
                text = " ".join(symptoms) if symptoms else ""
                
            elif field_name == "doc_study_type_text":
                study_types = doc.get("doc_study_type", [])
                text = " ".join(study_types) if study_types else ""
                
            elif field_name == "doc_condition_disease_text":
                conditions = doc.get("doc_condition_disease", [])
                text = " ".join(conditions) if conditions else ""
                
            elif field_name == "doc_target_symptoms_text":
                target_symptoms = doc.get("doc_target_symptoms", [])
                text = " ".join(target_symptoms) if target_symptoms else ""
                
            elif field_name == "mesh_terms_text":
                mesh_terms = doc.get("mesh_terms", [])
                text = " ".join(mesh_terms) if mesh_terms else ""
            
            # 섹션별 텍스트 (섹션 타입에 따라 분류)
            elif field_name.endswith("_text"):
                section_type = field_name.replace("_text", "")
                doc_section_type = doc.get("chunk_section_type", "").lower()
                if doc_section_type == section_type:
                    text = doc.get("text", "")
                else:
                    text = ""
            else:
                text = ""
            
            # 토크나이징
            tokens = self.tokenize_text(str(text))
            corpus.append(tokens)
        
        return corpus
    
    def build_bm25_indexes(self):
        """모든 필드에 대한 BM25 인덱스 구축"""
        logger.info("BM25 인덱스 구축 시작...")
        
        for field_name in self.field_weights.keys():
            logger.info(f"필드 '{field_name}' 인덱스 구축 중...")
            
            # 코퍼스 준비
            corpus = self.prepare_corpus_for_field(field_name)
            
            # 비어있지 않은 문서가 있는지 확인
            non_empty_count = sum(1 for tokens in corpus if tokens)
            logger.info(f"필드 '{field_name}': {non_empty_count}/{len(corpus)}개 문서에 내용 있음")
            
            if non_empty_count > 0:
                # BM25 인덱스 구축
                bm25 = BM25Okapi(corpus)
                self.bm25_indexes[field_name] = bm25
                logger.info(f"필드 '{field_name}' 인덱스 구축 완료")
            else:
                logger.warning(f"필드 '{field_name}': 내용이 없어 인덱스 건너뜀")
        
        logger.info(f"BM25 인덱스 구축 완료: {len(self.bm25_indexes)}개 필드")
    
    def save_indexes(self, cache_file: str):
        """BM25 인덱스를 파일로 저장 (콜드 스타트 방지)"""
        try:
            cache_data = {
                "indexes": self.bm25_indexes,
                "documents": self.documents,
                "document_map": self.document_map,
                "field_weights": self.field_weights,
                "timestamp": datetime.now().isoformat()
            }
            
            with open(cache_file, 'wb') as f:
                pickle.dump(cache_data, f)
            
            logger.info(f"BM25 인덱스 캐시 저장: {cache_file}")
            
        except Exception as e:
            logger.error(f"BM25 인덱스 캐시 저장 실패: {e}")
    
    def load_indexes(self, cache_file: str) -> bool:
        """캐시된 BM25 인덱스 로드"""
        try:
            if not os.path.exists(cache_file):
                return False
            
            with open(cache_file, 'rb') as f:
                cache_data = pickle.load(f)
            
            self.bm25_indexes = cache_data["indexes"]
            self.documents = cache_data["documents"]
            self.document_map = cache_data["document_map"]
            self.field_weights = cache_data.get("field_weights", self.field_weights)
            
            logger.info(f"BM25 인덱스 캐시 로드 완료: {len(self.bm25_indexes)}개 필드, {len(self.documents)}개 문서")
            return True
            
        except Exception as e:
            logger.error(f"BM25 인덱스 캐시 로드 실패: {e}")
            return False
    
    def initialize(self, json_file: str = None, force_rebuild: bool = False):
        """초기화 (데이터 로드 + 인덱스 구축)"""
        if self.is_loaded and not force_rebuild:
            return True
        
        # JSON 파일 자동 선택
        if json_file is None:
            json_files = list(self.data_dir.glob("combined_documents_*.json"))
            if json_files:
                json_file = str(sorted(json_files)[-1])  # 최신 파일
                logger.info(f"최신 데이터 파일 자동 선택: {json_file}")
            else:
                logger.error(f"데이터 파일을 찾을 수 없음: {self.data_dir}")
                return False
        
        cache_file = str(self.data_dir / "bm25_indexes.pkl")
        
        # 캐시 로드 시도
        if not force_rebuild and self.load_indexes(cache_file):
            self.is_loaded = True
            return True
        
        # 데이터 로드
        if not self.load_data_from_json(json_file):
            return False
        
        # BM25 인덱스 구축
        self.build_bm25_indexes()
        
        # 캐시 저장
        self.save_indexes(cache_file)
        
        self.is_loaded = True
        logger.info("하이브리드 검색 서비스 초기화 완료")
        return True
    
    def lexical_search(self, query: str, top_k: int = 50) -> List[Dict[str, Any]]:
        """BM25 기반 렉시컬 검색"""
        if not self.is_loaded:
            logger.error("서비스가 초기화되지 않음")
            return []
        
        query_tokens = self.tokenize_text(query)
        if not query_tokens:
            return []
        
        # 필드별 점수 계산
        field_scores = {}
        for field_name, bm25_index in self.bm25_indexes.items():
            try:
                scores = bm25_index.get_scores(query_tokens)
                field_scores[field_name] = scores
            except Exception as e:
                logger.warning(f"필드 '{field_name}' 점수 계산 실패: {e}")
                field_scores[field_name] = [0.0] * len(self.documents)
        
        # 가중합 계산
        final_scores = [0.0] * len(self.documents)
        for field_name, scores in field_scores.items():
            weight = self.field_weights.get(field_name, 1.0)
            for i, score in enumerate(scores):
                final_scores[i] += weight * score
        
        # 상위 top_k 결과
        doc_scores = [(i, score) for i, score in enumerate(final_scores)]
        doc_scores.sort(key=lambda x: x[1], reverse=True)
        
        results = []
        for i, score in doc_scores[:top_k]:
            if score > 0:  # 0점 제외
                doc = self.documents[i].copy()
                doc["bm25_score"] = float(score)
                results.append(doc)
        
        logger.info(f"BM25 검색 완료: {len(results)}개 결과 (쿼리: '{query[:50]}...')")
        return results
    
    async def dense_search(self, query: str, top_k: int = 50, namespace: str = None) -> List[Dict[str, Any]]:
        """Pinecone 기반 덴스 검색"""
        try:
            # 네임스페이스가 없으면 기본값 사용
            if namespace is None:
                namespace = RAGService.get_model_namespace()
            
            # 쿼리 임베딩 생성
            query_embedding = await RAGService.get_query_embedding(query)
            
            # Pinecone 검색
            index = RAGService.get_pinecone_client()
            search_results = index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True,
                namespace=namespace
            )
            
            # 결과 변환
            dense_results = []
            for match in search_results.matches:
                doc = {
                    "id": match.id,
                    "dense_score": float(match.score),
                    # 필요한 메타데이터만 추출
                    "title": match.metadata.get("title", ""),
                    "text": match.metadata.get("text", ""),
                    "url": match.metadata.get("url", ""),
                    "model_version": match.metadata.get("model_version", ""),
                }
                dense_results.append(doc)
            
            logger.info(f"Dense 검색 완료: {len(dense_results)}개 결과")
            return dense_results
            
        except Exception as e:
            logger.error(f"Dense 검색 실패: {e}")
            return []
    
    def rrf_fusion(self, dense_results: List[Dict], lexical_results: List[Dict], k: int = 60, top_k: int = 20) -> List[Dict[str, Any]]:
        """Reciprocal Rank Fusion (RRF)를 사용한 결과 융합"""
        
        # ID별 순위 매핑
        dense_rank = {doc["id"]: i for i, doc in enumerate(dense_results)}
        lexical_rank = {doc["id"]: i for i, doc in enumerate(lexical_results)}
        
        # 모든 문서 ID 수집
        all_ids = set(dense_rank.keys()) | set(lexical_rank.keys())
        
        # RRF 점수 계산
        fused_results = []
        for doc_id in all_ids:
            rrf_score = 0.0
            
            # Dense 검색 기여도
            if doc_id in dense_rank:
                rrf_score += 1.0 / (k + dense_rank[doc_id] + 1)
            
            # Lexical 검색 기여도
            if doc_id in lexical_rank:
                rrf_score += 1.0 / (k + lexical_rank[doc_id] + 1)
            
            # 원본 문서 정보 가져오기
            doc_info = None
            if doc_id in self.document_map:
                doc_info = self.documents[self.document_map[doc_id]].copy()
            else:
                # Dense 검색 결과에서 가져오기
                for d_doc in dense_results:
                    if d_doc["id"] == doc_id:
                        doc_info = d_doc.copy()
                        break
            
            if doc_info:
                doc_info["rrf_score"] = rrf_score
                doc_info["found_in"] = []
                
                if doc_id in dense_rank:
                    doc_info["found_in"].append("dense")
                    doc_info["dense_rank"] = dense_rank[doc_id]
                
                if doc_id in lexical_rank:
                    doc_info["found_in"].append("lexical")
                    doc_info["lexical_rank"] = lexical_rank[doc_id]
                
                fused_results.append(doc_info)
        
        # RRF 점수로 정렬
        fused_results.sort(key=lambda x: x["rrf_score"], reverse=True)
        
        logger.info(f"RRF 융합 완료: {len(fused_results)}개 결과")
        return fused_results[:top_k]
    
    def filter_results_by_model(self, results: List[Dict[str, Any]], model_filter: str = None) -> List[Dict[str, Any]]:
        """모델 버전으로 결과 필터링"""
        if not model_filter:
            return results
        
        filtered_results = []
        for result in results:
            model_version = result.get("model_version", "")
            
            # 모델 필터 매칭
            if model_filter == "hq" and "gpt-4o" in model_version:
                filtered_results.append(result)
            elif model_filter == "lq" and "gpt-3.5-turbo" in model_version:
                filtered_results.append(result)
            elif model_filter == model_version:
                filtered_results.append(result)
        
        return filtered_results

    async def hybrid_search(self, query: str, top_k: int = 20, lexical_k: int = 50, dense_k: int = 50, namespace: str = None, model_filter: str = None) -> Dict[str, Any]:
        """하이브리드 검색 (BM25 + Pinecone + RRF)"""
        
        if not self.is_loaded:
            logger.error("서비스가 초기화되지 않음")
            return {"error": "Service not initialized"}
        
        logger.info(f"하이브리드 검색 시작: '{query}' (namespace: {namespace}, model_filter: {model_filter})")
        
        # 병렬 검색 실행
        lexical_task = asyncio.create_task(
            asyncio.to_thread(self.lexical_search, query, lexical_k)
        )
        dense_task = asyncio.create_task(
            self.dense_search, query, dense_k, namespace
        )
        
        lexical_results, dense_results = await asyncio.gather(lexical_task, dense_task)
        
        # 모델 필터링 적용
        if model_filter:
            lexical_results = self.filter_results_by_model(lexical_results, model_filter)
            dense_results = self.filter_results_by_model(dense_results, model_filter)
            logger.info(f"모델 필터링 적용: lexical={len(lexical_results)}, dense={len(dense_results)}")
        
        # RRF 융합
        fused_results = self.rrf_fusion(dense_results, lexical_results, k=60, top_k=top_k)
        
        # 결과 통계
        result_stats = {
            "total_results": len(fused_results),
            "lexical_only": len([r for r in fused_results if r["found_in"] == ["lexical"]]),
            "dense_only": len([r for r in fused_results if r["found_in"] == ["dense"]]),
            "both": len([r for r in fused_results if len(r["found_in"]) == 2]),
            "lexical_total": len(lexical_results),
            "dense_total": len(dense_results),
            "model_filter": model_filter,
        }
        
        return {
            "query": query,
            "results": fused_results,
            "stats": result_stats,
            "timestamp": datetime.now().isoformat()
        }

# 전역 서비스 인스턴스 (싱글톤)
_hybrid_search_service = None

def get_hybrid_search_service() -> HybridSearchService:
    """하이브리드 검색 서비스 인스턴스 반환 (싱글톤)"""
    global _hybrid_search_service
    
    if _hybrid_search_service is None:
        _hybrid_search_service = HybridSearchService()
        # 초기화는 별도로 호출해야 함
    
    return _hybrid_search_service