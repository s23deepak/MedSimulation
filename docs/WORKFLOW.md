# MedSimulation Workflow

MedSimulation is a clinical reasoning **practice** tool for medical students and residents. Its initial success metric is completed practice sessions. Numeric feedback is educational and is neither an official competency assessment nor medical advice.

## Local Setup (WSL)

Run Git and `uv` from inside the WSL repository path, for example `/home/deepak/projects/MedSimulation`; do not use a Windows UNC path. Keep the Python environment in Linux (`UV_PROJECT_ENVIRONMENT=.venv-linux`) to avoid mixed Windows/Linux virtualenv permissions and lockfile churn.

```bash
UV_PROJECT_ENVIRONMENT=.venv-linux uv sync --group dev
cp .env.example .env
```

For local work, keep the server bound to `127.0.0.1`. The current temporary pilot mode has no built-in accounts or passwords; every request runs as the shared `pilot-learner` identity and reviewer access is disabled until third-party auth is added. Configure the vLLM backend, then start the app:

```bash
UV_PROJECT_ENVIRONMENT=.venv-linux uv run python main.py --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/`. A synthetic, local-only UI preview is available via `uv run python scripts/dev_preview.py`; it opens without sign-in, copies the existing local case bank into a temporary database, does not use a clinical model, and never grants clinical approval. Preview sessions and newly generated cases are discarded when the preview stops; the original case database is not changed.

## Case Review

Every built-in, generated, and imported case starts as `pending`. The review workflow is disabled while built-in auth is removed. When third-party auth is added, reviewer users will need a protected review surface to inspect case content, provenance, source material, scoring rubric, and any imaging; edit cases; set required/optional/contraindicated actions; set case-specific ordering penalties; and approve or reject a version. Approval should still require an explicit clinical review confirmation. Revisions reset approval and increment the case version. No clinician has reviewed the current built-in bank, so those cases currently produce unscored practice feedback.

An approved case's rubric controls all numeric scores. AI text may explain the recorded evidence but cannot change the numbers. Each session stores an immutable case snapshot, version, rubric evidence, reviewer, and approval timestamp. Exports identify practice feedback and carry the educational disclaimer. Session deletion removes its transcript and related assessment audit; case review audit remains.

## Hosted Deployment

For a resident pilot, set `APP_ENV=demo`, `DATABASE_URL=postgresql+psycopg://...`, and explicit HTTPS `ALLOWED_ORIGINS`. Anyone with the link can open the app as the shared pilot learner, so use synthetic cases and do not enter identifiable patient information. Reviewer actions stay unavailable and cases remain unscored until clinical review. Run migrations with `python -m src.simulation.migrations` (startup also applies migrations transactionally), then start the app and run `scripts/deployment_smoke.py BASE_URL` without credentials. The smoke script creates and deletes its test session. Keep imaging storage persistent and access controlled via the app; do not serve it through a public static bucket. Account-based access should be reintroduced through the planned third-party auth integration rather than the removed built-in password flow.

Local SQLite remains supported for single-user development. The Postgres adapter and migrations are exercised by CI's Postgres service. Before moving existing hosted SQLite data to Postgres, export it through an approved migration plan; the app does not silently copy patient or session data across databases.

## Checks

```bash
UV_PROJECT_ENVIRONMENT=.venv-linux uv run ruff check main.py src scripts tests
UV_PROJECT_ENVIRONMENT=.venv-linux uv run ruff format --check main.py src/web src/simulation/safety.py src/simulation/rubrics.py
UV_PROJECT_ENVIRONMENT=.venv-linux uv run mypy
UV_PROJECT_ENVIRONMENT=.venv-linux uv run pytest -q
```

CI also runs a browser workflow at desktop, tablet, and mobile sizes and checks start, history, exam, investigation, submission, and JSON export. The live clinical model and a human case review are separate verification steps; neither is simulated by those tests.
