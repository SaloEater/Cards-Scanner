from __future__ import annotations

import traceback

import cv2
import numpy as np
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.models import Series
from app.spreadsheet.base import SheetRow, SpreadsheetProvider

_OPT_W, _OPT_H = 220, 220

_CV_ROTATIONS = [
    None,
    cv2.ROTATE_90_CLOCKWISE,
    cv2.ROTATE_180,
    cv2.ROTATE_90_COUNTERCLOCKWISE,
]


def _bgr_to_pixmap(bgr: np.ndarray) -> QPixmap:
    h, w, ch = bgr.shape
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    img = QImage(rgb.data, w, h, w * ch, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(img.copy())


class RotationOption(QLabel):
    selected = Signal(int)
    approved = Signal(int)

    def __init__(self, idx: int, parent=None) -> None:
        super().__init__(parent)
        self._idx = idx
        self.setFixedSize(_OPT_W, _OPT_H)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_selected(False)

    def set_pixmap_from_bgr(self, bgr: np.ndarray) -> None:
        pixmap = _bgr_to_pixmap(bgr)
        self.setPixmap(pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def set_selected(self, sel: bool) -> None:
        border = "3px solid #4caf50" if sel else "1px solid #555"
        self.setStyleSheet(f"background: #222; border: {border};")

    def mousePressEvent(self, event) -> None:
        self.selected.emit(self._idx)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.approved.emit(self._idx)
        super().mouseDoubleClickEvent(event)


# ── Row-loading worker ────────────────────────────────────────────────────────

class _LoadRowsWorker(QThread):
    loaded = Signal(list)   # list[SheetRow]
    failed = Signal(str)

    def __init__(
        self,
        provider: SpreadsheetProvider,
        sheet: str,
        series_col: str,
        series_name: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._provider = provider
        self._sheet = sheet
        self._series_col = series_col
        self._series_name = series_name

    def run(self) -> None:
        try:
            rows = self._provider.get_rows_by_value(
                self._sheet, self._series_col, self._series_name
            )
            self.loaded.emit(rows)
        except Exception as e:
            print(f"[LoadRowsWorker] error:\n{traceback.format_exc()}")
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


# ── Screen ────────────────────────────────────────────────────────────────────

class ReviewScreen(QWidget):
    navigate_to_scanning = Signal(object)         # Series
    navigate_to_team_selection = Signal(object, object, str, str)  # (Series, final_bgr, name, price)

    def __init__(self, provider: SpreadsheetProvider | None = None, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._series: Series | None = None
        self._cropped_bgr: np.ndarray | None = None
        self._selected: int = 0
        self._rows: list[SheetRow] = []
        self._worker: QThread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setSpacing(12)
        outer.setContentsMargins(8, 8, 8, 8)

        # ── Left 2/3: image grid + action buttons ─────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setSpacing(8)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._options: list[RotationOption] = []
        grid = QGridLayout()
        grid.setSpacing(8)
        for i in range(4):
            r, c = divmod(i, 2)
            opt = RotationOption(i)
            opt.selected.connect(self._on_option_selected)
            opt.approved.connect(self._on_option_approved)
            self._options.append(opt)
            grid.addWidget(opt, r, c)
        left_layout.addLayout(grid, stretch=1)

        action_row = QHBoxLayout()
        approve_btn = QPushButton("Approve  [Enter]")
        approve_btn.clicked.connect(self._on_approve)
        retake_btn = QPushButton("Retake  [Esc]")
        retake_btn.clicked.connect(self._on_retake)
        action_row.addWidget(approve_btn)
        action_row.addWidget(retake_btn)
        left_layout.addLayout(action_row)

        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: red;")
        self._error_label.hide()
        left_layout.addWidget(self._error_label, alignment=Qt.AlignmentFlag.AlignCenter)

        outer.addWidget(left, stretch=2)

        # ── Right 1/3: name/price + spreadsheet controls ───────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setSpacing(8)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Card name (optional)…")
        self._name_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        right_layout.addWidget(self._name_edit)

        self._price_edit = QLineEdit()
        self._price_edit.setPlaceholderText("Price (optional)…")
        self._price_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        right_layout.addWidget(self._price_edit)

        if self._provider is not None:
            name_col_row = QHBoxLayout()
            name_col_row.addWidget(QLabel("Name col:"))
            self._name_col_combo = QComboBox()
            self._name_col_combo.currentTextChanged.connect(self._on_col_selection_changed)
            name_col_row.addWidget(self._name_col_combo, stretch=1)
            right_layout.addLayout(name_col_row)

            price_col_row = QHBoxLayout()
            price_col_row.addWidget(QLabel("Price col:"))
            self._price_col_combo = QComboBox()
            self._price_col_combo.currentTextChanged.connect(self._on_col_selection_changed)
            price_col_row.addWidget(self._price_col_combo, stretch=1)
            right_layout.addLayout(price_col_row)

            self._row_list = QListWidget()
            self._row_list.itemClicked.connect(self._on_row_selected)
            right_layout.addWidget(self._row_list, stretch=1)

            self._sheet_error_label = QLabel()
            self._sheet_error_label.setStyleSheet("color: orange; font-size: 12px;")
            self._sheet_error_label.setWordWrap(True)
            self._sheet_error_label.hide()
            right_layout.addWidget(self._sheet_error_label)

        outer.addWidget(right, stretch=1)

        QShortcut(QKeySequence(Qt.Key.Key_Return), self).activated.connect(self._on_approve)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self._on_retake)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self).activated.connect(lambda: self._move_selection(0, 1))
        QShortcut(QKeySequence(Qt.Key.Key_Left),  self).activated.connect(lambda: self._move_selection(0, -1))
        QShortcut(QKeySequence(Qt.Key.Key_Down),  self).activated.connect(lambda: self._move_selection(1, 0))
        QShortcut(QKeySequence(Qt.Key.Key_Up),    self).activated.connect(lambda: self._move_selection(-1, 0))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._options:
            return
        left_w = int(self.width() * 2 / 3) - 20
        cell_h = int((self.height() - 60) / 2)
        cell_w = int((left_w - 8) / 2)
        cell_size = max(80, min(cell_h, cell_w))
        for opt in self._options:
            opt.setFixedSize(cell_size, cell_size)
        self._update_previews()

    # ── load / data ───────────────────────────────────────────────────────────

    def load(self, series: Series, cropped_bgr: np.ndarray) -> None:
        self._series = series
        self._cropped_bgr = cropped_bgr
        self._selected = 0
        self._name_edit.clear()
        self._price_edit.clear()
        self._error_label.hide()
        self._rows = []
        if self._provider is not None:
            self._row_list.clear()
            self._sheet_error_label.hide()
            self._load_columns_and_rows()
        self._update_previews()
        self._update_selection()

    def _load_columns_and_rows(self) -> None:
        sheet = config.EXCEL_SHEET
        if not sheet:
            return
        worker = _LoadColumnsWorker(self._provider, sheet, parent=self)
        worker.loaded.connect(self._on_columns_loaded)
        worker.failed.connect(self._on_sheet_error)
        self._worker = worker
        worker.start()

    def _on_columns_loaded(self, columns: list) -> None:
        for combo, saved_key in (
            (self._name_col_combo, "EXCEL_NAME_COLUMN"),
            (self._price_col_combo, "EXCEL_PRICE_COLUMN"),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(columns)
            saved = getattr(config, saved_key)
            if saved in columns:
                combo.setCurrentText(saved)
            combo.blockSignals(False)
        self._fetch_rows()

    def _fetch_rows(self) -> None:
        if self._series is None or self._provider is None:
            return
        sheet = config.EXCEL_SHEET
        series_col = config.EXCEL_SERIES_COLUMN
        name_col = self._name_col_combo.currentText()
        price_col = self._price_col_combo.currentText()
        if not sheet or not series_col or not name_col or not price_col:
            return
        worker = _LoadRowsWorker(
            self._provider, sheet, series_col, self._series.series_name, parent=self
        )
        worker.loaded.connect(self._on_rows_loaded)
        worker.failed.connect(self._on_sheet_error)
        self._worker = worker
        worker.start()

    def _on_rows_loaded(self, rows: list) -> None:
        self._rows = rows
        self._row_list.clear()
        name_col = self._name_col_combo.currentText()
        price_col = self._price_col_combo.currentText()
        for row in rows:
            name_val = row.values.get(name_col, "")
            price_val = row.values.get(price_col, "")
            self._row_list.addItem(f"{name_val} — {price_val}")

    def _on_row_selected(self, item) -> None:
        idx = self._row_list.row(item)
        if idx < 0 or idx >= len(self._rows):
            return
        row = self._rows[idx]
        name_col = self._name_col_combo.currentText()
        price_col = self._price_col_combo.currentText()
        self._name_edit.setText(row.values.get(name_col, ""))
        self._price_edit.setText(row.values.get(price_col, ""))

    def _on_col_selection_changed(self, _: str) -> None:
        config.save("EXCEL_NAME_COLUMN", self._name_col_combo.currentText())
        config.save("EXCEL_PRICE_COLUMN", self._price_col_combo.currentText())
        self._fetch_rows()

    def _on_sheet_error(self, msg: str) -> None:
        self._sheet_error_label.setText(f"Spreadsheet: {msg}")
        self._sheet_error_label.show()

    # ── image / selection ─────────────────────────────────────────────────────

    def _update_previews(self) -> None:
        if self._cropped_bgr is None:
            return
        for i, rot in enumerate(_CV_ROTATIONS):
            img = cv2.rotate(self._cropped_bgr, rot) if rot is not None else self._cropped_bgr
            self._options[i].set_pixmap_from_bgr(img)

    def _update_selection(self) -> None:
        for i, opt in enumerate(self._options):
            opt.set_selected(i == self._selected)

    def _move_selection(self, dr: int, dc: int) -> None:
        row, col = divmod(self._selected, 2)
        new_idx = max(0, min(1, row + dr)) * 2 + max(0, min(1, col + dc))
        self._on_option_selected(new_idx)

    def mousePressEvent(self, event) -> None:
        self._name_edit.clearFocus()
        super().mousePressEvent(event)

    def _on_option_selected(self, idx: int) -> None:
        self._name_edit.clearFocus()
        self._selected = idx
        self._update_selection()

    def _on_option_approved(self, idx: int) -> None:
        self._selected = idx
        self._update_selection()
        self._on_approve()

    def _on_approve(self) -> None:
        if self._series is None or self._cropped_bgr is None:
            return
        rot = _CV_ROTATIONS[self._selected]
        final = cv2.rotate(self._cropped_bgr, rot) if rot is not None else self._cropped_bgr
        name = self._name_edit.text().strip()
        price = self._price_edit.text().strip()
        self.navigate_to_team_selection.emit(self._series, final, name, price)

    def _on_retake(self) -> None:
        if self._series is not None:
            self.navigate_to_scanning.emit(self._series)
