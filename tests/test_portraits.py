"""Synthetic portrait generation, case binding, and session image delivery."""

import io

import httpx
import pytest
from PIL import Image

from src.simulation import database
from src.simulation.comfyui_client import ComfyUIClient
from src.simulation.portraits import (
    build_prompt, cases_missing_portraits, generate_case_portrait, portrait_spec, workflow_for,
)


CHECKS = {"age_match": True, "appearance_match": True, "image_quality": True}


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


def test_child_and_explicit_appearance_are_preserved():
    case = {
        "case_id": "CHILD-1",
        "presentation": "A 5-year-old Black African boy presents with neck pain.",
    }
    prompt = build_prompt(case)
    assert "5-year-old boy" in prompt
    assert "Black African" in prompt
    assert "adult" not in prompt
    assert portrait_spec(case)["age"] == 5
    assert portrait_spec(case)["appearance"] == "Black African"


def test_unspecified_appearance_is_stable_and_not_based_on_diagnosis():
    case = {"case_id": "CASE-1", "presentation": "A 35-year-old woman has chest pain."}
    first = portrait_spec(case)
    case["presentation"] = "A 35-year-old woman has a rash."
    assert portrait_spec(case)["appearance"] == first["appearance"]


def test_missing_portraits_skips_current_files_and_retries_missing_files(webapp, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert "SIM-001" in cases_missing_portraits()
    assert len(cases_missing_portraits(limit=1)) == 1
    result = generate_case_portrait("SIM-001", fake_comfyui(png_bytes()))
    assert "SIM-001" not in cases_missing_portraits()
    (tmp_path / "portraits" / result["file_path"]).unlink()
    assert "SIM-001" in cases_missing_portraits()


def test_portrait_generation_and_session_fetch(webapp, tmp_path, monkeypatch):
    app, client = webapp
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from src.simulation.cases import CASES

    CASES["SIM-001"].patient_image_url = "https://example.test/real-patient.jpg"
    generated = generate_case_portrait("SIM-001", fake_comfyui(png_bytes()), seed=42)
    assert database.active_portrait("SIM-001", 1)["asset_id"] == generated["asset_id"]
    assert database.latest_portrait("SIM-001", 1)["asset_id"] == generated["asset_id"]
    with database._connect() as conn:
        run = conn.execute("SELECT * FROM model_runs WHERE asset_id=?", (generated["asset_id"],)).fetchone()
    assert run["status"] == "success" and run["prompt_version"] == 2
    assert run["duration_ms"] >= 0
    before_review = client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()
    assert client.get(before_review["generated_portrait_url"]).status_code == 200
    assert database.portrait_asset(generated["asset_id"])["review_status"] == "unreviewed"
    with pytest.raises(ValueError, match="requires"):
        database.review_portrait(generated["asset_id"], "reviewer", True)
    database.review_portrait(generated["asset_id"], "reviewer", True, CHECKS)
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
    assert client.get(url).status_code == 404
    database.review_portrait(newer["asset_id"], "reviewer", True, CHECKS)
    assert database.active_portrait("SIM-001", 1)["asset_id"] == newer["asset_id"]
    app.state.simulation_engine._sessions.clear()
    restored = client.get(f"/api/simulation/session/{first['session_id']}").json()
    assert restored["generated_portrait_url"] == ""
    assert client.get(url).status_code == 404
    second = client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()
    assert database.load_session(second["session_id"])["portrait_asset_id"] == newer["asset_id"]
    assert client.get("/api/simulation/session/unknown/portrait").status_code == 404


def test_review_required_hides_unreviewed_but_keeps_prior_approved(webapp, tmp_path, monkeypatch):
    _, client = webapp
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    first = generate_case_portrait("SIM-001", fake_comfyui(png_bytes()), seed=42)
    monkeypatch.setenv("PORTRAIT_REVIEW_REQUIRED", "true")
    assert database.active_portrait("SIM-001", 1) is None
    assert client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()[
        "generated_portrait_url"
    ] == ""
    database.review_portrait(first["asset_id"], "reviewer", True, CHECKS)
    older = client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()
    assert client.get(older["generated_portrait_url"]).status_code == 200
    newer = generate_case_portrait("SIM-001", fake_comfyui(png_bytes()), seed=43)
    assert database.active_portrait("SIM-001", 1)["asset_id"] == first["asset_id"]
    assert client.get(older["generated_portrait_url"]).status_code == 200
    database.review_portrait(newer["asset_id"], "reviewer", True, CHECKS)
    assert database.active_portrait("SIM-001", 1)["asset_id"] == newer["asset_id"]


def test_portrait_file_path_cannot_escape_storage(webapp, tmp_path, monkeypatch):
    _, client = webapp
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    result = generate_case_portrait("SIM-001", fake_comfyui(png_bytes()))
    database.review_portrait(result["asset_id"], "reviewer", True, CHECKS)
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
    with database._connect() as conn:
        run = conn.execute("SELECT status FROM model_runs WHERE component='portrait'").fetchone()
    assert run["status"] == "error"


def test_case_revision_does_not_reuse_portrait(webapp, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    first = generate_case_portrait("SIM-001", fake_comfyui(png_bytes()))
    database.review_portrait(first["asset_id"], "reviewer", True, CHECKS)
    current = database.case_record("SIM-001")
    database.save_case(current, source=current["source"], expected_version=1)
    assert database.active_portrait("SIM-001", 2) is None
    _, client = webapp
    started = client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()
    assert started["generated_portrait_url"] == ""


def test_rejected_portrait_stays_hidden_and_records_review(webapp, tmp_path, monkeypatch):
    _, client = webapp
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    result = generate_case_portrait("SIM-001", fake_comfyui(png_bytes()))
    database.review_portrait(result["asset_id"], "clinical-reviewer", False,
                             {"age_match": False}, "Looks too young")
    assert database.active_portrait("SIM-001", 1) is None
    assert database.portrait_asset(result["asset_id"])["review_status"] == "rejected"
    assert client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()[
        "generated_portrait_url"
    ] == ""
    with database._connect() as conn:
        evaluation = conn.execute(
            "SELECT * FROM model_evaluations WHERE asset_id=?", (result["asset_id"],)
        ).fetchone()
    assert evaluation["metric"] == "age_match" and evaluation["value"] == "fail"


def test_patient_agent_run_and_basic_evaluation_are_recorded(webapp, capsys):
    from scripts.review_model_runs import main as model_review

    _, client = webapp
    session = client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()
    response = client.post("/api/simulation/history", json={
        "session_id": session["session_id"], "question": "What hurts?"
    })
    assert response.status_code == 200
    with database._connect() as conn:
        run = conn.execute(
            "SELECT * FROM model_runs WHERE component='patient_agent'"
        ).fetchone()
        evaluation = conn.execute(
            "SELECT * FROM model_evaluations WHERE run_id=?", (run["run_id"],)
        ).fetchone()
    assert run["session_id"] == session["session_id"]
    assert run["prompt_version"] == 1 and run["duration_ms"] >= 0
    assert run["status"] == "success"
    assert evaluation["metric"] == "response_present" and evaluation["value"] == "True"
    assert database.load_session(session["session_id"])["history_questions"][0][
        "model_run_id"
    ] == run["run_id"]
    model_review(["show", run["run_id"]])
    assert "Question: What hurts?" in capsys.readouterr().out


def test_local_review_gallery_and_human_agent_score(webapp, tmp_path, monkeypatch, capsys):
    from scripts.review_case_portraits import main as portrait_review
    from scripts.review_model_runs import main as model_review

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    result = generate_case_portrait("SIM-001", fake_comfyui(png_bytes()))
    gallery = tmp_path / "portrait_review.html"
    portrait_review(["gallery", "--output", str(gallery)])
    markup = gallery.read_text()
    assert result["asset_id"] in markup
    assert "Expected age: 58" in markup

    run_id = database.record_model_run(
        "patient_agent", "patient_persona", 1, "hash", "medgemma", "success", 500,
        case_id="SIM-001", session_id="SESSION-1",
    )
    model_review(["score", run_id, "--metric", "in_character", "--value", "pass",
                  "--reviewer", "trainer"])
    model_review(["summary"])
    assert "p50=500 ms" in capsys.readouterr().out
    with database._connect() as conn:
        score = conn.execute(
            "SELECT * FROM model_evaluations WHERE run_id=?", (run_id,)
        ).fetchone()
    assert score["value"] == "pass" and score["evaluator"] == "trainer"


def test_review_cli_cannot_approve_missing_file(webapp, tmp_path, monkeypatch):
    from scripts.review_case_portraits import main as portrait_review

    _, client = webapp
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    result = generate_case_portrait("SIM-001", fake_comfyui(png_bytes()))
    (tmp_path / "portraits" / result["file_path"]).unlink()
    with pytest.raises(SystemExit):
        portrait_review([
            "approve", result["asset_id"], "--reviewer", "trainer",
            "--age-match", "--appearance-match", "--image-quality",
        ])
    assert database.portrait_asset(result["asset_id"])["review_status"] == "unreviewed"
    started = client.post("/api/simulation/start", json={"case_id": "SIM-001"}).json()
    assert client.get(started["generated_portrait_url"]).status_code == 404
