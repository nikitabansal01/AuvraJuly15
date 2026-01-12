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
                # Use whisper-1 (proven reliability)
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
                    "confidence": result.get("confidence", 0.95)
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
    
    async def generate_speech(
        self,
        text: str,
        voice: str = "nova",
        speed: float = 1.0,
        model: str = "tts-1"
    ) -> Dict[str, Any]:
        """
        Generate speech from text using OpenAI TTS.
        
        This makes Auvra SPEAK! Premium conversational experience.
        
        Args:
            text: Text to convert to speech
            voice: Voice to use (alloy, echo, fable, onyx, nova, shimmer)
            speed: Speed of speech (0.25 to 4.0)
            model: TTS model (tts-1 for speed, tts-1-hd for quality)
        
        Returns:
            Dict with audio bytes and metadata
        """
        try:
            logger.info(f"🎤 Generating speech: {len(text)} chars, voice={voice}, model={model}")
            
            # Validate voice
            valid_voices = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
            if voice not in valid_voices:
                logger.warning(f"Invalid voice '{voice}', defaulting to 'nova'")
                voice = "nova"
            
            # Validate speed
            speed = max(0.25, min(4.0, speed))
            
            # Generate speech
            response = await self.client.audio.speech.create(
                model=model,
                voice=voice,
                input=text,
                speed=speed
            )
            
            # Get audio bytes
            audio_bytes = response.content
            
            logger.info(f"✅ Speech generated: {len(audio_bytes)} bytes")
            
            return {
                "success": True,
                "audio_bytes": audio_bytes,
                "audio_base64": base64.b64encode(audio_bytes).decode('utf-8'),
                "format": "mp3",
                "voice": voice,
                "model": model,
                "text_length": len(text),
                "audio_size_kb": round(len(audio_bytes) / 1024, 2)
            }
            
        except Exception as e:
            logger.error(f"❌ TTS generation error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "audio_bytes": None
            }
    
    async def generate_speech_streaming(
        self,
        text: str,
        voice: str = "nova",
        speed: float = 1.0
    ):
        """
        Stream speech generation for longer responses.
        Yields audio chunks as they're generated.
        """
        try:
            logger.info(f"🎤 Streaming speech generation: voice={voice}")
            
            # For now, OpenAI doesn't support true TTS streaming
            # So we generate full audio and yield it in chunks
            result = await self.generate_speech(text, voice, speed)
            
            if result["success"]:
                audio_bytes = result["audio_bytes"]
                chunk_size = 4096  # 4KB chunks
                
                for i in range(0, len(audio_bytes), chunk_size):
                    chunk = audio_bytes[i:i + chunk_size]
                    yield {
                        "type": "audio_chunk",
                        "data": base64.b64encode(chunk).decode('utf-8'),
                        "chunk_index": i // chunk_size,
                        "is_final": i + chunk_size >= len(audio_bytes)
                    }
            else:
                yield {
                    "type": "error",
                    "error": result.get("error", "TTS generation failed")
                }
                
        except Exception as e:
            logger.error(f"❌ Streaming TTS error: {str(e)}")
            yield {
                "type": "error",
                "error": str(e)
            }
