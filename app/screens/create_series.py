from __future__ import annotations

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import traceback

from app import backend, config, state
from app.models import Series
from app.spreadsheet.base import SpreadsheetProvider


# ── Background workers ────────────────────────────────────────────────────────

class _CreateWorker(QThread):
    succeeded = Signal(object)  # Series
    failed = Signal(str)

    def __init__(self, name: str, total_cards: int, parent=None) -> None:
        super().__init__(parent)
        self._name = name
        self._total_cards = total_cards

    def run(self) -> None:
        try:
            result = backend.create_series(self._name, self._total_cards)
            series = state.create_series(self._name, series_id=str(result["id"]), total_cards=self._total_cards)
        except Exception as e:
            print(f"[CreateWorker] error:\n{traceback.format_exc()}")
            self.failed.emit(str(e))
            return
        self.succeeded.emit(series)


class _LoadSheetsWorker(QThread):
    loaded = Signal(list)
    failed = Signal(str)

    def __init__(self, provider: SpreadsheetProvider, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider

    def run(self) -> None:
        try:
            self.loaded.emit(self._provider.get_worksheets())
        except Exception as e:
            print(f"[LoadSheetsWorker] error:\n{traceback.format_exc()}")
            self.failed.emit(str(e))


class _LoadColumnsWorker(QThread):
    loaded = Signal(list)
    failed = Signal(str)

    def __init__(self, provider: SpreadsheetProvider, sheet: str, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._sheet = sheet

    def run(self) -> None:
        try:
            self.loaded.emit(self._provider.get_columns(self._sheet))
        except Exception as e:
            print(f"[LoadColumnsWorker] error:\n{traceback.format_exc()}")
            self.failed.emit(str(e))


class _LoadSeriesNamesWorker(QThread):
    loaded = Signal(list)
    failed = Signal(str)

    def __init__(self, provider: SpreadsheetProvider, sheet: str, column: str, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._sheet = sheet
        self._column = column

    def run(self) -> None:
        try:
            self.loaded.emit(self._provider.get_unique_values(self._sheet, self._column))
        except Exception as e:
            print(f"[LoadSeriesNamesWorker] error:\n{traceback.format_exc()}")
            self.failed.emit(str(e))


# ── Screen ────────────────────────────────────────────────────────────────────

class CreateSeriesScreen(QWidget):
    navigate_to_scanning = Signal(object)  # Series

    def __init__(self, provider: SpreadsheetProvider | None = None, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._worker: QThread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("New Series")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: bold; margin-bottom: 12px;")
        layout.addWidget(title)

        if self._provider is not None:
            self._sheet_combo = QComboBox()
            self._sheet_combo.setMaximumWidth(360)
            self._sheet_combo.setPlaceholderText("Loading sheets…")
            self._sheet_combo.currentTextChanged.connect(self._on_sheet_changed)
            layout.addWidget(self._sheet_combo, alignment=Qt.AlignmentFlag.AlignCenter)

            self._col_combo = QComboBox()
            self._col_combo.setMaximumWidth(360)
            self._col_combo.setPlaceholderText("Select sheet first…")
            self._col_combo.setEnabled(False)
            self._col_combo.currentTextChanged.connect(self._on_column_changed)
            layout.addWidget(self._col_combo, alignment=Qt.AlignmentFlag.AlignCenter)

            self._series_list = QListWidget()
            self._series_list.setMaximumWidth(360)
            self._series_list.setMaximumHeight(160)
            self._series_list.itemClicked.connect(
                lambda item: self._name_edit.setText(item.text())
            )
            layout.addWidget(self._series_list, alignment=Qt.AlignmentFlag.AlignCenter)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Series name…")
        self._name_edit.setMaximumWidth(360)
        self._name_edit.returnPressed.connect(self._on_create)
        layout.addWidget(self._name_edit, alignment=Qt.AlignmentFlag.AlignCenter)

        self._total_cards_edit = QLineEdit()
        self._total_cards_edit.setPlaceholderText("Total cards amount…")
        self._total_cards_edit.setMaximumWidth(360)
        self._total_cards_edit.setValidator(QIntValidator(1, 9999, self))
        self._total_cards_edit.returnPressed.connect(self._on_create)
        layout.addWidget(self._total_cards_edit, alignment=Qt.AlignmentFlag.AlignCenter)

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
        self._total_cards_edit.clear()
        self._error_label.hide()
        self._create_btn.setText("Create Series")
        self._create_btn.setEnabled(True)
        if self._provider is not None:
            self._load_sheets()
        else:
            self._name_edit.setFocus()

    # ── Spreadsheet loading ───────────────────────────────────────────────────

    def _load_sheets(self) -> None:
        self._sheet_combo.clear()
        self._sheet_combo.setPlaceholderText("Loading sheets…")
        self._sheet_combo.setEnabled(False)
        worker = _LoadSheetsWorker(self._provider, parent=self)
        worker.loaded.connect(self._on_sheets_loaded)
        worker.failed.connect(self._on_load_error)
        self._worker = worker
        worker.start()

    def _on_sheets_loaded(self, sheets: list) -> None:
        self._sheet_combo.setEnabled(True)
        self._sheet_combo.setPlaceholderText("Select sheet…")
        self._sheet_combo.addItems(sheets)
        saved = config.EXCEL_SHEET
        if saved in sheets:
            self._sheet_combo.setCurrentText(saved)

    def _on_sheet_changed(self, sheet: str) -> None:
        if not sheet:
            return
        config.save("EXCEL_SHEET", sheet)
        self._col_combo.clear()
        self._col_combo.setEnabled(False)
        self._col_combo.setPlaceholderText("Loading columns…")
        self._series_list.clear()
        worker = _LoadColumnsWorker(self._provider, sheet, parent=self)
        worker.loaded.connect(self._on_columns_loaded)
        worker.failed.connect(self._on_load_error)
        self._worker = worker
        worker.start()

    def _on_columns_loaded(self, columns: list) -> None:
        self._col_combo.setEnabled(True)
        self._col_combo.setPlaceholderText("Select series column…")
        self._col_combo.addItems(columns)
        saved = config.EXCEL_SERIES_COLUMN
        if saved in columns:
            self._col_combo.setCurrentText(saved)

    def _on_column_changed(self, column: str) -> None:
        if not column:
            return
        config.save("EXCEL_SERIES_COLUMN", column)
        sheet = self._sheet_combo.currentText()
        if not sheet:
            return
        self._series_list.clear()
        worker = _LoadSeriesNamesWorker(self._provider, sheet, column, parent=self)
        worker.loaded.connect(self._on_series_names_loaded)
        worker.failed.connect(self._on_load_error)
        self._worker = worker
        worker.start()

    def _on_series_names_loaded(self, names: list) -> None:
        self._series_list.clear()
        self._series_list.addItems(names)

    def _on_load_error(self, msg: str) -> None:
        self._error_label.setText(f"Spreadsheet error: {msg}")
        self._error_label.show()

    # ── Create ────────────────────────────────────────────────────────────────

    def _on_create(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            self._error_label.setText("Name cannot be empty.")
            self._error_label.show()
            return
        total_str = self._total_cards_edit.text().strip()
        if not total_str:
            self._error_label.setText("Total cards amount cannot be empty.")
            self._error_label.show()
            return
        total_cards = int(total_str)
        self._error_label.hide()
        self._create_btn.setEnabled(False)
        self._create_btn.setText("Creating…")
        worker = _CreateWorker(name, total_cards, parent=self)
        worker.succeeded.connect(self.navigate_to_scanning)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _on_failed(self, msg: str) -> None:
        self._error_label.setText(msg)
        self._error_label.show()
        self._create_btn.setEnabled(True)
        self._create_btn.setText("Create Series")
