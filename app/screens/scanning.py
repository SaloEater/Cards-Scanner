from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.camera import CameraWorker
from app.detector import CardDetector
from app.models import Series

_DBG_W, _DBG_H = 300, 225


def _make_pane(label: str) -> QLabel:
    w = QLabel()
    w.setFixedSize(_DBG_W, _DBG_H)
    w.setAlignment(Qt.AlignmentFlag.AlignCenter)
    w.setStyleSheet("background: black; color: #888; font-size: 11px;")
    w.setText(label)
    return w


class ScanningScreen(QWidget):
    navigate_to_review = Signal(object, object)    # (Series, cropped_bgr ndarray)
    navigate_to_thumbnail = Signal(object)          # Series

    def __init__(self, camera_worker: CameraWorker, detector: CardDetector, parent=None) -> None:
        super().__init__(parent)
        self._camera = camera_worker
        self._detector = detector
        self._series: Series | None = None
        self._cameras_populated = False
        self._rotation: int = config.CAMERA_ROTATION
        self._build_ui()
        self._connect_camera()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)

        # Left panel — QStackedWidget switching between normal and debug views
        self._preview_stack = QStackedWidget()
        self._preview_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Page 0: normal full preview
        self._preview = QLabel()
        self._preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet("background: black;")
        self._preview_stack.addWidget(self._preview)

        # Page 1: 2×2 debug grid
        dbg_widget = QWidget()
        dbg_grid = QGridLayout(dbg_widget)
        dbg_grid.setSpacing(4)
        dbg_grid.setContentsMargins(4, 4, 4, 4)

        self._dbg_live = _make_pane("Live feed")
        self._dbg_edges = _make_pane("Canny edges")
        self._dbg_contours = _make_pane("Contour candidates")
        self._dbg_reason = QLabel("—")
        self._dbg_reason.setFixedSize(_DBG_W, _DBG_H)
        self._dbg_reason.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dbg_reason.setWordWrap(True)
        self._dbg_reason.setStyleSheet(
            "background: black; color: #888; font-size: 15px; font-weight: bold; padding: 8px;"
        )

        dbg_grid.addWidget(self._dbg_live,     0, 0)
        dbg_grid.addWidget(self._dbg_edges,    0, 1)
        dbg_grid.addWidget(self._dbg_contours, 1, 0)
        dbg_grid.addWidget(self._dbg_reason,   1, 1)

        self._preview_stack.addWidget(dbg_widget)
        root.addWidget(self._preview_stack)

        # Right panel — controls (fixed width)
        right_widget = QWidget()
        right_widget.setFixedWidth(240)
        right = QVBoxLayout(right_widget)
        right.setAlignment(Qt.AlignmentFlag.AlignTop)
        right.setSpacing(12)

        right.addWidget(QLabel("Camera:"))
        self._camera_combo = QComboBox()
        self._camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        right.addWidget(self._camera_combo)

        right.addWidget(QLabel("Rotation:"))
        rot_row = QHBoxLayout()
        self._rot_label = QLabel("0°")
        self._rot_label.setFixedWidth(36)
        rot_ccw = QPushButton("↺")
        rot_ccw.setFixedWidth(36)
        rot_ccw.clicked.connect(self._rotate_ccw)
        rot_cw = QPushButton("↻")
        rot_cw.setFixedWidth(36)
        rot_cw.clicked.connect(self._rotate_cw)
        rot_row.addWidget(rot_ccw)
        rot_row.addWidget(self._rot_label)
        rot_row.addWidget(rot_cw)
        rot_row.addStretch()
        right.addLayout(rot_row)

        self._canny_low_label = QLabel(f"Canny low: {config.CANNY_LOW}")
        right.addWidget(self._canny_low_label)
        self._canny_low_slider = QSlider(Qt.Orientation.Horizontal)
        self._canny_low_slider.setRange(1, 255)
        self._canny_low_slider.setValue(config.CANNY_LOW)
        self._canny_low_slider.valueChanged.connect(self._on_canny_low_changed)
        right.addWidget(self._canny_low_slider)

        self._canny_high_label = QLabel(f"Canny high: {config.CANNY_HIGH}")
        right.addWidget(self._canny_high_label)
        self._canny_high_slider = QSlider(Qt.Orientation.Horizontal)
        self._canny_high_slider.setRange(1, 255)
        self._canny_high_slider.setValue(config.CANNY_HIGH)
        self._canny_high_slider.valueChanged.connect(self._on_canny_high_changed)
        right.addWidget(self._canny_high_slider)

        self._debug_btn = QPushButton("Debug")
        self._debug_btn.setCheckable(True)
        self._debug_btn.toggled.connect(self._on_debug_toggled)
        right.addWidget(self._debug_btn)

        self._status_label = QLabel("Waiting for camera…")
        self._status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: gray;")
        right.addWidget(self._status_label)

        self._count_label = QLabel("Cards staged: 0")
        right.addWidget(self._count_label)

        capture_btn = QPushButton("Capture  [Enter]")
        capture_btn.clicked.connect(self._camera.request_capture)
        right.addWidget(capture_btn)

        done_btn = QPushButton("Done Scanning  [Esc]")
        done_btn.clicked.connect(self._on_done)
        right.addWidget(done_btn)

        root.addWidget(right_widget)

        QShortcut(QKeySequence(Qt.Key.Key_Return), self).activated.connect(self._camera.request_capture)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self._on_done)

    def _connect_camera(self) -> None:
        self._camera.frame_ready.connect(self._on_frame)
        self._camera.capture_ready.connect(self._on_capture)
        self._camera.camera_error.connect(self._on_camera_error)

    def load(self, series: Series) -> None:
        self._series = series
        self._count_label.setText(f"Cards staged: {len(series.photos)}")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._cameras_populated:
            self._populate_cameras()
            self._cameras_populated = True
        else:
            self._camera.resume()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._camera.pause()

    def _populate_cameras(self) -> None:
        self._camera_combo.blockSignals(True)
        self._camera_combo.clear()
        try:
            from PySide6.QtMultimedia import QMediaDevices
            count = len(QMediaDevices.videoInputs())
        except Exception:
            count = 4
        for i in range(count):
            self._camera_combo.addItem(f"Camera {i}", i)
        for i in range(self._camera_combo.count()):
            if self._camera_combo.itemData(i) == config.CAMERA_INDEX:
                self._camera_combo.setCurrentIndex(i)
                break
        self._camera_combo.blockSignals(False)
        if self._camera_combo.count() > 0:
            self._camera.switch_camera(self._camera_combo.currentData())

    def _on_camera_changed(self, combo_index: int) -> None:
        device_index = self._camera_combo.itemData(combo_index)
        if device_index is not None:
            self._status_label.setText("Waiting for camera…")
            self._status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: gray;")
            self._preview.clear()
            self._camera.switch_camera(device_index)

    def _on_debug_toggled(self, enabled: bool) -> None:
        self._camera.set_debug(enabled)
        self._preview_stack.setCurrentIndex(1 if enabled else 0)
        if enabled:
            self._camera.debug_ready.connect(self._on_debug_frame)
        else:
            self._camera.debug_ready.disconnect(self._on_debug_frame)
            self._dbg_edges.clear()
            self._dbg_contours.clear()
            self._dbg_reason.setText("—")

    def _on_frame(self, pixmap: QPixmap, card_detected: bool, bounds) -> None:
        scaled = pixmap.scaled(
            self._preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview.setPixmap(scaled)
        # Keep live pane in debug view in sync
        self._dbg_live.setPixmap(
            pixmap.scaled(_DBG_W, _DBG_H, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        )
        if card_detected:
            self._status_label.setText("Ready")
            self._status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: green;")
        else:
            self._status_label.setText("Adjust")
            self._status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: orange;")

    def _on_debug_frame(self, edges_px: QPixmap, contours_px: QPixmap, rejection: str) -> None:
        self._dbg_edges.setPixmap(edges_px)
        self._dbg_contours.setPixmap(contours_px)
        detected = not rejection
        self._dbg_reason.setText("✓ Card detected" if detected else rejection)
        color = "#4caf50" if detected else "#ff9800"
        self._dbg_reason.setStyleSheet(
            f"background: black; color: {color}; font-size: 15px; font-weight: bold; padding: 8px;"
        )

    def _on_capture(self, bgr: np.ndarray, bounds) -> None:
        if self._series is None:
            return
        cropped = self._detector.crop_card(bgr, bounds)
        self.navigate_to_review.emit(self._series, cropped)

    def _on_camera_error(self, msg: str) -> None:
        self._status_label.setText(f"Camera error: {msg}")
        self._status_label.setStyleSheet("font-size: 14px; font-weight: bold; color: red;")

    def _on_canny_low_changed(self, value: int) -> None:
        self._canny_low_label.setText(f"Canny low: {value}")
        self._detector.canny_low = value

    def _on_canny_high_changed(self, value: int) -> None:
        self._canny_high_label.setText(f"Canny high: {value}")
        self._detector.canny_high = value

    def _rotate_cw(self) -> None:
        self._rotation = (self._rotation + 90) % 360
        self._rot_label.setText(f"{self._rotation}°")
        self._camera.set_rotation(self._rotation)

    def _rotate_ccw(self) -> None:
        self._rotation = (self._rotation - 90) % 360
        self._rot_label.setText(f"{self._rotation}°")
        self._camera.set_rotation(self._rotation)

    def _on_done(self) -> None:
        if self._series is not None:
            self.navigate_to_thumbnail.emit(self._series)
