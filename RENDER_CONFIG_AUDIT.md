# 🔍 RENDER CONFIGURATION DEEP AUDIT - CRITICAL FINDINGS

**Date:** December 15, 2025  
**Status:** ⚠️ **ACTION REQUIRED - Missing Environment Variables**

---

## ❌ CRITICAL ISSUES FOUND

### 1. **Missing OPENAI_API_KEY** ⚠️ CRITICAL
**Status:** NOT in render.yaml  
**Impact:** Chatbot will FAIL completely  
**Required by:**
- `app/services/chat/langgraph_agent.py` (line 105) - LangGraph ChatOpenAI model
- `app/services/chat/voice_service.py` (lines 30-31) - Whisper voice transcription
- `app/services/chat/chat_memory_service.py` (line 38) - Memory summarization
- `app/services/chat/tools.py` (line 607) - RAG embeddings

**Fix:** Added to render.yaml ✅

---

### 2. **Missing PINECONE_API_KEY & PINECONE_INDEX** ⚠️ HIGH PRIORITY
**Status:** NOT in render.yaml  
**Impact:** RAG tool `search_health_knowledge` will FAIL  
**Required by:**
- `app/services/chat/tools.py` (line 606-617) - Health knowledge retrieval
- Hard-coded index name: `"auvra-papers"` (line 617)
- Hard-coded namespace: `"combined"` (line 620)

**Fix:** Added both to render.yaml ✅

---

### 3. **Missing GROQ_API_KEY** ⚠️ MEDIUM PRIORITY
**Status:** NOT in render.yaml  
**Impact:** No fallback LLM if OpenAI fails  
**Required by:**
- Recommendation engine V3 (fallback model)
- Not critical for chatbot core functionality

**Fix:** Added to render.yaml ✅

---

## ✅ WHAT WAS ALREADY CORRECT

### Existing Environment Variables (Good)
1. ✅ `DATABASE_URL` - PostgreSQL connection
2. ✅ `FIREBASE_PROJECT_ID` - User authentication
3. ✅ `FIREBASE_PRIVATE_KEY` - Firebase auth
4. ✅ `FIREBASE_CLIENT_EMAIL` - Firebase service account
5. ✅ `GEMINI_API_KEY` - V3 recommendation engine
6. ✅ `ENVIRONMENT=production` - Correct environment
7. ✅ `PYTHON_VERSION=3.11` - Correct Python version

### Build & Start Commands (Perfect)
- ✅ Build: `pip install -r requirements.txt`
- ✅ Migration: `alembic upgrade head || echo "Migration failed"`
- ✅ Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

---

## 📋 COMPLETE ENVIRONMENT VARIABLES CHECKLIST

### Required for Chatbot (MUST HAVE):
- [x] `DATABASE_URL` - ✅ Already set
- [x] `OPENAI_API_KEY` - ⚠️ **ADDED NOW** (was missing)
- [x] `PINECONE_API_KEY` - ⚠️ **ADDED NOW** (was missing)
- [x] `PINECONE_INDEX` - ⚠️ **ADDED NOW** (was missing, hardcoded to "auvra-papers")
- [x] `FIREBASE_PROJECT_ID` - ✅ Already set
- [x] `FIREBASE_PRIVATE_KEY` - ✅ Already set
- [x] `FIREBASE_CLIENT_EMAIL` - ✅ Already set

### Optional (Nice to Have):
- [x] `GROQ_API_KEY` - ⚠️ **ADDED NOW** (fallback LLM)
- [x] `PINECONE_ENVIRONMENT` - ⚠️ **ADDED NOW** (set to "us-east-1")
- [x] `GEMINI_API_KEY` - ✅ Already set
- [ ] `REDIS_URL` - Not needed yet

---

## 🔧 WHAT I FIXED IN render.yaml

### Changes Made:
```yaml
# ADDED - OpenAI API (CRITICAL for chatbot)
- key: OPENAI_API_KEY
  sync: false

# ADDED - Groq fallback (optional but recommended)
- key: GROQ_API_KEY
  sync: false

# ADDED - Pinecone for RAG (CRITICAL for search_health_knowledge tool)
- key: PINECONE_API_KEY
  sync: false
- key: PINECONE_INDEX
  value: "auvra-papers"  # Hardcoded in tools.py
- key: PINECONE_ENVIRONMENT
  value: "us-east-1"     # Default Pinecone environment
```

---

## 🚨 ACTION REQUIRED - SET THESE ON RENDER DASHBOARD

After pushing the updated render.yaml, you MUST set these in Render Dashboard:

### Step 1: Go to Render Dashboard
https://dashboard.render.com → Select "auvra-backend" service

### Step 2: Go to Environment Variables
Click "Environment" tab

### Step 3: Add These Secret Values
(render.yaml marks them as `sync: false` - you must add manually)

```bash
# CRITICAL - Chatbot will not work without this
OPENAI_API_KEY=sk-proj-XXXXXX...  # Your OpenAI API key

# CRITICAL - RAG tool will fail without this
PINECONE_API_KEY=pcsk_XXXXXX...   # Your Pinecone API key

# OPTIONAL - Fallback LLM
GROQ_API_KEY=gsk_XXXXXX...        # Your Groq API key (if you have one)
```

### Step 4: Verify Already Set
These should already exist (don't change):
- `DATABASE_URL`
- `FIREBASE_PROJECT_ID`
- `FIREBASE_PRIVATE_KEY`
- `FIREBASE_CLIENT_EMAIL`
- `GEMINI_API_KEY`

---

## 🧪 TESTING CHECKLIST AFTER DEPLOYMENT

### 1. Health Check (Should Pass)
```bash
curl https://auvra-backend.onrender.com/api/v1/chat/health
```
Expected: `{"status": "healthy"}`

### 2. Test OpenAI Integration (Voice)
```bash
curl -X POST "https://auvra-backend.onrender.com/api/v1/chat/voice" \
  -F "user_id=test_user" \
  -F "audio=@test.webm" \
  -F "conversation_context=care_plan_modal"
```
Expected: Transcribed text + AI response

### 3. Test RAG Search Tool
```bash
curl -X POST "https://auvra-backend.onrender.com/api/v1/chat/message" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test_user",
    "message": "What are the benefits of magnesium for PMS?",
    "conversation_context": "know_body"
  }'
```
Expected: Response with citations from Pinecone knowledge base

### 4. Check Logs for Errors
In Render dashboard, check logs for:
- ❌ "OPENAI_API_KEY: NOT SET" → Fix: Add in dashboard
- ❌ "PINECONE_API_KEY: NOT SET" → Fix: Add in dashboard
- ✅ "OPENAI_API_KEY: SET" → Good!
- ✅ "PINECONE_INDEX: auvra-papers" → Good!

---

## 📊 CONFIGURATION MATRIX

| Environment Variable | Old render.yaml | New render.yaml | Render Dashboard | Priority |
|---------------------|-----------------|-----------------|------------------|----------|
| OPENAI_API_KEY      | ❌ Missing      | ✅ Added        | ⚠️ Must Set      | CRITICAL |
| PINECONE_API_KEY    | ❌ Missing      | ✅ Added        | ⚠️ Must Set      | CRITICAL |
| PINECONE_INDEX      | ❌ Missing      | ✅ Added ("auvra-papers") | Auto-set | CRITICAL |
| GROQ_API_KEY        | ❌ Missing      | ✅ Added        | Optional         | Medium   |
| DATABASE_URL        | ✅ Present      | ✅ Present      | ✅ Already Set   | CRITICAL |
| FIREBASE_*          | ✅ Present      | ✅ Present      | ✅ Already Set   | CRITICAL |
| GEMINI_API_KEY      | ✅ Present      | ✅ Present      | ✅ Already Set   | Medium   |

---

## 🎯 DEPLOYMENT PRIORITY

### Priority 1 (MUST DO NOW):
1. ✅ Updated render.yaml (DONE)
2. ⚠️ Set `OPENAI_API_KEY` in Render Dashboard
3. ⚠️ Set `PINECONE_API_KEY` in Render Dashboard
4. 🚀 Deploy to Render

### Priority 2 (Recommended):
5. Set `GROQ_API_KEY` in Render Dashboard (for fallback)

### Priority 3 (Optional):
6. Monitor logs after deployment
7. Test all chatbot endpoints

---

## 🔍 HOW TO VERIFY CONFIGURATION

### On Render Dashboard:
1. Go to your service
2. Click "Environment" tab
3. Verify you see:
   - `OPENAI_API_KEY` (secret value hidden)
   - `PINECONE_API_KEY` (secret value hidden)
   - `PINECONE_INDEX` = "auvra-papers"
   - All Firebase variables

### In Application Logs:
After deployment, check logs for:
```
✅ OPENAI_API_KEY: SET
✅ PINECONE_INDEX: auvra-papers
✅ PINECONE_API_KEY: SET (pcsk_4Bh...)
```

---

## 🚀 NEXT STEPS

### Step 1: Commit & Push Updated render.yaml
```bash
cd /Users/mohanganesh/AUVRA/AuvraJuly15
git add render.yaml
git commit -m "fix: Add missing environment variables for chatbot (OPENAI_API_KEY, PINECONE_*)"
git push origin main
```

### Step 2: Set Secrets on Render Dashboard
1. Go to https://dashboard.render.com
2. Select "auvra-backend"
3. Go to "Environment" tab
4. Click "Add Environment Variable"
5. Add:
   - `OPENAI_API_KEY` = your OpenAI key
   - `PINECONE_API_KEY` = your Pinecone key
   - `GROQ_API_KEY` = your Groq key (optional)

### Step 3: Deploy
- Auto-deploy should trigger
- OR click "Manual Deploy" → "Deploy latest commit"

### Step 4: Test
- Run health check
- Test chatbot endpoints
- Check logs for errors

---

## 📝 SUMMARY

**What was wrong:**
- ❌ OPENAI_API_KEY was missing (CRITICAL)
- ❌ PINECONE_API_KEY was missing (CRITICAL)
- ❌ PINECONE_INDEX was missing (CRITICAL)
- ❌ GROQ_API_KEY was missing (optional fallback)

**What I fixed:**
- ✅ Added all 4 missing environment variables to render.yaml
- ✅ Set PINECONE_INDEX to "auvra-papers" (hardcoded in code)
- ✅ Set PINECONE_ENVIRONMENT to "us-east-1" (default)

**What YOU need to do:**
1. Push updated render.yaml to GitHub
2. Set secret values in Render Dashboard
3. Deploy and test

**Status:** 🟡 **Ready to deploy AFTER setting secrets on Render Dashboard**

---

## ⚠️ IMPORTANT NOTES

1. **Don't commit API keys to Git** - They are marked `sync: false` in render.yaml
2. **Pinecone index name is hardcoded** - tools.py line 617: `index = pc.Index("auvra-papers")`
3. **Pinecone namespace is hardcoded** - tools.py line 620: `namespace="combined"`
4. **Test immediately after deployment** - Chatbot depends on these APIs

---

**Configuration Status:** ⚠️ **PARTIALLY COMPLETE**
- ✅ render.yaml updated
- ⚠️ Secrets need to be set manually on Render Dashboard
- 🚀 Ready to deploy after secrets are added
