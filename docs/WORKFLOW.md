# MedSimulation Workflow

MedSimulation is a clinical reasoning **practice** tool for medical students and residents. Its initial success metric is completed practice sessions. Numeric feedback is educational and is neither an official competency assessment nor medical advice.

## Local Setup (WSL)

Run Git and `uv` from inside the WSL repository path, for example `/home/deepak/projects/MedSimulation`; do not use a Windows UNC path. Keep the Python environment in Linux (`UV_PROJECT_ENVIRONMENT=.venv-linux`) to avoid mixed Windows/Linux virtualenv permissions and lockfile churn.

```bash
UV_PROJECT_ENVIRONMENT=.venv-linux uv sync --group dev
cp .env.example .env
```

For local work, keep the server bound to `127.0.0.1`. The current temporary pilot mode has no built-in accounts or passwords; an HTTP-only cookie identifies each browser. Learners can resume their own sessions in that browser. Clearing the cookie loses access. Legacy sessions owned by the shared pilot identity stay in storage and cannot be claimed by a browser. Reviewer access is disabled until third-party auth is added. Configure the vLLM backend, then start the app:

```bash
UV_PROJECT_ENVIRONMENT=.venv-linux uv run python main.py --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. For a local preview, run `uv run python scripts/dev_preview.py` and open `http://127.0.0.1:8177/simulation`. The preview opens without sign-in and uses synthetic patient responses. Clinical approval is disabled.

The preview reads cases and portraits from `DATABASE_PATH` (default `data/medsim.db`) in read-only mode. All database writes go to a temporary SQLite file, even when the shell has a `DATABASE_URL` set. Stopping the preview discards its sessions and newly generated cases.

## Case Review

Every built-in, generated, and imported case starts as `pending`. The review workflow is disabled while built-in auth is removed. When third-party auth is added, reviewer users will need a protected review surface to inspect case content, provenance, source material, scoring rubric, and any imaging; edit cases; set required/optional/contraindicated actions; set case-specific ordering penalties; and approve or reject a version. Approval should still require an explicit clinical review confirmation. Revisions reset approval and increment the case version. No clinician has reviewed the current built-in bank, so those cases currently produce unscored practice feedback.

An approved case's rubric controls all numeric scores. AI text may explain the recorded evidence but cannot change the numbers. Each session stores an immutable case snapshot, version, rubric evidence, reviewer, and approval timestamp. Exports identify practice feedback and carry the educational disclaimer. Session deletion removes its transcript and related assessment audit; case review audit remains.

## Hosted Deployment

For the current Modal resident pilot, the app uses SQLite on the Modal persistent volume via `ALLOW_HOSTED_SQLITE=1` and `DATABASE_PATH=/data/medsim.db`. Modal sets these in `modal_app.py`, so a separate Postgres `DATABASE_URL` is not required for the demo. Anyone with the link can open the app with their own browser identity. Use synthetic cases and avoid identifiable patient information. Reviewer actions stay unavailable and cases remain unscored until clinical review. Run migrations with `python -m src.simulation.migrations` (startup also applies migrations transactionally), then start the app and run `scripts/deployment_smoke.py BASE_URL` without credentials. The smoke script creates and deletes its test session. Keep imaging storage persistent and access controlled via the app; do not serve it through a public static bucket. Account-based access should be reintroduced through the planned third-party auth integration rather than the removed built-in password flow.

Local SQLite remains supported for single-user development. The Modal SQLite pilot uses one web container and serializes updates to each session across workers sharing its lock directory, but a managed Postgres database remains the production enhancement for reliable hosted and multi-container behavior. The Postgres adapter and migrations are exercised by CI's Postgres service. Before moving existing hosted SQLite data to Postgres, export it through an approved migration plan; the app does not silently copy patient or session data across databases.

## Checks

```bash
UV_PROJECT_ENVIRONMENT=.venv-linux uv run ruff check main.py src scripts tests
UV_PROJECT_ENVIRONMENT=.venv-linux uv run ruff format --check main.py src/web src/simulation/safety.py src/simulation/rubrics.py
UV_PROJECT_ENVIRONMENT=.venv-linux uv run mypy
UV_PROJECT_ENVIRONMENT=.venv-linux uv run pytest -q
```

CI also runs a browser workflow at desktop, tablet, and mobile sizes and checks start, history, exam, investigation, submission, and JSON export. The live clinical model and a human case review are separate verification steps; neither is simulated by those tests.
