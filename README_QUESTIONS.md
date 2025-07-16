# 질문 API 사용 가이드

## 🎯 **개요**

이 문서는 Auvra 호르몬 인사이트 플랫폼의 질문 API 사용법을 설명합니다.

## 📊 **데이터베이스 구조**

### **테이블 구조**

#### **1. users 테이블**
```sql
CREATE TABLE users (
    uid VARCHAR(255) PRIMARY KEY,  -- Firebase UID
    email VARCHAR(255),
    display_name VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

#### **2. question_sessions 테이블**
```sql
CREATE TABLE question_sessions (
    session_id VARCHAR(255) PRIMARY KEY,  -- 세션 고유 ID
    uid VARCHAR(255) NULL,                -- 사용자 ID (로그인 후 연결)
    device_id VARCHAR(255),               -- 디바이스 식별자
    created_at TIMESTAMP DEFAULT NOW(),   -- 세션 생성 시간
    completed_at TIMESTAMP NULL,          -- 세션 완료 시간
    status VARCHAR(50) DEFAULT 'in_progress'  -- 세션 상태
);
```

#### **3. user_responses 테이블**
```sql
CREATE TABLE user_responses (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255),
    uid VARCHAR(255) NULL,
    
    -- 기본 정보
    name VARCHAR(255) NULL,
    age INTEGER NULL,
    
    -- 생리 관련
    period_description VARCHAR(100) NULL,
    birth_control TEXT[] NULL,
    
    -- 생리 세부사항
    last_period_date VARCHAR(50) NULL,
    cycle_length VARCHAR(50) NULL,
    
    -- 건강 문제 (JSONB)
    period_concerns JSONB NULL,
    body_concerns JSONB NULL,
    skin_hair_concerns JSONB NULL,
    mental_health_concerns JSONB NULL,
    other_concerns JSONB NULL,
    
    -- 최우선 문제
    top_concern VARCHAR(255) NULL,
    
    -- 진단된 질환
    diagnosed_conditions TEXT[] NULL,
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

## 🔗 **API 엔드포인트**

### **1. 세션 관리**

#### **세션 생성**
```http
POST /api/v1/questions/sessions
Content-Type: application/json

{
    "device_id": "iPhone_14_Pro_ABC123"
}
```

**응답:**
```json
{
    "session_id": "session_abc123def456",
    "device_id": "iPhone_14_Pro_ABC123",
    "created_at": "2024-01-15T10:30:00Z",
    "status": "in_progress"
}
```

#### **세션 연결 (로그인 후)**
```http
POST /api/v1/questions/sessions/{session_id}/link
Authorization: Bearer {firebase_token}
Content-Type: application/json

{
    "uid": "firebase_uid_xyz789"
}
```

### **2. 응답 저장**

#### **응답 저장**
```http
POST /api/v1/questions/sessions/{session_id}/responses
Content-Type: application/json

{
    "session_id": "session_abc123def456",
    "responses": {
        "name": "김철수",
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

### **3. 데이터 조회**

#### **사용자 응답 조회**
```http
GET /api/v1/questions/users/{uid}/responses
Authorization: Bearer {firebase_token}
```

#### **세션 응답 조회**
```http
GET /api/v1/questions/sessions/{session_id}/responses
```

### **4. 세션 병합**

#### **여러 세션 병합**
```http
POST /api/v1/questions/users/{uid}/merge-sessions
Authorization: Bearer {firebase_token}
Content-Type: application/json

{
    "session_ids": ["session_1", "session_2", "session_3"]
}
```

### **5. 분석 데이터**

#### **분석 데이터 조회**
```http
GET /api/v1/questions/analytics
Authorization: Bearer {firebase_token}
```

**응답:**
```json
{
    "total_users": 150,
    "age_distribution": {
        "20대": 45,
        "30대": 67,
        "40대": 38
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

## 🔄 **사용 시나리오**

### **시나리오 1: 비로그인 사용자**

```typescript
// 1. 세션 생성
const sessionResponse = await fetch('/api/v1/questions/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ device_id: getDeviceId() })
});
const { session_id } = await sessionResponse.json();

// 2. 질문 응답 저장
const responseData = {
    session_id,
    responses: {
        name: "김철수",
        age: 25,
        // ... 기타 응답들
    }
};

await fetch(`/api/v1/questions/sessions/${session_id}/responses`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(responseData)
});
```

### **시나리오 2: 로그인 후 연결**

```typescript
// 1. 로그인 성공 후 세션 연결
const linkResponse = await fetch(`/api/v1/questions/sessions/${session_id}/link`, {
    method: 'POST',
    headers: { 
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${firebaseToken}`
    },
    body: JSON.stringify({ uid: firebaseUid })
});

// 2. 이후 응답은 자동으로 사용자와 연결됨
```

### **시나리오 3: 다중 디바이스**

```typescript
// 여러 디바이스의 세션을 하나로 병합
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

## 🔧 **설정 및 배포**

### **1. 환경변수 설정**
```bash
# .env 파일
DATABASE_URL=postgresql://user:password@localhost/auvra_db
FIREBASE_PROJECT_ID=your-project-id
FIREBASE_PRIVATE_KEY=your-private-key
FIREBASE_CLIENT_EMAIL=your-client-email
```

### **2. 데이터베이스 초기화**
```bash
# 테이블 생성
curl -X POST http://localhost:8000/api/v1/questions/init-database

# 또는 Alembic 사용
alembic upgrade head
```

### **3. 테스트 실행**
```bash
# API 테스트
python scripts/test_question_api.py
```

## ⚠️ **주의사항**

### **1. 보안**
- 모든 API는 Firebase 토큰 검증을 거침
- 본인의 데이터만 조회/수정 가능
- 세션 연결 시 본인 확인 필수

### **2. 데이터 무결성**
- 세션 ID는 고유해야 함
- 응답 데이터는 구조화된 형태로 저장
- JSONB 필드는 배열 형태로 저장

### **3. 성능**
- 대용량 데이터 조회 시 페이지네이션 사용 권장
- 분석 데이터는 캐싱 고려
- 인덱스 설정으로 쿼리 성능 최적화

## 📞 **지원**

문제가 있거나 질문이 있으시면 이슈를 생성해 주세요. 