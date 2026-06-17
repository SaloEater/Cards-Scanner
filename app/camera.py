from __future__ import annotations

import platform
import cv2
import numpy as np
from PySide6.QtCore import QMutex, QThread, Signal
from PySide6.QtGui import QImage, QPixmap

from app import config
from app.detector import CardDetector

_IS_WINDOWS = platform.system() == "Windows"


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

    _CV_ROTATIONS = {
        90:  cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }

    def __init__(self, detector: CardDetector, parent=None) -> None:
        super().__init__(parent)
        self._detector = detector
        self._camera_index: int = config.CAMERA_INDEX
        self._rotation: int = config.CAMERA_ROTATION  # 0 / 90 / 180 / 270
        self._running = False
        self._paused = False
        self._capture_requested = False
        self._debug_mode = False
        self._mutex = QMutex()

    def run(self) -> None:
        self._running = True
        backend = cv2.CAP_DSHOW if _IS_WINDOWS else cv2.CAP_ANY
        cap = cv2.VideoCapture(self._camera_index, backend)
        if not cap.isOpened():
            self._running = False
            self.camera_error.emit("Cannot open camera")
            return

        if _IS_WINDOWS:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, 30)
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[camera] requested {config.CAMERA_WIDTH}×{config.CAMERA_HEIGHT}, got {actual_w}×{actual_h}")

        try:
            while self._running:
                if self._paused:
                    self.msleep(50)
                    continue

                ret, bgr = cap.read()
                if not ret:
                    self.camera_error.emit("Frame read failed")
                    break

                self._mutex.lock()
                rotation = self._rotation
                self._mutex.unlock()
                if rotation in self._CV_ROTATIONS:
                    bgr = cv2.rotate(bgr, self._CV_ROTATIONS[rotation])

                bounds = self._detector.detect(bgr)
                display = self._detector.draw_overlay(bgr.copy(), bounds)
                ph, pw = display.shape[:2]
                if bounds is not None:
                    lx, ly = int(bounds[0][0]), int(bounds[0][1])
                else:
                    lx, ly = pw // 2, ph // 2
                cv2.line(display, (0, ly), (pw, ly), (0, 0, 255), 1)
                cv2.line(display, (lx, 0), (lx, ph), (0, 0, 255), 1)
                scale = config.PREVIEW_WIDTH / pw
                preview = cv2.resize(
                    display,
                    (config.PREVIEW_WIDTH, int(ph * scale)),
                    interpolation=cv2.INTER_AREA,
                )
                pixmap = _bgr_to_pixmap(preview)
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
                    dw, dh = 960, 720
                    fh, fw = edges_bgr.shape[:2]
                    scale = min(dw / fw, dh / fh)
                    scaled_size = (int(fw * scale), int(fh * scale))
                    edges_px = _bgr_to_pixmap(cv2.resize(edges_bgr, scaled_size))
                    contours_px = _bgr_to_pixmap(cv2.resize(contours_bgr, scaled_size))
                    self.debug_ready.emit(edges_px, contours_px, rejection)
        finally:
            cap.release()

    def set_rotation(self, degrees: int) -> None:
        self._mutex.lock()
        self._rotation = degrees % 360
        self._mutex.unlock()

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
