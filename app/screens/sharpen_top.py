from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.models import Series

_AMOUNT_DEFAULT = 4.0
_SEAM_FEATHER = 0.04
_DISPLAY_MAX_H = 700  # cap raw image height for live preview speed


def _unsharp(img: np.ndarray, amount: float, sigma: float) -> np.ndarray:
    blur = cv2.GaussianBlur(img, (0, 0), sigma)
    return cv2.addWeighted(img, 1 + amount, blur, -amount, 0)


def _apply_sharpen(
    img: np.ndarray, frac: float, top_amount: float, bot_amount: float, sigma: float
) -> np.ndarray:
    h = img.shape[0]
    split = int(h * frac)
    sharp_top = _unsharp(img, top_amount, sigma) if top_amount > 0.01 else img
    sharp_bot = _unsharp(img, bot_amount, sigma) if bot_amount > 0.01 else img
    out = sharp_top.copy()
    out[split:] = sharp_bot[split:]
    feather = min(int(h * _SEAM_FEATHER), split, h - split)
    for i in range(feather):
        a = 1.0 - (i + 1) / (feather + 1)
        row = split + i
        out[row] = cv2.addWeighted(sharp_top[row:row + 1], a, sharp_bot[row:row + 1], 1 - a, 0)[0]
    return out


def rescale(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    scale = max(config.CARD_OUTPUT_W / w, config.CARD_OUTPUT_H / h)
    if abs(scale - 1.0) > 0.01:
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=interp)
    return img


class _ImageLineWidget(QWidget):
    line_changed = Signal(float)  # fraction 0–1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._frac: float = 0.15
        self._pixmap: QPixmap | None = None
        self.setCursor(Qt.CursorShape.SplitVCursor)
        self.setStyleSheet("background: black;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(False)

    def set_preview(self, bgr: np.ndarray, frac: float) -> None:
        self._frac = frac
        h, w, ch = bgr.shape
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        img = QImage(rgb.data, w, h, w * ch, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(img.copy())
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._pixmap is None:
            return
        painter = QPainter(self)
        scaled = self._pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        px = (self.width() - scaled.width()) // 2
        py = (self.height() - scaled.height()) // 2
        painter.drawPixmap(px, py, scaled)
        line_y = py + int(self._frac * scaled.height())
        painter.setPen(QPen(QColor(255, 50, 50), 2))
        painter.drawLine(px, line_y, px + scaled.width(), line_y)
        painter.end()

    def mousePressEvent(self, event) -> None:
        self._handle_mouse(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._handle_mouse(event)

    def _handle_mouse(self, event) -> None:
        if self._pixmap is None:
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        ww, wh = self.width(), self.height()
        s = min(ww / pw, wh / ph)
        scaled_h = ph * s
        py = (wh - scaled_h) / 2
        frac = max(0.0, min(1.0, (event.position().y() - py) / scaled_h))
        if abs(frac - self._frac) > 0.002:
            self._frac = frac
            self.line_changed.emit(frac)


class SharpenTopScreen(QWidget):
    navigate_to_review = Signal(object, object)  # (Series, final_bgr)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: Series | None = None
        self._raw_bgr: np.ndarray | None = None
        self._display_bgr: np.ndarray | None = None
        self._frac: float = 0.15
        self._last_applied_frac: float = 0.15
        self._top_amount: float = _AMOUNT_DEFAULT
        self._bot_amount: float = 0.0
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        title = QLabel("Sharpen top — drag the red line to set the split")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #aaa; font-size: 13px;")
        root.addWidget(title)

        body = QHBoxLayout()
        body.setSpacing(12)

        self._image_widget = _ImageLineWidget()
        self._image_widget.line_changed.connect(self._on_line_changed)
        body.addWidget(self._image_widget, stretch=1)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)

        right.addWidget(QLabel("Top sharpen:"))
        self._top_label = QLabel(f"{_AMOUNT_DEFAULT:.1f}")
        self._top_slider = QSlider(Qt.Orientation.Horizontal)
        self._top_slider.setRange(0, 100)
        self._top_slider.setValue(int(_AMOUNT_DEFAULT * 10))
        self._top_slider.valueChanged.connect(self._on_top_changed)
        top_row = QHBoxLayout()
        top_row.addWidget(self._top_slider)
        top_row.addWidget(self._top_label)
        right.addLayout(top_row)

        right.addWidget(QLabel("Bottom sharpen:"))
        self._bot_label = QLabel("0.0")
        self._bot_slider = QSlider(Qt.Orientation.Horizontal)
        self._bot_slider.setRange(0, 100)
        self._bot_slider.setValue(0)
        self._bot_slider.valueChanged.connect(self._on_bot_changed)
        bot_row = QHBoxLayout()
        bot_row.addWidget(self._bot_slider)
        bot_row.addWidget(self._bot_label)
        right.addLayout(bot_row)

        right.addStretch()

        skip_btn = QPushButton("Skip")
        skip_btn.clicked.connect(self._on_skip)
        right.addWidget(skip_btn)
        apply_btn = QPushButton("Apply Sharpening")
        apply_btn.clicked.connect(self._on_apply)
        right.addWidget(apply_btn)

        body.addLayout(right)
        root.addLayout(body, stretch=1)

    def load(self, series: Series, raw_bgr: np.ndarray) -> None:
        self._series = series
        self._raw_bgr = raw_bgr
        self._frac = self._last_applied_frac
        rh, rw = raw_bgr.shape[:2]
        if rh > _DISPLAY_MAX_H:
            ds = _DISPLAY_MAX_H / rh
            self._display_bgr = cv2.resize(
                raw_bgr, (int(rw * ds), _DISPLAY_MAX_H), interpolation=cv2.INTER_AREA
            )
        else:
            self._display_bgr = raw_bgr
        self._update_preview()

    def _on_line_changed(self, frac: float) -> None:
        self._frac = frac
        self._update_preview()

    def _on_top_changed(self, value: int) -> None:
        self._top_amount = value / 10.0
        self._top_label.setText(f"{self._top_amount:.1f}")
        self._update_preview()

    def _on_bot_changed(self, value: int) -> None:
        self._bot_amount = value / 10.0
        self._bot_label.setText(f"{self._bot_amount:.1f}")
        self._update_preview()

    def _update_preview(self) -> None:
        if self._display_bgr is None:
            return
        preview = _apply_sharpen(self._display_bgr, self._frac, self._top_amount, self._bot_amount, sigma=1.0)
        self._image_widget.set_preview(preview, self._frac)

    def _on_skip(self) -> None:
        self.navigate_to_review.emit(self._series, rescale(self._raw_bgr))

    def _on_apply(self) -> None:
        self._last_applied_frac = self._frac
        raw_h, raw_w = self._raw_bgr.shape[:2]
        scale_factor = max(config.CARD_OUTPUT_W / raw_w, config.CARD_OUTPUT_H / raw_h)
        sigma = max(1.0, (1.0 / scale_factor) / 2.0)
        sharpened = _apply_sharpen(self._raw_bgr, self._frac, self._top_amount, self._bot_amount, sigma)
        self.navigate_to_review.emit(self._series, rescale(sharpened))
