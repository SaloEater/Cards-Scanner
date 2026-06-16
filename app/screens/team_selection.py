from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import config, state
from app.models import Photo, Series
from app.teams import TEAMS


class TeamSelectionScreen(QWidget):
    navigate_to_scanning = Signal(object)  # Series

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: Series | None = None
        self._final_bgr: np.ndarray | None = None
        self._name: str = ""
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)

        title = QLabel("Select team — double-click to confirm  [Esc: discard]")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 15px; color: #ccc;")
        layout.addWidget(title)

        self._list = QListWidget()
        self._list.setFlow(QListWidget.Flow.LeftToRight)
        self._list.setWrapping(True)
        self._list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._list.setGridSize(QSize(260, 34))
        self._list.setUniformItemSizes(True)
        self._list.setStyleSheet("font-size: 14px;")
        for team in TEAMS:
            self._list.addItem(QListWidgetItem(team))
        self._list.itemDoubleClicked.connect(self._on_team_selected)
        layout.addWidget(self._list)

        self._error_label = QLabel()
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setStyleSheet("color: red; font-size: 13px;")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self._on_cancel)

    def load(self, series: Series, final_bgr: np.ndarray, name: str) -> None:
        self._series = series
        self._final_bgr = final_bgr
        self._name = name
        self._error_label.hide()
        self._list.clearSelection()

    def _on_team_selected(self, item: QListWidgetItem) -> None:
        if self._series is None or self._final_bgr is None:
            return
        team = item.text()
        index = len(self._series.photos)
        series_dir = state.ensure_series_dir(self._series.series_id)
        filename = f"{index}.jpg"
        ok = cv2.imwrite(
            str(series_dir / filename), self._final_bgr,
            [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY],
        )
        if not ok:
            self._error_label.setText(f"Failed to save {series_dir / filename}")
            self._error_label.show()
            return
        self._series.photos.append(
            Photo(index=index, filename=filename, name=self._name, team=team)
        )
        state.save_series(self._series)
        self.navigate_to_scanning.emit(self._series)

    def _on_cancel(self) -> None:
        if self._series is not None:
            self.navigate_to_scanning.emit(self._series)
