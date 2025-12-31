#!/bin/bash

# AUVRA Backend Startup Script
# =============================

echo "🚀 Starting AUVRA Backend Server..."
echo ""

# Navigate to backend directory
cd "$(dirname "$0")"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "⚠️  Virtual environment not found. Creating one..."
    python3 -m venv venv
    echo "✅ Virtual environment created"
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install/Update dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt --quiet

# Run database migrations (skip if it fails)
echo "🗄️  Running database migrations..."
alembic upgrade head || echo "⚠️  Migrations skipped (database may already be up to date)"

# Start the server
echo ""
echo "✅ Starting FastAPI server on http://localhost:8000"
echo "📖 API docs available at http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop the server"
echo "================================"
echo ""

# Run the server
python main.py
