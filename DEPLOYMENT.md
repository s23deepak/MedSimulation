# MedSimulation Deployment Guide

For the temporary resident pilot, the app runs without built-in accounts or
passwords. Anyone with the link can open the app as the shared `pilot-learner`
identity. Use synthetic cases only and do not enter identifiable patient
information. Reviewer actions are unavailable until third-party auth is added,
and cases without clinical review remain unscored. Hosted deployments require
`APP_ENV=demo` or `production`, an explicit HTTPS `ALLOWED_ORIGINS`, and a
Postgres `DATABASE_URL`. Startup refuses to run without these hosted values.
See [the current workflow](docs/WORKFLOW.md) for case review rules.

Patient conversation and case generation require a configured LLM backend.
Configure one of these inference paths before deploying:

- `VLLM_MODE=local`: run an OpenAI-compatible vLLM server next to the app.
- `VLLM_MODE=cloud`: connect the app to a remote OpenAI-compatible LLM endpoint.
- `VLLM_MODE=modal`: use the Modal RPC path in `modal_app.py`.

## Option 1: Docker With Local vLLM

Use this path for GPU-capable hosts.

1. Build the image:

   ```bash
   docker build --build-arg MEDSIM_EXTRAS=gpu -t medsimulation .
   ```

2. Run with GPU access and persistent data:

   ```bash
   docker run --gpus all \
     -e VLLM_MODE=local \
     -e VLLM_MODEL=google/medgemma-4b-it \
     -e VLLM_MAX_MODEL_LEN=1024 \
     -e APP_ENV=production \
     -e DATABASE_URL=postgresql+psycopg://... \
     -e ALLOWED_ORIGINS=https://your-app.example \
     -v medsimulation-data:/app/data \
     -p 8000:8000 \
     -p 8001:8001 \
     medsimulation
   ```

3. Verify:

   ```bash
   curl http://localhost:8000/api/health
   python scripts/deployment_smoke.py https://your-app.example
   ```

## Option 2: CPU App With Cloud LLM

Use this path for hosts without GPUs. The app still requires an LLM, but the
model runs behind a remote OpenAI-compatible API.

1. Deploy a cloud LLM endpoint. Modal is the project-native option:

   ```bash
   modal run modal_app.py::download_model
   modal deploy modal_vllm.py
   ```

2. Configure the web app environment:

   ```env
   VLLM_MODE=cloud
   VLLM_CLOUD_URL=https://your-username--medsimulation-vllm-server.modal.run/v1
   VLLM_CLOUD_API_KEY=your-api-key
   VLLM_MODEL=google/medgemma-4b-it
   APP_ENV=production
   DATABASE_URL=postgresql+psycopg://...
   ALLOWED_ORIGINS=https://your-app.example
   PORT=8000
   ```

3. Start the app:

   ```bash
   python main.py --host 0.0.0.0 --port ${PORT:-8000}
   ```

## Option 3: Modal All-In-One

Use this path when you want Modal to host both the FastAPI app and GPU-backed
LLM service.

```bash
modal run modal_app.py::download_model
modal deploy modal_app.py
```

Open the deployed `medsimulation-serve` URL from the Modal output.

## Railway Notes

Railway can host the FastAPI layer when `VLLM_MODE=cloud` is configured. Do not
use Railway as the local vLLM host unless the selected Railway runtime provides
the required GPU and CUDA support.

Required variables:

| Variable | Purpose |
|----------|---------|
| `VLLM_MODE=cloud` | Select remote LLM mode |
| `VLLM_CLOUD_URL` | OpenAI-compatible `/v1` endpoint |
| `VLLM_CLOUD_API_KEY` | Endpoint authentication |
| `VLLM_MODEL` | Served model name |
| `PORT` | Web server port |
| `APP_ENV=demo` | Hosted pilot mode |
| `DATABASE_URL` | Persistent Postgres database |
| `ALLOWED_ORIGINS` | Exact HTTPS app origin |

Use managed Postgres for sessions and review metadata. Mount persistent storage
for imaging at `/app/data`; do not ship the development SQLite database.

## Post-Deployment Checks

- `GET /api/health` returns `status: ok` and loaded cases.
- `GET /api/model/status` reports a connected LLM backend.
- `/simulation` loads the case library independently of model readiness.
- Starting a case and asking a history question returns an AI patient response.
- Completed sessions export PDF and JSON successfully. Unreviewed cases export
  without numeric scores. Run `scripts/deployment_smoke.py` for this flow.

## Troubleshooting

### App fails during startup

Check `VLLM_MODE`. It must be `local`, `cloud`, or `modal`.

### Case library never appears

The case library loads independently of the model. Check browser errors, the
  database migration, and `/api/cases/recommended`.

### Local Docker cannot find `vllm`

Rebuild the image after dependency changes. The Dockerfile installs the `gpu`
extra because local mode starts vLLM in the container.

### Database resets on redeploy

Confirm `DATABASE_URL` points to persistent Postgres and imaging storage is
mounted at `/app/data`.
