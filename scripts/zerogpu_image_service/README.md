---
title: AUVRA Food Image Generator
emoji: 🍽️
colorFrom: purple
colorTo: pink
sdk: gradio
sdk_version: 4.44.0
app_file: app.py
pinned: false
license: mit
hardware: zero-gpu
---

# 🍽️ AUVRA Food Image Generator

Generate realistic food images using **Realistic Vision V5.1 + LCM-LoRA**.

## Features

- **Fast**: 4-6 steps (~3 seconds per image) thanks to LCM-LoRA
- **Realistic**: Photorealistic food photography
- **Free**: Powered by ZeroGPU (H200 with 70GB VRAM)
- **API Ready**: Use via Gradio Client or HTTP API

## Usage

### Web UI
Visit the Space and enter your food name!

### API (Python)

```python
from gradio_client import Client

client = Client("YOUR_USERNAME/auvra-food-generator")

# Generate single image
image_path, info = client.predict(
    food_name="grilled salmon with steamed vegetables",
    style="professional",
    guidance_scale=1.5,
    num_steps=6,
    seed=-1,
    api_name="/generate_food_image"
)

print(f"Image saved to: {image_path}")
```

### API (HTTP/cURL)

```bash
curl -X POST "https://YOUR_USERNAME-auvra-food-generator.hf.space/api/predict" \
  -H "Content-Type: application/json" \
  -d '{"data": ["grilled salmon", "professional", 1.5, 6, -1]}'
```

## Photography Styles

| Style | Description |
|-------|-------------|
| `professional` | Studio lighting, shallow depth of field, Michelin-star presentation |
| `casual` | Natural lighting, home kitchen setting |
| `overhead` | Top-down flat lay, Instagram style |

## Technical Details

- **Base Model**: Realistic Vision V5.1 (SD 1.5 fine-tune)
- **Acceleration**: LCM-LoRA for 4-8 step inference
- **Hardware**: ZeroGPU (Nvidia H200, 70GB VRAM)
- **Resolution**: 512x512 pixels

## Integration with AUVRA Backend

This Space serves as the image generation backend for AUVRA.
The main backend on Render calls this API to generate food images.

## Credits

- Model: [SG161222/Realistic_Vision_V5.1](https://huggingface.co/SG161222/Realistic_Vision_V5.1_noVAE)
- LCM-LoRA: [latent-consistency/lcm-lora-sdv1-5](https://huggingface.co/latent-consistency/lcm-lora-sdv1-5)
