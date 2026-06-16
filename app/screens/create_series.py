from __future__ import annotations

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import backend, state
from app.models import Series


class _CreateWorker(QThread):
    succeeded = Signal(object)  # Series
    failed = Signal(str)

    def __init__(self, name: str, parent=None) -> None:
        super().__init__(parent)
        self._name = name

    def run(self) -> None:
        try:
            result = backend.create_series(self._name)
            series = state.create_series(self._name, series_id=str(result["id"]))
        except Exception as e:
            self.failed.emit(str(e))
            return
        self.succeeded.emit(series)


class CreateSeriesScreen(QWidget):
    navigate_to_scanning = Signal(object)  # Series

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: _CreateWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("New Series")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: bold; margin-bottom: 12px;")
        layout.addWidget(title)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Series name…")
        self._name_edit.setMaximumWidth(360)
        self._name_edit.returnPressed.connect(self._on_create)
        layout.addWidget(self._name_edit, alignment=Qt.AlignmentFlag.AlignCenter)

        self._create_btn = QPushButton("Create Series")
        self._create_btn.setMaximumWidth(200)
        self._create_btn.clicked.connect(self._on_create)
        layout.addWidget(self._create_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: red;")
        self._error_label.hide()
        layout.addWidget(self._error_label, alignment=Qt.AlignmentFlag.AlignCenter)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._name_edit.clear()
        self._error_label.hide()
        self._create_btn.setText("Create Series")
        self._create_btn.setEnabled(True)
        self._name_edit.setFocus()

    def _on_create(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            self._error_label.setText("Name cannot be empty.")
            self._error_label.show()
            return
        self._error_label.hide()
        self._create_btn.setEnabled(False)
        self._create_btn.setText("Creating…")
        self._worker = _CreateWorker(name, parent=self)
        self._worker.succeeded.connect(self.navigate_to_scanning)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_failed(self, msg: str) -> None:
        self._error_label.setText(msg)
        self._error_label.show()
        self._create_btn.setEnabled(True)
        self._create_btn.setText("Create Series")
