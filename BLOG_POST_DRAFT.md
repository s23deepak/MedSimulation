# Cold Start Optimization for Serverless LLM Deployment

**5 minutes → 5-10 seconds with GPU snapshots, sleep mode, and weight caching**

---

## The Problem

I built MedSimulation—an AI clinical simulation engine for resident training. It runs a 4B parameter LLM (MedGemma) on serverless GPU via Modal.

**The issue:** When the container idles out and shuts down, the next user hits a **5-minute cold boot**:
1. Container spins up
2. Model downloads from HuggingFace (~8GB)
3. Weights load into VRAM
4. vLLM warms up JIT kernels
5. Finally, the user's request processes

Five minutes of dead air is a non-starter for user experience.

**Update:** After implementing GPU snapshots with sleep mode, cold starts now take **5-10 seconds**.

---

## The Setup

Before optimization:

```python
@app.function(gpu="A10G")
@modal.asgi_app()
def serve():
    from main import app
    return app  # Simple, but 5-min cold start
```

After optimization, the architecture looks like:

```
┌─────────────────┐     ┌──────────────────┐
│  FastAPI (CPU)  │────▶│  VLLMService     │
│  Routes, DB     │ RPC │  (GPU, snapshotted)│
└─────────────────┘     └──────────────────┘
        ▲                        ▲
        │                        │
   scales to zero          wakes on demand
```

---

## Optimization 1: Pre-cached Model Weights

**Problem:** Every cold container downloads 8GB from HuggingFace.

**Solution:** Store weights in a **Modal Volume**—persistent storage shared across containers.

```python
# Persistent volume for model weights
model_volume = Volume.from_name("medsimulation-models", create_if_missing=True)

# One-time download (run once before deploying)
@app.function(gpu="A10G", volumes={"/models": model_volume})
def download_model():
    from huggingface_hub import snapshot_download
    
    snapshot_download(
        repo_id="google/medgemma-4b-it",
        local_dir="/models/medgemma-4b-it",
        ignore_patterns=["*.msgpack", "*.h5", "flax_model*"],
    )
    model_volume.commit()
```

**Result:** First container downloads once. All subsequent containers mount the volume. **Saves ~2-3 minutes.**

---

## Optimization 2: GPU Memory Snapshots with Sleep Mode

**Problem:** Even with cached weights, loading into VRAM takes time. Plus, subprocess state doesn't survive snapshot restore.

**Solution:** Modal's `enable_memory_snapshot=True` captures the entire GPU state after warmup. But there's a critical detail: you need to put vLLM to **sleep** before snapshotting.

```python
@app.cls(
    gpu="A10G",
    volumes={"/models": model_volume, "/data": data_volume},
    enable_memory_snapshot=True,
    experimental_options={"enable_gpu_snapshot": True},  # Key flag
)
class VLLMService:
    @modal.enter(snap=True)
    def start(self):
        """Start vLLM and warmup—this state gets snapshotted."""
        import os, subprocess
        
        model_path = "/models/medgemma-4b-it"
        
        cmd = [
            "vllm", "serve", model_path,
            "--host", "0.0.0.0",
            "--port", "8001",
            "--dtype", "bfloat16",
            "--gpu-memory-utilization", "0.8",
            "--max-model-len", "4096",
            "--enforce-eager",           # Skip torch.compile
            "--disable-custom-all-reduce",
            "--enable-prefix-caching",
            "--enable-sleep-mode",       # Critical for snapshots!
        ]
        
        env = {
            **os.environ,
            "VLLM_SERVER_DEV_MODE": "1",  # Required for sleep mode
            "NCCL_DEBUG": "WARN",
        }
        
        self.vllm_proc = subprocess.Popen(cmd, env=env)
        wait_for_vllm(8001)
        warmup_vllm(8001)  # Trigger JIT before snapshot
        sleep_vllm(8001)   # Put vLLM to sleep - NOW snapshot captures clean state
```

The key insight: **sleep mode matters**. When vLLM enters sleep:
1. Model weights offload to CPU (clean state for checkpoint)
2. KV cache clears (no stale context)
3. CUDA quiesces (no active kernels)
4. Process stays alive but checkpointable

Without `sleep()`, the subprocess is in an unknown state with active CUDA operations. Modal's checkpoint API can't cleanly capture that, so you get a full cold start on restore.

**Result:** Cold start drops from ~5 minutes to **~5-10 seconds**.

---

## Optimization 3: vLLM Flags That Matter

| Flag | Value | Why |
|------|-------|-----|
| `--enforce-eager` | Set | Skips `torch.compile` (adds 60-90s at boot) |
| `--max-num-seqs` | 16 | Lower = less initial memory pressure |
| `--disable-custom-all-reduce` | Set | Reduces NCCL init overhead |
| `--enable-prefix-caching` | Set | RadixAttention for KV cache reuse |

For single-GPU deployments, `--enforce-eager` is the biggest win. You lose some throughput, but cold start matters more for low-traffic apps.

---

## Optimization 4: Two-Tier Architecture

Run FastAPI on CPU, proxy inference to GPU:

```python
@app.function(
    gpu=None,      # CPU-only
    cpu=2,
    memory=1024,
    scaledown_window=300,  # Shut down after 5 min idle
)
@modal.asgi_app()
def serve():
    from src.simulation.vllm_client import ModalVLLMClient
    
    _vc._injected_client = ModalVLLMClient(
        vllm_service_instance=VLLMService(),
        model="google/medgemma-4b-it",
    )
    
    from main import app as backend_app
    return backend_app
```

**Result:** CPU proxy scales independently. GPU wakes only for inference.

---

## Optimization 5: Fine-Tuning > Prompt Engineering

**Problem:** Patient persona consistency required ~400 tokens of system prompt.

**Solution:** Fine-tuned MedGemma-4B on ~200 synthetic Q&A pairs.

- Generated via Claude (patient dialogue: age-appropriate voice, no jargon, emotional states)
- LoRA fine-tuning on RunPod (~$75, 2 hours on A100)
- System prompt shrank from ~400 → ~80 tokens

**Result:** More consistent voice, lower latency, cheaper inference.

---

## Performance Summary

| Scenario | Cold Start Time |
|----------|-----------------|
| Fresh deploy (no volume) | ~5+ min |
| After volume cache | ~2-3 min |
| After snapshot (no sleep) | ~45 sec (unreliable) |
| After snapshot + sleep mode | **~5-10 sec** ✓ |

**Cost:** ~$0.60/hr when active, $0 at rest.

### Key Insight

The sleep mode step is critical. Without it, GPU snapshots are unreliable because:
- Subprocess state doesn't survive container shutdown
- Active CUDA kernels can't be checkpointed cleanly
- vLLM process needs to be in a known quiescent state

After calling `sleep()`, the container is in a clean, checkpointable state. Modal captures that, and restore becomes a simple memory copy operation.

---

## What Didn't Work

| Approach | Why I Tried | Why It Failed |
|----------|-------------|---------------|
| Warm container 24/7 | Eliminate cold start | ~$432/month—too expensive for demo |
| AWQ quantization | Smaller model = faster load | Bottleneck is VRAM init, not download |
| Keeping vLLM process alive | Avoid restart | Modal still reclaims GPU on scale-down |

---

## Open Questions

Curious how others handle this:

1. **Warm container strategy:** Do you keep at least one GPU warm 24/7? At what traffic threshold?
2. **Snapshot reliability:** Anyone using GPU snapshots at scale? How's the failure rate?
3. **Platform comparison:** RunPod, Lambda, Massed—how do cold starts compare?
4. **vLLM alternatives:** Considering TensorRT-LLM or SGLang for faster boot?

Drop a comment or reach out if you've tackled similar problems.

---

## Deployment Commands

```bash
# 1. Install Modal
uv add modal

# 2. Authenticate
python -m modal setup

# 3. Create secrets
modal secret create medsimulation-secrets \
    HF_TOKEN=hf_xxx \
    OPENAI_API_KEY=sk_xxx

# 4. Pre-download model (run once)
modal run modal_app.py::download_model

# 5. Deploy
modal deploy modal_app.py

# 6. View logs
modal app logs medsimulation
```

---

**About:** I build AI tools for healthcare. Previously [your background]. Reach out at [contact/Twitter/GitHub].

---

**Further Reading:**
- [Modal GPU Snapshots](https://modal.com/docs/guide/gpu-snapshots)
- [vLLM Performance Tuning](https://docs.vllm.ai/)
