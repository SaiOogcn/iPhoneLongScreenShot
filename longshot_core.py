"""Core video and image processing for the Longshot service.

Screen recordings are sampled, aligned with phase correlation, reduced to a
small set of overlapping keyframes, and assembled at exact ground-truth
offsets.  The same alignment core also supports manually captured screenshots.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np


ANALYSIS_FPS = 8
ANALYSIS_WIDTH = 390
MAX_VIDEO_SECONDS = 120
MAX_IMAGES = 24
MAX_FRAMES = 25

DEFAULT_TOP_PCT = 0.18
DEFAULT_BOTTOM_PCT = 0.12
MATCH_TOP_PCT = 0.20
MATCH_BOTTOM_PCT = 0.14

QUALITY_WIDTHS = {"compact": 586, "balanced": 780, "high": 1170}
MAX_OUTPUT_HEIGHT = 30000


class LongshotError(Exception):
    """A user-facing processing error."""

    def __init__(self, message: str, *, status: int = 400, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


@dataclass
class Options:
    """Processing options shared by video and image inputs."""

    quality: str = "balanced"
    top_pct: float = DEFAULT_TOP_PCT
    bottom_pct: float = DEFAULT_BOTTOM_PCT
    output_format: str = "jpg"
    jpeg_quality: int = 94
    max_frames: int = MAX_FRAMES
    max_output_height: int = MAX_OUTPUT_HEIGHT
    analysis_fps: int = ANALYSIS_FPS

    def validate(self) -> None:
        if self.quality not in QUALITY_WIDTHS:
            raise LongshotError("quality 必须是 compact、balanced 或 high。")
        if not (0.0 <= self.top_pct <= 0.32 and 0.0 <= self.bottom_pct <= 0.28):
            raise LongshotError("顶部/底部裁剪比例超出允许范围。")
        if self.top_pct + self.bottom_pct > 0.62:
            raise LongshotError("顶部和底部裁剪太大，没有足够的滚动内容可供匹配。")
        if self.output_format not in {"jpg", "jpeg", "png"}:
            raise LongshotError("输出格式只支持 jpg 或 png。")
        if not (1 <= self.max_frames <= 40):
            raise LongshotError("帧数上限必须在 1 到 40 之间。")
        if not (2000 <= self.max_output_height <= 50000):
            raise LongshotError("输出高度上限必须在 2000 到 50000 像素之间。")
        if not (50 <= self.jpeg_quality <= 100):
            raise LongshotError("JPEG 质量必须在 50 到 100 之间。")


@dataclass
class Result:
    """An encoded long screenshot plus diagnostic metadata."""

    data: bytes
    mimetype: str
    extension: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class VideoInfo:
    width: int
    height: int
    duration: float
    fps: float


def probe_video(path: str | os.PathLike[str]) -> VideoInfo:
    """Return display-oriented video metadata using OpenCV's FFmpeg backend."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise LongshotError("无法打开这个录屏文件。请确认它是 iPhone 录制的视频。")
    cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if not math.isfinite(fps) or fps <= 0:
        fps = 30.0
    frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    duration = frame_count / fps if frame_count > 0 and math.isfinite(frame_count) else 0.0
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise LongshotError("录屏里没有可读取的视频画面。")
    height, width = frame.shape[:2]
    if width <= 0 or height <= 0:
        raise LongshotError("无法读取录屏尺寸。")
    if duration > MAX_VIDEO_SECONDS + 0.5:
        raise LongshotError(f"录屏请控制在 {MAX_VIDEO_SECONDS} 秒内；当前约 {duration:.1f} 秒。")
    return VideoInfo(width=width, height=height, duration=duration, fps=fps)


def extract_frames(
    video_path: str | os.PathLike[str],
    frames_dir: str | os.PathLike[str],
    *,
    target_width: int,
    analysis_fps: int,
) -> tuple[list[Path], int, int, float]:
    """Extract an evenly sampled, width-normalised frame sequence."""
    frames_dir = Path(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise LongshotError("无法打开这个录屏文件。")
    cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if not math.isfinite(source_fps) or source_fps <= 0:
        source_fps = 30.0
    frame_step = max(1, round(source_fps / max(1, analysis_fps)))
    actual_fps = source_fps / frame_step
    written = 0
    index = 0
    max_source_frames = int(source_fps * (MAX_VIDEO_SECONDS + 2))
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if index > max_source_frames:
            cap.release()
            raise LongshotError(f"录屏请控制在 {MAX_VIDEO_SECONDS} 秒内。")
        if index % frame_step == 0:
            height, width = frame.shape[:2]
            if width > target_width:
                new_h = max(1, round(target_width * height / width))
                frame = cv2.resize(frame, (target_width, new_h), interpolation=cv2.INTER_AREA)
            target = frames_dir / f"{written:05d}.jpg"
            if not cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 92]):
                cap.release()
                raise LongshotError("写入录屏帧失败。", status=500)
            written += 1
        index += 1
    cap.release()
    paths = sorted(frames_dir.glob("*.jpg"), key=lambda p: p.name)
    if len(paths) < 2:
        raise LongshotError("录屏太短或没有可读取的画面。")
    first = cv2.imread(str(paths[0]))
    if first is None:
        raise LongshotError("抽帧失败，无法读取视频画面。")
    return paths, int(first.shape[1]), int(first.shape[0]), actual_fps


def _thumb(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    target_h = max(1, round(ANALYSIS_WIDTH * height / width))
    if width == ANALYSIS_WIDTH:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    else:
        gray = cv2.resize(image, (ANALYSIS_WIDTH, target_h), interpolation=cv2.INTER_AREA)
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (3, 3), 0)


def _matching_region(gray: np.ndarray, top_pct: float, bottom_pct: float) -> np.ndarray:
    height, width = gray.shape[:2]
    y0 = min(height - 2, max(1, round(top_pct * height)))
    y1 = max(y0 + 2, height - round(bottom_pct * height))
    x0 = min(width - 2, max(1, round(0.055 * width)))
    x1 = max(x0 + 2, width - x0)
    return gray[y0:y1, x0:x1]


def _scroll_from_pair(prev: np.ndarray, curr: np.ndarray) -> tuple[float, float, str]:
    """Estimate downward scroll in thumbnail pixels using phase correlation."""
    if prev.shape != curr.shape:
        h = min(prev.shape[0], curr.shape[0])
        w = min(prev.shape[1], curr.shape[1])
        prev = prev[:h, :w]
        curr = curr[:h, :w]
    if min(prev.shape) < 8:
        return 0.0, 0.0, "too-small"

    win = cv2.createHanningWindow((prev.shape[1], prev.shape[0]), cv2.CV_32F)
    content_h = prev.shape[0]
    phase_inputs = [("phase", prev, curr)]
    try:
        prev_edge = cv2.convertScaleAbs(cv2.Sobel(prev, cv2.CV_16S, 0, 1, ksize=3))
        curr_edge = cv2.convertScaleAbs(cv2.Sobel(curr, cv2.CV_16S, 0, 1, ksize=3))
        phase_inputs.append(("edge", prev_edge, curr_edge))
    except cv2.error:
        pass
    for method, prev_arr, curr_arr in phase_inputs:
        try:
            (_dx, dy), response = cv2.phaseCorrelate(
                prev_arr.astype(np.float32), curr_arr.astype(np.float32), win
            )
            scroll = float(-dy)
            if response >= 0.10 and abs(scroll) <= 0.92 * content_h:
                return scroll, float(response), method
        except cv2.error:
            continue

    # Fallback: a texture strip from the previous frame, searched in the next.
    patch_y = round(0.27 * content_h)
    patch_h = max(36, round(0.16 * content_h))
    patch_h = min(patch_h, content_h - patch_y - 2)
    if patch_h < 24:
        return 0.0, 0.0, "failed"
    patch = prev[patch_y : patch_y + patch_h]
    if np.std(patch) < 2.0:
        return 0.0, 0.0, "flat"
    try:
        result = cv2.matchTemplate(curr, patch, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
    except cv2.error:
        return 0.0, 0.0, "failed"
    match_y = int(max_loc[1])
    scroll = float(patch_y - match_y)
    if max_val >= 0.48 and abs(scroll) <= 0.92 * content_h:
        return scroll, float(max_val), "template"
    return 0.0, float(max_val), "failed"


def _first_scroll_run(steps: list[float], content_h: float) -> tuple[int, str]:
    """Find the first sustained scrolling run and its direction."""
    threshold = max(2.0, 0.012 * content_h)
    for idx in range(len(steps) - 2):
        signs = [np.sign(steps[idx + off]) for off in range(3)]
        magnitudes = [abs(steps[idx + off]) for off in range(3)]
        if signs[0] != 0 and signs[0] == signs[1] == signs[2] and min(magnitudes) >= threshold:
            return idx, "down" if signs[0] > 0 else "up"
    for idx, step in enumerate(steps):
        if abs(step) >= 0.025 * content_h:
            return idx, "down" if step > 0 else "up"
    return 0, "down"


def _analyse_frames(
    paths: list[Path],
    *,
    analysis_fps: int,
) -> tuple[list[float], dict[str, Any]]:
    """Compute per-pair scroll offsets without loading the whole video in RAM."""
    steps: list[float] = []
    responses: list[float] = []
    methods: dict[str, int] = {}
    prev: np.ndarray | None = None
    top = MATCH_TOP_PCT
    bottom = MATCH_BOTTOM_PCT
    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            raise LongshotError("读取录屏帧失败。", status=500)
        gray = _thumb(image)
        if prev is not None:
            prev_region = _matching_region(prev, top, bottom)
            curr_region = _matching_region(gray, top, bottom)
            scroll, response, method = _scroll_from_pair(prev_region, curr_region)
            steps.append(scroll)
            responses.append(response)
            methods[method] = methods.get(method, 0) + 1
        prev = gray
    if not steps:
        raise LongshotError("录屏中没有足够的画面。")
    return steps, {
        "frame_count": len(paths),
        "fps": analysis_fps,
        "methods": methods,
        "median_response": float(np.median(responses)) if responses else 0.0,
    }


def _select_keyframes(
    steps: list[float],
    *,
    content_h: float,
    max_frames: int,
) -> tuple[list[int], dict[str, Any]]:
    """Pick overlapping, well-spaced frames that cover the scroll range."""
    start, direction = _first_scroll_run(steps, content_h)
    oriented = list(steps)
    if direction == "up":
        oriented = [-value for value in steps]

    if start >= len(oriented):
        raise LongshotError("录屏中没有检测到滚动，请滚动页面后再停止录屏。")

    warnings: list[str] = []
    end = len(oriented) - 1
    opposite_at: int | None = None
    for idx in range(start, len(oriented)):
        if oriented[idx] < -0.012 * content_h:
            end = idx
            opposite_at = idx
            break

    positions = [0.0]
    for idx in range(start, end):
        delta = oriented[idx]
        if delta < 0:
            delta = 0.0
        positions.append(positions[-1] + delta)
    total_scroll = positions[-1]
    if total_scroll < 0.06 * content_h:
        raise LongshotError("滚动距离太短。请打开目标页面，向下滚动一小段后再停止录屏。")
    if opposite_at is not None:
        warnings.append("检测到反向滚动，长截图只保留到第一次反向位置为止。")

    min_step = max(0.19 * content_h, 20.0)
    max_step = 0.68 * content_h
    step = max(total_scroll / max(1, max_frames - 1), min_step)
    if step > max_step:
        needed = math.ceil(total_scroll / max_step) + 1
        raise LongshotError(
            f"这段录屏覆盖的页面太长，需要约 {needed} 个重叠帧，超过单次上限 {max_frames}。请分段录制。"
        )

    selected_local = [0]
    last_pos = 0.0
    for local_idx, value in enumerate(positions[1:], start=1):
        if value - last_pos >= step * 0.92:
            selected_local.append(local_idx)
            last_pos = value
    if selected_local[-1] != len(positions) - 1 and total_scroll - last_pos > 0.025 * content_h:
        selected_local.append(len(positions) - 1)

    if len(selected_local) < 2:
        raise LongshotError("录屏中没有足够的滚动内容。请放慢速度并滚动更远一些。")

    selected: list[int] = []
    for local_idx in selected_local:
        original_idx = start + local_idx if direction == "down" else len(steps) - 1 - (start + local_idx)
        selected.append(original_idx)
    if not selected:
        raise LongshotError("无法选出用于拼接的录屏帧。")

    if direction == "up":
        warnings.append("检测到向上滚动，已按阅读顺序重新排列。")

    actual_steps = [abs(selected[idx + 1] - selected[idx]) for idx in range(len(selected) - 1)]
    selected_offsets_thumb: list[float] = []
    for idx in range(len(selected_local) - 1):
        selected_offsets_thumb.append(positions[selected_local[idx + 1]] - positions[selected_local[idx]])
    return selected, {
        "direction": direction,
        "start_frame": selected[0],
        "end_frame": selected[-1],
        "selected_frames": len(selected),
        "total_scroll_thumb_px": round(total_scroll, 2),
        "selected_index_gaps": actual_steps,
        "selected_offsets_thumb": selected_offsets_thumb,
        "warnings": warnings,
    }


def _estimate_final_offset(
    prev: np.ndarray,
    curr: np.ndarray,
    *,
    top_crop: int,
    bottom_crop: int,
    expected: float,
) -> tuple[int, float, str]:
    """Estimate one final-frame offset, constrained by the thumbnail estimate."""
    height, width = prev.shape[:2]
    y0 = max(0, top_crop)
    y1 = min(height, height - bottom_crop)
    x0 = max(0, round(0.055 * width))
    x1 = width - x0
    prev_gray = cv2.cvtColor(prev[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    curr_gray = cv2.cvtColor(curr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    content_h = prev_gray.shape[0]
    tolerance = max(8.0, 0.035 * content_h)
    guess = float(expected)
    method = "thumbnail"
    response = 0.0
    try:
        win = cv2.createHanningWindow((prev_gray.shape[1], prev_gray.shape[0]), cv2.CV_32F)
        (_dx, dy), response = cv2.phaseCorrelate(
            prev_gray.astype(np.float32), curr_gray.astype(np.float32), win
        )
        measured = float(-dy)
        if response >= 0.08 and abs(measured - expected) <= tolerance:
            guess = measured
            method = "phase"
    except cv2.error:
        pass
    guess = int(round(guess))
    guess = max(1, min(guess, content_h - 8))
    return guess, float(response), method


def _estimate_unconstrained_offset(
    prev: np.ndarray,
    curr: np.ndarray,
    *,
    top_crop: int,
    bottom_crop: int,
) -> tuple[int, float, str]:
    """Estimate the vertical overlap of two screenshots without a prior."""
    height, width = prev.shape[:2]
    y0 = max(0, top_crop)
    y1 = min(height, height - bottom_crop)
    x0 = max(0, round(0.055 * width))
    x1 = width - x0
    prev_gray = cv2.cvtColor(prev[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    curr_gray = cv2.cvtColor(curr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    content_h = prev_gray.shape[0]
    scroll, response, method = _scroll_from_pair(prev_gray, curr_gray)
    offset = int(round(scroll))
    if offset < 2 or offset > content_h - 8:
        raise LongshotError("相邻截图没有足够的重叠。请按顺序截图，并保证每两张之间保留一段相同内容。")
    return offset, response, method


def _assemble_from_offsets(
    images: list[np.ndarray],
    offsets: list[int],
    *,
    top_pct: float,
    bottom_pct: float,
    max_output_height: int,
    jpeg_quality: int,
    output_format: str,
    pair_meta: list[dict[str, Any]] | None = None,
) -> tuple[bytes, str, str, dict[str, Any]]:
    """Assemble frames by cutting exactly at the known overlapping rows."""
    if len(images) < 2:
        raise LongshotError("没有足够的画面可用于拼接。")
    if len(offsets) != len(images) - 1:
        raise LongshotError("内部拼接偏移数量不正确。", status=500)
    height, width = images[0].shape[:2]
    for image in images:
        if image.shape[:2] != (height, width):
            raise LongshotError("录屏帧尺寸不一致，无法拼接。", status=500)
    top_crop = round(top_pct * height)
    bottom_crop = round(bottom_pct * height)
    content_h = height - top_crop - bottom_crop
    if content_h < 80:
        raise LongshotError("裁剪后没有足够的滚动内容。")

    if pair_meta is None:
        pair_meta = [
            {
                "confidence": 0.0,
                "offset": int(offset),
                "overlap": int(content_h - offset),
                "matched": True,
                "mode": "known",
            }
            for offset in offsets
        ]

    parts: list[np.ndarray] = [images[0][: height - bottom_crop]]
    for idx, offset in enumerate(offsets, start=1):
        overlap = content_h - offset
        start = top_crop + max(0, overlap)
        end = height - bottom_crop
        if end - start < 4:
            raise LongshotError("相邻画面重叠不足。请放慢滚动速度后重试。")
        parts.append(images[idx][start:end])
    parts.append(images[-1][height - bottom_crop :])
    total_h = sum(part.shape[0] for part in parts)
    if total_h > max_output_height:
        raise LongshotError(
            f"生成长图高度 {total_h}px 超过上限。请降低清晰度或分段录制。"
        )
    canvas = np.empty((total_h, width, 3), dtype=np.uint8)
    cursor = 0
    for part in parts:
        next_cursor = cursor + part.shape[0]
        canvas[cursor:next_cursor] = part
        cursor = next_cursor

    fmt = "png" if output_format == "png" else "jpg"
    if fmt == "png":
        ok, encoded = cv2.imencode(".png", canvas, [cv2.IMWRITE_PNG_COMPRESSION, 3])
        mimetype = "image/png"
    else:
        ok, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        mimetype = "image/jpeg"
    if not ok:
        raise LongshotError("生成图片失败。", status=500)
    meta = {
        "width": width,
        "height": total_h,
        "top_crop_px": top_crop,
        "bottom_crop_px": bottom_crop,
        "pairs": pair_meta,
        "warnings": [],
    }
    return encoded.tobytes(), mimetype, fmt, meta


def _assemble_video_frames(
    images: list[np.ndarray],
    expected_offsets: list[float],
    *,
    top_pct: float,
    bottom_pct: float,
    max_output_height: int,
    jpeg_quality: int,
    output_format: str,
) -> tuple[bytes, str, str, dict[str, Any]]:
    """Estimate constrained offsets for video keyframes and assemble them."""
    height, width = images[0].shape[:2]
    top_crop = round(top_pct * height)
    bottom_crop = round(bottom_pct * height)
    content_h = height - top_crop - bottom_crop
    offsets: list[int] = []
    pair_meta: list[dict[str, Any]] = []
    for idx in range(1, len(images)):
        expected = expected_offsets[idx - 1] if idx - 1 < len(expected_offsets) else 0.0
        offset, response, method = _estimate_final_offset(
            images[idx - 1],
            images[idx],
            top_crop=top_crop,
            bottom_crop=bottom_crop,
            expected=expected,
        )
        offsets.append(offset)
        pair_meta.append(
            {
                "confidence": round(response, 4),
                "offset": int(offset),
                "overlap": int(content_h - offset),
                "matched": bool(response >= 0.08 or method == "thumbnail"),
                "mode": method,
            }
        )
    return _assemble_from_offsets(
        images,
        offsets,
        top_pct=top_pct,
        bottom_pct=bottom_pct,
        max_output_height=max_output_height,
        jpeg_quality=jpeg_quality,
        output_format=output_format,
        pair_meta=pair_meta,
    )


def _natural_key(name: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _load_images(paths: Iterable[Path], target_width: int) -> list[np.ndarray]:
    images: list[np.ndarray] = []
    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            raise LongshotError(f"无法读取图片：{path.name}")
        height, width = image.shape[:2]
        if width != target_width:
            new_h = max(1, round(target_width * height / width))
            image = cv2.resize(image, (target_width, new_h), interpolation=cv2.INTER_AREA)
        images.append(image)
    return images


def process_video(
    video_path: str | os.PathLike[str],
    options: Options,
    *,
    work_root: str | os.PathLike[str] | None = None,
) -> Result:
    """Turn a screen recording into one long screenshot."""
    options.validate()
    started = time.monotonic()
    info = probe_video(video_path)
    if info.height <= info.width:
        raise LongshotError("请竖屏录制滚动页面；当前视频不是竖向画面。")

    target_width = min(info.width, QUALITY_WIDTHS[options.quality])
    if target_width % 2:
        target_width -= 1
    if target_width < 240:
        raise LongshotError("录屏分辨率过低。")

    with tempfile.TemporaryDirectory(prefix="longshot-video-", dir=work_root) as tmp:
        frame_paths, frame_w, frame_h, actual_fps = extract_frames(
            video_path,
            Path(tmp) / "frames",
            target_width=target_width,
            analysis_fps=options.analysis_fps,
        )
        steps, analysis = _analyse_frames(frame_paths, analysis_fps=actual_fps)
        thumb_h = ANALYSIS_WIDTH * frame_h / frame_w
        content_h_thumb = max(1.0, thumb_h * (1.0 - MATCH_TOP_PCT - MATCH_BOTTOM_PCT))
        selected, selection = _select_keyframes(
            steps,
            content_h=content_h_thumb,
            max_frames=options.max_frames,
        )
        images = _load_images((frame_paths[idx] for idx in selected), frame_w)

        expected_scroll = selection["total_scroll_thumb_px"] * (frame_h / thumb_h)
        expected_h = frame_h + expected_scroll
        if expected_h > options.max_output_height * 1.15:
            shrink = options.max_output_height / expected_h
            target_w = max(240, round(frame_w * shrink))
            if target_w % 2:
                target_w -= 1
            resized = []
            for image in images:
                ratio = target_w / image.shape[1]
                resized.append(cv2.resize(image, (target_w, max(1, round(image.shape[0] * ratio))), interpolation=cv2.INTER_AREA))
            images = resized
            frame_h = images[0].shape[0]

        expected_offsets = [
            value * (frame_h / thumb_h) for value in selection["selected_offsets_thumb"]
        ]
        data, mimetype, extension, meta = _assemble_video_frames(
            images,
            expected_offsets,
            top_pct=options.top_pct,
            bottom_pct=options.bottom_pct,
            max_output_height=options.max_output_height,
            jpeg_quality=options.jpeg_quality,
            output_format=options.output_format,
        )

    warnings = list(selection.get("warnings") or []) + list(meta.get("warnings") or [])
    meta.update(
        {
            "mode": "video",
            "source_width": info.width,
            "source_height": info.height,
            "duration_seconds": round(info.duration, 2),
            "source_fps": round(info.fps, 2),
            "frame_count": analysis["frame_count"],
            "selected_frames": len(selected),
            "selection": {k: v for k, v in selection.items() if k != "warnings"},
            "analysis": analysis,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "warnings": warnings,
        }
    )
    return Result(data=data, mimetype=mimetype, extension=extension, meta=meta)


def process_image_files(
    image_paths: list[str | os.PathLike[str]],
    options: Options,
) -> Result:
    """Stitch already-captured, overlapping screenshots."""
    options.validate()
    started = time.monotonic()
    if len(image_paths) < 2:
        raise LongshotError("至少选择两张有重叠的截图。")
    if len(image_paths) > MAX_IMAGES:
        raise LongshotError(f"单次最多拼接 {MAX_IMAGES} 张截图。")
    first = cv2.imread(str(image_paths[0]))
    if first is None:
        raise LongshotError("无法读取第一张截图。")
    target_width = min(first.shape[1], QUALITY_WIDTHS[options.quality])
    if target_width % 2:
        target_width -= 1
    images = _load_images((Path(p) for p in image_paths), target_width)
    height, width = images[0].shape[:2]
    if any(image.shape[:2] != (height, width) for image in images):
        raise LongshotError("截图尺寸不一致，请使用同一设备、同一方向的截图。")
    top_crop = round(options.top_pct * height)
    bottom_crop = round(options.bottom_pct * height)
    content_h = height - top_crop - bottom_crop
    offsets: list[int] = []
    pairs: list[dict[str, Any]] = []
    for idx in range(1, len(images)):
        offset, response, method = _estimate_unconstrained_offset(
            images[idx - 1],
            images[idx],
            top_crop=top_crop,
            bottom_crop=bottom_crop,
        )
        offsets.append(offset)
        pairs.append(
            {
                "confidence": round(float(response), 4),
                "offset": int(offset),
                "overlap": int(content_h - offset),
                "matched": bool(response >= 0.08),
                "mode": method,
            }
        )
    data, mimetype, extension, meta = _assemble_from_offsets(
        images,
        offsets,
        top_pct=options.top_pct,
        bottom_pct=options.bottom_pct,
        max_output_height=options.max_output_height,
        jpeg_quality=options.jpeg_quality,
        output_format=options.output_format,
        pair_meta=pairs,
    )
    meta.update(
        {
            "mode": "images",
            "input_images": len(image_paths),
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
    )
    return Result(data=data, mimetype=mimetype, extension=extension, meta=meta)


def extract_images_from_zip(zip_path: str | os.PathLike[str], output_dir: str | os.PathLike[str]) -> list[Path]:
    """Safely extract image files from a user-supplied zip archive."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > MAX_IMAGES:
                raise LongshotError(f"压缩包内最多 {MAX_IMAGES} 张截图。")
            extracted: list[Path] = []
            for index, info in enumerate(sorted(infos, key=lambda item: _natural_key(item.filename))):
                name = info.filename
                if name.startswith("__MACOSX/") or Path(name).name.startswith("."):
                    continue
                suffix = Path(name).suffix.lower()
                if suffix not in {".png", ".jpg", ".jpeg", ".heic", ".heif", ".webp"}:
                    continue
                if info.file_size > 40 * 1024 * 1024:
                    raise LongshotError("压缩包内有单张图片超过 40MB。")
                target = output_dir / f"{index:03d}{suffix}"
                with archive.open(info) as source, target.open("wb") as dest:
                    shutil.copyfileobj(source, dest)
                extracted.append(target)
    except zipfile.BadZipFile as exc:
        raise LongshotError("压缩包格式无效。") from exc
    if len(extracted) < 2:
        raise LongshotError("压缩包内没有找到至少两张图片。")
    return extracted
