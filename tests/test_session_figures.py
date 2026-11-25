import io
import os
from dataclasses import asdict

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.simulation import database, figures, imaging
from src.simulation.cases import CASES


def test_publisher_figure_uses_one_private_cached_url(webapp, tmp_path, monkeypatch):
    app, learner = webapp
    monkeypatch.setattr(imaging, "IMAGING_DIR", tmp_path / "imaging")
    original = "https://cdn.ncbi.nlm.nih.gov/pmc/blobs/example/figure.png"
    pixels = io.BytesIO()
    Image.new("RGB", (8, 6), "white").save(pixels, "PNG")
    requests = []

    def fetch(transport, request):
        requests.append(str(request.url))
        return httpx.Response(200, content=pixels.getvalue())

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fetch)
    case = asdict(CASES["SIM-001"])
    case["case_id"] = "FIG-1"
    case["imaging_studies"] = [
        dict(
            study_id="fig-1",
            modality="XR",
            description="Figure",
            findings="Example",
            thumbnail=original,
            file_path=original,
        )
    ]
    database.save_case(case, source="pubmed")
    view = learner.post("/api/simulation/start", json={"case_id": "FIG-1"}).json()
    media = view["imaging_studies"][0]
    opened = learner.post(
        "/api/simulation/imaging", json={"session_id": view["session_id"], "study_id": "fig-1"}
    )
    url = media["thumbnail"]
    assert url.startswith("/api/") and url == media["image_url"] == opened.json()["image_url"]
    with TestClient(app) as other:
        assert other.get(url).status_code == 404
    assert not requests
    first = learner.get(url)
    assert first.status_code == 200 and first.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(first.content)).size == (8, 6)
    assert learner.get(url).content == first.content
    assert requests == [original]
    snapshot = database.load_session(view["session_id"])["case_snapshot"]
    assert snapshot["imaging_studies"][0]["file_path"] == original


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not an image"),
        httpx.Response(302, headers={"Location": "https://unlisted.example/figure.jpg"}),
    ],
)
def test_failed_figure_fetch_does_not_publish_a_cache_entry(tmp_path, monkeypatch, response):
    monkeypatch.setattr(imaging, "IMAGING_DIR", tmp_path)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda transport, request: response)
    with pytest.raises(figures.FigureUnavailable):
        figures.cached_figure("https://cdn.ncbi.nlm.nih.gov/example.png")
    assert not list((tmp_path / "figures").iterdir())


def test_page_preview_is_not_a_figure():
    assert not figures.publisher_figure_url(
        {"notes": "Screenshot fallback", "file_path": "https://cdn.ncbi.nlm.nih.gov/sample.png"}
    )


def test_dicom_session_routes_to_its_slice_list(webapp, tmp_path, monkeypatch):
    import src.web.application as application
    from src.simulation.rubrics import draft_rubric

    app, learner = webapp
    monkeypatch.setattr(application, "IMAGING_DIR", tmp_path)
    folder = tmp_path / "dicom" / "DICOM-1"
    folder.mkdir(parents=True)
    for name in ("01.dcm", "02.dcm"):
        (folder / name).write_bytes(name.encode())
    case = asdict(CASES["SIM-001"])
    case["case_id"] = "DICOM-1"
    case["imaging_studies"] = [
        dict(
            study_id="stack",
            modality="CT",
            description="Test series",
            file_path="dicom/DICOM-1",
            thumbnail="",
            findings="Synthetic",
        )
    ]
    database.save_case(case, source="dicom")
    database.approve_case(
        "DICOM-1", "fixture", 1, "Reviewed synthetic example", draft_rubric(CASES["SIM-001"])
    )
    view = learner.post("/api/simulation/start", json={"case_id": "DICOM-1"}).json()
    opened = learner.post(
        "/api/simulation/imaging", json={"session_id": view["session_id"], "study_id": "stack"}
    ).json()
    assert (
        opened["image_url"] == view["imaging_studies"][0]["image_url"] == "/imaging/dicom/DICOM-1"
    )
    listing = "/api/simulation/imaging/dicom_list?case_id=DICOM-1"
    slices = learner.get(listing).json()["urls"]
    assert [learner.get(url).content for url in slices] == [b"01.dcm", b"02.dcm"]
    with TestClient(app) as other:
        assert other.get(listing).status_code == 404
        assert other.get(slices[0]).status_code == 404


@pytest.mark.skipif(not os.getenv("RUN_BROWSER_QA"), reason="Browser QA not requested")
def test_dicom_card_requests_the_stack_viewer():
    from pathlib import Path
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content("""
            <div id="lightboxOverlay"><div id="lbBody"></div><div id="lbTitle"></div>
            <div id="lbFindingsText"></div><button id="lbFindingsBtn"></button>
            <input id="lbContrast"><input id="lbBrightness"></div>
        """)
        page.evaluate("""() => {
            window.currentSession = {session_id: 'test', case_id: 'DICOM-1'};
            window.calls = [];
        }""")
        js = Path(__file__).resolve().parents[1] / "static/js"
        page.add_script_tag(path=str(js / "api.js"))
        # Intercept fetch below the app's apiFetch wrapper.
        page.evaluate("""() => {
            window.fetch = async url => {
                calls.push(url);
                return Response.json(url.endsWith('/imaging')
                    ? {study_id:'stack', modality:'CT', description:'Test scan', image_url:'/imaging/dicom/DICOM-1'}
                    : {urls: []});
            };
        }""")
        page.add_script_tag(path=str(js / "imaging.js"))
        page.evaluate("openImagingStudy('stack')")
        assert page.evaluate("calls") == [
            "/api/simulation/imaging",
            "/api/simulation/imaging/dicom_list?case_id=DICOM-1",
        ]
        assert page.locator("#dicomViewer").inner_text().endswith("No DICOM slices found")
        assert page.locator("#lbImage").count() == 0
        browser.close()
