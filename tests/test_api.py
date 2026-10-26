from __future__ import annotations

import base64
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
from app import app  # noqa: E402
from tests.fixture import make_screenshots, make_video  # noqa: E402


def decode_meta(response) -> dict:
    raw = response.headers["X-Longshot-Meta"]
    return json.loads(base64.urlsafe_b64decode(raw))


def test_signed_shortcut_download(tmp_path: Path, monkeypatch) -> None:
    signed = tmp_path / "longshot.shortcut"
    signed.write_bytes(b"AEA1" + b"test-signed-shortcut")
    monkeypatch.setattr(app_module, "SHORTCUT_PATH", signed)
    client = app.test_client()
    response = client.get("/longshot.shortcut")
    assert response.status_code == 200
    assert response.content_type == "application/x-apple-shortcut"
    assert response.data == signed.read_bytes()
    assert "longshot.shortcut" in response.headers["Content-Disposition"]


def test_public_url_defaults_to_request_host(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "PUBLIC_URL", "")
    client = app.test_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["public_url"] == "http://localhost"


def test_video_upload_api(tmp_path: Path) -> None:
    video = make_video(tmp_path / "api-demo.mp4", seconds=3.5, scroll_px=1800)
    client = app.test_client()
    response = client.post(
        "/api/stitch",
        data={"file": (io.BytesIO(video.read_bytes()), "screen.mov"), "quality": "compact"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.content_type == "image/jpeg"
    meta = decode_meta(response)
    assert meta["mode"] == "video"
    assert meta["width"] == 586
    assert meta["selected_frames"] >= 2


def test_raw_video_body_upload_api(tmp_path: Path) -> None:
    video = make_video(tmp_path / "raw-demo.mp4", seconds=3.5, scroll_px=1800)
    client = app.test_client()
    response = client.post(
        "/api/stitch?mode=video&quality=compact",
        data=video.read_bytes(),
        content_type="application/octet-stream",
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.content_type == "image/jpeg"
    meta = decode_meta(response)
    assert meta["mode"] == "video"
    assert meta["selected_frames"] >= 2


def test_text_file_form_returns_actionable_error() -> None:
    client = app.test_client()
    response = client.post(
        "/api/stitch",
        data={"file": "not-a-file", "mode": "video"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "重新导入最新版" in response.get_json()["error"]


def test_multi_image_upload_api(tmp_path: Path) -> None:
    paths = make_screenshots(
        [tmp_path / f"api-shot-{index}.png" for index in range(3)],
        scroll_positions=[0, 650, 1300],
    )
    data = {
        "file": [(io.BytesIO(path.read_bytes()), path.name) for path in paths],
        "quality": "compact",
    }
    client = app.test_client()
    response = client.post("/api/stitch", data=data, content_type="multipart/form-data")
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.content_type == "image/jpeg"
    meta = decode_meta(response)
    assert meta["mode"] == "images"
    assert meta["input_images"] == 3


def test_zip_image_upload_api(tmp_path: Path) -> None:
    paths = make_screenshots(
        [tmp_path / f"zip-shot-{index}.png" for index in range(3)],
        scroll_positions=[0, 600, 1200],
    )
    archive = tmp_path / "shots.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for path in paths:
            handle.write(path, arcname=path.name)
    client = app.test_client()
    response = client.post(
        "/api/stitch",
        data={"file": (io.BytesIO(archive.read_bytes()), archive.name), "quality": "compact"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert decode_meta(response)["mode"] == "images"
