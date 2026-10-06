"""Local-only UI preview with a synthetic patient response. Not a clinical model."""
import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.web.application import create_app
from src.web.config import Settings


class PreviewAgent:
    def chat(self, prompt):
        return "This is a synthetic preview response. The configured clinical model is not connected."


def seed_local_cases(source: Path, target: Path) -> int:
    if not source.is_file():
        return 0
    with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as existing:
        table = existing.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='cases'"
        ).fetchone()
        if not table:
            return 0
        rows = existing.execute("SELECT * FROM cases").fetchall()
        with sqlite3.connect(target) as preview:
            preview.execute(table[0])
            if rows:
                placeholders = ",".join("?" for _ in rows[0])
                preview.executemany(f"INSERT INTO cases VALUES ({placeholders})", rows)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8177)
    args = parser.parse_args()
    source = Path(os.getenv('DATABASE_PATH', str(ROOT / 'data' / 'medsim.db')))
    temporary = tempfile.TemporaryDirectory(prefix='medsim-preview-')
    target = Path(temporary.name) / 'preview.db'
    imported = seed_local_cases(source, target)
    os.environ['DATABASE_PATH'] = str(target)
    from src.simulation import database
    database._DB_PATH = str(target)
    settings = Settings(environment='local', allowed_origins=[f'http://127.0.0.1:{args.port}'])
    app = create_app(settings=settings, agent=PreviewAgent(), initialize_agent=False)
    print(f'Local preview (no sign-in): http://127.0.0.1:{args.port}/simulation', flush=True)
    print(f'Loaded {imported} existing cases into the temporary preview; source unchanged.', flush=True)
    import uvicorn
    try:
        uvicorn.run(app, host='127.0.0.1', port=args.port)
    finally:
        temporary.cleanup()

if __name__ == '__main__':
    main()
