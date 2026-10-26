"""HTTP service and web UI for the iPhone Longshot workflow."""

from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException
from longshot_core import (
    DEFAULT_BOTTOM_PCT,
    DEFAULT_TOP_PCT,
    LongshotError,
    Options,
    extract_images_from_zip,
    process_image_files,
    process_video,
)


BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = Path(os.environ.get("LONGSHOT_WORK_DIR", "/tmp/longshot-work"))
PUBLIC_URL = os.environ.get("LONGSHOT_PUBLIC_URL", "").strip().rstrip("/")
MAX_UPLOAD_MB = int(os.environ.get("LONGSHOT_MAX_UPLOAD_MB", "400"))
SHORTCUT_PATH = BASE_DIR / "shortcut" / "longshot.shortcut"

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.config["JSON_AS_ASCII"] = False

PROCESS_LOCK = threading.Lock()
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".hevc", ".h265"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".heic", ".heif", ".webp"}


def _pct(name: str, default: float) -> float:
    raw = request.form.get(name) or request.args.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise LongshotError(f"{name} 必须是数字。") from exc
    if value > 1.0:
        value = value / 100.0
    return value


def _form_int(name: str, default: int) -> int:
    raw = request.form.get(name) or request.args.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise LongshotError(f"{name} 必须是整数。") from exc


def _options() -> Options:
    return Options(
        quality=(request.form.get("quality") or request.args.get("quality") or "balanced").strip(),
        top_pct=_pct("top_pct", _pct("top", DEFAULT_TOP_PCT)),
        bottom_pct=_pct("bottom_pct", _pct("bottom", DEFAULT_BOTTOM_PCT)),
        output_format=(request.form.get("format") or request.args.get("format") or "jpg").lower(),
        jpeg_quality=_form_int("jpeg_quality", 94),
    )


def _safe_suffix(upload) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix in VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | {".zip"}:
        return suffix
    mimetype = (upload.mimetype or "").lower()
    if mimetype.startswith("video/"):
        return ".mov"
    if mimetype.startswith("image/"):
        return ".jpg"
    if mimetype in {"application/zip", "application/x-zip-compressed"}:
        return ".zip"
    return ".bin"


def _save_uploads(uploads, directory: Path) -> list[Path]:
    saved: list[Path] = []
    for index, upload in enumerate(uploads):
        suffix = _safe_suffix(upload)
        path = directory / f"upload-{index:02d}{suffix}"
        upload.save(path)
        if path.stat().st_size == 0:
            raise LongshotError("上传的文件为空。")
        saved.append(path)
    return saved


def _raw_body_suffix(content_type: str, mode: str, head: bytes) -> str:
    """Choose a filename extension for a request body sent as a raw file."""
    mimetype = content_type.split(";", 1)[0].strip().lower()
    if mimetype in {"video/quicktime", "video/mov"}:
        return ".mov"
    if mimetype in {"video/mp4", "video/x-m4v"}:
        return ".mp4"
    if mimetype.startswith("video/"):
        return ".mov"
    if mimetype in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if mimetype == "image/png":
        return ".png"
    if mimetype in {"image/heic", "image/heif"}:
        return ".heic"
    if mimetype == "image/webp":
        return ".webp"
    if mimetype in {"application/zip", "application/x-zip-compressed"}:
        return ".zip"

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"PK\x03\x04"):
        return ".zip"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        return ".mp4" if brand in {b"isom", b"mp41", b"mp42", b"avc1"} else ".mov"
    return ".mov" if mode == "video" else ".bin"


def _save_raw_body(directory: Path, mode: str) -> Path:
    """Stream a non-multipart request body to disk for Shortcuts file uploads."""
    if not request.content_length:
        raise LongshotError("没有收到文件。请上传录屏、截图或 zip。")

    path = directory / "upload-body"
    head = b""
    with path.open("wb") as handle:
        while True:
            chunk = request.stream.read(1024 * 1024)
            if not chunk:
                break
            if len(head) < 32:
                head += chunk[: 32 - len(head)]
            handle.write(chunk)

    if path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        raise LongshotError("上传的文件为空。")

    suffix = _raw_body_suffix(request.content_type or "", mode, head)
    final_path = path.with_suffix(suffix)
    path.replace(final_path)
    return final_path


def _meta_header(meta: dict) -> str:
    raw = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _public_url() -> str:
    return PUBLIC_URL or request.host_url.rstrip("/")


@app.get("/")
def index():
    return render_template(
        "index.html",
        public_url=_public_url(),
        has_shortcut=SHORTCUT_PATH.is_file(),
        max_upload_mb=MAX_UPLOAD_MB,
        top_pct=round(DEFAULT_TOP_PCT * 100, 1),
        bottom_pct=round(DEFAULT_BOTTOM_PCT * 100, 1),
    )


@app.get("/qr.png")
def qr_png():
    import qrcode

    image = qrcode.make(_public_url())
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png", download_name="longshot-qr.png")


@app.get("/healthz")
def healthz():
    return jsonify(
        {
            "ok": True,
            "service": "longshot",
            "busy": PROCESS_LOCK.locked(),
            "public_url": _public_url(),
            "max_upload_mb": MAX_UPLOAD_MB,
        }
    )


@app.get("/longshot.shortcut")
def longshot_shortcut():
    if not SHORTCUT_PATH.is_file():
        raise LongshotError("快捷指令文件尚未生成。", status=404)
    if request.args.get("download") == "1":
        response = send_file(
            SHORTCUT_PATH,
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name="longshot.shortcut",
            max_age=0,
        )
    else:
        response = send_file(
            SHORTCUT_PATH,
            mimetype="application/x-apple-shortcut",
            as_attachment=False,
            download_name="longshot.shortcut",
            max_age=0,
        )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/stitch")
def stitch():
    uploads = request.files.getlist("file") + request.files.getlist("files")
    uploads = [upload for upload in uploads if upload and upload.filename]
    requested_mode = (request.form.get("mode") or request.args.get("mode") or "auto").lower()
    raw_body = bool(request.content_length) and request.mimetype != "multipart/form-data"
    if not uploads:
        if not raw_body:
            if request.form.get("file"):
                raise LongshotError(
                    "快捷指令把文件当成了文本发送。请删除旧快捷指令，重新导入最新版。"
                )
            raise LongshotError("没有收到文件。请上传录屏、截图或 zip。")

    if not PROCESS_LOCK.acquire(blocking=False):
        raise LongshotError("服务器正在处理另一个长截图，请稍后重试。", status=429)

    started = time.monotonic()
    try:
        options = _options()
        options.validate()
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="longshot-request-", dir=WORK_DIR) as tmp:
            root = Path(tmp)
            saved = _save_uploads(uploads, root) if uploads else [_save_raw_body(root, requested_mode)]
            suffixes = [path.suffix.lower() for path in saved]
            has_video = any(suffix in VIDEO_EXTENSIONS for suffix in suffixes)
            has_zip = any(suffix == ".zip" for suffix in suffixes)
            image_paths: list[Path] = []

            if requested_mode not in {"auto", "video", "images", "image"}:
                raise LongshotError("mode 只能是 auto、video 或 images。")
            if requested_mode == "video" and len(saved) != 1:
                raise LongshotError("录屏模式一次只能上传一个视频。")
            if requested_mode in {"images", "image"} and has_video:
                raise LongshotError("截图模式不能上传视频。")

            if requested_mode == "video" or (requested_mode == "auto" and has_video):
                if len(saved) != 1:
                    raise LongshotError("视频模式一次只能上传一个录屏文件。")
                if saved[0].suffix.lower() not in VIDEO_EXTENSIONS:
                    raise LongshotError("这个文件不是支持的视频格式（.mov/.mp4/.m4v）。")
                result = process_video(saved[0], options, work_root=root)
            else:
                if has_zip:
                    if len(saved) != 1:
                        raise LongshotError("zip 压缩包需要单独上传。")
                    image_paths = extract_images_from_zip(saved[0], root / "zip")
                else:
                    if len(saved) < 2:
                        raise LongshotError("至少选择两张有重叠的截图。")
                    if any(suffix not in IMAGE_EXTENSIONS for suffix in suffixes):
                        raise LongshotError("请上传图片，或一个包含图片的 zip 压缩包。")
                    image_paths = saved
                result = process_image_files(image_paths, options)

        result.meta.setdefault("server_elapsed_seconds", round(time.monotonic() - started, 2))
        filename = f"longshot-{time.strftime('%Y%m%d-%H%M%S')}.{result.extension}"
        response = send_file(
            io.BytesIO(result.data),
            mimetype=result.mimetype,
            as_attachment=False,
            download_name=filename,
            max_age=0,
        )
        response.headers["Cache-Control"] = "no-store"
        compact_meta = {
            key: result.meta.get(key)
            for key in (
                "mode",
                "width",
                "height",
                "top_crop_px",
                "bottom_crop_px",
                "selected_frames",
                "input_images",
                "elapsed_seconds",
                "server_elapsed_seconds",
                "warnings",
            )
            if result.meta.get(key) is not None
        }
        response.headers["X-Longshot-Meta"] = _meta_header(compact_meta)
        response.headers["X-Longshot-Elapsed"] = str(result.meta.get("server_elapsed_seconds", ""))
        return response
    finally:
        PROCESS_LOCK.release()


@app.errorhandler(LongshotError)
def handle_longshot_error(exc: LongshotError):
    payload = {"ok": False, "error": exc.message}
    if exc.detail:
        payload["detail"] = exc.detail
    return jsonify(payload), exc.status


@app.errorhandler(413)
def handle_too_large(_exc):
    return jsonify({"ok": False, "error": f"文件超过 {MAX_UPLOAD_MB}MB 上限。"}), 413


@app.errorhandler(HTTPException)
def handle_http_error(exc: HTTPException):
    return jsonify({"ok": False, "error": exc.description or exc.name}), exc.code or 500


@app.errorhandler(Exception)
def handle_unexpected(exc: Exception):
    app.logger.exception("Unhandled Longshot error", exc_info=exc)
    return jsonify({"ok": False, "error": "服务器处理失败。", "detail": str(exc)}), 500


if __name__ == "__main__":
    host = os.environ.get("LONGSHOT_HOST", "0.0.0.0")
    port = int(os.environ.get("LONGSHOT_PORT", "8787"))
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    app.logger.info("Longshot listening on %s:%s", host, port)
    if PUBLIC_URL:
        app.logger.info("Configured public URL: %s", PUBLIC_URL)
    app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)
