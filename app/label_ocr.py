from __future__ import annotations

import csv
import io
import logging
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import TypedDict

import cv2
import numpy as np

log = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]

_TESSERACT_FALLBACK = "/opt/homebrew/bin/tesseract"
_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#/-.&"
_warned = False


class LabelText(TypedDict):
    kind: str
    rows: list[list[str]]  # always 4 rows x 2 strings


@dataclass
class OcrSettings:
    threshold: int = 150            # 0-255, fixed mode
    mode: str = "fixed"             # "fixed" | "adaptive"
    adaptive_block: int = 31        # odd, 11-75
    adaptive_c: int = 10            # -20..20
    confidence: int = 30            # min word confidence (first word of a line: confidence - 10)
    shave_pct: float = 2.0          # border inset, % of the label WIDTH, same pixel inset on all four sides
    upscale: int = 0                # 0 = auto (2x when < 1000 px wide), else 1 / 2 / 3
    blur: int = 0                   # gaussian radius px before thresholding
    psm: int = 6                    # 6 block | 4 columns | 11 sparse
    deskew: bool = False
    whitelist: bool = False
    logo_mask: bool = True          # blank the PSA logo region before OCR
    logo_x_pct: float = 40.0        # mask rect, % of the shaved crop (x from left, width, y from top to bottom)
    logo_w_pct: float = 20.0
    logo_y_pct: float = 72.0
    best_of_two: bool = False       # also run at threshold +-25 and keep the better row set

    @classmethod
    def from_mapping(cls, m: dict) -> OcrSettings:
        d = cls()
        if "shave_pct" not in m and m.get("shave_x_pct") is not None:  # legacy two-value keys
            m = {**m, "shave_pct": m["shave_x_pct"]}
        for f in fields(cls):
            if f.name in m and m[f.name] is not None:
                try:
                    cur, v = getattr(d, f.name), m[f.name]
                    if isinstance(cur, bool):
                        v = v.strip().lower() in ("true", "1") if isinstance(v, str) else bool(v)
                    else:
                        v = type(cur)(v)
                    setattr(d, f.name, v)
                except (TypeError, ValueError):
                    pass
        return d

    def to_mapping(self) -> dict:
        return asdict(self)


@dataclass
class OcrWord:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int
    line: int          # index of Tesseract's line (sorted order)
    kept: bool = False
    reason: str = ""   # why dropped ("" when kept)
    row: int = -1      # grid row for kept words


@dataclass
class OcrResult:
    rows: list[list[str]] = field(default_factory=lambda: [["", ""] for _ in range(4)])
    bitmap: np.ndarray | None = None
    words: list[OcrWord] = field(default_factory=list)
    splits: dict[int, tuple[int, int, int]] = field(default_factory=dict)  # row -> (x, y0, y1) in bitmap px
    skew_deg: float = 0.0
    raw_text: str = ""
    elapsed_ms: float = 0.0
    logo_rect: Rect | None = None   # in bitmap px
    best_of: str = ""               # "base" | "alt" when best_of_two ran
    error: str = ""

    def regrid(self, settings: OcrSettings) -> None:
        """Re-apply the confidence rule to the stored words (no Tesseract run)."""
        self.rows, self.splits = _build_grid(self.words, self.bitmap.shape[1], settings.confidence)
        self.raw_text = _raw_text(self.words)

    def dropped(self) -> list[OcrWord]:
        return [w for w in self.words if not w.kept]


def _warn_once(msg: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning(msg)


def _tesseract_bin() -> str | None:
    return shutil.which("tesseract") or (_TESSERACT_FALLBACK if Path(_TESSERACT_FALLBACK).exists() else None)


def _estimate_skew(gray: np.ndarray) -> float:
    """Text-line angle in degrees (positive = text runs downward to the right), by maximising the
    sharpness of the horizontal projection profile of the dark pixels over small rotations."""
    h, w = gray.shape
    scale = 400 / w if w > 400 else 1.0
    small = cv2.resize(gray, (int(w * scale), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    ink = (small < 140).astype(np.float32)
    if ink.sum() < 20:
        return 0.0
    sh, sw = ink.shape
    c = (sw / 2, sh / 2)

    def score(a: float) -> float:
        m = cv2.getRotationMatrix2D(c, a, 1.0)
        r = cv2.warpAffine(ink, m, (sw, sh), flags=cv2.INTER_LINEAR)
        prof = r.sum(axis=1)
        return float(np.sum(np.diff(prof) ** 2))

    best = max(np.arange(-5.0, 5.01, 0.5), key=score)
    best = max(np.arange(best - 0.5, best + 0.51, 0.1), key=score)
    return float(round(best, 2))


def _preprocess(bgr_full: np.ndarray, rect: Rect, s: OcrSettings, threshold: int | None = None
                ) -> tuple[np.ndarray, float, Rect | None]:
    x, y, w, h = rect
    ih, iw = bgr_full.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(iw, x + w), min(ih, y + h)
    crop = bgr_full[y0:y1, x0:x1]
    cw, ch = crop.shape[1], crop.shape[0]
    bx = by = min(int(cw * s.shave_pct / 100), (cw - 1) // 2, (ch - 1) // 2)  # shave the red border
    crop = crop[by:ch - by, bx:cw - bx]
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    up = s.upscale if s.upscale in (1, 2, 3) else (2 if g.shape[1] < 1000 else 1)
    if up > 1:
        g = cv2.resize(g, (g.shape[1] * up, g.shape[0] * up), interpolation=cv2.INTER_LANCZOS4)
    # autocontrast, cutoff=1 (1% clipped from each end of the histogram)
    lo, hi = np.percentile(g, 1), np.percentile(g, 99)
    if hi > lo:
        g = np.clip((g.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
    if s.blur > 0:
        k = s.blur * 2 + 1
        g = cv2.GaussianBlur(g, (k, k), 0)
    skew = 0.0
    if s.deskew:
        skew = _estimate_skew(g)
        if abs(skew) >= 0.3:
            hh, ww = g.shape
            m = cv2.getRotationMatrix2D((ww / 2, hh / 2), skew, 1.0)
            g = cv2.warpAffine(g, m, (ww, hh), flags=cv2.INTER_LINEAR, borderValue=255)
    if s.mode == "adaptive":
        blk = max(3, int(s.adaptive_block) | 1)
        bw = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, blk, s.adaptive_c)
    else:
        t = s.threshold if threshold is None else threshold
        bw = np.where(g > t, 255, 0).astype(np.uint8)
    logo = None
    if s.logo_mask:
        hh, ww = bw.shape
        lx = int(ww * s.logo_x_pct / 100)
        lw = int(ww * s.logo_w_pct / 100)
        ly = int(hh * s.logo_y_pct / 100)
        bw[ly:, lx:lx + lw] = 255
        logo = (lx, ly, lw, hh - ly)
    return bw, skew, logo


def preview_bitmap(bgr_full: np.ndarray, rect: Rect, settings: OcrSettings) -> tuple[np.ndarray, float, Rect | None]:
    """Just the preprocessing (no Tesseract): the bitmap that would be read, skew angle, logo rect."""
    return _preprocess(bgr_full, tuple(int(v) for v in rect), settings)


def _run_tesseract(exe: str, img: np.ndarray, s: OcrSettings) -> str:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "label.png"
        cv2.imwrite(str(p), img)
        cmd = [exe, str(p), "stdout", "--psm", str(s.psm), "-l", "eng"]
        if s.whitelist:
            cmd += ["-c", f"tessedit_char_whitelist={_WHITELIST}"]
        res = subprocess.run(cmd + ["tsv"], capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise RuntimeError(f"tesseract exit {res.returncode}")
    return res.stdout


def _read_words(tsv: str) -> list[OcrWord]:
    rows = [r for r in csv.DictReader(io.StringIO(tsv), delimiter="\t")
            if (r.get("text") or "").strip() and float(r["conf"]) >= 0]
    keys = sorted({(int(r["block_num"]), int(r["par_num"]), int(r["line_num"])) for r in rows})
    idx = {k: i for i, k in enumerate(keys)}
    words = [OcrWord(r["text"], float(r["conf"]), int(r["left"]), int(r["top"]), int(r["width"]),
                     int(r["height"]), idx[(int(r["block_num"]), int(r["par_num"]), int(r["line_num"]))])
             for r in rows]
    words.sort(key=lambda w: (w.line, w.left))
    return words


def _build_grid(words: list[OcrWord], width: int, confidence: int
                ) -> tuple[list[list[str]], dict[int, tuple[int, int, int]]]:
    """Group words by Tesseract line, filter, split columns. Fills kept/reason/row on the words."""
    grid: list[list[str]] = []
    splits: dict[int, tuple[int, int, int]] = {}
    by_line: dict[int, list[OcrWord]] = {}
    for w in words:
        w.kept, w.reason, w.row = False, "", -1
        by_line.setdefault(w.line, []).append(w)
    for li in sorted(by_line):
        ws = by_line[li]
        survivors: list[OcrWord] = []
        for n, w in enumerate(ws):
            need = confidence - 10 if n == 0 else confidence  # first word of a line is the usual casualty
            if not re.search(r"[A-Za-z0-9]", w.text):
                w.reason = "no alphanumerics"
            elif w.conf < need:
                w.reason = f"conf {w.conf:.0f} < {need}"
            else:
                survivors.append(w)
        if not survivors:
            continue
        if len(grid) >= 4:
            for w in survivors:
                w.reason = "beyond row 4"
            continue
        split = len(survivors)
        gaps = [(survivors[i + 1].left - (survivors[i].left + survivors[i].width), i)
                for i in range(len(survivors) - 1)]
        if gaps:
            gap, i = max(gaps)
            if gap > width * 0.06:
                split = i + 1
        r = len(grid)
        for w in survivors:
            w.kept, w.row = True, r
        grid.append([" ".join(w.text for w in survivors[:split]), " ".join(w.text for w in survivors[split:])])
        if split < len(survivors):
            a, b = survivors[split - 1], survivors[split]
            top = min(w.top for w in survivors)
            bot = max(w.top + w.height for w in survivors)
            splits[r] = ((a.left + a.width + b.left) // 2, top, bot)
    # A lone cert number (8-9 digits) on a line is the right cell, not the left one.
    for row in grid:
        if not row[1] and re.fullmatch(r"\d{8,9}", row[0]):
            row[0], row[1] = "", row[0]
    while len(grid) < 4:
        grid.append(["", ""])
    return grid, splits


def _mean_conf(words: list[OcrWord]) -> float:
    k = [w.conf for w in words if w.kept]
    return sum(k) / len(k) if k else 0.0


def ocr_label_debug(bgr_full: np.ndarray, rect: Rect, settings: OcrSettings | None = None) -> OcrResult:
    """OCR a PSA-style label and return everything needed to explain the result."""
    s = settings or OcrSettings()
    t0 = time.perf_counter()
    res = OcrResult()
    exe = _tesseract_bin()
    if exe is None:
        _warn_once("tesseract not found; label OCR disabled")
        res.error = "tesseract not found"
        return res
    try:
        rect = tuple(int(v) for v in rect)
        thresholds = [None]
        if s.best_of_two and s.mode == "fixed":
            thresholds.append(s.threshold + 25 if s.threshold + 25 <= 255 else s.threshold - 25)
        best = None
        for n, t in enumerate(thresholds):
            img, skew, logo = _preprocess(bgr_full, rect, s, t)
            tsv = _run_tesseract(exe, img, s)
            words = _read_words(tsv)
            rows, splits = _build_grid(words, img.shape[1], s.confidence)
            cand = (_mean_conf(words), sum(w.kept for w in words), n, img, skew, logo, tsv, words, rows, splits)
            if best is None or cand[:2] > best[:2]:
                best = cand
        _, _, n, img, skew, logo, tsv, words, rows, splits = best
        res.bitmap, res.skew_deg, res.logo_rect = img, skew, logo
        res.words, res.rows, res.splits = words, rows, splits
        if len(thresholds) > 1:
            res.best_of = "base" if n == 0 else "alt"
        res.raw_text = _raw_text(words)
    except Exception as e:  # never crash the screen
        _warn_once(f"label OCR failed: {e!r}")
        res.error = repr(e)
    res.elapsed_ms = (time.perf_counter() - t0) * 1000
    return res


def _raw_text(words: list[OcrWord]) -> str:
    out: list[str] = []
    for li in sorted({w.line for w in words}):
        ws = [w for w in words if w.line == li]
        out.append(" ".join(f"{w.text}({w.conf:.0f})" for w in ws))
    drops = [w for w in words if not w.kept]
    if drops:
        out.append("")
        out.append("dropped:")
        out += [f"  '{w.text}' conf {w.conf:.0f}: {w.reason}" for w in drops]
    return "\n".join(out)


def ocr_label(bgr_full: np.ndarray, rect: Rect, settings: OcrSettings | None = None) -> LabelText | None:
    """Read a PSA-style label into a 4x2 grid. None if Tesseract is missing or fails."""
    r = ocr_label_debug(bgr_full, rect, settings)
    if r.error:
        return None
    return {"kind": "psa", "rows": r.rows}
