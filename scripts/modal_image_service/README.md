# AUVRA Food Image Generator - Modal GPU Service

Self-hosted **Realistic Vision V5.1 + LCM-LoRA** on Modal serverless GPU.

## 💰 Cost Breakdown

| Metric | Value |
|--------|-------|
| **GPU** | NVIDIA T4 |
| **GPU Cost** | $0.000164/second (~$0.59/hour) |
| **Inference Time (warm)** | ~3-5 seconds |
| **Inference Time (cold)** | ~30-60 seconds |
| **Cost per warm image** | ~$0.001 |
| **Cost per cold image** | ~$0.007 |
| **FREE credits/month** | $30 |
| **FREE images/month** | ~30,000+ (mostly warm) |

### How Billing Works

1. **Per-second billing** - You only pay when GPU is actually running
2. **scaledown_window=5** - Container shuts down 5s after request (minimal idle cost)
3. **No min_containers** - Scales to zero when not in use (FREE when idle)
4. **Volume caching** - Model downloaded once, stored permanently

### Cost Optimizations Applied

- ✅ **T4 GPU** - Cheapest option ($0.000164/sec vs H100 at $0.001097/sec)
- ✅ **SD 1.5 model** - Only 2GB vs Flux at 12GB (faster cold starts)
- ✅ **LCM-LoRA** - 6 inference steps vs 50+ (6x faster)
- ✅ **scaledown_window=5** - Shuts down after 5 seconds idle
- ✅ **Volume caching** - Model cached, no re-download

## 🚀 Deployment

```bash
# One-time setup
pip install modal
modal setup

# Deploy
cd AuvraJuly15/scripts/modal_image_service
modal deploy app.py
```

## 📡 Endpoints

After deployment, you'll get:

| Endpoint | URL |
|----------|-----|
| Generate Image | `https://YOUR_USER--auvra-food-gen-generate.modal.run` |
| Health Check | `https://YOUR_USER--auvra-food-gen-health.modal.run` |

## 🧪 Testing

```bash
# Health check
curl https://YOUR_USER--auvra-food-gen-health.modal.run

# Generate image
curl -X POST "https://YOUR_USER--auvra-food-gen-generate.modal.run" \
  -H "Content-Type: application/json" \
  -d '{"food_name": "grilled salmon with vegetables", "style": "professional"}'
```

## 🔧 Backend Integration

Add to Render environment:
```
MODAL_ENDPOINT_URL=https://YOUR_USER--auvra-food-gen-generate.modal.run
```

The backend `image_service.py` will automatically use this endpoint.

## 📊 Monitor Usage

```bash
# View dashboard
open https://modal.com/apps

# Check costs
modal billing  # Shows current usage
```

## 🛑 Stop/Delete

```bash
# Stop the app (can redeploy later)
modal app stop auvra-food-gen

# Delete the app permanently
modal app delete auvra-food-gen

# Delete cached models (to free storage)
modal volume delete auvra-models
```
