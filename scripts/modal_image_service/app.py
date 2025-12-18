"""
AUVRA Food Image Generator - Modal Serverless GPU

Following Modal's official best practices:
- T4 GPU: $0.000164/second (~$0.59/hr)
- @modal.enter(): Model loads ONCE per container start
- Volume: Model cached, never re-downloaded
- LCM-LoRA: 4-step inference (ultra fast)

Deploy: modal deploy app.py
Test:   See docs at endpoint URL
"""

import modal
from pydantic import BaseModel

# Request model for JSON body
class ImageRequest(BaseModel):
    food_name: str
    style: str = "professional"

# ============================================================================
# CONFIGURATION
# ============================================================================
APP_NAME = "auvra-food"
MODEL_ID = "SG161222/Realistic_Vision_V5.1_noVAE"
LCM_LORA_ID = "latent-consistency/lcm-lora-sdv1-5"
CACHE_DIR = "/cache"

# ============================================================================
# MODAL APP
# ============================================================================
app = modal.App(APP_NAME)

# Persistent volume for model weights
cache_volume = modal.Volume.from_name("auvra-cache", create_if_missing=True)

# Container image - pin numpy<2 for torch 2.1.2 compatibility
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "numpy<2",  # MUST be first - torch 2.1.2 requires numpy 1.x
        "torch==2.1.2",
        "diffusers==0.25.1", 
        "transformers==4.36.2",
        "accelerate==0.25.0",
        "safetensors==0.4.1",
        "peft==0.7.1",
        "Pillow==10.1.0",
        "huggingface_hub==0.20.3",
        "fastapi",
    )
    .env({
        "HF_HOME": CACHE_DIR,
        "HF_HUB_CACHE": CACHE_DIR,
    })
)

# ============================================================================
# INFERENCE CLASS - GPU
# ============================================================================
@app.cls(
    image=image,
    gpu="T4",
    timeout=300,
    volumes={CACHE_DIR: cache_volume},
    scaledown_window=30,
)
class FoodGenerator:
    """Generate food images with Stable Diffusion + LCM-LoRA."""

    @modal.enter()
    def setup(self):
        """Load model once when container starts."""
        import torch
        from diffusers import StableDiffusionPipeline, LCMScheduler

        print(f"Loading {MODEL_ID}...")
        
        self.pipe = StableDiffusionPipeline.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.float16,
            safety_checker=None,
            requires_safety_checker=False,
            cache_dir=CACHE_DIR,
        )
        
        print(f"Loading {LCM_LORA_ID}...")
        self.pipe.load_lora_weights(LCM_LORA_ID, cache_dir=CACHE_DIR)
        self.pipe.fuse_lora()
        
        self.pipe.scheduler = LCMScheduler.from_config(self.pipe.scheduler.config)
        self.pipe = self.pipe.to("cuda")
        self.pipe.enable_attention_slicing()
        
        cache_volume.commit()
        print("✅ Ready!")

    @modal.method()
    def generate(self, food_name: str, style: str = "professional") -> dict:
        """Generate food image, return base64."""
        import torch
        import io
        import base64
        import random

        styles = {
            "professional": "professional food photography, studio lighting, shallow depth of field, elegant plating",
            "casual": "casual home cooking, natural lighting, appetizing",
            "overhead": "overhead flat lay, top-down view, clean background",
        }
        style_text = styles.get(style, styles["professional"])

        prompt = f"((masterpiece)), ((best quality)), ((photorealistic)), {food_name}, {style_text}, highly detailed, 8k"
        negative = "cartoon, anime, blurry, low quality, deformed, text, watermark"

        seed = random.randint(0, 2**31)
        generator = torch.Generator("cuda").manual_seed(seed)

        print(f"Generating: {food_name}")
        
        result = self.pipe(
            prompt=prompt,
            negative_prompt=negative,
            num_inference_steps=4,
            guidance_scale=1.0,
            generator=generator,
            width=512,
            height=512,
        )

        buf = io.BytesIO()
        result.images[0].save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        return {
            "success": True,
            "image_base64": img_b64,
            "food_name": food_name,
            "style": style,
            "seed": seed,
        }

    @modal.fastapi_endpoint(method="POST", docs=True)
    def api(self, request: ImageRequest):
        """
        POST endpoint for image generation.
        
        Body: {"food_name": "salmon", "style": "professional"}
        """
        return self.generate.local(request.food_name, request.style)

    @modal.fastapi_endpoint(method="GET", docs=True)
    def health(self):
        """Health check."""
        return {"status": "ok", "model": MODEL_ID}


# ============================================================================
# LOCAL TEST
# ============================================================================
@app.local_entrypoint()
def main(food: str = "grilled salmon with lemon"):
    """Test: modal run app.py --food "avocado toast" """
    import base64
    from pathlib import Path

    gen = FoodGenerator()
    result = gen.generate.remote(food)
    
    path = Path(f"/tmp/{food.replace(' ', '_')}.png")
    path.write_bytes(base64.b64decode(result["image_base64"]))
    print(f"✅ Saved: {path}")
