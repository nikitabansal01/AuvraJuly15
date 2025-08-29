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
    
    -- Family history
    family_history TEXT[] NULL,
    
    -- Lifestyle
    workout_intensity VARCHAR(50) NULL,
    sleep_duration VARCHAR(50) NULL,
    stress_level VARCHAR(50) NULL,
    
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
        "diagnosed_conditions": ["PCOS"],
        "family_history": ["PCOS", "Endometriosis"],
        "workout_intensity": "Moderate",
        "sleep_duration": "7-8 hours",
        "stress_level": "High"
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
        "18-25": 45,
        "26-35": 67,
        "36-45": 38
    },
    "period_concerns_stats": {
        "Painful Periods": 89,
        "Irregular Periods": 67,
        "Heavy periods": 34
    },
    "body_concerns_stats": {
        "Bloating": 78,
        "Recent weight gain": 56,
        "Hot Flashes": 23
    },
    "top_concerns_stats": {
        "Painful Periods": 45,
        "Bloating": 34,
        "Recent weight gain": 28
    },
    "diagnosed_conditions_stats": {
        "PCOS": 67,
        "Endometriosis": 23,
        "None of the above": 60
    },
    "family_history_stats": {
        "PCOS": 34,
        "Endometriosis": 12,
        "None of the above": 104
    },
    "workout_intensity_stats": {
        "Low": 45,
        "Moderate": 78,
        "High": 27
    },
    "sleep_duration_stats": {
        "<6 hours": 23,
        "6-7 hours": 45,
        "7-8 hours": 67,
        "8+ hours": 15
    },
    "stress_level_stats": {
        "Low": 34,
        "Moderate": 67,
        "High": 49
    }
}
```

## 📋 **Field Options**

### **Period Description**
- `"Regular"`
- `"Irregular"`
- `"Occasional Skips"`
- `"I don't get periods"`
- `"I'm not sure"`

### **Cycle Length**
- `"Less than 21 days"`
- `"21-25 days"`
- `"26-30 days"`
- `"31-35 days"`
- `"35+ days"`
- `"I'm not sure"`

### **Birth Control**
- `"Hormonal Birth Control Pills"`
- `"IUD (Intrauterine Device)"`

### **Period Concerns**
- `"Irregular Periods"`
- `"Painful Periods"`
- `"Light periods / Spotting"`
- `"Heavy periods"`

### **Body Concerns**
- `"Bloating"`
- `"Hot Flashes"`
- `"Nausea"`
- `"Difficulty losing weight / stubborn belly fat"`
- `"Recent weight gain"`
- `"Menstrual headaches"`

### **Skin & Hair Concerns**
- `"Hirsutism (hair growth on chin, nipples etc)"`
- `"Thinning of hair"`
- `"Adult Acne"`

### **Mental Health Concerns**
- `"Mood swings"`
- `"Stress"`
- `"Fatigue"`

### **Top Concern**
- `"Painful Periods"`
- `"Bloating"`
- `"Recent weight gain"`
- `"Hirsutism (hair growth on chin, nipples etc)"`
- `"Adult Acne"`
- `"Mood swings"`

### **Diagnosed Conditions**
- `"PCOS"`
- `"PCOD"`
- `"Endometriosis"`
- `"Dysmenorrhea (painful periods)"`
- `"Amenorrhea (absence of periods)"`
- `"Menorrhagia (prolonged/heavy bleeding)"`
- `"Metrorrhagia (irregular bleeding)"`
- `"Cushing's Syndrome (PMS)"`
- `"Premenstrual Syndrome (PMS)"`
- `"None of the above"`
- `"Others (please specify)"`

### **Family History**
- `"PCOS"`
- `"PCOD"`
- `"Endometriosis"`
- `"Dysmenorrhea (painful periods)"`
- `"Amenorrhea (absence of periods)"`
- `"Menorrhagia (prolonged/heavy bleeding)"`
- `"Metrorrhagia (irregular bleeding)"`
- `"Cushing's Syndrome (PMS)"`
- `"Premenstrual Syndrome (PMS)"`
- `"None of the above"`
- `"Others (please specify)"`

### **Workout Intensity**
- `"Low"`
- `"Moderate"`
- `"High"`

### **Sleep Duration**
- `"<6 hours"`
- `"6-7 hours"`
- `"7-8 hours"`
- `"8+ hours"`

### **Stress Level**
- `"Low"`
- `"Moderate"`
- `"High"`

### **Other Concerns**
- `"None of these"`
- `"Others (please specify)"`

## 🔧 **Error Handling**

### **Validation Errors**
If invalid values are provided, the API will return a 422 Unprocessable Entity error with details about which fields failed validation.

### **Session Not Found**
If a session ID doesn't exist, the API will return a 404 Not Found error.

### **Authentication Errors**
Protected endpoints require valid Firebase authentication tokens.

## 📝 **Notes**

- All fields are optional except `session_id` when saving responses
- Arrays can be empty or null
- Date format should be MM/DD/YYYY for `last_period_date`
- Age should be between 0 and 120
- The API automatically validates all field values against the allowed options
