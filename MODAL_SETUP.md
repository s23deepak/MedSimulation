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

Update your Railway/Render environment variables:

```bash
VLLM_MODE=cloud
VLLM_CLOUD_URL=https://your-username--medsimulation-vllm-server.modal.run/v1
VLLM_CLOUD_API_KEY=your-modal-api-key
VLLM_MODEL=google/medgemma-4b-it
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

## Step 7: Deploy to Railway

1. Push code with Modal config
2. Deploy to Railway
3. Set environment variables in Railway dashboard
4. Test a simulation!

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
Modal may take 1-2 minutes to spin up a cold GPU. First request will be slow.

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
User on Phone          Railway (Web App)          Modal (GPU)
     │                        │                        │
     │  1. Open app           │                        │
     │───────────────────────>│                        │
     │                        │                        │
     │  2. Ask patient        │  3. Forward to vLLM    │
     │───────────────────────>│───────────────────────>│
     │                        │                        │
     │                        │         4. GPU spins up (cold start ~30s)
     │                        │         5. MedGemma generates response
     │                        │                        │
     │  6. Show response      │  7. Return text        │
     │<───────────────────────│<───────────────────────│
     │                        │                        │
     │                        │                        │ 8. Idle 5 min → spin down
```
