# Question API Usage Guide

## 🎯 **Overview**

This document explains how to use the Question API for the Auvra Hormone Insight platform.

## 📊 **Database Structure**

### **Table Structure**

#### **1. users table**
```sql
CREATE TABLE users (
    uid VARCHAR(255) PRIMARY KEY,  -- Firebase UID
    email VARCHAR(255),
    display_name VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

#### **2. question_sessions table**
```sql
CREATE TABLE question_sessions (
    session_id VARCHAR(255) PRIMARY KEY,  -- Unique session ID
    uid VARCHAR(255) NULL,                -- User ID (linked after login)
    device_id VARCHAR(255),               -- Device identifier
    created_at TIMESTAMP DEFAULT NOW(),   -- Session creation time
    completed_at TIMESTAMP NULL,          -- Session completion time
    status VARCHAR(50) DEFAULT 'in_progress'  -- Session status
);
```

#### **3. user_responses table**
```sql
CREATE TABLE user_responses (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255),
    uid VARCHAR(255) NULL,
    
    -- Basic information
    name VARCHAR(255) NULL,
    age INTEGER NULL,
    
    -- Menstrual related
    period_description VARCHAR(100) NULL,
    birth_control TEXT[] NULL,
    
    -- Menstrual details
    last_period_date VARCHAR(50) NULL,
    cycle_length VARCHAR(50) NULL,
    
    -- Health concerns (JSONB)
    period_concerns JSONB NULL,
    body_concerns JSONB NULL,
    skin_hair_concerns JSONB NULL,
    mental_health_concerns JSONB NULL,
    other_concerns JSONB NULL,
    
    -- Top priority concern
    top_concern VARCHAR(255) NULL,
    
    -- Diagnosed conditions
    diagnosed_conditions TEXT[] NULL,
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

## 🔗 **API Endpoints**

### **1. Session Management**

#### **Create Session**
```http
POST /api/v1/questions/sessions
Content-Type: application/json

{
    "device_id": "iPhone_14_Pro_ABC123"
}
```

**Response:**
```json
{
    "session_id": "session_abc123def456",
    "device_id": "iPhone_14_Pro_ABC123",
    "created_at": "2024-01-15T10:30:00Z",
    "status": "in_progress"
}
```

#### **Link Session (after login)**
```http
POST /api/v1/questions/sessions/{session_id}/link
Authorization: Bearer {firebase_token}
Content-Type: application/json

{
    "uid": "firebase_uid_xyz789"
}
```

### **2. Response Storage**

#### **Save Response**
```http
POST /api/v1/questions/sessions/{session_id}/responses
Content-Type: application/json

{
    "session_id": "session_abc123def456",
    "responses": {
        "name": "John Doe",
        "age": 25,
        "period_description": "Regular",
        "birth_control": ["Hormonal Birth Control Pills"],
        "last_period_date": "01/15/2024",
        "cycle_length": "26-30 days",
        "period_concerns": ["Painful Periods", "Irregular Periods"],
        "body_concerns": ["Bloating", "Recent weight gain"],
        "skin_hair_concerns": ["Adult Acne"],
        "mental_health_concerns": ["Mood swings"],
        "other_concerns": ["None of these"],
        "top_concern": "Painful Periods",
        "diagnosed_conditions": ["PCOS"]
    }
}
```

### **3. Data Retrieval**

#### **Get User Responses**
```http
GET /api/v1/questions/users/{uid}/responses
Authorization: Bearer {firebase_token}
```

#### **Get Session Responses**
```http
GET /api/v1/questions/sessions/{session_id}/responses
```

### **4. Session Merge**

#### **Merge Multiple Sessions**
```http
POST /api/v1/questions/users/{uid}/merge-sessions
Authorization: Bearer {firebase_token}
Content-Type: application/json

{
    "session_ids": ["session_1", "session_2", "session_3"]
}
```

### **5. Analytics Data**

#### **Get Analytics Data**
```http
GET /api/v1/questions/analytics
Authorization: Bearer {firebase_token}
```

**Response:**
```json
{
    "total_users": 150,
    "age_distribution": {
        "20s": 45,
        "30s": 67,
        "40s": 38
    },
    "period_concerns_stats": {
        "Painful Periods": 89,
        "Irregular Periods": 67,
        "Heavy periods": 34
    },
    "body_concerns_stats": {
        "Bloating": 123,
        "Recent weight gain": 78
    },
    "top_concerns_stats": {
        "Painful Periods": 45,
        "Bloating": 23
    }
}
```

## 🔄 **Usage Scenarios**

### **Scenario 1: Non-logged in User**

```typescript
// 1. Create session
const sessionResponse = await fetch('/api/v1/questions/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ device_id: getDeviceId() })
});
const { session_id } = await sessionResponse.json();

// 2. Save question responses
const responseData = {
    session_id,
    responses: {
        name: "John Doe",
        age: 25,
        // ... other responses
    }
};

await fetch(`/api/v1/questions/sessions/${session_id}/responses`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(responseData)
});
```

### **Scenario 2: Link after Login**

```typescript
// 1. Link session after successful login
const linkResponse = await fetch(`/api/v1/questions/sessions/${session_id}/link`, {
    method: 'POST',
    headers: { 
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${firebaseToken}`
    },
    body: JSON.stringify({ uid: firebaseUid })
});

// 2. Subsequent responses are automatically linked to the user
```

### **Scenario 3: Multiple Devices**

```typescript
// Merge sessions from multiple devices into one
const mergeResponse = await fetch(`/api/v1/questions/users/${uid}/merge-sessions`, {
    method: 'POST',
    headers: { 
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${firebaseToken}`
    },
    body: JSON.stringify({ 
        session_ids: ['session_1', 'session_2'] 
    })
});
```

## 🔧 **Setup and Deployment**

### **1. Environment Variables**
```bash
# .env 파일
DATABASE_URL=postgresql://user:password@localhost/auvra_db
FIREBASE_PROJECT_ID=your-project-id
FIREBASE_PRIVATE_KEY=your-private-key
FIREBASE_CLIENT_EMAIL=your-client-email
```

### **2. Database Initialization**
```bash
# Create tables
curl -X POST http://localhost:8000/api/v1/questions/init-database

# Or use Alembic
alembic upgrade head
```

### **3. Run Tests**
```bash
# API test
python scripts/test_question_api.py
```

## ⚠️ **Important Notes**

### **1. Security**
- All APIs go through Firebase token verification
- Users can only view/modify their own data
- Identity verification required when linking sessions

### **2. Data Integrity**
- Session IDs must be unique
- Response data is stored in structured format
- JSONB fields are stored as arrays

### **3. Performance**
- Pagination recommended for large data queries
- Consider caching for analytics data
- Optimize query performance with index settings

## 📞 **Support**

If you have problems or questions, please create an issue. 