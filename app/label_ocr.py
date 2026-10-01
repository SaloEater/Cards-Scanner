from __future__ import annotations

import csv
import io
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TypedDict

import cv2
import numpy as np

log = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]

_TESSERACT_FALLBACK = "/opt/homebrew/bin/tesseract"
_warned = False


class LabelText(TypedDict):
    kind: str
    rows: list[list[str]]  # always 4 rows x 2 strings


def _warn_once(msg: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning(msg)


def _tesseract_bin() -> str | None:
    return shutil.which("tesseract") or (_TESSERACT_FALLBACK if Path(_TESSERACT_FALLBACK).exists() else None)


def _preprocess(bgr_full: np.ndarray, rect: Rect) -> np.ndarray:
    x, y, w, h = rect
    ih, iw = bgr_full.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(iw, x + w), min(ih, y + h)
    crop = bgr_full[y0:y1, x0:x1]
    cw, ch = crop.shape[1], crop.shape[0]
    bx, by = int(cw * 0.04), int(ch * 0.06)  # shave the red border
    crop = crop[by:ch - by, bx:cw - bx]
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if g.shape[1] < 1000:
        g = cv2.resize(g, (g.shape[1] * 2, g.shape[0] * 2), interpolation=cv2.INTER_LANCZOS4)
    # autocontrast, cutoff=1 (1% clipped from each end of the histogram)
    lo, hi = np.percentile(g, 1), np.percentile(g, 99)
    if hi > lo:
        g = np.clip((g.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
    return np.where(g > 150, 255, 0).astype(np.uint8)


def _parse(tsv: str, width: int) -> list[list[str]]:
    rows = [r for r in csv.DictReader(io.StringIO(tsv), delimiter="\t")
            if (r.get("text") or "").strip() and float(r["conf"]) >= 0]
    lines: dict[tuple[int, int, int], list[dict]] = {}
    for r in rows:
        lines.setdefault((int(r["block_num"]), int(r["par_num"]), int(r["line_num"])), []).append(r)
    grid: list[list[str]] = []
    for key in sorted(lines):
        ws = sorted(lines[key], key=lambda r: int(r["left"]))
        ws = [r for r in ws if float(r["conf"]) >= 30 and re.search(r"[A-Za-z0-9]", r["text"])]
        if not ws:
            continue
        split = len(ws)
        gaps = [(int(ws[i + 1]["left"]) - (int(ws[i]["left"]) + int(ws[i]["width"])), i)
                for i in range(len(ws) - 1)]
        if gaps:
            gap, i = max(gaps)
            if gap > width * 0.06:
                split = i + 1
        grid.append([" ".join(r["text"] for r in ws[:split]), " ".join(r["text"] for r in ws[split:])])
        if len(grid) == 4:
            break
    # A lone cert number (8-9 digits) on a line is the right cell, not the left one.
    for row in grid:
        if not row[1] and re.fullmatch(r"\d{8,9}", row[0]):
            row[0], row[1] = "", row[0]
    while len(grid) < 4:
        grid.append(["", ""])
    return grid


def ocr_label(bgr_full: np.ndarray, rect: Rect) -> LabelText | None:
    """Read a PSA-style label into a 4x2 grid. None if Tesseract is missing or fails."""
    exe = _tesseract_bin()
    if exe is None:
        _warn_once("tesseract not found; label OCR disabled")
        return None
    try:
        img = _preprocess(bgr_full, tuple(int(v) for v in rect))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "label.png"
            cv2.imwrite(str(p), img)
            res = subprocess.run([exe, str(p), "stdout", "--psm", "6", "-l", "eng", "tsv"],
                                 capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            _warn_once(f"tesseract failed (exit {res.returncode})")
            return None
        return {"kind": "psa", "rows": _parse(res.stdout, img.shape[1])}
    except Exception as e:  # never crash the screen
        _warn_once(f"label OCR failed: {e!r}")
        return None
