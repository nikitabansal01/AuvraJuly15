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

# Initialize OpenAI client
client = AsyncOpenAI()

T = TypeVar('T', bound=BaseModel)


async def call_llm(
    prompt: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 1000
) -> str:
    """
    Call LLM with simple text prompt, return text response.
    
    Args:
        prompt: The prompt to send
        model: OpenAI model name
        temperature: Sampling temperature
        max_tokens: Maximum response tokens
        
    Returns:
        LLM response as string
    """
    try:
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
    
    Args:
        prompt: The prompt to send
        response_model: Pydantic model for structured output
        model: OpenAI model name
        temperature: Sampling temperature
        max_retries: Number of retry attempts
        
    Returns:
        Parsed Pydantic model instance
    """
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
    
    Args:
        prompt: The prompt to send
        model: OpenAI model name
        max_retries: Maximum retry attempts
        fallback_response: Response to return if all retries fail
        
    Returns:
        LLM response or fallback
    """
    for attempt in range(max_retries):
        try:
            return await call_llm(prompt, model=model)
            
        except Exception as e:
            if attempt == max_retries - 1:
                # Final attempt failed
                if fallback_response:
                    logger.warning(f"All retries failed, using fallback: {e}")
                    return fallback_response
                else:
                    raise
            
            logger.warning(f"Retry {attempt + 1}/{max_retries}: {e}")
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
    
    # Should never reach here
    return fallback_response or ""
