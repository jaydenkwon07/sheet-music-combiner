import dataclasses

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

import web.backend.app as app_module
from web.backend.app import app

client = TestClient(app)


def _png_bytes(spacing=30, width=800, n_lines=5):
    import io
    top, thick, pad = 40, 3, 40
    height = top + spacing * (n_lines - 1) + thick + pad
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    for i in range(n_lines):
        y = top + i * spacing
        img[y : y + thick, 30 : width - 30] = 0
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()


def _upload(prefix, n):
    files = [
        ("files", (f"{prefix}_{i}.png", _png_bytes(), "image/png"))
        for i in range(1, n + 1)
    ]
    return client.post("/api/session", files=files)


def test_upload_valid():
    r = _upload("Song", 9)
    assert r.status_code == 200
    body = r.json()
    assert body["prefix"] == "Song" and body["num_pieces"] == 9


def test_upload_missing_number():
    files = [
        ("files", (f"Song_{i}.png", _png_bytes(), "image/png"))
        for i in (1, 2, 4)
    ]
    r = client.post("/api/session", files=files)
    assert r.status_code == 422
    assert "3" in str(r.json()["detail"])


def test_assemble_happy_path_and_download():
    sid = _upload("Song", 9).json()["session_id"]
    r = client.post(f"/api/session/{sid}/assemble", json={"prefix": "Song"})
    assert r.status_code == 200
    body = r.json()
    assert body["counts"] == [5, 4]
    assert len(body["page_urls"]) == 2
    pdf = client.get(body["pdf_url"])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    page = client.get(body["page_urls"][0])
    assert page.status_code == 200


def test_assemble_over_memory_budget_returns_413(monkeypatch):
    sid = _upload("Song", 5).json()["session_id"]
    tiny = dataclasses.replace(app_module.settings, max_assemble_megapixels=0.0001)
    monkeypatch.setattr(app_module, "settings", tiny)
    r = client.post(f"/api/session/{sid}/assemble", json={"prefix": "Song"})
    assert r.status_code == 413
    detail = r.json()["detail"]
    assert detail["too_large"] is True and "megapixel" in detail["error"].lower()


def test_assemble_n7_needs_split_then_override():
    sid = _upload("Song", 7).json()["session_id"]
    r = client.post(f"/api/session/{sid}/assemble", json={"prefix": "Song"})
    assert r.status_code == 422
    assert r.json()["detail"]["needs_split"] is True
    r2 = client.post(
        f"/api/session/{sid}/assemble", json={"prefix": "Song", "pages": "4,3"}
    )
    assert r2.status_code == 200 and r2.json()["counts"] == [4, 3]


def test_assemble_margin_passthrough():
    sid = _upload("Song", 9).json()["session_id"]
    r = client.post(
        f"/api/session/{sid}/assemble", json={"prefix": "Song", "margin": 100}
    )
    assert r.status_code == 200  # smaller usable area still succeeds


def test_file_endpoint_rejects_traversal():
    sid = _upload("Song", 9).json()["session_id"]
    client.post(f"/api/session/{sid}/assemble", json={"prefix": "Song"})
    r = client.get(f"/api/session/{sid}/file/..%2f..%2fin%2fSong_1.png")
    assert r.status_code == 404


def test_unknown_session():
    r = client.post("/api/session/deadbeef/assemble", json={"prefix": "Song"})
    assert r.status_code == 404


def test_upload_over_max_bytes_rejected():
    original = app_module.settings
    tiny_settings = dataclasses.replace(original, max_upload_bytes=100)
    app_module.settings = tiny_settings
    try:
        files = [
            ("files", (f"Song_{i}.png", _png_bytes(), "image/png"))
            for i in (1, 2)
        ]
        r = client.post("/api/session", files=files)
    finally:
        app_module.settings = original
    assert r.status_code == 413


def test_upload_degenerate_filename_rejected():
    files = [("files", ("..", _png_bytes(), "image/png"))]
    r = client.post("/api/session", files=files)
    assert r.status_code == 422


def test_lifespan_runs_and_cancels_background_sweep():
    with TestClient(app) as ctx_client:
        assert ctx_client.get("/docs").status_code == 200
        task = app.state.sweep_task
        assert not task.done()
    assert task.cancelled() or task.done()
