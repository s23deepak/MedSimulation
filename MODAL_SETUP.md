# Deploy MedGemma 4B to Modal (All-in-One with GPU Snapshots)

Modal is perfect for your use case: **you only pay when the GPU is actually used**.

## Cost Estimate

- **A10G GPU**: ~$0.60/hour = ~$0.03 per case simulation
- **Monthly**: ~$20-40 for moderate usage (2-3 hrs/day)
- **Idle**: $0 (spins down after 5 minutes)
- **Cold Start**: ~5-10 seconds with GPU snapshots enabled

---

## Step 1: Sign Up for Modal

1. Go to https://modal.com/signup
2. Sign in with GitHub
3. Get $30 free credit to start

---

## Step 2: Install Modal CLI

```bash
uv add modal
modal token new
```

This opens a browser window to authenticate.

---

## Step 3: Create Secrets (HuggingFace + OpenAI)

MedGemma requires accepting terms on HuggingFace:

1. Go to https://huggingface.co/google/medgemma-4b-it
2. Click "Accept terms" (you may need to request access)
3. Get your HF token: https://huggingface.co/settings/tokens
4. Get OpenAI key for TTS (optional): https://platform.openai.com/api-keys
5. Store in Modal:
   ```bash
   modal secret create medsimulation-secrets \
     HF_TOKEN=hf_xxxxx \
     OPENAI_API_KEY=sk_xxxxx
   ```

---

## Step 4: Pre-cache Model Weights (one-time, strongly recommended)

This downloads MedGemma 4B into a persistent Modal Volume so containers never
re-download it from HuggingFace on cold starts. **Run once after the first deploy.**

```bash
modal run modal_app.py::download_model
```

- Takes ~2–5 minutes while downloading (~9 GB).
- Only needs to be repeated if you switch models.
- All GPU containers share the same volume, so the download happens once total.

Without this step, each cold start downloads the model (~1–2 extra minutes).

---

## Step 5: Deploy the All-in-One App

```bash
modal deploy modal_app.py
```

You'll see output like:
```
✓ Deployed medsimulation to prod
✓ Endpoint: https://your-username--medsimulation-serve.modal.run
```

**Your API URL**: `https://your-username--medsimulation-serve.modal.run`

Open the endpoint URL in your browser and test a simulation!

---

## Step 6: Configure (Optional)

Edit `.env.modal` to customize:

```bash
# Model selection
VLLM_MODEL=google/medgemma-4b-it

# GPU tuning
VLLM_GPU_MEMORY=0.7        # Higher = more model, less KV cache
VLLM_MAX_MODEL_LEN=4096    # Context window size

# Use cloud LLM instead of local GPU (cheapest option)
VLLM_MODE=cloud
VLLM_CLOUD_URL=https://api.together.xyz/v1
VLLM_CLOUD_API_KEY=your_together_key
```

---

## Monitoring Costs

In Modal dashboard:
- https://modal.com/activity
- See real-time GPU usage
- Set spending alerts

---

## Troubleshooting

### "Model not found"
Make sure you accepted MedGemma terms on HuggingFace and added the secret.

### "GPU unavailable" or slow cold start
First-ever cold start (before snapshot is taken) may take 1–2 minutes. After that, GPU snapshots enable ~5-10 second restores.

If snapshots aren't working:
1. Check that `--enable-sleep-mode` is in the vLLM command
2. Verify `VLLM_SERVER_DEV_MODE=1` is set
3. Ensure `sleep_vllm()` is called after warmup

### "Too expensive"
- Reduce `scaledown_window` from 300 to 120 seconds
- Use CPU mode with cloud LLM (`VLLM_MODE=cloud`)
- Pre-seed cases to reduce on-the-fly generation time

---

## Alternative: CPU-Only Deployment

For lowest cost (no GPU), use cloud LLM providers:

```bash
# 1. Set up .env.modal for cloud mode
VLLM_MODE=cloud
VLLM_CLOUD_URL=https://api.together.xyz/v1
VLLM_CLOUD_API_KEY=your_together_key
VLLM_MODEL=google/gemma-2b-it

# 2. Deploy
modal run modal_app.py --cpu
```

**Cost:** ~$0.05/hr + token costs (~$5-15/month total)

---

## Pre-seeding Cases

Generate cases ahead of time to avoid on-the-fly generation delays:

```bash
# Generate specific topics
python preseed_cases.py --topics "chest pain,knee injury" --count 3

# Generate default topics (50+ conditions)
python preseed_cases.py --default --count 2

# List existing cases
python preseed_cases.py --list
```

---

## Summary: Complete Flow

```
User on Phone/Browser         Modal (All-in-One App)
     │                              │
     │  1. Open app                 │
     │─────────────────────────────>│
     │                              │ 2. FastAPI starts (CPU)
     │                              │ 3. VLLMService wakes (GPU, snapshotted)
     │                              │    - GPU snapshot restore: ~5-10s
     │                              │    - Model weights cached in Volume
     │                              │
     │  2. Search topic             │
     │─────────────────────────────>│
     │                              │ 3. Generate case (vLLM)
     │                              │    - Pre-seeded: instant
     │                              │    - On-fly: ~30-60s
     │                              │
     │  3. Start simulation         │
     │─────────────────────────────>│
     │                              │ 4. AI patient responses (vLLM)
     │                              │
     │  4. Submit diagnosis         │
     │─────────────────────────────>│
     │                              │ 5. Score + debrief (vLLM)
     │                              │
     │  5. View results             │
     │<─────────────────────────────│
     │                              │
     │                              │ 6. Idle 5 min → spin down
     │                              │    Next request: fast snapshot restore
```

---

## What Changed (April 2026)

### GPU Snapshot Fix
- Added `--enable-sleep-mode` to vLLM command
- Added `VLLM_SERVER_DEV_MODE=1` environment variable
- Call `sleep_vllm()` after warmup (critical for clean snapshot state)
- Removed `@modal.exit()` handler that was killing vLLM process
- Updated `wake_up()` to call `wake_vllm()` before checking readiness

**Result:** Cold start reduced from ~45 seconds to **~5-10 seconds**

### Pre-seed Script
- New `preseed_cases.py` for generating cases ahead of time
- Supports batch generation with default topics
- Saves to database for immediate use

**Result:** Case generation time reduced from ~30-60s to **instant** (for pre-seeded cases)
