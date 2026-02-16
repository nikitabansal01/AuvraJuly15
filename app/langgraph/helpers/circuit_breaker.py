"""
Circuit Breaker Pattern for LLM Calls
Prevents cascading failures when external APIs (OpenAI, Groq) experience outages.

Pattern: Netflix Hystrix-style circuit breaker
- CLOSED: Normal operation, requests go through
- OPEN: After N failures, stop making requests (fast-fail)
- HALF_OPEN: After timeout, try 1 request to test recovery

Why this matters:
- OpenAI outage → Without breaker: ALL requests wait 12s timeout → server crash
- OpenAI outage → With breaker: First 5 fail → circuit opens → instant 503 response
"""

import asyncio
import logging
from typing import Optional, Callable, Any
from aiobreaker import CircuitBreaker, CircuitBreakerError
from functools import wraps

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Circuit Breakers (one per external service)
# ═══════════════════════════════════════════════════════════════════════════

openai_breaker = CircuitBreaker(
    fail_max=5,              # Open after 5 consecutive failures
    timeout_duration=30,     # Stay open for 30 seconds
    name="openai_api",
    exclude=[asyncio.TimeoutError]  # Don't count timeouts (handled by retry logic)
)

groq_breaker = CircuitBreaker(
    fail_max=5,
    timeout_duration=30,
    name="groq_api",
    exclude=[asyncio.TimeoutError]
)


# ═══════════════════════════════════════════════════════════════════════════
# Protected Wrappers
# ═══════════════════════════════════════════════════════════════════════════

async def call_with_circuit_breaker(
    primary_func: Callable,
    fallback_func: Optional[Callable] = None,
    breaker: CircuitBreaker = openai_breaker,
    fallback_breaker: Optional[CircuitBreaker] = None,
    *args,
    **kwargs
) -> Any:
    """
    Call function with circuit breaker protection + optional fallback.
    
    Args:
        primary_func: Main function to call (e.g., OpenAI API)
        fallback_func: Fallback function if primary fails (e.g., Groq API)
        breaker: Circuit breaker for primary
        fallback_breaker: Circuit breaker for fallback (optional)
        *args, **kwargs: Arguments to pass to functions
    
    Returns:
        Result from primary or fallback
    
    Raises:
        CircuitBreakerError: If both primary and fallback circuits are open
        Exception: If all attempts fail
    """
    
    # Try primary with circuit breaker
    try:
        result = await breaker.call(primary_func, *args, **kwargs)
        return result
    
    except CircuitBreakerError as e:
        # Circuit is OPEN - primary service is down
        logger.warning(
            f"Circuit breaker OPEN for {breaker.name} "
            f"(failures: {breaker.fail_counter}/{breaker.fail_max}, "
            f"opened_at: {breaker.opened_at})"
        )
        
        # Try fallback if available
        if fallback_func and fallback_breaker:
            try:
                logger.info(f"Attempting fallback to {fallback_breaker.name}")
                result = await fallback_breaker.call(fallback_func, *args, **kwargs)
                logger.info(f"Fallback to {fallback_breaker.name} succeeded")
                return result
            
            except CircuitBreakerError:
                logger.error(f"Both {breaker.name} and {fallback_breaker.name} circuits are OPEN")
                raise
            
            except Exception as fallback_error:
                logger.error(f"Fallback to {fallback_breaker.name} failed: {fallback_error}")
                raise
        
        else:
            # No fallback available
            raise
    
    except Exception as e:
        # Unexpected error - let it propagate (breaker will count it)
        logger.error(f"Error in primary function: {e}")
        raise


def with_circuit_breaker(
    breaker: CircuitBreaker = openai_breaker,
    fallback_breaker: Optional[CircuitBreaker] = None
):
    """
    Decorator to add circuit breaker protection to async functions.
    
    Usage:
        @with_circuit_breaker(breaker=openai_breaker)
        async def call_openai_api(...):
            return await client.chat.completions.create(...)
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await breaker.call(func, *args, **kwargs)
            except CircuitBreakerError:
                logger.warning(f"Circuit breaker OPEN for {func.__name__}")
                # Return safe default or raise
                raise
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════════════════
# Degraded Response Helpers
# ═══════════════════════════════════════════════════════════════════════════

def get_degraded_response(intent: str = "general") -> dict:
    """
    Return a safe degraded response when all LLM services are down.
    
    This prevents users from seeing blank screens or 500 errors.
    Instead, they get a transparent message about temporary issues.
    """
    
    degraded_responses = {
        "complete_action": (
            "I'm experiencing temporary technical issues. "
            "Your completion was recorded, but I can't generate a personalized celebration right now. "
            "Great work! 🎉"
        ),
        "skip_action": (
            "I'm temporarily unable to process your request. "
            "Please try again in 30 seconds."
        ),
        "general": (
            "I'm experiencing technical difficulties and temporarily unable to respond. "
            "Please try again in 30 seconds. "
            "Your data is safe - this is just a temporary service interruption."
        ),
        "request_alternates": (
            "I'm temporarily unable to generate alternatives. "
            "Please try again in 30 seconds, or refresh your plan later when you have tokens available."
        )
    }
    
    return {
        "response": degraded_responses.get(intent, degraded_responses["general"]),
        "degraded": True,
        "retry_after": 30
    }


# ═══════════════════════════════════════════════════════════════════════════
# Health Check & Metrics
# ═══════════════════════════════════════════════════════════════════════════

def get_circuit_breaker_status() -> dict:
    """
    Get current status of all circuit breakers.
    Useful for health checks and monitoring dashboards.
    
    Returns:
        {
            "openai": {"state": "closed", "failures": 0, "opened_at": None},
            "groq": {"state": "closed", "failures": 2, "opened_at": None}
        }
    """
    def breaker_info(b: CircuitBreaker) -> dict:
        return {
            "name": b.name,
            "state": b.current_state,
            "failures": b.fail_counter,
            "fail_max": b.fail_max,
            "opened_at": b.opened_at.isoformat() if b.opened_at else None,
            "timeout_duration": b.timeout_duration
        }
    
    return {
        "openai": breaker_info(openai_breaker),
        "groq": breaker_info(groq_breaker)
    }


async def reset_circuit_breakers():
    """
    Manually reset all circuit breakers.
    Use this for manual recovery or testing.
    """
    openai_breaker._state = openai_breaker._closed_state
    openai_breaker.fail_counter = 0
    openai_breaker.opened_at = None
    
    groq_breaker._state = groq_breaker._closed_state
    groq_breaker.fail_counter = 0
    groq_breaker.opened_at = None
    
    logger.info("All circuit breakers manually reset")
