# Secrets Management Guide

This document explains how to manage sensitive credentials for MedSimulation across local development and cloud deployment.

## Quick Start

### Local Development
```bash
# Copy the template
cp .env.example .env

# Add your HuggingFace token (required for MedGemma)
echo "HF_TOKEN=hf_your_token_here" >> .env

# Optional: OpenAI for TTS
echo "OPENAI_API_KEY=sk_your_key_here" >> .env
```

### Modal Deployment (Recommended)
```bash
# Create secrets in Modal (secure, encrypted storage)
modal secret create medsimulation-secrets \
    HF_TOKEN=hf_your_token_here \
    OPENAI_API_KEY=sk_your_key_here

# Deploy
modal deploy modal_app.py
```

---

## Required Credentials

### 1. HuggingFace Token (Required for MedGemma)

MedGemma is a gated model. You must:

1. **Sign up** at [huggingface.co](https://huggingface.co/signup)
2. **Accept terms** at [MedGemma model page](https://huggingface.co/google/medgemma-4b-it)
3. **Create access token** at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
   - Type: **Read** (write not needed)
   - Name: `medsimulation`

### 2. OpenAI API Key (Optional - for TTS)

For patient voice generation:

1. Sign up at [platform.openai.com](https://platform.openai.com)
2. Create key at [api keys page](https://platform.openai.com/api-keys)

### 3. Cloud LLM Provider (Optional - for CPU deployment)

If using Together AI instead of local GPU:

1. Sign up at [together.ai](https://together.ai)
2. Get API key from dashboard

---

## Environment Files

| File | Purpose | Commit to git? |
|------|---------|----------------|
| `.env.example` | Template with placeholders | ✅ Yes |
| `.env` | Local development secrets | ❌ No (gitignored) |
| `.env.modal` | Modal deployment (alternative to secrets) | ❌ No |
| `.env.local` | Local overrides | ❌ No |

---

## Deployment Options

### Option 1: Modal Secrets (Recommended)

Most secure for production:

```bash
# Create secret
modal secret create medsimulation-secrets \
    HF_TOKEN=hf_xxx \
    OPENAI_API_KEY=sk_xxx

# Deploy (secrets auto-injected)
modal deploy modal_app.py
```

**Benefits:**
- Encrypted at rest
- Never in logs or images
- Easy rotation: `modal secret update medsimulation-secrets HF_TOKEN=hf_new`

### Option 2: .env.modal File

Simpler for testing:

```bash
# Copy template
cp .env.modal .env.modal.local

# Edit with your values
nano .env.modal.local

# Deploy (file auto-loaded)
modal deploy modal_app.py
```

**Warning:** Don't commit this file with real values!

### Option 3: GitHub Secrets (for CI/CD)

For automated deployments:

1. Go to repo **Settings → Secrets and variables → Actions**
2. Add secrets: `HF_TOKEN`, `OPENAI_API_KEY`
3. Reference in workflow:
   ```yaml
   env:
     HF_TOKEN: ${{ secrets.HF_TOKEN }}
   ```

---

## Security Best Practices

1. **Never commit `.env` files** - They're in `.gitignore` for a reason
2. **Use Modal Secrets for production** - More secure than dotenv files
3. **Rotate tokens periodically** - Especially if repo is public
4. **Use minimal permissions** - Read-only HF token is sufficient
5. **Check git history** - If you accidentally committed secrets:
   ```bash
   # Use BFG Repo-Cleaner
   java -jar bfg.jar --delete-files .env
   ```

---

## Troubleshooting

### "Access denied" for MedGemma

1. Did you accept the terms at the model page?
2. Is your HF_TOKEN correct?
3. Try: `huggingface-cli whoami` to verify token

### Modal deployment fails with "secret not found"

```bash
# List existing secrets
modal secret list

# Create if missing
modal secret create medsimulation-secrets HF_TOKEN=hf_xxx
```

### Check what's in git

```bash
# See all tracked files
git ls-files

# Check for any .env files
git ls-files | grep env
```
