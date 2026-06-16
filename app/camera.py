from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QMutex, QThread, Signal
from PySide6.QtGui import QImage, QPixmap

from app import config
from app.detector import CardDetector


def _bgr_to_pixmap(bgr: np.ndarray) -> QPixmap:
    h, w, ch = bgr.shape
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    img = QImage(rgb.data, w, h, w * ch, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(img.copy())


class CameraWorker(QThread):
    frame_ready = Signal(QPixmap, bool, object)   # (pixmap, card_detected, bounds)
    capture_ready = Signal(object, object)         # (raw_bgr, bounds)
    camera_error = Signal(str)
    debug_ready = Signal(QPixmap, QPixmap, str)   # (edges_pixmap, contours_pixmap, rejection)

    def __init__(self, detector: CardDetector, parent=None) -> None:
        super().__init__(parent)
        self._detector = detector
        self._camera_index: int = config.CAMERA_INDEX
        self._running = False
        self._paused = False
        self._capture_requested = False
        self._debug_mode = False
        self._mutex = QMutex()

    def run(self) -> None:
        self._running = True
        cap = cv2.VideoCapture(self._camera_index)
        if not cap.isOpened():
            self._running = False
            self.camera_error.emit("Cannot open camera")
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)

        try:
            while self._running:
                if self._paused:
                    self.msleep(50)
                    continue

                ret, bgr = cap.read()
                if not ret:
                    self.camera_error.emit("Frame read failed")
                    break

                bounds = self._detector.detect(bgr)
                display = self._detector.draw_overlay(bgr.copy(), bounds)
                pixmap = _bgr_to_pixmap(display)
                self.frame_ready.emit(pixmap, bounds is not None, bounds)

                self._mutex.lock()
                should_capture = self._capture_requested
                if should_capture:
                    self._capture_requested = False
                debug = self._debug_mode
                self._mutex.unlock()

                if should_capture:
                    self.capture_ready.emit(bgr, bounds)

                if debug:
                    _, edges_bgr, contours_bgr, rejection = self._detector.detect_debug(bgr)
                    dw, dh = 300, 225
                    fh, fw = edges_bgr.shape[:2]
                    scale = min(dw / fw, dh / fh)
                    scaled_size = (int(fw * scale), int(fh * scale))
                    edges_px = _bgr_to_pixmap(cv2.resize(edges_bgr, scaled_size))
                    contours_px = _bgr_to_pixmap(cv2.resize(contours_bgr, scaled_size))
                    self.debug_ready.emit(edges_px, contours_px, rejection)
        finally:
            cap.release()

    def set_debug(self, enabled: bool) -> None:
        self._mutex.lock()
        self._debug_mode = enabled
        self._mutex.unlock()

    def request_capture(self) -> None:
        self._mutex.lock()
        self._capture_requested = True
        self._mutex.unlock()

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def switch_camera(self, index: int) -> None:
        if self.isRunning():
            self.stop()
        self._camera_index = index
        self._paused = False
        self.start()

    def stop(self) -> None:
        self._running = False
        self.wait()
