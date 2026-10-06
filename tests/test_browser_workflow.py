import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
import httpx
import pytest
import uvicorn
from src.web.application import create_app
from src.web.config import Settings

@pytest.mark.skipif(not os.getenv('RUN_BROWSER_QA'), reason='Browser QA not requested')
def test_browser_workflow(tmp_path, monkeypatch):
    from src.simulation import database
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr(database, '_DB_PATH', str(tmp_path / 'browser.db'))
    class Agent:
        def chat(self, prompt): return 'This is a synthetic patient response for the browser test.'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    settings = Settings(environment='local', allowed_origins=[f'http://127.0.0.1:{port}'])
    app = create_app(settings=settings, agent=Agent(), initialize_agent=False)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            try:
                if httpx.get(f'http://127.0.0.1:{port}/api/health', timeout=1).status_code == 200: break
            except httpx.RequestError: pass
            time.sleep(.1)
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/browser_smoke.py'),
                   f'http://127.0.0.1:{port}']
        subprocess.run(command, check=True, timeout=180)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.skipif(not os.getenv('RUN_BROWSER_QA'), reason='Browser QA not requested')
def test_anonymous_browser_workflow(tmp_path, monkeypatch):
    from src.simulation import database
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr(database, '_DB_PATH', str(tmp_path / 'anonymous-browser.db'))
    class Agent:
        def chat(self, prompt): return 'This is a synthetic patient response for the browser test.'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    settings = Settings(environment='local', allowed_origins=[f'http://127.0.0.1:{port}'])
    app = create_app(settings=settings, agent=Agent(), initialize_agent=False)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            try:
                if httpx.get(f'http://127.0.0.1:{port}/api/health', timeout=1).status_code == 200: break
            except httpx.RequestError: pass
            time.sleep(.1)
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/browser_smoke.py'),
                   f'http://127.0.0.1:{port}']
        subprocess.run(command, check=True, timeout=180)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
