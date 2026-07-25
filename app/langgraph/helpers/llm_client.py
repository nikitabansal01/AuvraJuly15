"""
LLM Client Wrapper with Retry Logic and Structured Outputs
Provides unified interface for all LangGraph nodes to call LLMs.
Includes Groq fallback for reliability.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional, Type, TypeVar
from pydantic import BaseModel
import openai
from openai import AsyncOpenAI
import httpx

logger = logging.getLogger(__name__)

_client: Optional[AsyncOpenAI] = None


def _get_openai_client() -> AsyncOpenAI:
    """Create the SDK client only when an LLM call is actually made."""
    global _client
    if _client is None:
        _client = AsyncOpenAI()
    return _client

# Groq fallback configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")
GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"

T = TypeVar('T', bound=BaseModel)


async def _call_groq_fallback(
    prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 1000,
    response_format: Optional[dict] = None
) -> str:
    """Call Groq as fallback when OpenAI fails."""
    if not GROQ_API_KEY:
        raise Exception("Groq API key not configured for fallback")
    
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        payload = {
            "model": GROQ_FALLBACK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        # Add JSON mode if requested (check if model supports it)
        if response_format and response_format.get("type") == "json_object":
            # Groq's llama models may not support response_format, so add instruction
            payload["messages"][0]["content"] = prompt + "\n\nIMPORTANT: Respond with valid JSON only. No markdown."
        
        response = await http_client.post(
            GROQ_BASE_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload
        )
        
        if response.status_code != 200:
            raise Exception(f"Groq API error: {response.status_code} - {response.text}")
        
        data = response.json()
        return data["choices"][0]["message"]["content"]


async def call_llm(
    prompt: str,
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 1000
) -> str:
    """
    Call LLM with simple text prompt, return text response.
    Falls back to Groq if OpenAI fails.
    
    Args:
        prompt: The prompt to send
        model: OpenAI model name
        temperature: Sampling temperature
        max_tokens: Maximum response tokens
        
    Returns:
        LLM response as string
    """
    openai_error = None
    
    try:
        response = await _get_openai_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_completion_tokens=max_tokens
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        openai_error = str(e)
        logger.warning(f"OpenAI call failed, trying Groq fallback: {e}")
    
    # Groq fallback
    if GROQ_API_KEY:
        try:
            result = await _call_groq_fallback(prompt, temperature, max_tokens)
            logger.info("Successfully used Groq fallback")
            return result
        except Exception as groq_e:
            logger.error(f"Groq fallback also failed: {groq_e}")
            raise Exception(f"Both OpenAI and Groq failed. OpenAI: {openai_error}, Groq: {groq_e}")
    else:
        raise Exception(f"OpenAI failed and no Groq fallback configured: {openai_error}")


async def call_llm_structured(
    prompt: str,
    response_model: Type[T],
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_retries: int = 2
) -> T:
    """
    Call LLM with structured output using Pydantic model.
    Falls back to Groq if OpenAI fails.
    
    Args:
        prompt: The prompt to send
        response_model: Pydantic model for structured output
        model: OpenAI model name
        temperature: Sampling temperature
        max_retries: Number of retry attempts
        
    Returns:
        Parsed Pydantic model instance
    """
    openai_error = None
    
    for attempt in range(max_retries + 1):
        try:
            response = await _get_openai_client().chat.completions.create(
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
                openai_error = f"Parse error after {max_retries} retries: {e}"
                logger.warning(openai_error)
                break  # Try Groq fallback
            
            logger.warning(f"Retry {attempt + 1}/{max_retries} due to parse error: {e}")
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
            
        except Exception as e:
            openai_error = str(e)
            logger.warning(f"OpenAI structured call failed: {e}")
            break  # Try Groq fallback
    
    # Groq fallback for structured output
    if GROQ_API_KEY:
        try:
            logger.info(f"Trying Groq fallback for structured output")
            content = await _call_groq_fallback(
                prompt, 
                temperature, 
                max_tokens=2000,
                response_format={"type": "json_object"}
            )
            
            # Clean markdown if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            
            data = json.loads(content.strip())
            logger.info("Successfully used Groq fallback for structured output")
            return response_model.model_validate(data)
            
        except Exception as groq_e:
            logger.error(f"Groq structured fallback also failed: {groq_e}")
            raise Exception(f"Both OpenAI and Groq structured calls failed. OpenAI: {openai_error}, Groq: {groq_e}")
    else:
        raise Exception(f"OpenAI structured call failed and no Groq fallback: {openai_error}")
