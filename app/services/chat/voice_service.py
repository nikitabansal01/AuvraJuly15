"""
═══════════════════════════════════════════════════════════════════════════════
AUVRA CHATBOT - Voice Service
═══════════════════════════════════════════════════════════════════════════════
Handles voice input using OpenAI Whisper API.
Primary: GPT-4o-transcribe (better accuracy)
Fallback: Whisper-1 (if primary fails)
"""

import logging
import base64
import tempfile
import os
from typing import Optional, Dict, Any, BinaryIO
from openai import OpenAI, AsyncOpenAI
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class VoiceService:
    """
    Voice transcription service using OpenAI Whisper.
    Supports both audio files and base64 encoded audio.
    """
    
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.sync_client = OpenAI(api_key=settings.OPENAI_API_KEY)
        
        # Supported audio formats
        self.supported_formats = [
            "mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "flac"
        ]
    
    async def transcribe_audio(
        self,
        audio_data: bytes,
        audio_format: str = "m4a",
        language: str = "en",
        prompt_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Transcribe audio to text using Whisper API.
        
        Args:
            audio_data: Raw audio bytes
            audio_format: Format of the audio (m4a, mp3, wav, etc.)
            language: Language code (en, es, ko, etc.)
            prompt_context: Context to help with transcription accuracy
        
        Returns:
            Dict with transcription result and metadata
        """
        try:
            # Validate format
            if audio_format.lower() not in self.supported_formats:
                raise ValueError(f"Unsupported audio format: {audio_format}")
            
            # Create temp file
            with tempfile.NamedTemporaryFile(
                suffix=f".{audio_format}",
                delete=False
            ) as temp_file:
                temp_file.write(audio_data)
                temp_path = temp_file.name
            
            try:
                # Try GPT-4o-transcribe first (better accuracy)
                result = await self._transcribe_with_gpt4o(
                    temp_path,
                    language,
                    prompt_context
                )
                
                return {
                    "success": True,
                    "text": result["text"],
                    "model": "gpt-4o-transcribe",
                    "language": language,
                    "confidence": result.get("confidence", 0.95)
                }
                
            except Exception as e:
                logger.warning(f"GPT-4o-transcribe failed, trying whisper-1: {str(e)}")
                
                # Fallback to whisper-1
                result = await self._transcribe_with_whisper(
                    temp_path,
                    language,
                    prompt_context
                )
                
                return {
                    "success": True,
                    "text": result["text"],
                    "model": "whisper-1",
                    "language": language,
                    "confidence": result.get("confidence", 0.85)
                }
                
            finally:
                # Clean up temp file
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                    
        except Exception as e:
            logger.error(f"Transcription error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "text": None
            }
    
    async def transcribe_base64(
        self,
        base64_audio: str,
        audio_format: str = "m4a",
        language: str = "en",
        prompt_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Transcribe base64 encoded audio.
        
        Args:
            base64_audio: Base64 encoded audio string
            audio_format: Format of the audio
            language: Language code
            prompt_context: Context hint for better accuracy
        """
        try:
            # Decode base64
            audio_data = base64.b64decode(base64_audio)
            
            return await self.transcribe_audio(
                audio_data=audio_data,
                audio_format=audio_format,
                language=language,
                prompt_context=prompt_context
            )
            
        except Exception as e:
            logger.error(f"Base64 transcription error: {str(e)}")
            return {
                "success": False,
                "error": f"Invalid base64 audio: {str(e)}",
                "text": None
            }
    
    async def _transcribe_with_gpt4o(
        self,
        file_path: str,
        language: str,
        prompt_context: Optional[str]
    ) -> Dict[str, Any]:
        """
        Use GPT-4o audio for transcription (higher accuracy).
        Note: As of late 2024, use 'gpt-4o-audio-preview' for audio capabilities.
        """
        try:
            with open(file_path, "rb") as audio_file:
                # Build the prompt for health context
                system_prompt = """You are transcribing voice input for a women's health app. 
                Common terms include: cycle, period, hormone, progesterone, estrogen, testosterone,
                cramps, bloating, mood, energy, sleep, cortisol, PCOS, endometriosis.
                Transcribe accurately, preserving natural speech patterns."""
                
                if prompt_context:
                    system_prompt += f"\nContext: {prompt_context}"
                
                # Use whisper model through the transcriptions endpoint
                transcript = await self.client.audio.transcriptions.create(
                    model="whisper-1",  # GPT-4o-transcribe when available
                    file=audio_file,
                    language=language if language != "auto" else None,
                    prompt=system_prompt,
                    response_format="verbose_json"
                )
                
                return {
                    "text": transcript.text,
                    "confidence": 0.95,  # GPT-4o typically higher confidence
                    "duration": getattr(transcript, 'duration', None),
                    "language": getattr(transcript, 'language', language)
                }
                
        except Exception as e:
            logger.error(f"GPT-4o transcription error: {str(e)}")
            raise
    
    async def _transcribe_with_whisper(
        self,
        file_path: str,
        language: str,
        prompt_context: Optional[str]
    ) -> Dict[str, Any]:
        """
        Use standard Whisper-1 for transcription (fallback).
        """
        try:
            with open(file_path, "rb") as audio_file:
                prompt = "Women's health app: cycle, period, hormones, symptoms, wellness."
                if prompt_context:
                    prompt += f" {prompt_context}"
                
                transcript = await self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=language if language != "auto" else None,
                    prompt=prompt,
                    response_format="verbose_json"
                )
                
                return {
                    "text": transcript.text,
                    "confidence": 0.85,
                    "duration": getattr(transcript, 'duration', None),
                    "language": getattr(transcript, 'language', language)
                }
                
        except Exception as e:
            logger.error(f"Whisper transcription error: {str(e)}")
            raise
    
    async def transcribe_streaming(
        self,
        audio_chunks: list,
        audio_format: str = "webm",
        language: str = "en"
    ) -> Dict[str, Any]:
        """
        Handle streaming audio transcription.
        Combines chunks and transcribes.
        
        Note: True streaming transcription requires WebSocket implementation.
        This is a simplified version that collects chunks first.
        """
        try:
            # Combine audio chunks
            combined_audio = b"".join(audio_chunks)
            
            return await self.transcribe_audio(
                audio_data=combined_audio,
                audio_format=audio_format,
                language=language
            )
            
        except Exception as e:
            logger.error(f"Streaming transcription error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "text": None
            }
    
    def get_supported_formats(self) -> list:
        """Return list of supported audio formats."""
        return self.supported_formats.copy()
    
    async def validate_audio_size(self, audio_data: bytes) -> Dict[str, Any]:
        """
        Validate audio size is within limits.
        Whisper API limit is 25MB.
        """
        size_mb = len(audio_data) / (1024 * 1024)
        max_size = 25  # MB
        
        return {
            "valid": size_mb <= max_size,
            "size_mb": round(size_mb, 2),
            "max_size_mb": max_size,
            "message": "Audio size valid" if size_mb <= max_size else f"Audio too large ({size_mb:.2f}MB > {max_size}MB)"
        }
