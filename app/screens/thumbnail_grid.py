from __future__ import annotations

from pathlib import Path

import cv2
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app import config, state
from app.models import Series

_THUMB_W, _THUMB_H = 150, 210
_COLUMNS = 4


def _load_thumb(path: Path) -> QPixmap | None:
    bgr = cv2.imread(str(path))
    if bgr is None:
        return None
    h, w, ch = bgr.shape
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    img = QImage(rgb.data, w, h, w * ch, QImage.Format.Format_RGB888)
    pixmap = QPixmap.fromImage(img.copy())
    return pixmap.scaled(_THUMB_W, _THUMB_H, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


class ThumbnailCard(QWidget):
    remove_requested = Signal()

    def __init__(self, pixmap: QPixmap | None, uploaded: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(_THUMB_W, _THUMB_H)

        img = QLabel(self)
        img.setFixedSize(_THUMB_W, _THUMB_H)
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img.setStyleSheet("background: #333;")
        if pixmap:
            img.setPixmap(pixmap)
        else:
            img.setText("?")

        btn = QPushButton("✕", self)
        btn.setFixedSize(22, 22)
        btn.move(_THUMB_W - 26, 4)
        btn.setStyleSheet(
            "QPushButton { background: #e53935; color: white; border: none; "
            "border-radius: 11px; font-weight: bold; font-size: 12px; }"
            "QPushButton:hover { background: #b71c1c; }"
        )
        btn.clicked.connect(self.remove_requested)

        if uploaded:
            badge = QLabel("✓", self)
            badge.setFixedSize(22, 22)
            badge.move(4, _THUMB_H - 26)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                "background: #4caf50; color: white; border-radius: 11px;"
                "font-weight: bold; font-size: 12px;"
            )


class ThumbnailGridScreen(QWidget):
    navigate_to_scanning = Signal(object)  # Series
    navigate_to_upload = Signal(object)    # Series
    navigate_to_launch = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: Series | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Header
        header = QHBoxLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Series name…")
        self._name_edit.editingFinished.connect(self._on_name_edited)
        header.addWidget(self._name_edit)
        self._count_label = QLabel("0 cards")
        header.addWidget(self._count_label)
        root.addLayout(header)

        # Body: grid on left, buttons on right
        body = QHBoxLayout()
        body.setSpacing(12)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._grid_widget = QWidget()
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setSpacing(6)
        self._scroll.setWidget(self._grid_widget)
        body.addWidget(self._scroll)

        sidebar = QVBoxLayout()
        sidebar.setAlignment(Qt.AlignmentFlag.AlignTop)
        sidebar.setSpacing(8)

        back_btn = QPushButton("Back  [Esc]")
        back_btn.clicked.connect(self.navigate_to_launch)
        sidebar.addWidget(back_btn)

        self._add_more_btn = QPushButton("Add More")
        self._add_more_btn.clicked.connect(self._on_add_more)
        sidebar.addWidget(self._add_more_btn)

        self._send_btn = QPushButton("Send")
        self._send_btn.clicked.connect(self._on_send)
        sidebar.addWidget(self._send_btn)

        self._status_label = QLabel()
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("color: green;")
        self._status_label.setWordWrap(True)
        self._status_label.hide()
        sidebar.addWidget(self._status_label)

        body.addLayout(sidebar)
        root.addLayout(body)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self.navigate_to_launch)

    def load(self, series: Series) -> None:
        self._series = series
        self._name_edit.setText(series.series_name)
        self._count_label.setText(f"{len(series.photos)} card{'s' if len(series.photos) != 1 else ''}")
        if series.status == "uploaded":
            self._status_label.setText("Uploaded")
            self._status_label.show()
        else:
            self._status_label.hide()
        self._repopulate_grid()

    def _repopulate_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self._series is None:
            return

        for i, photo in enumerate(self._series.photos):
            row, col = divmod(i, _COLUMNS)
            img_path = config.DATA_DIR / self._series.series_id / photo.filename
            card = ThumbnailCard(pixmap=_load_thumb(img_path), uploaded=photo.uploaded)
            card.remove_requested.connect(lambda checked=False, idx=i: self._remove_photo(idx))
            self._grid.addWidget(card, row, col)

    def _remove_photo(self, photo_index: int) -> None:
        if self._series is None:
            return
        photo = self._series.photos[photo_index]
        file_path = config.DATA_DIR / self._series.series_id / photo.filename
        file_path.unlink(missing_ok=True)

        self._series.photos.pop(photo_index)

        series_dir = config.DATA_DIR / self._series.series_id
        for new_idx, p in enumerate(self._series.photos):
            if p.index != new_idx:
                old_path = series_dir / p.filename
                new_filename = f"{new_idx}.jpg"
                if old_path.exists():
                    old_path.rename(series_dir / new_filename)
                p.index = new_idx
                p.filename = new_filename

        state.save_series(self._series)
        count = len(self._series.photos)
        self._count_label.setText(f"{count} card{'s' if count != 1 else ''}")
        self._repopulate_grid()

    def _on_name_edited(self) -> None:
        if self._series is None:
            return
        name = self._name_edit.text().strip()
        if name:
            self._series.series_name = name
            state.save_series(self._series)

    def _on_add_more(self) -> None:
        if self._series is not None:
            if self._series.status in ("uploaded", "uploading"):
                self._series.status = "pending"
                state.save_series(self._series)
                self._status_label.hide()
            self.navigate_to_scanning.emit(self._series)

    def _on_send(self) -> None:
        if self._series is None:
            return
        self._series.status = "uploading"
        state.save_series(self._series)
        self.navigate_to_upload.emit(self._series)
