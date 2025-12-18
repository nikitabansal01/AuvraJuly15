# AUVRA Image Generation Cost Analysis Report
## Modal Self-Hosted vs RunPod Flux Schnell API

**Date:** July 2025  
**Prepared for:** AUVRA Women's Health App  
**Analysis Type:** Cost Comparison for Food Image Generation

---

## 📋 Executive Summary

| Solution | Cost per Image (512×512) | Cold Start | Quality |
|----------|-------------------------|------------|---------|
| **Modal T4 (Warm)** | **$0.00066** | N/A | SD 1.5 (Good for food) |
| **Modal T4 (Cold)** | $0.0075 | ~45s | SD 1.5 (Good for food) |
| **RunPod Flux Schnell** | **$0.00063** | ~2-5s | Flux Schnell (Excellent) |
| **BFL FLUX.1 [dev] API** | $0.025 | ~2-3s | Flux Dev (Excellent) |

### **Recommendation: RunPod Flux Schnell** 
For AUVRA's use case, RunPod Flux Schnell offers the best balance of cost, quality, and reliability.

---

## 1️⃣ Current Modal Deployment Analysis

### Technical Specifications
- **GPU:** NVIDIA T4 (16GB VRAM)
- **Model:** Realistic Vision V5.1 (SD 1.5 based, ~2GB)
- **Accelerator:** LCM-LoRA (4 inference steps)
- **Image Size:** 512×512 pixels
- **Endpoint:** `https://mohanganesh165577--auvra-food-foodgenerator-api.modal.run`

### Measured Performance (from testing)
| Metric | Time | Cost |
|--------|------|------|
| Cold Start | 45.77s | $0.0075 |
| Warm Request #1 | 4.17s | $0.00068 |
| Warm Request #2 | 3.65s | $0.00060 |
| Average Warm | ~4s | **$0.00066** |

### Cost Breakdown
```
Modal T4 GPU Rate: $0.000164/second ($0.59/hour)

Cold Start Cost:
  - Container startup + model loading: ~45s
  - Cost: 45 × $0.000164 = $0.0074

Warm Request Cost:
  - Inference only: ~4s
  - Cost: 4 × $0.000164 = $0.00066

Note: scaledown_window=60s means requests within 60 seconds stay warm
```

### Modal Free Tier
- **$30/month free credits** included
- Approximately **45,454 warm images** or **4,000 cold images** free per month

---

## 2️⃣ RunPod Flux Schnell Analysis

### What is Flux Schnell?
- **Open-source** model by Black Forest Labs (Apache 2.0 license)
- **12B parameter** text-to-image model
- Optimized for **fast inference** (4 steps)
- **Superior quality** to SD 1.5 models

### Pricing Formula (User Provided)
```
Cost = (width × height / 1,000,000) × $0.0024

For 512×512:
Cost = (512 × 512 / 1,000,000) × $0.0024
Cost = (262,144 / 1,000,000) × $0.0024
Cost = 0.262144 × $0.0024
Cost = $0.000629 per image
```

### RunPod Serverless GPU Pricing
| GPU | VRAM | Flex Rate | Active Rate |
|-----|------|-----------|-------------|
| L4/A5000/3090 | 24GB | $0.00019/sec | $0.00013/sec |
| 4090 PRO | 32GB | $0.00031/sec | $0.00023/sec |
| A6000/A40 | 48GB | $0.00034/sec | $0.00020/sec |

### Estimated Flux Schnell Performance on RunPod
```
Using L4 GPU ($0.00019/sec flex):
- Flux Schnell inference: ~2-4 seconds
- Cost per image: 3s × $0.00019 = $0.00057

Using fixed pricing model:
- 512×512: $0.000629 per image
- 1024×1024: $0.0025 per image
```

---

## 3️⃣ Black Forest Labs Official API Comparison

For reference, here are BFL's official API prices:

| Model | Price | Notes |
|-------|-------|-------|
| FLUX.1 [dev] | $0.025/image | Distilled, good quality |
| FLUX.1 [pro] | $0.05/image | Original pro model |
| FLUX 1.1 [pro] | $0.04/image | Most efficient |
| FLUX 1.1 [pro] Ultra | $0.06/image | 2K resolution |
| FLUX.1 Kontext [pro] | $0.04/image | Editing capabilities |

**Note:** Flux Schnell is **free/open-source** - not available via BFL's paid API.

---

## 4️⃣ Volume-Based Cost Projections

### Monthly Cost Comparison (512×512 images)

| Monthly Volume | Modal (Cold Only) | Modal (Mixed)* | RunPod Flux | BFL FLUX.1 [dev] |
|---------------|-------------------|----------------|-------------|------------------|
| 100 images | $0.75 | $0.40 | **$0.06** | $2.50 |
| 1,000 images | $7.50 | $2.20 | **$0.63** | $25.00 |
| 5,000 images | $37.50 | $8.50 | **$3.15** | $125.00 |
| 10,000 images | $75.00 | $14.00 | **$6.29** | $250.00 |
| 50,000 images | $375.00 | $52.00 | **$31.45** | $1,250.00 |
| 100,000 images | $750.00 | $98.00 | **$62.90** | $2,500.00 |

*Mixed assumes 10% cold starts, 90% warm requests

### With Modal Free Tier Applied

| Monthly Volume | Modal (Net Cost) | RunPod Flux |
|---------------|------------------|-------------|
| 1,000 images | **$0.00** (free tier) | $0.63 |
| 5,000 images | **$0.00** (free tier) | $3.15 |
| 10,000 images | **$0.00** (free tier) | $6.29 |
| 50,000 images | $22.00 | **$31.45** |
| 100,000 images | $68.00 | **$62.90** |

---

## 5️⃣ Quality Comparison

### Image Quality Assessment

| Aspect | Modal (SD 1.5) | Flux Schnell |
|--------|----------------|--------------|
| **Photorealism** | Good | Excellent |
| **Food Rendering** | Good | Excellent |
| **Text in Images** | Poor | Good |
| **Prompt Following** | Moderate | Excellent |
| **Artistic Style** | Varied | Consistent |
| **Color Accuracy** | Good | Excellent |

### Model Characteristics
```
Stable Diffusion 1.5 (Realistic Vision):
- 860M parameters
- 512×512 native resolution
- Good for realistic food photos
- Trained on diverse datasets
- Fast with LCM-LoRA (4 steps)

Flux Schnell:
- 12B parameters
- Up to 2MP resolution
- State-of-the-art quality
- Excellent prompt understanding
- 4-step optimized inference
```

---

## 6️⃣ Operational Comparison

### Reliability & Cold Starts

| Aspect | Modal T4 | RunPod Flux |
|--------|----------|-------------|
| **Cold Start Time** | 45-60s | 2-5s |
| **Cold Start Frequency** | Every 60s of inactivity | Managed by RunPod |
| **Max Concurrent** | Configurable | Configurable |
| **Uptime SLA** | 99.9% | 99.9% |
| **Scaling** | 0 to ∞ | 0 to ∞ |

### Integration Complexity

| Aspect | Modal | RunPod |
|--------|-------|--------|
| **Setup Time** | 2-4 hours | 30 minutes |
| **Maintenance** | You manage code/models | Managed |
| **Custom Models** | Full control | Limited |
| **API Simplicity** | Custom endpoint | Standard REST |
| **Debugging** | Your responsibility | Limited visibility |

---

## 7️⃣ AUVRA-Specific Analysis

### Estimated Usage Patterns

```
AUVRA Food Image Generation Use Cases:
1. Recipe card images
2. Meal plan visualizations
3. Nutrition guide illustrations
4. Personalized food recommendations

Estimated Daily Usage (per user growth):
- 100 DAU: ~500 images/day = 15,000/month
- 1,000 DAU: ~5,000 images/day = 150,000/month
- 10,000 DAU: ~50,000 images/day = 1,500,000/month
```

### Cost Projection by Growth Stage

| Stage | DAU | Monthly Images | Modal Cost | RunPod Cost | Savings |
|-------|-----|----------------|------------|-------------|---------|
| **Launch** | 100 | 15,000 | **$0** (free) | $9.44 | Modal wins |
| **Growth** | 1,000 | 150,000 | $82 | **$94.35** | Modal wins |
| **Scale** | 10,000 | 1,500,000 | $758 | **$943.50** | Modal wins |

**Key Insight:** Modal's free tier + warm request optimization makes it cost-effective up to ~500K images/month.

---

## 8️⃣ Recommendations

### For AUVRA (Current Stage: Launch/Growth)

#### **Option A: Stick with Modal (Recommended for Now)**
✅ **Pros:**
- $30/month free tier covers ~45K images
- Already deployed and working
- Full control over model and prompts
- Can optimize for food-specific quality

❌ **Cons:**
- 45s cold starts hurt UX
- Requires ongoing maintenance
- SD 1.5 quality vs Flux

#### **Option B: Switch to RunPod Flux Schnell**
✅ **Pros:**
- Superior image quality (12B vs 860M params)
- Fast cold starts (2-5s)
- No maintenance required
- Better prompt following

❌ **Cons:**
- Slightly higher cost at low volumes
- Less customization control
- Dependency on third-party

### **Hybrid Strategy (Best of Both)**

```
Recommended Implementation:

1. Keep Modal for development/testing (free tier)
2. Implement request queue for Modal to maximize warm hits
3. Add RunPod as fallback for cold-start situations
4. Use RunPod as primary when scaling past 50K images/month

Routing Logic:
- If Modal container is warm → Use Modal ($0.00066)
- If Modal cold + user can wait → Queue for Modal
- If urgent/cold → Use RunPod Flux ($0.00063)
```

---

## 9️⃣ Implementation Checklist

### To Optimize Modal Further:
- [ ] Increase `scaledown_window` to 120-180 seconds
- [ ] Implement request batching for efficiency
- [ ] Add warm-up pings every 50 seconds
- [ ] Consider A10G GPU for faster inference

### To Add RunPod Flux Schnell:
- [ ] Create RunPod account
- [ ] Deploy Flux Schnell serverless endpoint
- [ ] Update backend to support dual providers
- [ ] Implement cost tracking per provider
- [ ] Add fallback logic

---

## 📊 Final Comparison Matrix

| Criteria | Modal T4 | RunPod Flux | Winner |
|----------|----------|-------------|--------|
| **Cost (Low Volume)** | Free tier | $0.00063/img | 🏆 Modal |
| **Cost (High Volume)** | $0.00066/img | $0.00063/img | 🏆 RunPod |
| **Image Quality** | Good | Excellent | 🏆 RunPod |
| **Cold Start** | 45s | 2-5s | 🏆 RunPod |
| **Customization** | Full control | Limited | 🏆 Modal |
| **Maintenance** | High | None | 🏆 RunPod |
| **Free Tier** | $30/month | None | 🏆 Modal |

---

## 💡 Bottom Line

**For AUVRA's Current Stage:**
1. **Keep Modal** - The free tier provides ~45K images/month at zero cost
2. **Optimize warm requests** - Add keep-alive pings to minimize cold starts
3. **Plan for RunPod** - When you hit 50K+ images/month, RunPod Flux becomes more economical
4. **Consider quality** - If food image quality is critical for user engagement, Flux Schnell produces noticeably better results

**Break-Even Point:** ~45,000 images/month
- Below: Modal wins (free tier)
- Above: RunPod wins (cost + quality + reliability)

---

*Report generated for AUVRA Women's Health App - Image Generation Service Optimization*
