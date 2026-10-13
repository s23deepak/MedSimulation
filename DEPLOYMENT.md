# MedSimulation Deployment Guide

For the temporary resident pilot, the app runs without built-in accounts or
passwords. Anyone with the link can open the app as the shared `pilot-learner`
identity. Use synthetic cases only and do not enter identifiable patient
information. Reviewer actions are unavailable until third-party auth is added,
and cases without clinical review remain unscored. The current Modal deployment
uses SQLite on a persistent Modal volume via `ALLOW_HOSTED_SQLITE=1`; managed
Postgres remains the production enhancement for reliable hosted and
multi-container behavior.
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
| `ALLOW_HOSTED_SQLITE=1` | Temporary Modal SQLite pilot mode |
| `DATABASE_PATH=/data/medsim.db` | SQLite file on the Modal persistent volume |
| `ALLOWED_ORIGINS` | Exact HTTPS app origin |

Use managed Postgres for sessions and review metadata before production or
multi-user pilots. Mount persistent storage for imaging at `/app/data`; do not
ship the development SQLite database.

## Post-Deployment Checks

- `GET /api/health` returns `status: ok` and loaded cases.
- `GET /api/model/status` reports a connected LLM backend.
- `/simulation` loads the case library independently of model readiness.
- Starting a case and asking a history question returns an AI patient response.
- Completed sessions export PDF and JSON successfully. Unreviewed cases export
  without numeric scores. Run `scripts/deployment_smoke.py` for this flow.

## Synthetic Case Portraits

Portraits are generated from fictional case demographics only. Existing
`patient_image_url` fields are ignored by the simulation UI. The ComfyUI
workflow is `workflows/patient_portrait_api.json`; generation runs outside
learner sessions, and missing portraits simply leave the portrait area empty.
Prompt version 2 preserves child ages, boy/girl wording, and explicitly stated
appearance. When appearance is unspecified, it uses a stable case-ID-based
fictional appearance profile rather than inferring ethnicity from diagnosis.
No case image is sent to ComfyUI.

For local development, start ComfyUI with the three Z-Image Turbo model files
listed in `modal_portraits.py`, then run:

```bash
UV_PROJECT_ENVIRONMENT=.venv-linux uv run python scripts/generate_case_portraits.py SIM-001
```

The standard Z-Image Turbo workflow requires more GPU memory than the project's
8 GB laptop GPU. Local integration tests mock ComfyUI; a real image test needs
a suitable GPU. The same workflow runs on Modal with an A10G:

```bash
modal run modal_portraits.py::download_models
modal run modal_portraits.py::generate_local --case-id SIM-001
modal run modal_portraits.py::generate_missing_local
modal run modal_portraits.py::generate_missing_hosted
modal deploy modal_portraits.py
UV_PROJECT_ENVIRONMENT=.venv-linux uv run python scripts/generate_case_portraits.py SIM-001 --modal
```

The local entrypoints return image bytes to the local case database and save
WebPs under `data/portraits/<case-id>/v<version>/`. The deployed worker path
instead writes to the hosted data Volume. Its files are saved under
`/data/portraits/<case-id>/v<version>/<asset-id>.webp` in the existing
`medsimulation-data` Volume. Model weights use the separate
`medsimulation-portrait-models` Volume.

`generate_missing_hosted` migrates the hosted database without replacing its
cases or learner sessions, then renders only case versions missing a current
portrait file. It commits each image and its database row to the shared data
Volume. Run it after `download_models` and before deploying the web app; reruns
skip completed images and retry missing files. Local-only cases are not
automatically imported into the hosted case database.

**Generated portraits are visible before review by default.** Migration 4
quarantines legacy prompt-v1 assets, and migration 5 publishes the newest
unreviewed prompt-v2 asset for each current case version. Newly generated
assets replace the visible portrait. Their review status remains `unreviewed`;
set `PORTRAIT_REVIEW_REQUIRED=true` on the web service to show only approved
portraits when the human review process is ready. Because the pilot app's
reviewer login is disabled, local review is an operator-only CLI workflow:

```bash
UV_PROJECT_ENVIRONMENT=.venv-linux uv run python scripts/review_case_portraits.py gallery
UV_PROJECT_ENVIRONMENT=.venv-linux uv run python scripts/review_case_portraits.py list
UV_PROJECT_ENVIRONMENT=.venv-linux uv run python scripts/review_case_portraits.py approve ASSET_ID --reviewer NAME --age-match --appearance-match --image-quality
UV_PROJECT_ENVIRONMENT=.venv-linux uv run python scripts/review_case_portraits.py reject ASSET_ID --reviewer NAME --reason "Age does not match"
```

The gallery is a local read-only file at `data/portrait_review.html`. The web
app serves the currently visible asset for a case version from
`/api/simulation/session/<session-id>/portrait`; replacing or rejecting it
revokes older session URLs. Do not approve an asset without visual review.

`model_runs` records patient-agent and portrait latency, status, model, case,
and prompt name/version/hash without storing learner questions or patient
responses. `model_evaluations` records portrait review checks and a basic
patient-agent response-present check. Human patient-agent quality scores can
be recorded with `scripts/review_model_runs.py`:

```bash
UV_PROJECT_ENVIRONMENT=.venv-linux uv run python scripts/review_model_runs.py list
UV_PROJECT_ENVIRONMENT=.venv-linux uv run python scripts/review_model_runs.py show RUN_ID
UV_PROJECT_ENVIRONMENT=.venv-linux uv run python scripts/review_model_runs.py summary --component portrait
UV_PROJECT_ENVIRONMENT=.venv-linux uv run python scripts/review_model_runs.py score RUN_ID --metric in_character --value pass --reviewer NAME
```

Langfuse is an optional third-party destination for versioned prompt management,
traces, datasets, and evaluation scores. It is not configured by this repo;
the local audit tables above work without external credentials. Modal's own
dashboard covers GPU, cold-start, and function health separately.

The current hosted pilot stores SQLite on the shared data Volume. Migrate the
hosted database before deploying a web build that expects migration 5. A
separate authenticated hosted review workflow is required before enabling
`PORTRAIT_REVIEW_REQUIRED` in production. For concurrent production generation, use managed Postgres
for portrait metadata; the image route reloads the data Volume if a reviewed
image is not yet visible in a running web container.

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

Confirm the Modal data volume is mounted and `DATABASE_PATH` points at that
volume. For production, migrate to managed Postgres with `DATABASE_URL`.
