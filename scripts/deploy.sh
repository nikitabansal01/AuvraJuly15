#!/bin/bash

# Auvra Backend 배포 스크립트

set -e  # 오류 발생 시 스크립트 중단

echo "🚀 Auvra Backend 배포 시작..."

# 환경변수 확인
if [ ! -f ".env" ]; then
    echo "⚠️  .env 파일이 없습니다. env.example을 복사하여 생성합니다."
    cp env.example .env
    echo "📝 .env 파일을 편집하여 필요한 설정을 변경하세요."
    exit 1
fi

# Docker 확인
if ! command -v docker &> /dev/null; then
    echo "❌ Docker가 설치되어 있지 않습니다."
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose가 설치되어 있지 않습니다."
    exit 1
fi

# 기존 컨테이너 중지 및 제거
echo "🛑 기존 컨테이너 중지 중..."
docker-compose down

# 이미지 빌드
echo "🔨 Docker 이미지 빌드 중..."
docker-compose build --no-cache

# 컨테이너 시작
echo "🚀 컨테이너 시작 중..."
docker-compose up -d

# 헬스체크
echo "🏥 헬스체크 중..."
sleep 10

# 헬스체크 테스트
if curl -f http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ 애플리케이션이 정상적으로 시작되었습니다!"
else
    echo "❌ 애플리케이션 시작에 실패했습니다."
    echo "📋 로그를 확인하세요:"
    docker-compose logs app
    exit 1
fi

echo "🎉 배포가 완료되었습니다!"
echo ""
echo "📋 접속 정보:"
echo "   - API 서버: http://localhost:8000"
echo "   - API 문서: http://localhost:8000/docs"
echo "   - 헬스체크: http://localhost:8000/health"
echo ""
echo "📋 유용한 명령어:"
echo "   - 로그 확인: docker-compose logs -f app"
echo "   - 서비스 중지: docker-compose down"
echo "   - 서비스 재시작: docker-compose restart" 