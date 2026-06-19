from __future__ import annotations

import sys

import numpy as np
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget

from app.camera import CameraWorker
from app.detector import CardDetector
from app.models import Series
from app.screens.create_series import CreateSeriesScreen
from app.screens.launch import LaunchScreen
from app.screens.review import ReviewScreen
from app.screens.scanning import ScanningScreen
from app.screens.team_selection import TeamSelectionScreen
from app.screens.thumbnail_grid import ThumbnailGridScreen
from app.screens.upload_progress import UploadProgressScreen
from app.spreadsheet import get_provider

LAUNCH_IDX = 0
CREATE_IDX = 1
SCANNING_IDX = 2
REVIEW_IDX = 3
TEAM_IDX = 4
THUMBNAIL_IDX = 5
UPLOAD_IDX = 6


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Card Scanner")
        self.setMinimumSize(900, 700)

        self._current_series: Series | None = None

        detector = CardDetector()
        self._camera = CameraWorker(detector=detector)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        provider = get_provider()

        self._launch = LaunchScreen()
        self._create = CreateSeriesScreen(provider=provider)
        self._scanning = ScanningScreen(camera_worker=self._camera, detector=detector)
        self._review = ReviewScreen(provider=provider)
        self._team_selection = TeamSelectionScreen()
        self._thumbnail = ThumbnailGridScreen()
        self._upload_progress = UploadProgressScreen()

        self._stack.addWidget(self._launch)           # 0
        self._stack.addWidget(self._create)           # 1
        self._stack.addWidget(self._scanning)         # 2
        self._stack.addWidget(self._review)           # 3
        self._stack.addWidget(self._team_selection)   # 4
        self._stack.addWidget(self._thumbnail)        # 5
        self._stack.addWidget(self._upload_progress)  # 6

        self._wire_signals()

    def _wire_signals(self) -> None:
        self._launch.navigate_to_create.connect(
            lambda: self._stack.setCurrentIndex(CREATE_IDX)
        )
        self._launch.navigate_to_thumbnail.connect(self._on_resume)

        self._create.navigate_to_scanning.connect(self._on_series_created)

        self._scanning.navigate_to_review.connect(self._on_capture)
        self._scanning.navigate_to_thumbnail.connect(self._on_done_scanning)

        self._review.navigate_to_scanning.connect(self._on_approved_or_retaken)
        self._review.navigate_to_team_selection.connect(self._on_to_team_selection)

        self._team_selection.navigate_to_scanning.connect(self._on_approved_or_retaken)

        self._thumbnail.navigate_to_scanning.connect(self._on_add_more)
        self._thumbnail.navigate_to_upload.connect(self._on_send)
        self._thumbnail.navigate_to_launch.connect(
            lambda: self._stack.setCurrentIndex(LAUNCH_IDX)
        )

        self._upload_progress.navigate_to_launch.connect(
            lambda: self._stack.setCurrentIndex(LAUNCH_IDX)
        )

    # ── Navigation handlers ──────────────────────────────────────────────────

    def _on_resume(self, series: Series) -> None:
        self._current_series = series
        self._thumbnail.load(series)
        self._stack.setCurrentIndex(THUMBNAIL_IDX)

    def _on_series_created(self, series: Series) -> None:
        self._current_series = series
        self._scanning.load(series)
        self._stack.setCurrentIndex(SCANNING_IDX)

    def _on_capture(self, series: Series, cropped_bgr: np.ndarray) -> None:
        self._current_series = series
        self._review.load(series, cropped_bgr)
        self._stack.setCurrentIndex(REVIEW_IDX)

    def _on_to_team_selection(self, series: Series, final_bgr: np.ndarray, name: str, price: str) -> None:
        self._current_series = series
        self._team_selection.load(series, final_bgr, name, price)
        self._stack.setCurrentIndex(TEAM_IDX)

    def _on_approved_or_retaken(self, series: Series) -> None:
        self._current_series = series
        self._scanning.load(series)
        self._stack.setCurrentIndex(SCANNING_IDX)

    def _on_done_scanning(self, series: Series) -> None:
        self._current_series = series
        self._thumbnail.load(series)
        self._stack.setCurrentIndex(THUMBNAIL_IDX)

    def _on_add_more(self, series: Series) -> None:
        self._current_series = series
        self._scanning.load(series)
        self._stack.setCurrentIndex(SCANNING_IDX)

    def _on_send(self, series: Series) -> None:
        self._current_series = series
        self._upload_progress.load(series)
        self._stack.setCurrentIndex(UPLOAD_IDX)

    def closeEvent(self, event) -> None:
        self._camera.stop()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Card Scanner")
    window = MainWindow()
    app.aboutToQuit.connect(window._camera.stop)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
