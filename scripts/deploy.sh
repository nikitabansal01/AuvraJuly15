#!/bin/bash

# Auvra Backend deployment script

set -e  # Exit on error

echo "🚀 Starting Auvra Backend deployment..."

# Check environment variables
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found. Copying env.example to create it."
    cp env.example .env
    echo "📝 Please edit the .env file with your required settings."
    exit 1
fi

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed."
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed."
    exit 1
fi

# Stop and remove existing containers
echo "🛑 Stopping existing containers..."
docker-compose down

# Build images
echo "🔨 Building Docker images..."
docker-compose build --no-cache

# Start containers
echo "🚀 Starting containers..."
docker-compose up -d

# Health check
echo "🏥 Performing health check..."
sleep 10

# Health check test
if curl -f http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ Application started successfully!"
else
    echo "❌ Application startup failed."
    echo "📋 Check logs:"
    docker-compose logs app
    exit 1
fi

echo "🎉 Deployment completed!"
echo ""
echo "📋 Access Information:"
echo "   - API Server: http://localhost:8000"
echo "   - API Documentation: http://localhost:8000/docs"
echo "   - Health Check: http://localhost:8000/health"
echo ""
echo "📋 Useful Commands:"
echo "   - View logs: docker-compose logs -f app"
echo "   - Stop service: docker-compose down"
echo "   - Restart service: docker-compose restart" 