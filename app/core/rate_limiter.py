"""
Rate Limiting Configuration for Auvra API

Production-grade rate limiting using slowapi (Redis-backed) to prevent abuse and ensure fair usage.

Industry Patterns:
- Stripe API: 100 requests/second per user, 429 with Retry-After header
- OpenAI API: Tiered limits (60/min free, 3500/min paid), sliding window
- GitHub API: 5000/hour authenticated, X-RateLimit headers
- ChatGPT: ~50 messages/3 hours for free tier, ~100/hr for Plus

Our Implementation:
- 60 requests/minute per user (prevents abuse, allows normal usage)
- 10 requests/minute for auth endpoints (prevent brute force)
- Redis-backed storage (shared across instances, survives restarts)
- Graceful degradation (if Redis down, skip rate limiting)
- Standard 429 response with Retry-After header
- Custom key function (by user_id, IP, or endpoint)

References:
- slowapi docs: https://slowapi.readthedocs.io/
- FastAPI middleware: https://fastapi.tiangolo.com/advanced/middleware/
- Redis rate limiting pattern: https://redis.io/commands/incr#pattern-rate-limiter
"""

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from typing import Optional
import logging

from app.core.config import settings  # Import settings for Redis URL

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# CUSTOM KEY FUNCTION - Rate limit by user_id (from Firebase auth) first,
# fallback to IP address if not authenticated
# ════════════════════════════════════════════════════════════════════════════
def get_user_identifier(request: Request) -> str:
    """
    Get rate limit identifier: user_id > IP address
    
    Priority:
    1. user_id from Firebase auth (request.state.user_id)
    2. user_id from query params (for testing)
    3. IP address (for anonymous/failed auth)
    
    Returns:
        Unique identifier string for rate limiting
    """
    try:
        # Try Firebase auth user_id (set by auth middleware)
        if hasattr(request.state, 'user_id') and request.state.user_id:
            return f"user:{request.state.user_id}"
        
        # Fallback: query param user_id (for testing, guest users)
        user_id = request.query_params.get('user_id')
        if user_id:
            return f"user:{user_id}"
        
        # Fallback: IP address (for completely anonymous requests)
        ip = get_remote_address(request)
        return f"ip:{ip}"
    
    except Exception as e:
        logger.warning(f"[RATE_LIMIT] Error getting user identifier: {e}")
        # Ultimate fallback: use IP
        return f"ip:{get_remote_address(request)}"


# ════════════════════════════════════════════════════════════════════════════
# LIMITER CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════
limiter = Limiter(
    key_func=get_user_identifier,
    # Global default: 60 requests/minute per user (can override per-endpoint)
    default_limits=["60/minute"],
    # Redis storage (shared across instances, survives restarts)
    storage_uri=settings.REDIS_URL,  # Use REDIS_URL from config
    # Sliding window strategy (more accurate than fixed window)
    strategy="moving-window",
    # Headers to include in response
    headers_enabled=True,  # X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
    # Graceful degradation: if Redis unavailable, skip rate limiting (don't block users)
    swallow_errors=True,
    # Custom retry-after header
    retry_after="http-date"
)


# ════════════════════════════════════════════════════════════════════════════
# ENDPOINT-SPECIFIC RATE LIMITS
# ════════════════════════════════════════════════════════════════════════════

# Standard conversation endpoints: 60/minute (default)
CONVERSATION_LIMIT = "60/minute"

# Auth endpoints: 10/minute (prevent brute force)
AUTH_LIMIT = "10/minute"

# Action plan generation: 20/hour (expensive operations)
ACTION_PLAN_LIMIT = "20/hour"

# Health check: No limit (needed for monitoring)
HEALTH_CHECK_LIMIT = "1000/minute"

# Chat endpoints: 30/minute (prevents rapid-fire spam)
CHAT_LIMIT = "30/minute"


# ════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTION - Get limiter instance (for dependency injection)
# ════════════════════════════════════════════════════════════════════════════
def get_rate_limiter() -> Limiter:
    """
    Get configured rate limiter instance.
    
    Usage in FastAPI:
        from app.core.rate_limiter import get_rate_limiter, CHAT_LIMIT
        
        @app.post("/api/v1/chat")
        @limiter.limit(CHAT_LIMIT)
        async def chat_endpoint(request: Request, ...):
            ...
    
    Returns:
        Configured Limiter instance
    """
    return limiter


# ════════════════════════════════════════════════════════════════════════════
# CUSTOM RATE LIMIT EXCEEDED RESPONSE
# ════════════════════════════════════════════════════════════════════════════
async def custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """
    Custom 429 response with helpful message.
    
    Standard format matches industry (Stripe, GitHub, OpenAI):
    {
        "error": "rate_limit_exceeded",
        "message": "Too many requests. Please try again in 32 seconds.",
        "retry_after": 32,
        "limit": "60/minute"
    }
    
    Headers:
    - X-RateLimit-Limit: 60
    - X-RateLimit-Remaining: 0
    - X-RateLimit-Reset: 1645123456
    - Retry-After: 32
    """
    from fastapi.responses import JSONResponse
    
    # Extract retry-after from exception
    retry_after = getattr(exc, 'retry_after', 60)
    
    logger.warning(
        f"[RATE_LIMIT] User {get_user_identifier(request)} hit rate limit on {request.url.path}"
    )
    
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "message": f"Too many requests. Please try again in {retry_after} seconds.",
            "retry_after": retry_after,
            "limit": "60/minute",
            "documentation": "https://docs.auvra.com/rate-limits"
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Remaining": "0"
        }
    )


# ════════════════════════════════════════════════════════════════════════════
# RATE LIMIT BYPASS (for internal services, monitoring, load testing)
# ════════════════════════════════════════════════════════════════════════════
RATE_LIMIT_BYPASS_IPS = {
    "127.0.0.1",  # Localhost
    "::1",        # IPv6 localhost
    # Add Render internal IPs, DataDog agents, load balancers, etc.
}

def should_bypass_rate_limit(request: Request) -> bool:
    """
    Check if request should bypass rate limiting.
    
    Use cases:
    - Health checks from load balancers
    - Internal service-to-service calls
    - Monitoring probes (DataDog, Pingdom)
    - Load testing with X-Internal-Request header
    
    Returns:
        True if should bypass, False otherwise
    """
    # Check for internal request header
    if request.headers.get("X-Internal-Request") == "true":
        return True
    
    # Check IP whitelist
    client_ip = get_remote_address(request)
    if client_ip in RATE_LIMIT_BYPASS_IPS:
        return True
    
    # Check for health check paths
    if request.url.path in ["/health", "/healthz", "/"]:
        return True
    
    return False
