# 🚀 RENDER DEPLOYMENT CHECKLIST FOR AUVRA CHATBOT

## 📋 Pre-Deployment Summary

Your Render configuration is **ALREADY SET UP CORRECTLY** in `render.yaml`. Here's what you need to do:

---

## ✅ REQUIRED ENVIRONMENT VARIABLES (Set in Render Dashboard)

Go to **Render Dashboard → Your Service → Environment → Add Environment Variable**

### 🔴 CRITICAL (Chatbot won't work without these):

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | ✅ YES | PostgreSQL connection string (Neon/Supabase) |
| `OPENAI_API_KEY` | ✅ YES | OpenAI API key for LangGraph agent + voice |
| `PINECONE_API_KEY` | ✅ YES | Pinecone for RAG search tool |
| `FIREBASE_PROJECT_ID` | ✅ YES | Firebase auth |
| `FIREBASE_PRIVATE_KEY` | ✅ YES | Firebase auth |
| `FIREBASE_CLIENT_EMAIL` | ✅ YES | Firebase auth |

### 🟡 OPTIONAL (Pre-configured with defaults):

| Variable | Default Value | Description |
|----------|---------------|-------------|
| `PINECONE_INDEX` | `auvra-papers` | Already in render.yaml |
| `PINECONE_ENVIRONMENT` | `us-east-1` | Already in render.yaml |
| `GROQ_API_KEY` | - | Optional LLM fallback |
| `GEMINI_API_KEY` | - | For V3 recommendations |

---

## 🔧 HOW TO SET VARIABLES IN RENDER

1. Go to: https://dashboard.render.com
2. Click on your service: `auvra-backend`
3. Go to **Environment** tab
4. Click **Add Environment Variable**
5. Add each variable (mark sensitive ones as "Secret")

### Example Format for FIREBASE_PRIVATE_KEY:
```
-----BEGIN PRIVATE KEY-----
MIIEvgIBADA...
-----END PRIVATE KEY-----
```
⚠️ **Important**: Include the full key with `-----BEGIN` and `-----END` headers!

---

## 📊 DATABASE MIGRATION

The migration runs automatically on deploy via `startCommand`:
```yaml
startCommand: |
  alembic upgrade head || echo "Migration failed, continuing..."
  uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### If migration fails:
1. Check Render logs for the error
2. The chatbot tables might already exist (that's OK)
3. The `|| echo` ensures the app still starts

---

## 🌐 FRONTEND CONFIGURATION

Your frontend (`mobileFEKD`) needs the API URL. Set this in your Expo app:

**Option 1**: Environment variable in `.env`:
```
EXPO_PUBLIC_API_URL=https://auvra-backend.onrender.com
```

**Option 2**: Update directly in `config/` folder if you have one.

---

## ✅ YOUR RENDER.YAML IS COMPLETE

```yaml
services:
  - type: web
    name: auvra-backend
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: |
      alembic upgrade head || echo "Migration failed, continuing..."
      uvicorn app.main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: PYTHON_VERSION
        value: 3.11
      - key: ENVIRONMENT
        value: production
      # ... all other vars are configured
```

---

## 🧪 POST-DEPLOYMENT VERIFICATION

After deploying, test these endpoints:

1. **Health Check**:
   ```
   GET https://auvra-backend.onrender.com/api/v1/health
   ```

2. **Chat API** (requires auth token):
   ```
   POST https://auvra-backend.onrender.com/api/v1/chat/message
   ```

3. **Check Logs** in Render Dashboard for:
   - ✅ `Database tables created successfully`
   - ✅ `Firebase initialized successfully`
   - ✅ `RAG: generate_rag_recommendations LOADED SUCCESSFULLY`
   - ✅ `OPENAI_API_KEY: SET`
   - ✅ `PINECONE_API_KEY: SET`

---

## 🚀 DEPLOYMENT STEPS

1. **Push to GitHub** (already done ✅):
   ```bash
   git push origin main
   ```

2. **In Render Dashboard**:
   - Click **Manual Deploy** → **Deploy latest commit**
   - OR if auto-deploy is on, it will deploy automatically

3. **Wait for build** (~3-5 minutes)

4. **Check logs** for any errors

5. **Test the health endpoint**

---

## ⚠️ COMMON ISSUES & FIXES

### Issue: "ModuleNotFoundError"
- **Fix**: Check `requirements.txt` has all dependencies

### Issue: "Migration failed"
- **Fix**: Tables might exist; check if app still works

### Issue: "OPENAI_API_KEY not set"
- **Fix**: Add it in Render Environment tab

### Issue: "Connection refused" from frontend
- **Fix**: Update `EXPO_PUBLIC_API_URL` to your Render URL

---

## 📝 SUMMARY

| Item | Status |
|------|--------|
| `render.yaml` | ✅ Configured |
| `requirements.txt` | ✅ Has all dependencies |
| `Dockerfile` | ✅ Ready (backup option) |
| Migrations | ✅ Auto-run on deploy |
| CORS | ✅ Allows all origins |
| Chat API | ✅ Registered at `/api/v1/chat` |

**You just need to set the environment variables in Render Dashboard!**
