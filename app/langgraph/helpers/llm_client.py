"""
LLM Client Wrapper with Retry Logic and Structured Outputs
Provides unified interface for all LangGraph nodes to call LLMs.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Type, TypeVar
from pydantic import BaseModel
import openai
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Remove global client = AsyncOpenAI()

def get_client() -> AsyncOpenAI:
    """Lazy initialization of OpenAI client."""
    from app.core.config import settings
    # Initialize with settings key, falling back to env var if explicit
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def call_llm(
    prompt: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 1000
) -> str:
    """
    Call LLM with simple text prompt, return text response.
    """
    try:
        client = get_client()
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        raise


async def call_llm_structured(
    prompt: str,
    response_model: Type[T],
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_retries: int = 2
) -> T:
    """
    Call LLM with structured output using Pydantic model.
    """
    client = get_client()
    for attempt in range(max_retries + 1):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            data = json.loads(content)
            
            # Parse with Pydantic
            return response_model.model_validate(data)
            
        except (json.JSONDecodeError, ValueError) as e:
            if attempt == max_retries:
                logger.error(f"Failed to parse structured output after {max_retries} retries: {e}")
                raise
            
            logger.warning(f"Retry {attempt + 1}/{max_retries} due to parse error: {e}")
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
            
        except Exception as e:
            logger.error(f"LLM structured call failed: {e}")
            raise


async def call_llm_with_retry(
    prompt: str,
    model: str = "gpt-4o-mini",
    max_retries: int = 3,
    fallback_response: Optional[str] = None
) -> str:
    """
    Call LLM with automatic retry logic and fallback.
    """
    for attempt in range(max_retries):
        try:
            return await call_llm(prompt, model=model)
            
        except Exception as e:
            if attempt == max_retries - 1:
                if fallback_response:
                    logger.warning(f"All retries failed, using fallback: {e}")
                    return fallback_response
                else:
                    raise
            
            logger.warning(f"Retry {attempt + 1}/{max_retries}: {e}")
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
    
    return fallback_response or ""
