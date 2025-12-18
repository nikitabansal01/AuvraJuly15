"""
AUVRA Food Image Generator - HuggingFace ZeroGPU Space
Self-hosted Realistic Vision V5.1 + LCM-LoRA

Deploy this to HuggingFace Spaces with ZeroGPU hardware.
FREE GPU access with H200 (70GB VRAM)!

Usage:
1. Create a new HuggingFace Space (Gradio SDK)
2. Select ZeroGPU hardware
3. Upload this file as app.py
4. Add requirements.txt
"""

import spaces
import gradio as gr
import torch
from diffusers import StableDiffusionPipeline, LCMScheduler
from PIL import Image
import io
import base64

# Model configuration
MODEL_ID = "SG161222/Realistic_Vision_V5.1_noVAE"
LCM_LORA_ID = "latent-consistency/lcm-lora-sdv1-5"

# Global pipeline (loaded once, reused)
pipe = None

def load_pipeline():
    """Load the Realistic Vision pipeline with LCM-LoRA."""
    global pipe
    if pipe is not None:
        return pipe
    
    print("Loading Realistic Vision V5.1...")
    pipe = StableDiffusionPipeline.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.float16,
        safety_checker=None,
        requires_safety_checker=False,
    )
    
    # Load LCM-LoRA for fast inference (4-8 steps instead of 50)
    print("Loading LCM-LoRA...")
    pipe.load_lora_weights(LCM_LORA_ID)
    pipe.fuse_lora()
    
    # Use LCM scheduler for fast inference
    pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
    
    # Move to GPU
    pipe.to("cuda")
    
    # Enable memory optimizations
    pipe.enable_attention_slicing()
    
    print("Pipeline ready!")
    return pipe


# Pre-load pipeline on startup (outside GPU decorator for faster first inference)
load_pipeline()


@spaces.GPU(duration=30)  # 30 second max per generation
def generate_food_image(
    food_name: str,
    style: str = "professional",
    guidance_scale: float = 1.5,
    num_steps: int = 6,
    seed: int = -1,
) -> tuple[Image.Image, str]:
    """
    Generate a realistic food image.
    
    Args:
        food_name: Name of the food (e.g., "salmon with vegetables")
        style: Image style - "professional", "casual", "overhead"
        guidance_scale: CFG scale (1.0-2.0 for LCM)
        num_steps: Inference steps (4-8 for LCM)
        seed: Random seed (-1 for random)
    
    Returns:
        tuple: (PIL Image, generation info string)
    """
    global pipe
    
    # Ensure pipeline is loaded
    if pipe is None:
        pipe = load_pipeline()
    
    # Style-specific prompt modifiers
    style_prompts = {
        "professional": "professional food photography, studio lighting, shallow depth of field, 85mm lens, michelin star presentation",
        "casual": "casual food photo, natural lighting, home kitchen setting, appetizing",
        "overhead": "overhead flat lay food photography, top-down view, clean background, instagram style",
    }
    
    style_modifier = style_prompts.get(style, style_prompts["professional"])
    
    # Build the prompt
    prompt = f"((masterpiece)), ((best quality)), ((ultra realistic)), {food_name}, {style_modifier}, 8k uhd, highly detailed"
    
    # Negative prompt for better quality
    negative_prompt = "cartoon, anime, illustration, painting, drawing, blurry, low quality, deformed, ugly, text, watermark, logo"
    
    # Set seed
    generator = None
    if seed >= 0:
        generator = torch.Generator(device="cuda").manual_seed(seed)
    else:
        import random
        seed = random.randint(0, 2147483647)
        generator = torch.Generator(device="cuda").manual_seed(seed)
    
    # Generate image
    result = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt,
        num_inference_steps=num_steps,
        guidance_scale=guidance_scale,
        generator=generator,
        width=512,
        height=512,
    )
    
    image = result.images[0]
    
    info = f"Generated: {food_name}\nStyle: {style}\nSteps: {num_steps}\nSeed: {seed}"
    
    return image, info


@spaces.GPU(duration=60)  # 60 seconds for batch
def generate_batch(
    food_names: str,
    style: str = "professional",
) -> list[tuple[Image.Image, str]]:
    """
    Generate multiple food images in one GPU session.
    
    Args:
        food_names: Comma-separated list of food names
        style: Image style for all images
    
    Returns:
        List of (image, info) tuples
    """
    foods = [f.strip() for f in food_names.split(",")]
    results = []
    
    for food in foods[:4]:  # Max 4 images per batch
        image, info = generate_food_image(food, style)
        results.append((image, info))
    
    return results


# API endpoint for programmatic access
def api_generate(
    food_name: str,
    style: str = "professional",
    return_base64: bool = True,
) -> dict:
    """
    API endpoint for generating food images.
    Returns base64-encoded image for easy integration.
    """
    image, info = generate_food_image(food_name, style)
    
    if return_base64:
        # Convert to base64
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        img_base64 = base64.b64encode(buffer.getvalue()).decode()
        
        return {
            "success": True,
            "image_base64": img_base64,
            "food_name": food_name,
            "style": style,
            "info": info,
        }
    
    return {
        "success": True,
        "image": image,
        "info": info,
    }


# Gradio Interface
with gr.Blocks(title="AUVRA Food Image Generator") as demo:
    gr.Markdown("""
    # 🍽️ AUVRA Food Image Generator
    
    Generate realistic food images using **Realistic Vision V5.1 + LCM-LoRA**.
    
    - **Fast**: 4-6 steps (~3 seconds per image)
    - **Realistic**: Photorealistic food images
    - **Free**: Powered by ZeroGPU
    """)
    
    with gr.Row():
        with gr.Column(scale=1):
            food_input = gr.Textbox(
                label="Food Name",
                placeholder="e.g., grilled salmon with steamed vegetables",
                value="fresh avocado toast with poached eggs",
            )
            
            style_dropdown = gr.Dropdown(
                label="Photography Style",
                choices=["professional", "casual", "overhead"],
                value="professional",
            )
            
            with gr.Accordion("Advanced Settings", open=False):
                guidance = gr.Slider(
                    label="Guidance Scale (CFG)",
                    minimum=1.0,
                    maximum=2.5,
                    value=1.5,
                    step=0.1,
                )
                steps = gr.Slider(
                    label="Inference Steps",
                    minimum=4,
                    maximum=8,
                    value=6,
                    step=1,
                )
                seed = gr.Number(
                    label="Seed (-1 for random)",
                    value=-1,
                )
            
            generate_btn = gr.Button("🎨 Generate Image", variant="primary")
        
        with gr.Column(scale=1):
            output_image = gr.Image(
                label="Generated Food Image",
                type="pil",
            )
            output_info = gr.Textbox(
                label="Generation Info",
                interactive=False,
            )
    
    # Batch generation section
    gr.Markdown("---")
    gr.Markdown("### 📦 Batch Generation (up to 4 images)")
    
    with gr.Row():
        batch_input = gr.Textbox(
            label="Food Names (comma-separated)",
            placeholder="salmon, avocado toast, greek yogurt, quinoa salad",
            value="grilled chicken, fresh salad, berry smoothie, overnight oats",
        )
        batch_style = gr.Dropdown(
            label="Style",
            choices=["professional", "casual", "overhead"],
            value="professional",
        )
    
    batch_btn = gr.Button("🎨 Generate Batch", variant="secondary")
    batch_gallery = gr.Gallery(
        label="Generated Images",
        columns=4,
        height=300,
    )
    
    # Event handlers
    generate_btn.click(
        fn=generate_food_image,
        inputs=[food_input, style_dropdown, guidance, steps, seed],
        outputs=[output_image, output_info],
    )
    
    batch_btn.click(
        fn=generate_batch,
        inputs=[batch_input, batch_style],
        outputs=[batch_gallery],
    )
    
    # API documentation
    gr.Markdown("""
    ---
    ### 🔌 API Usage
    
    You can call this Space programmatically using the Gradio Client:
    
    ```python
    from gradio_client import Client
    
    client = Client("YOUR_SPACE_NAME")
    
    # Generate single image
    result = client.predict(
        food_name="grilled salmon with vegetables",
        style="professional",
        api_name="/generate_food_image"
    )
    
    # result[0] = image path, result[1] = info
    ```
    
    Or use the HTTP API directly from your backend:
    
    ```python
    import requests
    
    response = requests.post(
        "https://YOUR_SPACE_NAME.hf.space/api/predict",
        json={
            "data": ["grilled salmon", "professional", 1.5, 6, -1]
        }
    )
    ```
    """)


# Launch
if __name__ == "__main__":
    demo.launch()
