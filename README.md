# AUVRA Backend API

FastAPI backend for the AUVRA women's health recommendation platform.

## Features

- **Personalized Recommendations**: AI-powered health recommendations based on menstrual cycle phase
- **Action Plans**: Daily actionable health guidance with research-backed evidence
- **RAG Integration**: Retrieval-augmented generation using PubMed research papers
- **Hybrid Search**: Combined BM25 + vector search for optimal relevance

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env

# Run database migrations
alembic upgrade head

# Start the server
uvicorn main:app --reload
```

## Project Structure

```
├── app/
│   ├── api/           # API routes and endpoints
│   ├── core/          # Configuration, database, logging
│   ├── models/        # SQLAlchemy database models
│   ├── services/      # Business logic and AI services
│   └── utils/         # Helper functions
├── alembic/           # Database migrations
├── data/              # BM25 search indices
├── docs/              # Documentation
├── scripts/           # Utility scripts
└── tests/             # Test files
```

## API Documentation

When running locally, access the API docs at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Environment Variables

See `.env.example` for required configuration.

## Deployment

Deployed on Render. See `docs/RENDER_DEPLOYMENT_GUIDE.md` for details.
