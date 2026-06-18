from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import backend, config, state
from app.models import Series


class UploadWorker(QThread):
    progress = Signal(int, int)    # (current_index, total)
    photo_done = Signal(int)       # photo.index
    all_done = Signal()
    upload_error = Signal(str)

    def __init__(self, series: Series, parent=None) -> None:
        super().__init__(parent)
        self._series = series

    def run(self) -> None:
        photos_to_send = [p for p in self._series.photos if not p.uploaded]
        total = len(photos_to_send)
        for i, photo in enumerate(photos_to_send):
            self.progress.emit(i, total)
            path = config.DATA_DIR / self._series.series_id / photo.filename
            try:
                backend.upload_photo(self._series.series_id, path, photo.name, photo.team, photo.price)
            except Exception as e:
                self.upload_error.emit(str(e))
                return
            photo.uploaded = True
            state.save_series(self._series)
            self.photo_done.emit(photo.index)
        try:
            backend.close_series(self._series.series_id)
        except Exception as e:
            self.upload_error.emit(str(e))
            return
        self.all_done.emit()


class UploadProgressScreen(QWidget):
    navigate_to_launch = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: Series | None = None
        self._worker: UploadWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        self._status_label = QLabel("Preparing upload…")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("font-size: 16px;")
        layout.addWidget(self._status_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimumWidth(400)
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar, alignment=Qt.AlignmentFlag.AlignCenter)

        self._error_label = QLabel()
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setStyleSheet("color: red; font-size: 13px;")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        layout.addWidget(self._error_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._action_btn = QPushButton()
        self._action_btn.setMaximumWidth(200)
        self._action_btn.hide()
        btn_row.addWidget(self._action_btn)

        self._cancel_btn = QPushButton("Back to Start")
        self._cancel_btn.setMaximumWidth(200)
        self._cancel_btn.hide()
        self._cancel_btn.clicked.connect(self.navigate_to_launch)
        btn_row.addWidget(self._cancel_btn)

        layout.addLayout(btn_row)

    def load(self, series: Series) -> None:
        self._series = series
        self._error_label.hide()
        self._action_btn.hide()
        self._progress_bar.show()

        total = len([p for p in series.photos if not p.uploaded])
        self._progress_bar.setRange(0, max(total, 1))
        self._progress_bar.setValue(0)
        self._status_label.setText(f"Uploading 0 / {total}")

        self._worker = UploadWorker(series)
        self._worker.progress.connect(self._on_progress)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.upload_error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, current: int, total: int) -> None:
        self._progress_bar.setValue(current)
        self._status_label.setText(f"Uploading {current} / {total}")

    def _on_all_done(self) -> None:
        if self._series is not None:
            self._series.status = "uploaded"
            state.save_series(self._series)
        self._progress_bar.hide()
        self._status_label.setText("Upload complete")
        self._status_label.setStyleSheet("font-size: 16px; color: green; font-weight: bold;")
        self._action_btn.setText("Done")
        try:
            self._action_btn.clicked.disconnect()
        except RuntimeError:
            pass
        self._action_btn.clicked.connect(self._on_done)
        self._action_btn.show()
        self._cancel_btn.hide()

    def _on_error(self, msg: str) -> None:
        self._error_label.setText(f"Upload failed: {msg}")
        self._error_label.show()
        self._action_btn.setText("Retry")
        try:
            self._action_btn.clicked.disconnect()
        except RuntimeError:
            pass
        self._action_btn.clicked.connect(self._on_retry)
        self._action_btn.show()
        self._cancel_btn.show()

    def _on_retry(self) -> None:
        if self._series is not None:
            self._error_label.hide()
            self._action_btn.hide()
            self._cancel_btn.hide()
            self._status_label.setStyleSheet("font-size: 16px;")
            self.load(self._series)

    def _on_done(self) -> None:
        self._status_label.setStyleSheet("font-size: 16px;")
        self.navigate_to_launch.emit()
