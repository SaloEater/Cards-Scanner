"""Thumbnail generation: contain-fit resize + label-safe vivid saturation boost.

Ported from the throwaway checklist quality test at thumbnail_test.py — see
that file for the visual comparisons that led to this recipe (INTER_AREA
downscale + HSV saturation x5 below an operator-chosen line, feathered seam).
Image manipulation here is vividness + resize ONLY (no unsharp mask, no other
filters — that was explicitly rejected after the test).
"""
from __future__ import annotations

import cv2
import numpy as np


def contain_size(src_w: int, src_h: int, box_w: float, box_h: float) -> tuple[int, int]:
    """Pixel size of `object-fit: contain` for the source inside the box."""
    scale = min(box_w / src_w, box_h / src_h)
    return max(1, round(src_w * scale)), max(1, round(src_h * scale))


def vivid(img: np.ndarray, factor: float) -> np.ndarray:
    """Saturation boost: multiply HSV saturation, clipped. factor 1.0 = unchanged."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * factor, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def label_safe(
    base: np.ndarray, boosted: np.ndarray, band_fraction: float, feather_px: int = 8
) -> np.ndarray:
    """Top `band_fraction` of rows come from `base`, the rest from `boosted`.

    The seam between the two is linearly feathered over `feather_px` rows
    below the split so the saturation jump doesn't read as a hard line.
    """
    h = base.shape[0]
    split = int(h * band_fraction)
    out = boosted.copy()
    out[:split] = base[:split]
    feather = min(feather_px, h - split)
    for i in range(feather):
        a = (i + 1) / (feather + 1)  # 0 -> base, 1 -> boosted
        row = split + i
        out[row] = cv2.addWeighted(boosted[row : row + 1], a, base[row : row + 1], 1 - a, 0)
    return out


def generate_thumbnail(
    bgr: np.ndarray,
    line_fraction: float,
    box_w: int,
    box_h: int,
    sat: float = 5.0,
    feather_px: int = 8,
) -> np.ndarray:
    """Build the label-safe vivid thumbnail from the stored capture.

    `bgr` is the same pixels written to `<index>.jpg` and `line_fraction` is
    measured on that image. The photo's `rotation` metadata is deliberately
    ignored here — it's a display-only effect the frontend applies via CSS to
    the full photo and the thumbnail alike, so the thumbnail must stay in
    storage orientation with the protected band at its top.
    """
    line_fraction = max(0.0, min(1.0, line_fraction))
    w, h = contain_size(bgr.shape[1], bgr.shape[0], box_w, box_h)
    base = cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA)
    return label_safe(base, vivid(base, sat), line_fraction, feather_px)
