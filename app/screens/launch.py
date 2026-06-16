from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import state
from app.models import Series


class LaunchScreen(QWidget):
    navigate_to_create = Signal()
    navigate_to_thumbnail = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series_map: dict[int, Series] = {}
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Card Scanner")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 28px; font-weight: bold; margin-bottom: 16px;")
        layout.addWidget(title)

        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(lambda _: self._on_resume())
        layout.addWidget(self._list)

        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("color: gray;")
        layout.addWidget(self._status_label)

        btn_row = QHBoxLayout()
        self._resume_btn = QPushButton("Resume")
        self._resume_btn.setEnabled(False)
        self._resume_btn.clicked.connect(self._on_resume)
        btn_row.addWidget(self._resume_btn)

        new_btn = QPushButton("Start New")
        new_btn.clicked.connect(self.navigate_to_create)
        btn_row.addWidget(new_btn)
        layout.addLayout(btn_row)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._populate()

    def _populate(self) -> None:
        all_series = state.list_series()
        self._series_map.clear()
        self._list.clear()

        if not all_series:
            self._list.hide()
            self._status_label.setText("No series found.")
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.navigate_to_create)
            return

        self._list.show()
        self._status_label.setText("Select a series to view, or start a new one.")
        for i, series in enumerate(all_series):
            count = len(series.photos)
            text = f"{series.series_name}  —  {count} card{'s' if count != 1 else ''}  [{series.status}]"
            item = QListWidgetItem(text)
            if series.status == "uploaded":
                item.setForeground(QColor("#5a9e6f"))
            elif series.status == "uploading":
                item.setForeground(QColor("#5b8cba"))
            self._list.addItem(item)
            self._series_map[i] = series

    def _on_selection_changed(self, row: int) -> None:
        self._resume_btn.setEnabled(row >= 0)

    def _on_resume(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self.navigate_to_thumbnail.emit(self._series_map[row])
