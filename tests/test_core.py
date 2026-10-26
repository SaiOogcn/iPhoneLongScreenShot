from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from longshot_core import Options, process_image_files, process_video  # noqa: E402
from tests.fixture import HEIGHT, WIDTH, make_screenshots, make_video  # noqa: E402


def test_video_stitches_with_expected_height(tmp_path: Path) -> None:
    video = make_video(tmp_path / "demo.mp4", seconds=5.0, scroll_px=2400)
    result = process_video(video, Options(quality="balanced"), work_root=tmp_path)
    output = tmp_path / "video.jpg"
    output.write_bytes(result.data)
    image = cv2.imread(str(output))

    expected_h = round(2400 * 780 / WIDTH + 780 * HEIGHT / WIDTH)
    assert image is not None
    assert image.shape[1] == 780
    assert abs(image.shape[0] - expected_h) <= expected_h * 0.03
    assert result.meta["mode"] == "video"
    assert all(pair["matched"] for pair in result.meta["pairs"])
    assert not result.meta["warnings"]


def test_screenshot_stitching(tmp_path: Path) -> None:
    positions = [0, 700, 1400, 2100]
    paths = make_screenshots(
        [tmp_path / f"shot-{index}.png" for index in range(len(positions))],
        scroll_positions=positions,
    )
    result = process_image_files(paths, Options(quality="balanced"))
    image = cv2.imdecode(np.frombuffer(result.data, dtype="uint8"), cv2.IMREAD_COLOR)
    assert image is not None
    assert image.shape[1] == 780
    assert image.shape[0] >= 3000
    assert result.meta["mode"] == "images"
