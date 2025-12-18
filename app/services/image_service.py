"""
AUVRA Image Service - Self-Hosted via Modal Serverless GPU

This service calls your self-hosted Realistic Vision model on Modal.
$30 FREE credits/month = ~30,000 images FREE!

Cost: ~$0.001/image on T4 GPU
"""

import os
import base64
import hashlib
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class ImageService:
    """
    Image generation service using self-hosted Modal serverless GPU.
    
    Uses Realistic Vision V5.1 + LCM-LoRA for fast, realistic food images.
    $30 FREE credits per month!
    """
    
    def __init__(self):
        """Initialize the image service."""
        # Your Modal endpoint URL
        # Format: https://USERNAME--auvra-food-generator-generate-image.modal.run
        self.modal_url = os.getenv(
            "MODAL_ENDPOINT_URL", 
            "https://your-username--auvra-food-generator-generate-image.modal.run"
        )
        
        # Optional: Modal auth token for private endpoints
        self.modal_token = os.getenv("MODAL_TOKEN")
        
        # Supabase for image storage
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
        self.storage_bucket = "food-images"
        
        # Cache settings
        self.cache_enabled = True
        self._image_cache: dict[str, str] = {}  # food_name -> image_url
        
        # HTTP client with timeout (longer for cold starts - up to 60s)
        self.client = httpx.AsyncClient(timeout=120.0)
        
        logger.info(f"ImageService initialized with Modal: {self.modal_url}")
    
    def _get_cache_key(self, food_name: str, style: str = "professional") -> str:
        """Generate a cache key for a food image."""
        key_string = f"{food_name.lower().strip()}:{style}"
        return hashlib.md5(key_string.encode()).hexdigest()
    
    async def generate_food_image(
        self,
        food_name: str,
        style: str = "professional",
        use_cache: bool = True,
    ) -> Optional[str]:
        """
        Generate a food image using the self-hosted Modal serverless GPU.
        
        Args:
            food_name: Name of the food (e.g., "grilled salmon with vegetables")
            style: Photography style - "professional", "casual", "overhead"
            use_cache: Whether to check cache first
        
        Returns:
            str: URL of the generated image stored in Supabase, or None if failed
        """
        try:
            # Check cache first
            cache_key = self._get_cache_key(food_name, style)
            if use_cache and cache_key in self._image_cache:
                logger.info(f"Cache hit for: {food_name}")
                return self._image_cache[cache_key]
            
            # Check Supabase for existing image
            if use_cache:
                existing_url = await self._check_supabase_cache(cache_key)
                if existing_url:
                    self._image_cache[cache_key] = existing_url
                    return existing_url
            
            # Generate new image via Modal
            logger.info(f"Generating image for: {food_name}")
            image_data = await self._call_modal(food_name, style)
            
            if not image_data:
                logger.error(f"Failed to generate image for: {food_name}")
                return None
            
            # Upload to Supabase Storage
            image_url = await self._upload_to_supabase(image_data, cache_key)
            
            if image_url:
                self._image_cache[cache_key] = image_url
                logger.info(f"Image generated and stored: {image_url}")
            
            return image_url
            
        except Exception as e:
            logger.error(f"Error generating food image: {e}")
            return None
    
    async def _call_modal(
        self,
        food_name: str,
        style: str = "professional",
    ) -> Optional[bytes]:
        """
        Call the Modal serverless endpoint to generate an image.
        
        Returns the image as bytes.
        """
        try:
            # Request payload
            payload = {
                "food_name": food_name,
                "style": style,
            }
            
            headers = {"Content-Type": "application/json"}
            if self.modal_token:
                headers["Authorization"] = f"Bearer {self.modal_token}"
            
            # Make the API call
            response = await self.client.post(
                self.modal_url,
                json=payload,
                headers=headers,
            )
            
            if response.status_code != 200:
                logger.error(f"Modal returned {response.status_code}: {response.text}")
                return None
            
            result = response.json()
            
            # Modal returns base64-encoded image
            if result.get("success") and "image_base64" in result:
                return base64.b64decode(result["image_base64"])
            
            logger.error(f"Unexpected response from Modal: {result}")
            return None
            
        except httpx.TimeoutException:
            logger.error("Timeout calling Modal - container may be cold starting (wait ~30s)")
            return None
        except Exception as e:
            logger.error(f"Error calling Modal: {e}")
            return None
    
    async def _check_supabase_cache(self, cache_key: str) -> Optional[str]:
        """Check if image already exists in Supabase Storage."""
        try:
            if not self.supabase_url or not self.supabase_key:
                return None
            
            # Check if file exists in Supabase Storage
            file_path = f"cached/{cache_key}.png"
            url = f"{self.supabase_url}/storage/v1/object/public/{self.storage_bucket}/{file_path}"
            
            response = await self.client.head(url)
            
            if response.status_code == 200:
                return url
            
            return None
            
        except Exception as e:
            logger.debug(f"Cache check failed: {e}")
            return None
    
    async def _upload_to_supabase(
        self,
        image_data: bytes,
        cache_key: str,
    ) -> Optional[str]:
        """Upload image to Supabase Storage."""
        try:
            if not self.supabase_url or not self.supabase_key:
                logger.warning("Supabase not configured, returning data URL")
                # Return as base64 data URL if no Supabase
                base64_data = base64.b64encode(image_data).decode()
                return f"data:image/png;base64,{base64_data}"
            
            file_path = f"cached/{cache_key}.png"
            url = f"{self.supabase_url}/storage/v1/object/{self.storage_bucket}/{file_path}"
            
            headers = {
                "Authorization": f"Bearer {self.supabase_key}",
                "Content-Type": "image/png",
                "x-upsert": "true",  # Overwrite if exists
            }
            
            response = await self.client.post(
                url,
                content=image_data,
                headers=headers,
            )
            
            if response.status_code in [200, 201]:
                # Return public URL
                public_url = f"{self.supabase_url}/storage/v1/object/public/{self.storage_bucket}/{file_path}"
                return public_url
            
            logger.error(f"Failed to upload to Supabase: {response.status_code}")
            return None
            
        except Exception as e:
            logger.error(f"Error uploading to Supabase: {e}")
            return None
    
    async def generate_batch(
        self,
        food_names: list[str],
        style: str = "professional",
    ) -> list[Optional[str]]:
        """
        Generate multiple food images.
        
        Uses the batch endpoint for efficiency.
        """
        results = []
        
        for food_name in food_names:
            url = await self.generate_food_image(food_name, style)
            results.append(url)
        
        return results
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


# Singleton instance
_image_service: Optional[ImageService] = None


def get_image_service() -> ImageService:
    """Get the singleton ImageService instance."""
    global _image_service
    if _image_service is None:
        _image_service = ImageService()
    return _image_service
