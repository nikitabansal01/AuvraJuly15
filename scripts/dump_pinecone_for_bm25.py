#!/usr/bin/env python3
"""
Pinecone 데이터를 BM25 검색용 JSON으로 덤프하는 스크립트
HQ(gpt-4o) vs LQ(gpt-3.5-turbo) 모델 성능 비교를 위한 데이터 추출
모든 메타데이터와 태그를 포함하여 복잡한 가중치 실험 가능
"""
import os
import sys
import json
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

# 프로젝트 루트를 Python path에 추가
sys.path.append(str(Path(__file__).parent.parent))

# .env 파일 로드 (중요!)
from dotenv import load_dotenv
load_dotenv()

from app.services.rag_service import RAGService
from app.core.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PineconeDumper:
    def __init__(self):
        self.index = None
        
    def connect_pinecone(self):
        """Pinecone 클라이언트 초기화"""
        try:
            self.index = RAGService.get_pinecone_client()
            logger.info("Pinecone 클라이언트 연결 성공")
            return True
        except Exception as e:
            logger.error(f"Pinecone 연결 실패: {e}")
            return False
    
    def get_namespace_stats(self, namespace: str) -> Dict[str, Any]:
        """네임스페이스 통계 정보 조회"""
        try:
            stats = self.index.describe_index_stats()
            ns_info = stats.namespaces.get(namespace, {})
            return {
                "namespace": namespace,
                "vector_count": ns_info.vector_count if hasattr(ns_info, 'vector_count') else 0,
                "total_vectors": stats.total_vector_count
            }
        except Exception as e:
            logger.error(f"네임스페이스 통계 조회 실패: {e}")
            return {"namespace": namespace, "vector_count": 0, "total_vectors": 0}
    
    def extract_all_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """모든 메타데이터를 안전하게 추출"""
        doc = {}
        
        # 기본 텍스트 필드들 (BM25 검색용)
        text_fields = {
            "title": metadata.get("title", ""),
            "abstract": metadata.get("abstract", ""),
            "text": metadata.get("text", ""),  # 청크 본문
            "section_title": metadata.get("section_title", ""),
            "chunk_summary": metadata.get("chunk_summary", ""),
            "study_arms_text": metadata.get("study_arms_text", ""),
        }
        doc.update(text_fields)
        
        # 식별자 및 기본 정보
        identifiers = {
            "url": metadata.get("url", ""),
            "pmid": metadata.get("pmid", ""),
            "pmcid": metadata.get("pmcid", ""),
            "doi": metadata.get("doi", ""),
            "model_version": metadata.get("model_version", ""),
            "tagging_timestamp": metadata.get("tagging_timestamp", ""),
            "start_idx": metadata.get("start_idx", 0),
            "end_idx": metadata.get("end_idx", 0),
        }
        doc.update(identifiers)
        
        # 저널 및 출판 정보
        publication_info = {
            "journal": metadata.get("journal", ""),
            "journal_issn": metadata.get("journal_issn", ""),
            "publication_year": metadata.get("publication_year", 0),
            "authors": metadata.get("authors", []),
            "mesh_terms": metadata.get("mesh_terms", []),
        }
        doc.update(publication_info)
        
        # 섹션 정보 (가중치 계산용)
        section_info = {
            "section_type": metadata.get("section_type", ""),
            "chunk_section_type": metadata.get("chunk_section_type", ""),
            "section_priority": metadata.get("section_priority", 0),
            "overlap_ratio": metadata.get("overlap_ratio", 0.0),
            "overlapping_sections": metadata.get("overlapping_sections", []),
        }
        doc.update(section_info)
        
        # 문서 레벨 태그 (doc_*)
        doc_level_tags = {
            "doc_study_type": metadata.get("doc_study_type", []),
            "doc_condition_disease": metadata.get("doc_condition_disease", []),
            "doc_target": metadata.get("doc_target", []),
            "doc_target_age_distribution": metadata.get("doc_target_age_distribution", []),
            "doc_num_of_participants": metadata.get("doc_num_of_participants", 0),
            "doc_study_duration": metadata.get("doc_study_duration", ""),
            "doc_intervention_type": metadata.get("doc_intervention_type", []),
            "doc_hormone_focus": metadata.get("doc_hormone_focus", []),
            "doc_target_symptoms": metadata.get("doc_target_symptoms", []),
            "doc_risk_of_bias": metadata.get("doc_risk_of_bias", ""),
            "doc_summary": metadata.get("doc_summary", ""),
        }
        doc.update(doc_level_tags)
        
        # 청크 레벨 태그
        chunk_level_tags = {
            "intervention_type": metadata.get("intervention_type", []),
            "symptoms_focus": metadata.get("symptoms_focus", []),
            "hormone_focus": metadata.get("hormone_focus", []),
        }
        doc.update(chunk_level_tags)
        
        # 추가 필드들 (혹시 놓친 것들)
        additional_fields = {}
        for key, value in metadata.items():
            if key not in doc:
                # 안전하게 값 처리
                if isinstance(value, (str, int, float, bool, list)):
                    additional_fields[key] = value
                elif isinstance(value, dict):
                    additional_fields[key] = value
                else:
                    additional_fields[key] = str(value)
        
        doc.update(additional_fields)
        
        return doc
    
    def extract_vectors_from_namespace(self, namespace: str, batch_size: int = 100) -> List[Dict[str, Any]]:
        """특정 네임스페이스에서 모든 벡터 추출 (모든 메타데이터 포함)"""
        logger.info(f"네임스페이스 '{namespace}'에서 벡터 추출 시작...")
        
        # 네임스페이스 통계 확인
        stats = self.get_namespace_stats(namespace)
        total_count = stats["vector_count"]
        logger.info(f"총 {total_count}개 벡터 예상")
        
        documents = []
        
        try:
            # 더미 쿼리로 모든 벡터를 가져오는 방법
            # Pinecone은 scan 기능이 없으므로 큰 top_k로 쿼리
            max_fetch = min(total_count if total_count > 0 else 10000, 10000)  # Pinecone 제한
            
            logger.info(f"최대 {max_fetch}개 벡터 요청 중...")
            
            response = self.index.query(
                vector=[0.0] * 1536,  # 더미 벡터 (text-embedding-3-small 차원)
                top_k=max_fetch,
                include_metadata=True,
                namespace=namespace
            )
            
            logger.info(f"실제 가져온 벡터 수: {len(response.matches)}")
            
            # 메타데이터 키 통계 수집
            all_keys = set()
            
            for i, match in enumerate(response.matches):
                # 모든 메타데이터 추출
                doc = self.extract_all_metadata(match.metadata)
                
                # ID와 유사도 점수 추가
                doc["id"] = match.id
                doc["similarity_score"] = float(match.score)
                
                documents.append(doc)
                all_keys.update(doc.keys())
                
                # 진행상황 로깅
                if (i + 1) % 100 == 0:
                    logger.info(f"처리 진행: {i + 1}/{len(response.matches)}")
            
            logger.info(f"네임스페이스 '{namespace}'에서 {len(documents)}개 문서 추출 완료")
            logger.info(f"발견된 메타데이터 키 수: {len(all_keys)}")
            logger.info(f"메타데이터 키 목록: {sorted(all_keys)}")
            
            return documents
            
        except Exception as e:
            logger.error(f"벡터 추출 중 오류: {e}")
            return documents
    
    def analyze_document_stats(self, documents: List[Dict]) -> Dict[str, Any]:
        """문서 통계 분석"""
        if not documents:
            return {}
        
        stats = {
            "total_documents": len(documents),
            "model_versions": {},
            "section_types": {},
            "chunk_section_types": {},
            "intervention_types": {},
            "hormone_focus": {},
            "doc_study_types": {},
            "doc_condition_disease": {},
            "publication_years": {},
            "text_lengths": [],
            "all_keys": set()
        }
        
        for doc in documents:
            # 모든 키 수집
            stats["all_keys"].update(doc.keys())
            
            # 모델 버전
            model = doc.get("model_version", "unknown")
            stats["model_versions"][model] = stats["model_versions"].get(model, 0) + 1
            
            # 섹션 타입들
            section = doc.get("section_type", "unknown")
            stats["section_types"][section] = stats["section_types"].get(section, 0) + 1
            
            chunk_section = doc.get("chunk_section_type", "unknown")
            stats["chunk_section_types"][chunk_section] = stats["chunk_section_types"].get(chunk_section, 0) + 1
            
            # 개입 타입
            interventions = doc.get("intervention_type", [])
            if isinstance(interventions, list):
                for intervention in interventions:
                    stats["intervention_types"][intervention] = stats["intervention_types"].get(intervention, 0) + 1
            
            # 호르몬 포커스
            hormones = doc.get("hormone_focus", [])
            if isinstance(hormones, list):
                for hormone in hormones:
                    stats["hormone_focus"][hormone] = stats["hormone_focus"].get(hormone, 0) + 1
            
            # 문서 연구 타입
            study_types = doc.get("doc_study_type", [])
            if isinstance(study_types, list):
                for study_type in study_types:
                    stats["doc_study_types"][study_type] = stats["doc_study_types"].get(study_type, 0) + 1
            
            # 문서 질병/상태
            conditions = doc.get("doc_condition_disease", [])
            if isinstance(conditions, list):
                for condition in conditions:
                    stats["doc_condition_disease"][condition] = stats["doc_condition_disease"].get(condition, 0) + 1
            
            # 출판년도
            year = doc.get("publication_year", 0)
            if year > 0:
                stats["publication_years"][year] = stats["publication_years"].get(year, 0) + 1
            
            # 텍스트 길이
            text_len = len(doc.get("text", ""))
            stats["text_lengths"].append(text_len)
        
        # 리스트를 정렬된 리스트로 변환
        stats["all_keys"] = sorted(stats["all_keys"])
        
        return stats
    
    def save_to_json(self, documents: List[Dict], output_file: str, stats: Dict[str, Any] = None):
        """JSON 파일로 저장"""
        try:
            # 메타데이터와 문서를 함께 저장
            output_data = {
                "metadata": {
                    "timestamp": datetime.now().isoformat(),
                    "document_count": len(documents),
                    "stats": stats
                },
                "documents": documents
            }
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"데이터가 {output_file}에 저장됨 ({len(documents)}개 문서)")
            
            # 통계 정보 출력
            if stats:
                logger.info("=== 덤프 통계 ===")
                logger.info(f"총 문서 수: {stats['total_documents']}")
                logger.info(f"발견된 메타데이터 키 수: {len(stats['all_keys'])}")
                
                logger.info("모델 버전별 분포:")
                for model, count in sorted(stats["model_versions"].items()):
                    logger.info(f"  {model}: {count}개")
                
                logger.info("청크 섹션 타입별 분포:")
                for section, count in sorted(stats["chunk_section_types"].items()):
                    if count > 0:
                        logger.info(f"  {section}: {count}개")
                
                logger.info("개입 타입별 분포:")
                for intervention, count in sorted(stats["intervention_types"].items()):
                    if count > 0:
                        logger.info(f"  {intervention}: {count}개")
                
                if stats["text_lengths"]:
                    avg_length = sum(stats["text_lengths"]) / len(stats["text_lengths"])
                    logger.info(f"평균 텍스트 길이: {avg_length:.1f}자")
            
        except Exception as e:
            logger.error(f"JSON 저장 실패: {e}")

def main():
    """메인 실행 함수"""
    dumper = PineconeDumper()
    
    # Pinecone 연결
    if not dumper.connect_pinecone():
        logger.error("Pinecone 연결 실패, 종료")
        return
    
    # 출력 디렉토리 생성
    output_dir = Path("data/bm25")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # HQ 모델 (gpt-4o) 데이터 덤프
    hq_namespace = "pcos-rag-gpt_4o"
    logger.info(f"=== HQ 모델 데이터 덤프: {hq_namespace} ===")
    hq_docs = dumper.extract_vectors_from_namespace(hq_namespace)
    hq_stats = dumper.analyze_document_stats(hq_docs) if hq_docs else {}
    
    if hq_docs:
        hq_file = output_dir / f"hq_documents_{timestamp}.json"
        dumper.save_to_json(hq_docs, str(hq_file), hq_stats)
    
    # LQ 모델 (gpt-3.5-turbo) 데이터 덤프  
    lq_namespace = "pcos-rag-gpt_3_5_turbo"
    logger.info(f"=== LQ 모델 데이터 덤프: {lq_namespace} ===")
    lq_docs = dumper.extract_vectors_from_namespace(lq_namespace)
    lq_stats = dumper.analyze_document_stats(lq_docs) if lq_docs else {}
    
    if lq_docs:
        lq_file = output_dir / f"lq_documents_{timestamp}.json"
        dumper.save_to_json(lq_docs, str(lq_file), lq_stats)
    
    # 통합 데이터셋 생성 (비교 테스트용)
    all_docs = hq_docs + lq_docs
    if all_docs:
        combined_stats = dumper.analyze_document_stats(all_docs)
        combined_file = output_dir / f"combined_documents_{timestamp}.json"
        dumper.save_to_json(all_docs, str(combined_file), combined_stats)
        
        logger.info("=== 전체 통계 ===")
        logger.info(f"HQ 문서: {len(hq_docs)}개")
        logger.info(f"LQ 문서: {len(lq_docs)}개") 
        logger.info(f"총 문서: {len(all_docs)}개")
        logger.info(f"전체 메타데이터 키: {len(combined_stats.get('all_keys', []))}개")
        logger.info(f"키 목록: {combined_stats.get('all_keys', [])}")
    
    logger.info("데이터 덤프 완료!")

if __name__ == "__main__":
    main()