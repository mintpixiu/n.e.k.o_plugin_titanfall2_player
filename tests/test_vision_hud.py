"""Vision/HUD tests using synthetic frames only (no screenshots shipped)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _loader  # noqa: E402

vision = _loader.vision
hud_mod = _loader.hud


def dark(width=1280, height=720, value=18):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:] = value
    return image


def test_black_frame_is_flagged() -> None:
    analyzer = vision.FrameAnalyzer()
    features = analyzer.analyze(dark(value=0))
    assert features.black
    assert features.objective_visible is False


def test_motion_is_localised_at_centre() -> None:
    analyzer = vision.FrameAnalyzer()
    first = dark(640, 360, 30)
    first[140:220, 280:360] = 80
    analyzer.analyze(first)
    second = first.copy()
    second[150:210, 290:350] = 230
    features = analyzer.analyze(second)
    assert features.motion > 0
    assert features.center_motion > 0
    assert features.center_motion_x > 0.30
    assert features.center_motion_y > 0.20


def test_objective_marker_is_found_and_steerable() -> None:
    analyzer = vision.FrameAnalyzer()
    image = dark(1280, 720, 24)
    height, width = image.shape[:2]
    cx, cy = int(width * 0.72), int(height * 0.36)
    radius = 20
    ys, xs = np.mgrid[0:height, 0:width]
    diamond = np.abs(xs - cx) + np.abs(ys - cy) <= radius
    image[diamond] = (255, 150, 12)
    features = analyzer.analyze(image)
    assert features.objective_visible
    assert abs(features.objective_x - 0.72) < 0.06
    assert abs(features.objective_y - 0.36) < 0.06
    assert features.objective_score > 0.28
    assert "任务标记" in vision.frame_features_summary(features)


def test_large_orange_area_is_not_a_waypoint() -> None:
    analyzer = vision.FrameAnalyzer()
    image = dark(1280, 720, 24)
    image[100:650, 100:1180] = (255, 140, 0)
    features = analyzer.analyze(image)
    # A sunset/fire is allowed to be seen as warm, but must not be sold as a
    # compact objective marker that makes the policy steer at random.
    assert not features.objective_visible


def test_centre_red_and_hit_flash_are_measured() -> None:
    analyzer = vision.FrameAnalyzer()
    image = dark(640, 360, 16)
    image[172:188, 312:328] = (255, 20, 20)
    features = analyzer.analyze(image)
    assert features.center_red > 0.02
    assert features.hit_flash > 0.8


def test_titan_meter_warm_region() -> None:
    analyzer = vision.FrameAnalyzer()
    image = dark(640, 360, 16)
    image[285:345, 220:420] = (230, 130, 20)
    features = analyzer.analyze(image)
    assert features.titan_meter > 0.04


def test_hud_tracker_reports_stability_and_warmth() -> None:
    tracker = hud_mod.HudTracker(style="titanfall", window=4)
    frames = []
    for index in range(5):
        image = dark(640, 360, 28)
        image[300:350, 20:160] = 150  # health block
        image[300:350, 480:620] = 150  # ammo block
        image[300:350, 230:410] = (210, 120, 30)  # Titan slot
        image[120:240, 280:360] = 40 + index * 20  # moving world
        frames.append(image)
    state = None
    for image in frames:
        features = vision.FrameAnalyzer().analyze(image)
        state = tracker.observe(image, features)
    assert state is not None
    assert state.present
    assert state.titan_ready_hint
    assert state.summary()["health_numbers"].startswith("not OCR")


def test_hud_region_style_switch_resets() -> None:
    tracker = hud_mod.HudTracker(style="titanfall")
    tracker.set_style("titan")
    assert "health" in tracker.regions
    tracker.reset()
    assert tracker.state.present is False
