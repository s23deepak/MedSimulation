# Deploy MedGemma 4B to Modal (Pay-Per-Use GPU)

Modal is perfect for your use case: **you only pay when the GPU is actually used**.

## Cost Estimate

- **T4 GPU**: ~$0.0006/second = ~$0.03 per case simulation
- **Monthly**: ~$2-5 for moderate usage (10-20 simulations/day)
- **Idle**: $0 (spins down after 5 minutes)

---

## Step 1: Sign Up for Modal

1. Go to https://modal.com/signup
2. Sign in with GitHub
3. Get $30 free credit to start

---

## Step 2: Install Modal CLI

```bash
pip install modal
modal token new
```

This opens a browser window to authenticate.

---

## Step 3: Add HuggingFace Token (for MedGemma)

MedGemma requires accepting terms on HuggingFace:

1. Go to https://huggingface.co/google/medgemma-4b-it
2. Click "Accept terms" (you may need to request access)
3. Get your HF token: https://huggingface.co/settings/tokens
4. Store in Modal:
   ```bash
   modal secret create huggingface --from-dict '{"HF_TOKEN": "hf_xxxxx"}'
   ```

---

## Step 3.5: Pre-cache Model Weights (one-time, strongly recommended)

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

## Step 4: Deploy vLLM Server

```bash
modal deploy modal_vllm.py
```

You'll see output like:
```
✓ Deployed medsimulation-vllm to prod
✓ Endpoint: https://your-username--medsimulation-vllm-server.modal.run
```

**Your API URL**: `https://your-username--medsimulation-vllm-server.modal.run/v1`

---

## Step 5: Configure MedSimulation

Set the following environment variables for your Modal web app deployment (or a `.env` file for local testing):

```bash
VLLM_MODE=cloud
VLLM_CLOUD_URL=https://your-username--medsimulation-vllm-server.modal.run/v1
VLLM_CLOUD_API_KEY=your-modal-api-key
VLLM_MODEL=google/medgemma-4b-it
```

To inject secrets into Modal, use:
```bash
modal secret create medsimulation-env \
  --from-dict '{"VLLM_CLOUD_URL": "...", "VLLM_CLOUD_API_KEY": "ak-xxxxx", "VLLM_MODEL": "google/medgemma-4b-it"}'
```

### Get Modal API Key:
1. Go to https://modal.com/settings
2. Click "Create new API key"
3. Copy the key (starts with `ak-`)

---

## Step 6: Test It

```bash
# Test the Modal endpoint
curl https://your-username--medsimulation-vllm-server.modal.run/v1/models \
  -H "Authorization: Bearer ak-xxxxx"

# Should return model info
```

---

## Step 7: Deploy the Web App to Modal

```bash
modal deploy modal_web.py
```

You'll see output like:
```
✓ Deployed medsimulation-web to prod
✓ Endpoint: https://your-username--medsimulation-web.modal.run
```

Open the endpoint URL in your browser and test a simulation!

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

### "GPU unavailable"
Modal may take ~15–30 seconds to spin up after idle (cold start). With GPU
snapshots enabled this is much faster than before. First-ever cold start (before
the snapshot is taken) may still take 1–2 minutes.

### "Too expensive"
Reduce `container_idle_timeout` in `modal_vllm.py` from 300 to 120 seconds.

---

## Alternative: Use a Cheaper Model

If MedGemma access is slow to approve, you can temporarily use:

```python
# In modal_vllm.py, change:
model = os.getenv("MODEL_NAME", "google/gemma-2-9b-it")  # More available
```

---

## Summary: Complete Flow

```
User on Phone        Modal (Web App)            Modal (GPU / vLLM)
     │                      │                           │
     │  1. Open app         │                           │
     │────────────────────->│                           │
     │                      │                           │
     │  2. Ask patient      │  3. Forward to vLLM       │
     │────────────────────->│──────────────────────────>│
     │                      │                           │
     │                      │    4. GPU spins up (cold start ~30s)
     │                      │    5. MedGemma generates response
     │                      │                           │
     │  6. Show response    │  7. Return text           │
     │<────────────────────│<──────────────────────────│
     │                      │                           │
     │                      │                           │ 8. Idle → spin down
```
