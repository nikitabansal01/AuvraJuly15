하이브리드 검색 시스템 사용 가이드

BM25 + Pinecone Vector 하이브리드 검색을 통한 HQ vs LQ 모델 성능 비교

## 🚀 빠른 시작

### 1. 패키지 설치
```bash
pip install rank-bm25 nltk
# 또는
pip install -r requirements.txt
```

### 2. Pinecone 데이터 덤프
```bash
cd scripts
python dump_pinecone_for_bm25.py
```

이 스크립트는 다음 파일들을 생성합니다:
- `data/bm25/hq_documents_YYYYMMDD_HHMMSS.json` - HQ 모델 (gpt-4o) 데이터
- `data/bm25/lq_documents_YYYYMMDD_HHMMSS.json` - LQ 모델 (gpt-3.5-turbo) 데이터  
- `data/bm25/combined_documents_YYYYMMDD_HHMMSS.json` - 통합 데이터

### 3. 서비스 초기화
```bash
# FastAPI 서버 실행
uvicorn app.main:app --reload

# 하이브리드 검색 서비스 초기화
curl -X POST "http://localhost:8000/api/v1/hybrid-search/initialize"
```

## 📡 API 엔드포인트

### 기본 검색

#### 하이브리드 검색 (GET)
```bash
curl "http://localhost:8000/api/v1/hybrid-search/search?q=PCOS+diet+treatment&top_k=10"
```

#### 하이브리드 검색 (POST)
```bash
curl -X POST "http://localhost:8000/api/v1/hybrid-search/search" \
-H "Content-Type: application/json" \
-d '{
  "query": "PCOS diet treatment",
  "top_k": 10,
  "lexical_k": 50,
  "dense_k": 50,
  "field_weights": {
    "title": 5.0,
    "doc_condition_disease_text": 4.0
  }
}'
```

#### 렉시컬 검색만 (BM25)
```bash
curl "http://localhost:8000/api/v1/hybrid-search/lexical-only?q=PCOS+exercise&top_k=10"
```

#### 벡터 검색만 (Pinecone)
```bash
curl "http://localhost:8000/api/v1/hybrid-search/dense-only?q=PCOS+mindfulness&top_k=10"
```

### HQ vs LQ 모델 비교

```bash
curl -X POST "http://localhost:8000/api/v1/hybrid-search/compare-models" \
-H "Content-Type: application/json" \
-d '{
  "query": "PCOS insulin resistance treatment",
  "top_k": 10
}'
```

### 가중치 관리

#### 현재 가중치 조회
```bash
curl "http://localhost:8000/api/v1/hybrid-search/field-weights"
```

#### 가중치 업데이트
```bash
curl -X POST "http://localhost:8000/api/v1/hybrid-search/field-weights" \
-H "Content-Type: application/json" \
-d '{
  "title": 6.0,
  "abstract": 4.0,
  "doc_condition_disease_text": 5.0,
  "intervention_type_text": 4.5
}'
```

#### 서비스 상태 및 통계
```bash
curl "http://localhost:8000/api/v1/hybrid-search/health"
curl "http://localhost:8000/api/v1/hybrid-search/stats"
```

## 🎯 필드별 가중치 시스템

### 기본 텍스트 필드
- `title`: 4.0 - 논문 제목
- `abstract`: 3.0 - 초록
- `text`: 1.0 - 본문 (기준값)
- `section_title`: 2.0 - 섹션 제목
- `chunk_summary`: 2.5 - 청크 요약
- `study_arms_text`: 2.0 - 연구 팔 정보

### 태그 기반 필드 (리스트 → 텍스트 변환)
- `intervention_type_text`: 3.0 - 개입 유형
- `hormone_focus_text`: 3.0 - 호르몬 포커스
- `symptoms_focus_text`: 3.0 - 증상 포커스
- `doc_condition_disease_text`: 3.5 - 질병/상태
- `doc_target_symptoms_text`: 3.0 - 대상 증상
- `mesh_terms_text`: 1.5 - MeSH 용어

### 섹션별 가중치
- `methods_text`: 0.5 - 연구방법
- `results_text`: 1.5 - 결과
- `discussion_text`: 1.3 - 토론
- `conclusion_text`: 1.4 - 결론
- `introduction_text`: 0.8 - 도입

## 🔬 A/B 테스트 시나리오

### 1. 가중치 실험
```python
# 시나리오 A: 제목과 초록 강조
weights_a = {"title": 6.0, "abstract": 5.0, "text": 1.0}

# 시나리오 B: 의학적 태그 강조  
weights_b = {
    "doc_condition_disease_text": 5.0,
    "intervention_type_text": 4.5,
    "hormone_focus_text": 4.0
}

# 시나리오 C: 연구 결과 강조
weights_c = {
    "results_text": 3.0,
    "conclusion_text": 2.5,
    "discussion_text": 2.0
}
```

### 2. 모델 품질 비교
```bash
# HQ 모델 (50개 문서, 500개 청크)
curl -X POST ".../compare-models" -d '{"query": "PCOS metformin", "top_k": 20}'

# 결과 분석:
# - 공통 결과 개수 (common_results)
# - 각 모델별 고유 결과
# - RRF 점수 분포
```

### 3. 검색 방법 비교
```bash
# 같은 쿼리로 세 가지 방법 비교
query="PCOS lifestyle intervention"

# 1) 렉시컬만
curl ".../lexical-only?q=$query"

# 2) 벡터만  
curl ".../dense-only?q=$query"

# 3) 하이브리드
curl ".../search?q=$query"
```

## 📊 응답 형식

### 하이브리드 검색 응답
```json
{
  "query": "PCOS diet treatment",
  "results": [
    {
      "id": "pmid_12345_chunk_0",
      "rrf_score": 0.85,
      "found_in": ["dense", "lexical"],
      "title": "Dietary interventions for PCOS...",
      "text_preview": "This study investigated...",
      "model_version": "gpt-4o",
      "dense_rank": 2,
      "lexical_rank": 1,
      "dense_score": 0.87,
      "bm25_score": 12.5
    }
  ],
  "stats": {
    "total_results": 10,
    "lexical_only": 2,
    "dense_only": 3,
    "both": 5,
    "lexical_total": 50,
    "dense_total": 50
  },
  "processing_time": 0.45
}
```

### 모델 비교 응답
```json
{
  "query": "PCOS insulin resistance",
  "hq_model": {
    "namespace": "pcos-rag-gpt_4o",
    "results": [...],
    "stats": {"total_results": 10, ...}
  },
  "lq_model": {
    "namespace": "pcos-rag-gpt_3_5_turbo",
    "results": [...], 
    "stats": {"total_results": 8, ...}
  },
  "comparison": {
    "hq_count": 10,
    "lq_count": 8,
    "common_results": 6
  }
}
```

## 🛠️ 고급 사용법

### 1. 캐시 관리
```bash
# 강제 재빌드 (새로운 데이터 반영)
curl -X POST ".../initialize?force_rebuild=true"

# 특정 JSON 파일 사용
curl -X POST ".../initialize" \
-d '{"json_file": "data/bm25/custom_data.json"}'
```

### 2. 커스텀 토크나이저
```python
# hybrid_search_service.py에서 수정
def tokenize_text(self, text: str) -> List[str]:
    # 한국어 형태소 분석기 추가 가능
    # konlpy, mecab 등 활용
    pass
```

### 3. 성능 최적화
- BM25 인덱스는 pickle로 캐시됨 (`data/bm25/bm25_indexes.pkl`)
- 초기 로딩 후 인메모리에서 빠른 검색
- 1,500개 문서 기준 초기화 시간: ~30초, 검색 시간: ~100ms

## 📈 실험 결과 측정

### 주요 메트릭
1. **검색 품질**
   - Precision@K (상위 K개 결과의 정확도)
   - Recall (전체 관련 문서 중 검색된 비율)
   - MRR (Mean Reciprocal Rank)

2. **하이브리드 효과**
   - Dense-only vs Lexical-only vs Hybrid 성능 비교
   - RRF 파라미터 (k값) 최적화

3. **모델 품질**
   - HQ (gpt-4o) vs LQ (gpt-3.5-turbo) 태깅 품질 차이
   - 공통 결과 vs 모델별 고유 결과 분석

### 평가 방법
1. **수동 평가**: 의료진이 검색 결과의 관련성 평가
2. **자동 평가**: 기존 RAG 시스템과 성능 비교  
3. **사용자 평가**: A/B 테스트를 통한 사용자 만족도

이제 `python dump_pinecone_for_bm25.py` 실행 → 서비스 초기화 → API 테스트 순으로 진행하면 됩니다!