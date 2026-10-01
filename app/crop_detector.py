from __future__ import annotations

import cv2
import numpy as np

Rect = tuple[int, int, int, int]  # x, y, w, h in image pixels

ART_MIN_AREA = 0.20
ART_MAX_AREA = 0.90
ART_ASPECT_MIN = 0.55
ART_ASPECT_MAX = 0.85
LABEL_MIN_WIDTH_FRAC = 0.40
_GRAD_T = 60      # Sobel magnitude counted as an edge (at 300 px width)
_DENS_T = 0.5     # local edge density counted as busy
_SAT_T = 60       # HSV saturation counted as busy
_ERODE = 13       # px at 300 px width; cuts thin slab strips off the card blob


def detect_art(bgr: np.ndarray) -> Rect:
    """Card inside a slab: the largest busy/saturated blob, axis-aligned.

    Plain Canny + approxPolyDP (CardDetector's approach) fails inside a slab:
    the card outline merges with the slab's many internal lines. So the edge
    signal is turned into a dense "busy" mask (edge density | saturation, red
    label border excluded), thin slab strips are cut off by a strong erosion,
    and the largest remaining blob's bounding rect is validated with the
    area/aspect limits. Falls back to the whole image.
    """
    h, w = bgr.shape[:2]
    full: Rect = (0, 0, w, h)
    sw = 300
    sh = max(1, int(round(h * sw / w)))
    s = cv2.resize(bgr, (sw, sh), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(s, cv2.COLOR_BGR2HSV)
    g = cv2.GaussianBlur(cv2.cvtColor(s, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    gm = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    dens = cv2.boxFilter((gm > _GRAD_T).astype(np.float32), -1, (9, 9))
    mask = ((dens > _DENS_T) | (hsv[..., 1] > _SAT_T)).astype(np.uint8) * 255
    red = ((hsv[..., 0] < 10) | (hsv[..., 0] > 170)) & (hsv[..., 1] > 100)
    mask[red & (np.arange(sh)[:, None] < sh * 0.3)] = 0
    k = _ERODE
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    core = cv2.erode(mask, np.ones((k, k), np.uint8))
    contours, _ = cv2.findContours(core, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return full
    x, y, bw, bh = cv2.boundingRect(max(contours, key=cv2.contourArea))
    pad = k // 2
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(sw, x + bw + pad), min(sh, y + bh + pad)
    fx, fy = w / sw, h / sh
    rx, ry = int(round(x0 * fx)), int(round(y0 * fy))
    rw, rh = int(round((x1 - x0) * fx)), int(round((y1 - y0) * fy))
    area = rw * rh
    if not (ART_MIN_AREA * w * h <= area <= ART_MAX_AREA * w * h):
        return full
    aspect = rw / rh
    if not (ART_ASPECT_MIN <= aspect <= ART_ASPECT_MAX
            or 1 / ART_ASPECT_MAX <= aspect <= 1 / ART_ASPECT_MIN):
        return full
    return (rx, ry, rw, rh)


def detect_label(bgr: np.ndarray, art: Rect) -> Rect | None:
    """Bounding rect of the red-bordered grader label above the art, or None."""
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lo = cv2.inRange(hsv, (0, 100, 80), (10, 255, 255))
    hi = cv2.inRange(hsv, (170, 100, 80), (180, 255, 255))
    mask = cv2.bitwise_or(lo, hi)
    k = max(5, w // 60) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best: Rect | None = None
    for cnt in sorted(contours, key=cv2.contourArea, reverse=True):
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw < LABEL_MIN_WIDTH_FRAC * w:
            continue
        if y + bh / 2 >= art[1]:
            continue
        if best is None or bw * bh > best[2] * best[3]:
            best = (x, y, bw, bh)
    return best
