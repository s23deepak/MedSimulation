"""Synthetic portrait generation, case binding, and session image delivery."""

import io

import httpx
import pytest
from PIL import Image

from src.simulation import database
from src.simulation.comfyui_client import ComfyUIClient
from src.simulation.portraits import build_prompt, generate_case_portrait, workflow_for


def png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (768, 768), (35, 110, 95)).save(output, format="PNG")
    return output.getvalue()


def fake_comfyui(image):
    def handle(request):
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "test-job"})
        if request.url.path == "/history/test-job":
            return httpx.Response(
                200,
                json={
                    "test-job": {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {"9": {"images": [{"filename": "test.png", "type": "output"}]}},
                    }
                },
            )
        if request.url.path == "/view":
            return httpx.Response(200, content=image)
        raise AssertionError(f"Unexpected ComfyUI request: {request.url}")

    return ComfyUIClient("http://comfy.test", transport=httpx.MockTransport(handle))


def test_workflow_contains_only_generated_text():
    case = {
        "presentation": "A 54-year-old woman reports chest pain.",
        "patient_image_url": "https://example.test/real-patient.jpg",
    }
    prompt = build_prompt(case)
    workflow = workflow_for(prompt, 42)
    assert "54-year-old" in prompt
    assert "chest pain" not in prompt
    assert "real-patient" not in str(workflow)
    assert all(node["class_type"] != "LoadImage" for node in workflow.values())
    assert workflow["3"]["inputs"]["seed"] == 42


def test_portrait_generation_and_session_fetch(webapp, tmp_path, monkeypatch):
    app, client = webapp
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from src.simulation.cases import CASES

    CASES["SIM-001"].patient_image_url = "https://example.test/real-patient.jpg"
    generated = generate_case_portrait("SIM-001", fake_comfyui(png_bytes()), seed=42)
    assert database.active_portrait("SIM-001", 1)["asset_id"] == generated["asset_id"]
    first = client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()
    assert "patient_image_url" not in first
    assert database.load_session(first["session_id"])["case_snapshot"]["patient_image_url"] == ""
    url = first["generated_portrait_url"]
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/webp")
    assert response.content.startswith(b"RIFF")

    newer = generate_case_portrait("SIM-001", fake_comfyui(png_bytes()), seed=43)
    assert newer["asset_id"] != generated["asset_id"]
    assert database.active_portrait("SIM-001", 1)["asset_id"] == newer["asset_id"]
    app.state.simulation_engine._sessions.clear()
    restored = client.get(f"/api/simulation/session/{first['session_id']}").json()
    assert restored["generated_portrait_url"] == url
    assert client.get(url).content == response.content
    second = client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()
    assert database.load_session(second["session_id"])["portrait_asset_id"] == newer["asset_id"]
    assert client.get("/api/simulation/session/unknown/portrait").status_code == 404


def test_portrait_file_path_cannot_escape_storage(webapp, tmp_path, monkeypatch):
    _, client = webapp
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    result = generate_case_portrait("SIM-001", fake_comfyui(png_bytes()))
    started = client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()
    with database._connect() as conn:
        conn.execute(
            "UPDATE portrait_assets SET file_path=? WHERE asset_id=?",
            ("../../medsim.db", result["asset_id"]),
        )
    assert client.get(started["generated_portrait_url"]).status_code == 404


def test_invalid_comfyui_image_does_not_register_asset(webapp, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="invalid image"):
        generate_case_portrait("SIM-001", fake_comfyui(b"not an image"))
    assert database.active_portrait("SIM-001", 1) is None


def test_case_revision_does_not_reuse_portrait(webapp, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    generate_case_portrait("SIM-001", fake_comfyui(png_bytes()))
    current = database.case_record("SIM-001")
    database.save_case(current, source=current["source"], expected_version=1)
    assert database.active_portrait("SIM-001", 2) is None
    _, client = webapp
    started = client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()
    assert started["generated_portrait_url"] == ""
