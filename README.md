# Auvra Backend API

Production-ready backend API server based on FastAPI.

## 🚀 Features

- FastAPI-based RESTful API
- Firebase Authentication system
- Social login support (Google, Facebook, GitHub, etc.)
- PostgreSQL database integration
- Redis caching
- Docker containerization
- Nginx reverse proxy
- Automatic logging system
- CORS configuration
- Security headers setup

## 📋 Requirements

- Python 3.11+
- Docker & Docker Compose
- PostgreSQL
- Redis
- Firebase project (created in Firebase Console)

## 🛠️ Installation and Execution

### Development Environment

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Firebase Setup**
   ```bash
   # Create project in Firebase Console and download service account key
   # https://console.firebase.google.com/
   
   # Method 1: Use service account key file
   cp firebase-service-account.example.json firebase-service-account.json
   # Replace firebase-service-account.json with key downloaded from Firebase Console
   
   # Method 2: Use environment variables
   cp env.example .env
   # Update Firebase settings in .env file
   ```

3. **Start Server**
   ```bash
   python main.py
   ```

### Docker Environment

1. **Run entire stack with Docker Compose**
   ```bash
   docker-compose up -d
   ```

2. **Run individual services**
   ```bash
   # Run application only
   docker-compose up app
   
   # Run database only
   docker-compose up db
   ```

## 📚 API Documentation

After starting the server, you can check the API documentation at the following URLs:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## 🔧 Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ENVIRONMENT` | Execution environment (development/production) | development |
| `DEBUG` | Debug mode | false |
| `HOST` | Server host | 0.0.0.0 |
| `PORT` | Server port | 8000 |
| `FIREBASE_PROJECT_ID` | Firebase project ID | your-firebase-project-id |
| `FIREBASE_PRIVATE_KEY` | Firebase service account private key | your-private-key |
| `FIREBASE_CLIENT_EMAIL` | Firebase service account email | firebase-adminsdk-xxx@project.iam.gserviceaccount.com |
| `DATABASE_URL` | PostgreSQL connection URL | postgresql://user:password@localhost/auvra_db |
| `REDIS_URL` | Redis connection URL | redis://localhost:6379 |

## 🏗️ Project Structure

```
auvra-backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application
│   ├── api/
│   │   └── v1/
│   │       ├── api.py       # API router
│   │       └── endpoints/   # Endpoints
│   └── core/
│       ├── config.py        # Configuration management
│       └── logging.py       # Logging setup
├── nginx/
│   └── nginx.conf          # Nginx configuration
├── logs/                   # Log files
├── uploads/               # Upload files
├── requirements.txt        # Python dependencies
├── Dockerfile             # Docker image
├── docker-compose.yml     # Docker Compose
├── main.py               # Server execution script
└── README.md             # Project documentation
```

## 🔒 Security

- Firebase Authentication-based authentication
- Utilizing Google's security infrastructure
- Social login support
- Email verification
- Password reset
- CORS configuration
- Security headers setup
- Input validation
- SQL injection prevention

## 📊 Monitoring

- Health check endpoint: `/health`
- Detailed health check: `/health/detailed`
- Log files: `logs/app.log`
- Firebase Console: User management and authentication monitoring

## 🧪 Testing

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=app
```

## 🚀 Deployment

### Docker Deployment

```bash
# Production build
docker-compose -f docker-compose.yml up -d

# Check logs
docker-compose logs -f app
```

### Manual Deployment

1. Deploy code to server
2. Set environment variables
3. Install dependencies
4. Run with Gunicorn

```bash
pip install gunicorn
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

## 🤝 Contributing

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 License

This project is distributed under the MIT License.

## 📞 Support

If you have any issues or questions, please create an issue. 