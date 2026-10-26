"""Synthetic iPhone screen-recording fixtures used by the tests."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


WIDTH = 1170
HEIGHT = 2532
TOP_BAR = 310
BOTTOM_BAR = 230
TALL_HEIGHT = 12000


def build_page() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    page = np.full((TALL_HEIGHT, WIDTH, 3), 248, dtype=np.uint8)
    palette = [
        (242, 246, 252),
        (252, 245, 236),
        (238, 250, 243),
        (250, 238, 246),
        (246, 246, 238),
    ]
    for index, y in enumerate(range(0, TALL_HEIGHT, 220)):
        color = palette[index % len(palette)]
        cv2.rectangle(page, (40, y + 20), (WIDTH - 40, y + 190), color, -1)
        cv2.rectangle(page, (40, y + 20), (WIDTH - 40, y + 190), (190, 200, 215), 3)
        cv2.putText(
            page,
            f"Item {index:03d}",
            (90, y + 100),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.8,
            (30, 40, 60),
            4,
            cv2.LINE_AA,
        )
        marker = int(rng.integers(1000, 9999))
        cv2.putText(
            page,
            f"Unique {marker}",
            (90, y + 165),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.25,
            (80, 90, 110),
            3,
            cv2.LINE_AA,
        )
    header = np.full((TOP_BAR, WIDTH, 3), (48, 28, 18), dtype=np.uint8)
    cv2.putText(header, "Demo App", (60, 180), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (255, 255, 255), 5, cv2.LINE_AA)
    footer = np.full((BOTTOM_BAR, WIDTH, 3), 250, dtype=np.uint8)
    cv2.putText(footer, "Home     Search     Me", (60, 145), cv2.FONT_HERSHEY_SIMPLEX, 1.45, (30, 30, 30), 4, cv2.LINE_AA)
    return page, header, footer


def viewport(page: np.ndarray, header: np.ndarray, footer: np.ndarray, scroll_y: int) -> np.ndarray:
    inner_h = HEIGHT - TOP_BAR - BOTTOM_BAR
    scroll_y = max(0, min(scroll_y, page.shape[0] - inner_h))
    frame = np.empty((HEIGHT, WIDTH, 3), dtype=np.uint8)
    frame[:TOP_BAR] = header
    frame[TOP_BAR : TOP_BAR + inner_h] = page[scroll_y : scroll_y + inner_h]
    frame[TOP_BAR + inner_h :] = footer
    return frame


def make_video(
    path: str | Path,
    *,
    seconds: float = 5.0,
    scroll_px: int = 3000,
    fps: int = 30,
) -> Path:
    page, header, footer = build_page()
    path = Path(path)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (WIDTH, HEIGHT)
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not create the test video")
    total_frames = max(2, round(seconds * fps))
    for index in range(total_frames):
        t = index / max(1, total_frames - 1)
        # Pause at both ends, ease through the middle.
        u = min(1.0, max(0.0, (t - 0.12) / 0.76))
        smooth = u * u * (3.0 - 2.0 * u)
        writer.write(viewport(page, header, footer, int(scroll_px * smooth)))
    writer.release()
    return path


def make_screenshots(paths: list[str | Path], *, scroll_positions: list[int]) -> list[Path]:
    page, header, footer = build_page()
    result: list[Path] = []
    for position, path in zip(scroll_positions, paths, strict=True):
        path = Path(path)
        cv2.imwrite(str(path), viewport(page, header, footer, position))
        result.append(path)
    return result
