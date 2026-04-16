# MedSimulation Deployment Guide

Deploy your PWA to production with HTTPS for mobile app distribution.

---

## Option 1: Railway (Recommended - Easiest)

**Free tier:** $5 credit/month, enough for light usage

### Steps:

1. **Push code to GitHub**
   ```bash
   git add -A
   git commit -m "feat: PWA mobile app deployment ready"
   git push origin progressive-web-app
   ```

2. **Deploy on Railway**
   - Go to https://railway.app
   - Click "New Project" → "Deploy from GitHub repo"
   - Select `MedSimulation` repo
   - Railway auto-detects Dockerfile

3. **Configure environment variables**
   ```
   VLLM_MODE=simulated    # Or 'local'/'cloud' if you have vLLM
   PORT=8000
   ```

4. **Add persistent storage** (for case database)
   - In Railway dashboard: New → Volume
   - Mount path: `/app/data`
   - Size: 1GB minimum

5. **Get your URL**
   - Railway gives you: `https://medsimulation-production.up.railway.app`
   - PWA is now installable with HTTPS!

---

## Option 2: Render

**Free tier:** Available with web service spinning down after inactivity

### Steps:

1. **Push code to GitHub** (same as above)

2. **Deploy on Render**
   - Go to https://render.com
   - Click "New +" → "Web Service"
   - Connect GitHub repo
   - Select `render.yaml` for auto-configuration

3. **Settings**
   - Region: Oregon (closest to most users)
   - Plan: Starter (free) or Standard ($7/mo)
   - Python version: 3.12

4. **Add Disk** (for persistent data)
   - Mount path: `/app/data`
   - Size: 1GB

---

## Option 3: Fly.io

**Free tier:** Limited, but very cheap (~$2/mo for small app)

### Steps:

1. **Install Fly CLI**
   ```bash
   curl -L https://fly.io/install.sh | sh
   fly auth signup
   ```

2. **Deploy**
   ```bash
   fly launch --name medsimulation
   fly volumes create medsim_data --size 1 --region ord
   fly deploy
   ```

3. **Your URL**: `https://medsimulation.fly.dev`

---

## Option 4: Vercel (Frontend) + Railway (Backend)

For maximum performance, split frontend and backend:

### Frontend (Vercel):
```bash
cd frontend
npm install -g vercel
vercel
```

### Backend (Railway):
Same as Option 1, but API-only.

Update frontend `.env`:
```
VITE_API_URL=https://medsimulation-production.up.railway.app
```

---

## Post-Deployment Checklist

### 1. Test PWA Features
- [ ] Manifest loads: `https://your-domain.com/static/manifest.json`
- [ ] Service worker registers
- [ ] Install prompt appears on mobile
- [ ] Offline mode works

### 2. Configure Custom Domain (Optional)
- Railway: Settings → Domains → Add custom domain
- Render: Settings → Custom Domain
- Add DNS records as instructed

### 3. Environment Variables for Production

| Variable | Value | Purpose |
|----------|-------|---------|
| `VLLM_MODE` | `simulated` | Use keyword mode (no GPU) |
| `VLLM_MODE` | `cloud` | Use cloud vLLM (needs API key) |
| `VLLM_CLOUD_URL` | Your vLLM endpoint | Cloud inference URL |
| `VLLM_CLOUD_API_KEY` | Your API key | Authentication |
| `OPENAI_API_KEY` | Optional | For voice generation |

### 4. Database Backup
Set up automated backups for `data/medsim.db`:
- Railway: Built-in postgres available
- Render: Use Render Postgres add-on
- Or export JSON backups via `/api/cases/db` endpoint

---

## Sharing Your PWA

Once deployed, share with users:

### QR Code for Easy Install
```bash
# Generate QR code for your URL
qrcode-terminal https://your-domain.com
```

### Instructions for Users:

**iOS Safari:**
1. Open your app URL
2. Tap Share button
3. "Add to Home Screen"

**Android Chrome:**
1. Open your app URL
2. Tap menu (⋮) → "Install app"
3. Or: Settings → Apps → "Add to Home screen"

---

## Troubleshooting

### PWA doesn't show install prompt
- Ensure HTTPS (required except localhost)
- Check manifest is valid: Chrome DevTools → Application → Manifest
- Service worker must be registered

### App shows "Offline" constantly
- Check API endpoints are accessible
- Service worker might be caching error responses
- Clear cache: DevTools → Application → Clear storage

### Database resets on redeploy
- Ensure persistent volume is mounted at `/app/data`
- Check mount path matches in deployment config

---

## Cost Estimates

| Platform | Free Tier | Paid (Production) |
|----------|-----------|-------------------|
| Railway | $5 credit | ~$5-10/mo |
| Render | Free (spins down) | $7/mo |
| Fly.io | Limited free | ~$2-5/mo |
| Vercel + Railway | Free frontend | ~$5-10/mo total |

---

## Next Steps

1. Choose deployment platform
2. Push `progressive-web-app` branch to GitHub
3. Follow platform-specific steps above
4. Test PWA install on your phone
5. Share URL with users!
