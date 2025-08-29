# Hybrid Search System Usage Guide

BM25 + Pinecone Vector Hybrid Search for HQ vs LQ Model Performance Comparison

## 🚀 Quick Start

### 1. Package Installation
```bash
pip install rank-bm25 nltk
# or
pip install -r requirements.txt
```

### 2. Pinecone Data Dump
```bash
cd scripts
python dump_pinecone_for_bm25.py
```

This script generates the following files:
- `data/bm25/hq_documents_YYYYMMDD_HHMMSS.json` - HQ model (gpt-4o) data
- `data/bm25/lq_documents_YYYYMMDD_HHMMSS.json` - LQ model (gpt-3.5-turbo) data  
- `data/bm25/combined_documents_YYYYMMDD_HHMMSS.json` - Combined data

### 3. Service Initialization
```bash
# Start FastAPI server
uvicorn app.main:app --reload

# Initialize hybrid search service
curl -X POST "http://localhost:8000/api/v1/hybrid-search/initialize"
```

## 📡 API Endpoints

### Basic Search

#### Hybrid Search (GET)
```bash
curl "http://localhost:8000/api/v1/hybrid-search/search?q=PCOS+diet+treatment&top_k=10"
```

#### Hybrid Search (POST)
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

#### Lexical Search Only (BM25)
```bash
curl "http://localhost:8000/api/v1/hybrid-search/lexical-only?q=PCOS+exercise&top_k=10"
```

#### Vector Search Only (Pinecone)
```bash
curl "http://localhost:8000/api/v1/hybrid-search/dense-only?q=PCOS+mindfulness&top_k=10"
```

### HQ vs LQ Model Comparison

```bash
curl -X POST "http://localhost:8000/api/v1/hybrid-search/compare-models" \
-H "Content-Type: application/json" \
-d '{
  "query": "PCOS insulin resistance treatment",
  "top_k": 10
}'
```

### Weight Management

#### Get Current Weights
```bash
curl "http://localhost:8000/api/v1/hybrid-search/field-weights"
```

#### Update Weights
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

#### Service Status and Statistics
```bash
curl "http://localhost:8000/api/v1/hybrid-search/health"
curl "http://localhost:8000/api/v1/hybrid-search/stats"
```

## 🎯 Field-based Weight System

### Basic Text Fields
- `title`: 4.0 - Paper title
- `abstract`: 3.0 - Abstract
- `text`: 1.0 - Main text (baseline)
- `section_title`: 2.0 - Section title
- `chunk_summary`: 2.5 - Chunk summary
- `study_arms_text`: 2.0 - Study arms information

### Tag-based Fields (List → Text conversion)
- `intervention_type_text`: 3.0 - Intervention type
- `hormone_focus_text`: 3.0 - Hormone focus
- `symptoms_focus_text`: 3.0 - Symptom focus
- `doc_condition_disease_text`: 3.5 - Disease/condition
- `doc_target_symptoms_text`: 3.0 - Target symptoms
- `mesh_terms_text`: 1.5 - MeSH terms

### Section-based Weights
- `methods_text`: 0.5 - Research methods
- `results_text`: 1.5 - Results
- `discussion_text`: 1.3 - Discussion
- `conclusion_text`: 1.4 - Conclusion
- `introduction_text`: 0.8 - Introduction

## 🔬 A/B Testing Scenarios

### 1. Weight Experiments
```python
# Scenario A: Emphasize title and abstract
weights_a = {"title": 6.0, "abstract": 5.0, "text": 1.0}

# Scenario B: Emphasize medical tags  
weights_b = {
    "doc_condition_disease_text": 5.0,
    "intervention_type_text": 4.5,
    "hormone_focus_text": 4.0
}

# Scenario C: Emphasize research results
weights_c = {
    "results_text": 3.0,
    "conclusion_text": 2.5,
    "discussion_text": 2.0
}
```

### 2. Model Quality Comparison
```bash
# HQ model (50 documents, 500 chunks)
curl -X POST ".../compare-models" -d '{"query": "PCOS metformin", "top_k": 20}'

# Result analysis:
# - Number of common results (common_results)
# - Unique results for each model
# - RRF score distribution
```

### 3. Search Method Comparison
```bash
# Compare three methods with the same query
query="PCOS lifestyle intervention"

# 1) Lexical only
curl ".../lexical-only?q=$query"

# 2) Vector only  
curl ".../dense-only?q=$query"

# 3) Hybrid
curl ".../search?q=$query"
```

## 📊 Response Format

### Hybrid Search Response
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

### Model Comparison Response
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

## 🛠️ Advanced Usage

### 1. Cache Management
```bash
# Force rebuild (reflect new data)
curl -X POST ".../initialize?force_rebuild=true"

# Use specific JSON file
curl -X POST ".../initialize" \
-d '{"json_file": "data/bm25/custom_data.json"}'
```

### 2. Custom Tokenizer
```python
# Modify in hybrid_search_service.py
def tokenize_text(self, text: str) -> List[str]:
    # Can add Korean morphological analyzer
    # Utilize konlpy, mecab, etc.
    pass
```

### 3. Performance Optimization
- BM25 indexes are cached with pickle (`data/bm25/bm25_indexes.pkl`)
- Fast in-memory search after initial loading
- For 1,500 documents: initialization time ~30s, search time ~100ms

## 📈 Experimental Result Measurement

### Key Metrics
1. **Search Quality**
   - Precision@K (accuracy of top K results)
   - Recall (ratio of retrieved relevant documents)
   - MRR (Mean Reciprocal Rank)

2. **Hybrid Effect**
   - Dense-only vs Lexical-only vs Hybrid performance comparison
   - RRF parameter (k value) optimization

3. **Model Quality**
   - HQ (gpt-4o) vs LQ (gpt-3.5-turbo) tagging quality differences
   - Common results vs model-specific unique results analysis

### Evaluation Methods
1. **Manual Evaluation**: Medical professionals evaluate relevance of search results
2. **Automatic Evaluation**: Performance comparison with existing RAG system  
3. **User Evaluation**: User satisfaction through A/B testing

Now proceed with: `python dump_pinecone_for_bm25.py` execution → Service initialization → API testing!