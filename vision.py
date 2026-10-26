"""Cheap, dependency-light frame understanding for Titanfall 2.

The frozen N.E.K.O host ships ``numpy`` and ``Pillow`` but no OpenCV, so every
signal here is plain array arithmetic.  These are *observations*, not object
detection:

* is the frame black / frozen / a menu / a loading screen?
* where is the bright orange campaign objective marker?
* how much motion is in the centre (a likely enemy) versus the whole view?
* is the screen edge flashing red (damage), and is there a red hit flash?
* is the bottom-centre Titan meter warm/bright (a weak "Titan ready" hint)?
* is a HUD/stable block present, and is there a subtitle band?

The policy consumes these numbers; the dialogue LLM gets the JPEG.  Nothing
here claims to know an enemy's identity, health or ammo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:  # pragma: no cover - import environment dependent
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

__all__ = [
    "FrameAnalyzer",
    "FrameFeatures",
    "frame_features_summary",
    "perceptual_hash",
]


def _require_numpy() -> None:
    if np is None:
        raise RuntimeError("numpy is required for frame analysis")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class FrameFeatures:
    width: int = 0
    height: int = 0
    mean_luma: float = 0.0
    contrast: float = 0.0
    motion: float = 0.0
    scene_hash: int = 0
    black: bool = False
    frozen: bool = False
    red_border: float = 0.0
    center_luma: float = 0.0
    center_edge: float = 0.0
    crosshair: bool = False
    crosshair_rgb: tuple[int, int, int] = (0, 0, 0)
    crosshair_red: float = 0.0
    crosshair_blue: float = 0.0
    crosshair_orange: float = 0.0
    text_band: float = 0.0
    motion_x: float = 0.5
    motion_y: float = 0.5
    motion_strength: float = 0.0
    center_motion: float = 0.0
    center_motion_strength: float = 0.0
    center_motion_x: float = 0.5
    center_motion_y: float = 0.5
    center_red: float = 0.0
    hit_flash: float = 0.0
    objective_visible: bool = False
    objective_x: float = 0.5
    objective_y: float = 0.5
    objective_score: float = 0.0
    objective_area: float = 0.0
    titan_meter: float = 0.0
    edge_density: float = 0.0
    aspect: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "size": [self.width, self.height],
            "mean_luma": round(self.mean_luma, 3),
            "contrast": round(self.contrast, 3),
            "motion": round(self.motion, 4),
            "scene_hash": f"{self.scene_hash:016x}",
            "black": self.black,
            "frozen": self.frozen,
            "red_border": round(self.red_border, 4),
            "center_luma": round(self.center_luma, 3),
            "crosshair": self.crosshair,
            "crosshair_rgb": list(self.crosshair_rgb),
            "crosshair_red": round(self.crosshair_red, 3),
            "text_band": round(self.text_band, 3),
            "motion_centroid": [round(self.motion_x, 3), round(self.motion_y, 3)],
            "motion_strength": round(self.motion_strength, 4),
            "center_motion": round(self.center_motion, 5),
            "center_motion_strength": round(self.center_motion_strength, 4),
            "center_motion_centroid": [
                round(self.center_motion_x, 3),
                round(self.center_motion_y, 3),
            ],
            "center_red": round(self.center_red, 4),
            "hit_flash": round(self.hit_flash, 3),
            "objective": {
                "visible": self.objective_visible,
                "x": round(self.objective_x, 3),
                "y": round(self.objective_y, 3),
                "score": round(self.objective_score, 3),
                "area": round(self.objective_area, 5),
            },
            "titan_meter": round(self.titan_meter, 3),
            "edge_density": round(self.edge_density, 3),
        }


def perceptual_hash(image, *, width: int = 9, height: int = 8) -> int:
    """64-bit dHash; resolution independent and cheap to compare."""
    _require_numpy()
    array = np.asarray(image)
    if array.ndim == 3:
        luma = array[..., 0] * 0.299 + array[..., 1] * 0.587 + array[..., 2] * 0.114
    else:
        luma = array.astype("float32")
    if luma.size == 0:
        return 0
    rows = np.linspace(0, luma.shape[0] - 1, height).astype(int)
    cols = np.linspace(0, luma.shape[1] - 1, width).astype(int)
    small = luma[np.ix_(rows, cols)]
    bits = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in bits.reshape(-1):
        value = (value << 1) | int(bool(bit))
    return value


class FrameAnalyzer:
    """Stateful analyser: remembers the previous frame for motion features."""

    def __init__(self, *, analysis_width: int = 192) -> None:
        _require_numpy()
        self.analysis_width = max(64, int(analysis_width))
        self._prev_small = None
        self._prev_hash = 0
        self._prev_luma = 0.0
        self._frozen_ticks = 0
        self.frames = 0

    # -- helpers ---------------------------------------------------------

    def _resize_width(self, array, target: int):
        height, width = array.shape[:2]
        if width <= target:
            return array
        step = max(1, int(round(width / target)))
        return array[::step, ::step]

    def _small(self, image):
        array = np.asarray(image)
        if array.ndim == 2:
            array = np.stack([array] * 3, axis=-1)
        return self._resize_width(array, self.analysis_width)

    @staticmethod
    def _luma(array):
        return (
            array[..., 0].astype("float32") * 0.299
            + array[..., 1].astype("float32") * 0.587
            + array[..., 2].astype("float32") * 0.114
        )

    def _red_border(self, array) -> float:
        height, width = array.shape[:2]
        band_y = max(2, int(height * 0.08))
        band_x = max(2, int(width * 0.08))
        border = np.concatenate(
            [
                array[:band_y, :, :].reshape(-1, 3),
                array[-band_y:, :, :].reshape(-1, 3),
                array[band_y:-band_y, :band_x, :].reshape(-1, 3),
                array[band_y:-band_y, -band_x:, :].reshape(-1, 3),
            ]
        )
        if border.size == 0:
            return 0.0
        r = border[:, 0].astype("float32")
        g = border[:, 1].astype("float32")
        b = border[:, 2].astype("float32")
        mask = (r > 80) & (r > g * 1.45 + 10) & (r > b * 1.35 + 10)
        return float(mask.mean())

    def _text_band(self, array) -> float:
        height, width = array.shape[:2]
        y0, y1 = int(height * 0.68), int(height * 0.94)
        x0, x1 = int(width * 0.12), int(width * 0.88)
        patch = array[y0:y1, x0:x1]
        if patch.size == 0:
            return 0.0
        luma = self._luma(patch)
        bright = (luma > 170).astype("float32")
        if bright.shape[1] < 3:
            return 0.0
        transitions = np.abs(np.diff(bright, axis=1)).mean()
        density = bright.mean()
        return float(min(1.0, transitions * 6.0) * min(1.0, density * 4.0))

    def _crosshair(self, array):
        height, width = array.shape[:2]
        cx, cy = width // 2, height // 2
        half = max(3, min(width, height) // 90)
        patch = array[
            max(0, cy - half) : cy + half + 1,
            max(0, cx - half) : cx + half + 1,
        ]
        if patch.size == 0:
            return False, (0, 0, 0), 0.0, 0.0, 0.0
        luma = self._luma(patch)
        inner = float(luma.max())
        ring = self._luma(
            array[
                max(0, cy - half * 4) : min(height, cy + half * 4),
                max(0, cx - half * 4) : min(width, cx + half * 4),
            ]
        )
        ambient = float(np.median(ring)) if ring.size else 0.0
        score = (inner - ambient) / 255.0
        present = score > 0.16

        flat = patch.reshape(-1, 3)
        bright_idx = np.argsort(luma.reshape(-1))[-max(1, flat.shape[0] // 8) :]
        rgb = flat[bright_idx].mean(axis=0) if flat.size else np.zeros(3)
        r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
        total = max(1.0, r + g + b)
        red = max(0.0, (r - max(g, b)) / total)
        blue = max(0.0, (b - max(r, g)) / total)
        orange = max(0.0, (r - b) / total) * max(0.0, (g - b) / total) * 2.0
        return present, (int(r), int(g), int(b)), red, blue, orange

    def _motion(self, small):
        luma = self._luma(small)
        if luma.shape[0] < 8 or luma.shape[1] < 8:
            return 0.0, 0.5, 0.5, 0.0, 0.0, 0.5, 0.5, luma
        height, width = luma.shape
        grid_y, grid_x = 6, 8
        cell_h, cell_w = max(1, height // grid_y), max(1, width // grid_x)
        previous = self._prev_small
        diff = np.abs(luma - previous) if previous is not None and previous.shape == luma.shape else np.zeros_like(luma)
        motion = float(diff.mean() / 255.0)
        cells: list[tuple[float, int, int]] = []
        for gy in range(grid_y):
            for gx in range(grid_x):
                block = diff[
                    gy * cell_h : (gy + 1) * cell_h,
                    gx * cell_w : (gx + 1) * cell_w,
                ]
                if block.size:
                    cells.append((float(block.mean()), gx, gy))
        strength = 0.0
        mx = 0.5
        my = 0.5
        if cells:
            best = max(cells, key=lambda item: item[0])
            strength = best[0] / 255.0
            average = sum(c[0] for c in cells) / len(cells)
            if best[0] > average * 1.8 + 1.0:
                mx = (best[1] + 0.5) / grid_x
                my = (best[2] + 0.5) / grid_y

        # Centre-only motion: a moving enemy in front of the player changes the
        # middle cells while a camera pan changes the whole grid.  This is the
        # main "something is happening in front of me" cue.
        cx0, cx1 = int(width * 0.30), int(width * 0.70)
        cy0, cy1 = int(height * 0.26), int(height * 0.74)
        centre_diff = diff[cy0:cy1, cx0:cx1]
        cmotion = float(centre_diff.mean() / 255.0) if centre_diff.size else 0.0
        cstrength = 0.0
        cmx, cmy = 0.5, 0.5
        if centre_diff.size:
            # A 2x3 sub-grid inside the centre box.
            sh, sw = centre_diff.shape
            best_cell = (0.0, 0, 0)
            for gy in range(3):
                for gx in range(2):
                    block = centre_diff[
                        gy * sh // 3 : (gy + 1) * sh // 3,
                        gx * sw // 2 : (gx + 1) * sw // 2,
                    ]
                    if block.size and float(block.mean()) > best_cell[0]:
                        best_cell = (float(block.mean()), gx, gy)
            cstrength = best_cell[0] / 255.0
            if cstrength > 0.0:
                cmx = 0.30 + (best_cell[1] + 0.5) / 2 * 0.40
                cmy = 0.26 + (best_cell[2] + 0.5) / 3 * 0.48
        return motion, mx, my, strength, cmotion, cstrength, cmx, cmy, luma

    def _center_red(self, array) -> tuple[float, float]:
        """Return (red-pixel ratio, hit-flash brightness) around the reticle."""
        height, width = array.shape[:2]
        cx, cy = width // 2, height // 2
        half_w = max(6, int(width * 0.055))
        half_h = max(6, int(height * 0.075))
        patch = array[
            max(0, cy - half_h) : min(height, cy + half_h),
            max(0, cx - half_w) : min(width, cx + half_w),
        ]
        if patch.size == 0:
            return 0.0, 0.0
        r = patch[..., 0].astype("float32")
        g = patch[..., 1].astype("float32")
        b = patch[..., 2].astype("float32")
        red = (r > 130) & (r > g * 1.45 + 18) & (r > b * 1.35 + 18)
        red_ratio = float(red.mean())
        # A hitmarker is a very bright, short-lived cross at the reticle.  Use
        # the brightest 1% of the *max channel*, not luma: a saturated red
        # hitmarker has a low luma but is unmistakably bright.
        channels = patch.max(axis=2)
        flash = float(np.percentile(channels, 99.0) / 255.0) if channels.size else 0.0
        return red_ratio, flash

    def _objective_marker(self, array) -> tuple[bool, float, float, float, float]:
        """Find the compact warm-orange campaign waypoint.

        Titanfall 2 marks the next objective with an orange/yellow diamond.
        We deliberately require a *compact* cluster with high saturation and
        cap its screen area: a sunset or a fire can cover the same colours, but
        it is large, while the waypoint is a small icon.  The result is a hint,
        not a promise.
        """
        medium = self._resize_width(array, 320)
        height, width = medium.shape[:2]
        if height < 24 or width < 32:
            return False, 0.5, 0.5, 0.0, 0.0
        x0, x1 = int(width * 0.06), int(width * 0.94)
        y0, y1 = int(height * 0.10), int(height * 0.82)
        crop = medium[y0:y1, x0:x1].astype("float32")
        if crop.size == 0:
            return False, 0.5, 0.5, 0.0, 0.0
        r, g, b = crop[..., 0], crop[..., 1], crop[..., 2]
        mask = (r > 145) & (g > 72) & (g < r * 0.94) & (b < g * 0.82) & (r - b > 55)
        total = mask.sum()
        area = float(total) / float(mask.size)
        if total < 6 or area > 0.10:
            return False, 0.5, 0.5, 0.0, area

        h, w = mask.shape
        grid_y, grid_x = 10, 16
        cell_h, cell_w = max(1, h // grid_y), max(1, w // grid_x)
        best = (0.0, 0, 0)
        for gy in range(grid_y):
            for gx in range(grid_x):
                block = mask[
                    gy * cell_h : (gy + 1) * cell_h,
                    gx * cell_w : (gx + 1) * cell_w,
                ]
                count = float(block.sum())
                if count > best[0]:
                    best = (count, gx, gy)
        if best[0] <= 0:
            return False, 0.5, 0.5, 0.0, area

        _, bx, by = best
        nx0, nx1 = max(0, bx - 1), min(grid_x, bx + 2)
        ny0, ny1 = max(0, by - 1), min(grid_y, by + 2)
        cluster = mask[
            ny0 * cell_h : ny1 * cell_h,
            nx0 * cell_w : nx1 * cell_w,
        ]
        cluster_count = float(cluster.sum())
        concentration = cluster_count / max(1.0, float(total))
        ys, xs = np.nonzero(cluster)
        if xs.size:
            cxp = (nx0 * cell_w) + float(xs.mean())
            cyp = (ny0 * cell_h) + float(ys.mean())
        else:
            cxp = (bx + 0.5) * cell_w
            cyp = (by + 0.5) * cell_h
        ox = max(0.0, min(1.0, (x0 + cxp) / max(1, width)))
        oy = max(0.0, min(1.0, (y0 + cyp) / max(1, height)))

        # Compactness + brightness + enough pixels: all three must agree.
        density = cluster_count / max(1.0, float(cluster.size))
        score = min(1.0, cluster_count / 25.0) * min(1.0, concentration * 1.6) * min(1.0, 0.55 + density * 4.0)
        visible = bool(score >= 0.28 and 0.00015 <= area <= 0.085)
        return visible, ox, oy, float(score), area

    def _titan_meter(self, array) -> float:
        """Warm-pixel ratio in the bottom-centre Titan-meter slot.

        This is intentionally a weak hint.  The LLM's screenshot is the source
        of truth for whether the Titan is actually ready.
        """
        height, width = array.shape[:2]
        patch = array[
            int(height * 0.78) : int(height * 0.99),
            int(width * 0.34) : int(width * 0.66),
        ]
        if patch.size == 0:
            return 0.0
        r = patch[..., 0].astype("float32")
        g = patch[..., 1].astype("float32")
        b = patch[..., 2].astype("float32")
        warm = (r > 125) & (r > b * 1.35) & (g > b * 1.10)
        return float(warm.mean())

    # -- public ----------------------------------------------------------

    def analyze(self, image) -> FrameFeatures:
        _require_numpy()
        array = np.asarray(image)
        if array.ndim != 3 or array.shape[0] < 8 or array.shape[1] < 8:
            return FrameFeatures(
                width=int(array.shape[1]) if array.ndim >= 2 else 0,
                height=int(array.shape[0]) if array.ndim >= 2 else 0,
                black=True,
            )
        small = self._small(array)
        luma = self._luma(small)
        mean = float(luma.mean())
        contrast = float(luma.std())
        black = mean < 8.0 and float(luma.max()) < 28.0
        (
            motion,
            mx,
            my,
            strength,
            cmotion,
            cstrength,
            cmx,
            cmy,
            _,
        ) = self._motion(small)
        if motion < 0.0004:
            self._frozen_ticks += 1
        else:
            self._frozen_ticks = 0
        scene_hash = perceptual_hash(small)
        crosshair, rgb, red, blue, orange = self._crosshair(array)
        center_red, hit_flash = self._center_red(array)
        obj_visible, obj_x, obj_y, obj_score, obj_area = self._objective_marker(array)
        titan_meter = self._titan_meter(array)
        height, width = array.shape[:2]
        cx0, cx1 = int(width * 0.30), int(width * 0.70)
        cy0, cy1 = int(height * 0.25), int(height * 0.75)
        centre = luma[int(height * 0.25) : int(height * 0.75), cx0:cx1]
        edge = 0.0
        if centre.shape[0] > 2 and centre.shape[1] > 2:
            gx = np.abs(np.diff(centre, axis=1)).mean()
            gy = np.abs(np.diff(centre, axis=0)).mean()
            edge = float((gx + gy) / 255.0)
        whole_edge = 0.0
        if luma.shape[0] > 2 and luma.shape[1] > 2:
            whole_edge = float(
                (np.abs(np.diff(luma, axis=1)).mean() + np.abs(np.diff(luma, axis=0)).mean()) / 255.0 * 2.0
            )

        features = FrameFeatures(
            width=width,
            height=height,
            mean_luma=mean / 255.0,
            contrast=contrast / 255.0,
            motion=motion,
            scene_hash=scene_hash,
            black=black,
            frozen=self._frozen_ticks >= 3,
            red_border=self._red_border(small),
            center_luma=float(self._luma(array[cy0:cy1, cx0:cx1]).mean()) / 255.0,
            center_edge=edge,
            crosshair=crosshair,
            crosshair_rgb=rgb,
            crosshair_red=red,
            crosshair_blue=blue,
            crosshair_orange=orange,
            text_band=self._text_band(array),
            motion_x=mx,
            motion_y=my,
            motion_strength=strength,
            center_motion=cmotion,
            center_motion_strength=cstrength,
            center_motion_x=cmx,
            center_motion_y=cmy,
            center_red=center_red,
            hit_flash=hit_flash,
            objective_visible=obj_visible,
            objective_x=obj_x,
            objective_y=obj_y,
            objective_score=obj_score,
            objective_area=obj_area,
            titan_meter=titan_meter,
            edge_density=whole_edge,
            aspect=width / float(height) if height else 0.0,
        )
        self._prev_small = luma
        self._prev_hash = scene_hash
        self._prev_luma = mean
        self.frames += 1
        return features

    def reset(self) -> None:
        self._prev_small = None
        self._prev_hash = 0
        self._prev_luma = 0.0
        self._frozen_ticks = 0


def frame_features_summary(features: FrameFeatures | None) -> str:
    """One-line Chinese description used in prompts and status."""
    if features is None:
        return "还没有画面"
    parts = [
        f"亮度{features.mean_luma:.2f}",
        f"运动{features.motion:.3f}",
        f"对比{features.contrast:.2f}",
    ]
    if features.black:
        parts.append("全黑")
    if features.frozen:
        parts.append("画面几乎静止")
    if features.red_border > 0.012:
        parts.append(f"画面边缘泛红{features.red_border:.2f}（可能受伤）")
    if features.objective_visible:
        parts.append(
            f"橙色任务标记在({features.objective_x:.2f},{features.objective_y:.2f})，置信{features.objective_score:.2f}"
        )
    if features.crosshair:
        parts.append(f"准星RGB{features.crosshair_rgb}")
        if features.crosshair_red > 0.22:
            parts.append("准星附近偏红")
    if features.center_motion > 0.015:
        parts.append(
            f"中央运动{features.center_motion:.3f}@({features.center_motion_x:.2f},{features.center_motion_y:.2f})"
        )
    if features.center_red > 0.02:
        parts.append(f"中央红色{features.center_red:.2f}")
    if features.hit_flash > 0.86 and features.center_motion > 0.01:
        parts.append("中央有亮闪（可能是命中/爆炸）")
    if features.titan_meter > 0.06:
        parts.append(f"底部中央暖色{features.titan_meter:.2f}（泰坦计量提示）")
    if features.text_band > 0.25:
        parts.append("底部有字幕/提示")
    return "，".join(parts)
