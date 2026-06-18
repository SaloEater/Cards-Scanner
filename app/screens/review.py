from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models import Series

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


class ReviewScreen(QWidget):
    navigate_to_scanning = Signal(object)         # Series
    navigate_to_team_selection = Signal(object, object, str, str)  # (Series, final_bgr, name, price)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: Series | None = None
        self._cropped_bgr: np.ndarray | None = None
        self._selected: int = 0
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        self._options: list[RotationOption] = []
        grid = QGridLayout()
        grid.setSpacing(8)
        for i in range(4):
            row, col = divmod(i, 2)
            opt = RotationOption(i)
            opt.selected.connect(self._on_option_selected)
            opt.approved.connect(self._on_option_approved)
            self._options.append(opt)
            grid.addWidget(opt, row, col)
        layout.addLayout(grid)

        inputs_row = QHBoxLayout()
        inputs_row.setSpacing(8)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Card name (optional)…")
        self._name_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        inputs_row.addWidget(self._name_edit)

        self._price_edit = QLineEdit()
        self._price_edit.setPlaceholderText("Price (optional)…")
        self._price_edit.setMaximumWidth(140)
        self._price_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        inputs_row.addWidget(self._price_edit)

        layout.addLayout(inputs_row)

        action_row = QHBoxLayout()
        approve_btn = QPushButton("Approve  [Enter]")
        approve_btn.clicked.connect(self._on_approve)
        retake_btn = QPushButton("Retake  [Esc]")
        retake_btn.clicked.connect(self._on_retake)
        action_row.addWidget(approve_btn)
        action_row.addWidget(retake_btn)
        layout.addLayout(action_row)

        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: red;")
        self._error_label.hide()
        layout.addWidget(self._error_label, alignment=Qt.AlignmentFlag.AlignCenter)

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
        # Two rows of cells should fill 80% of window height; keep cells square
        cell_h = int((self.height() * 0.80 - 8) / 2)
        cell_w = int((self.width() - 8) / 2)
        cell_size = max(80, min(cell_h, cell_w))
        for opt in self._options:
            opt.setFixedSize(cell_size, cell_size)
        self._update_previews()

    def load(self, series: Series, cropped_bgr: np.ndarray) -> None:
        self._series = series
        self._cropped_bgr = cropped_bgr
        self._selected = 0
        self._name_edit.clear()
        self._price_edit.clear()
        self._error_label.hide()
        self._update_previews()
        self._update_selection()

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
