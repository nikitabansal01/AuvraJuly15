# Firebase Authentication 설정 가이드

## 🔥 Firebase 프로젝트 설정

### 1. Firebase 프로젝트 생성

1. [Firebase Console](https://console.firebase.google.com/)에 접속
2. "프로젝트 추가" 클릭
3. 프로젝트 이름 입력 (예: `auvra-backend`)
4. Google Analytics 설정 (선택사항)
5. "프로젝트 만들기" 클릭

### 2. Authentication 활성화

1. Firebase Console에서 "Authentication" 메뉴 클릭
2. "시작하기" 클릭
3. "로그인 방법" 탭에서 원하는 인증 제공자 활성화:
   - **이메일/비밀번호**: 기본 제공자
   - **Google**: 소셜 로그인
   - **Facebook**: 소셜 로그인
   - **GitHub**: 소셜 로그인
   - **전화번호**: SMS 인증

### 3. 서비스 계정 키 생성

1. Firebase Console에서 "프로젝트 설정" 클릭
2. "서비스 계정" 탭 클릭
3. "새 비공개 키 생성" 클릭
4. JSON 파일 다운로드
5. 다운로드한 파일을 `firebase-service-account.json`으로 이름 변경하여 프로젝트 루트에 저장

### 4. 웹 앱 등록 (클라이언트용)

1. Firebase Console에서 "프로젝트 개요" 옆의 웹 아이콘 클릭
2. 앱 닉네임 입력 (예: `auvra-web`)
3. "앱 등록" 클릭
4. Firebase 설정 객체 복사:

```javascript
const firebaseConfig = {
  apiKey: "your-api-key",
  authDomain: "your-project.firebaseapp.com",
  projectId: "your-project-id",
  storageBucket: "your-project.appspot.com",
  messagingSenderId: "your-sender-id",
  appId: "your-app-id"
};
```

## ⚙️ 서버 설정

### 방법 1: 서비스 계정 키 파일 사용

1. Firebase Console에서 다운로드한 서비스 계정 키 파일을 `firebase-service-account.json`으로 저장
2. 프로젝트 루트 디렉토리에 위치

### 방법 2: 환경변수 사용

1. `.env` 파일에 Firebase 설정 추가:

```bash
# Firebase 설정
FIREBASE_PROJECT_ID=your-firebase-project-id
FIREBASE_PRIVATE_KEY_ID=your-private-key-id
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nYOUR_PRIVATE_KEY\n-----END PRIVATE KEY-----\n"
FIREBASE_CLIENT_EMAIL=firebase-adminsdk-xxxxx@your-project.iam.gserviceaccount.com
FIREBASE_CLIENT_ID=your-client-id
FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com
FIREBASE_STORAGE_BUCKET=your-project.appspot.com
FIREBASE_MESSAGING_SENDER_ID=your-sender-id
FIREBASE_APP_ID=your-app-id
```

## 🔧 클라이언트 설정

### HTML/JavaScript 예시

```html
<!DOCTYPE html>
<html>
<head>
    <script src="https://www.gstatic.com/firebasejs/9.0.0/firebase-app-compat.js"></script>
    <script src="https://www.gstatic.com/firebasejs/9.0.0/firebase-auth-compat.js"></script>
</head>
<body>
    <script>
        // Firebase 설정
        const firebaseConfig = {
            apiKey: "your-api-key",
            authDomain: "your-project.firebaseapp.com",
            projectId: "your-project-id",
            storageBucket: "your-project.appspot.com",
            messagingSenderId: "your-sender-id",
            appId: "your-app-id"
        };

        // Firebase 초기화
        firebase.initializeApp(firebaseConfig);
        const auth = firebase.auth();

        // 로그인 예시
        async function signIn() {
            try {
                const userCredential = await auth.signInWithEmailAndPassword(email, password);
                const user = userCredential.user;
                const idToken = await user.getIdToken();
                
                // 서버에 토큰 전송
                const response = await fetch('/api/v1/auth/verify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id_token: idToken })
                });
                
                const data = await response.json();
                console.log('인증 성공:', data);
            } catch (error) {
                console.error('로그인 실패:', error);
            }
        }
    </script>
</body>
</html>
```

### React 예시

```bash
npm install firebase
```

```javascript
import { initializeApp } from 'firebase/app';
import { getAuth, signInWithEmailAndPassword } from 'firebase/auth';

const firebaseConfig = {
  apiKey: "your-api-key",
  authDomain: "your-project.firebaseapp.com",
  projectId: "your-project-id",
  storageBucket: "your-project.appspot.com",
  messagingSenderId: "your-sender-id",
  appId: "your-app-id"
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);

// 로그인 함수
const signIn = async (email, password) => {
  try {
    const userCredential = await signInWithEmailAndPassword(auth, email, password);
    const user = userCredential.user;
    const idToken = await user.getIdToken();
    
    // 서버에 토큰 전송
    const response = await fetch('/api/v1/auth/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id_token: idToken })
    });
    
    return await response.json();
  } catch (error) {
    console.error('로그인 실패:', error);
    throw error;
  }
};
```

## 🔐 보안 설정

### 1. 인증 제공자 설정

Firebase Console에서 다음 설정을 확인하세요:

- **이메일/비밀번호**: 기본 활성화
- **이메일 인증**: 사용자 등록 시 이메일 인증 필요 여부
- **비밀번호 재설정**: 비밀번호 재설정 이메일 템플릿 설정
- **소셜 로그인**: 각 제공자의 클라이언트 ID 및 시크릿 설정

### 2. 보안 규칙 설정

Firebase Console에서 보안 규칙을 설정할 수 있습니다:

```javascript
// Firestore 보안 규칙 예시
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /users/{userId} {
      allow read, write: if request.auth != null && request.auth.uid == userId;
    }
  }
}
```

### 3. CORS 설정

프로덕션 환경에서는 Firebase Console에서 허용된 도메인을 설정하세요:

1. Firebase Console → Authentication → Settings → Authorized domains
2. 도메인 추가 (예: `your-domain.com`)

## 🚀 배포 시 주의사항

### 1. 환경변수 설정

프로덕션 환경에서는 환경변수를 통해 Firebase 설정을 관리하세요:

```bash
# Docker 환경변수 예시
FIREBASE_PROJECT_ID=your-production-project-id
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nYOUR_PRODUCTION_PRIVATE_KEY\n-----END PRIVATE KEY-----\n"
FIREBASE_CLIENT_EMAIL=firebase-adminsdk-xxxxx@your-production-project.iam.gserviceaccount.com
```

### 2. 서비스 계정 키 보안

- 서비스 계정 키 파일을 Git에 커밋하지 마세요
- 프로덕션 환경에서는 환경변수 사용을 권장합니다
- 키가 노출된 경우 즉시 재생성하세요

### 3. 도메인 설정

프로덕션 도메인을 Firebase Console에 등록하세요:

1. Firebase Console → Authentication → Settings → Authorized domains
2. 프로덕션 도메인 추가

## 📊 모니터링

### Firebase Console 모니터링

- **Authentication**: 사용자 등록, 로그인 통계
- **Analytics**: 사용자 행동 분석
- **Crashlytics**: 앱 크래시 모니터링

### 서버 로그 모니터링

```bash
# 로그 확인
docker-compose logs -f app

# Firebase 관련 로그 필터링
docker-compose logs app | grep -i firebase
```

## 🔧 문제 해결

### 일반적인 문제들

1. **Firebase 초기화 실패**
   - 서비스 계정 키 파일 경로 확인
   - 환경변수 설정 확인
   - Firebase 프로젝트 ID 확인

2. **토큰 검증 실패**
   - 클라이언트와 서버의 Firebase 프로젝트가 동일한지 확인
   - 토큰 만료 시간 확인
   - 네트워크 연결 확인

3. **CORS 오류**
   - Firebase Console에서 허용된 도메인 설정
   - 서버 CORS 설정 확인

### 디버깅 팁

```python
# Firebase 초기화 디버깅
import firebase_admin
from firebase_admin import auth

try:
    # 토큰 검증 테스트
    decoded_token = auth.verify_id_token("test-token")
    print("Firebase 설정 정상")
except Exception as e:
    print(f"Firebase 설정 오류: {e}")
```

## 📚 추가 리소스

- [Firebase Authentication 문서](https://firebase.google.com/docs/auth)
- [Firebase Admin SDK 문서](https://firebase.google.com/docs/admin/setup)
- [Firebase Console](https://console.firebase.google.com/)
- [Firebase 보안 규칙 가이드](https://firebase.google.com/docs/rules) 