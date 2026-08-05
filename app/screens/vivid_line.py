from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import config, thumbnail
from app.models import Series
from app.screens.review import _bgr_to_pixmap

# Session-remembered default. config.save() writes .env but does not refresh
# the in-memory constants, so this variable is the live default for the rest
# of the process.
_session_default_fraction: float = config.VIVID_LINE_DEFAULT


class _LineDragLabel(QLabel):
    """Displays the view-oriented capture and lets the operator drag the
    label-safe split line over it."""

    fraction_changed = Signal(float)     # live, while dragging
    fraction_committed = Signal(float)   # on mouse release

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: #111;")
        self.setMinimumSize(160, 220)
        self._pixmap: QPixmap | None = None
        self._fraction: float = 0.15
        self._dragging = False

    def set_bgr(self, bgr: np.ndarray) -> None:
        self._pixmap = _bgr_to_pixmap(bgr)
        self.update()

    def set_fraction(self, fraction: float) -> None:
        self._fraction = max(0.0, min(1.0, fraction))
        self.update()

    def fraction(self) -> float:
        return self._fraction

    def _drawn_rect(self) -> QRect | None:
        if self._pixmap is None or self._pixmap.isNull():
            return None
        scaled_size = self._pixmap.size().scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio
        )
        x = (self.width() - scaled_size.width()) // 2
        y = (self.height() - scaled_size.height()) // 2
        return QRect(x, y, scaled_size.width(), scaled_size.height())

    def _fraction_from_event(self, event) -> float | None:
        rect = self._drawn_rect()
        if rect is None or rect.height() <= 0:
            return None
        offset_y = rect.top()
        y = event.position().y()
        return max(0.0, min(1.0, (y - offset_y) / rect.height()))

    def mousePressEvent(self, event) -> None:
        frac = self._fraction_from_event(event)
        if frac is not None:
            self._dragging = True
            self._fraction = frac
            self.update()
            self.fraction_changed.emit(self._fraction)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            frac = self._fraction_from_event(event)
            if frac is not None:
                self._fraction = frac
                self.update()
                self.fraction_changed.emit(self._fraction)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            self.fraction_committed.emit(self._fraction)
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111"))
        rect = self._drawn_rect()
        if rect is not None and self._pixmap is not None:
            scaled = self._pixmap.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(rect.topLeft(), scaled)

            split_y = rect.top() + int(rect.height() * self._fraction)
            overlay_rect = QRect(rect.left(), rect.top(), rect.width(), split_y - rect.top())
            painter.fillRect(overlay_rect, QColor(0, 0, 0, 130))

            pen = QPen(QColor("#4caf50"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawLine(rect.left(), split_y, rect.right(), split_y)
        painter.end()


class VividLineScreen(QWidget):
    navigate_to_scanning = Signal(object)  # Series (cancel/discard)
    # (Series, final_bgr UNROTATED original, name, price, rotation_deg, line_fraction)
    navigate_to_team_selection = Signal(object, object, str, str, int, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: Series | None = None
        self._final_bgr: np.ndarray | None = None
        self._name: str = ""
        self._price: str = ""
        self._rotation: int = 0
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)

        title = QLabel("Drag the line — area above stays un-boosted  [Enter: confirm, Esc: cancel]")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 15px; color: #ccc;")
        layout.addWidget(title)

        body = QHBoxLayout()
        body.setSpacing(12)

        self._drag_label = _LineDragLabel()
        self._drag_label.fraction_committed.connect(self._on_fraction_committed)
        body.addWidget(self._drag_label, stretch=1)

        preview_col = QVBoxLayout()
        preview_col.setAlignment(Qt.AlignmentFlag.AlignTop)
        preview_col.addWidget(QLabel("Thumbnail preview:"))
        self._preview_label = QLabel()
        self._preview_label.setFixedWidth(config.THUMB_BOX_W)
        self._preview_label.setMinimumHeight(config.THUMB_BOX_H)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setStyleSheet("background: #222; border: 1px solid #555;")
        preview_col.addWidget(self._preview_label)
        preview_col.addStretch()
        body.addLayout(preview_col)

        layout.addLayout(body, stretch=1)

        whole_card_btn = QPushButton("Whole card")
        whole_card_btn.setToolTip("Boost the whole card — no protected band")
        whole_card_btn.clicked.connect(self._on_whole_card)
        layout.addWidget(whole_card_btn)

        action_row = QHBoxLayout()
        confirm_btn = QPushButton("Confirm  [Enter]")
        confirm_btn.clicked.connect(self._on_confirm)
        cancel_btn = QPushButton("Cancel  [Esc]")
        cancel_btn.clicked.connect(self._on_cancel)
        action_row.addWidget(confirm_btn)
        action_row.addWidget(cancel_btn)
        layout.addLayout(action_row)

        QShortcut(QKeySequence(Qt.Key.Key_Return), self).activated.connect(self._on_confirm)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self._on_cancel)

    # ── load / data ───────────────────────────────────────────────────────────

    def load(
        self,
        series: Series,
        final_bgr: np.ndarray,
        name: str,
        price: str = "",
        rotation: int = 0,
    ) -> None:
        self._series = series
        self._final_bgr = final_bgr
        self._name = name
        self._price = price
        self._rotation = rotation

        # Rotation is display-only metadata the frontend applies later; the
        # stored image is what the thumbnail is built from, so the operator
        # drags the line on the original, unrotated pixels.
        self._drag_label.set_bgr(final_bgr)
        self._drag_label.set_fraction(_session_default_fraction)
        self._update_preview()

    def _update_preview(self) -> None:
        if self._final_bgr is None:
            return
        try:
            thumb = thumbnail.generate_thumbnail(
                self._final_bgr,
                self._drag_label.fraction(),
                config.THUMB_BOX_W,
                config.THUMB_BOX_H,
                config.VIVID_SATURATION,
                config.VIVID_FEATHER_PX,
            )
        except Exception as e:
            print(f"[VividLineScreen] preview generation failed: {e}")
            return
        pixmap = _bgr_to_pixmap(thumb)
        self._preview_label.setPixmap(
            pixmap.scaled(
                config.THUMB_BOX_W,
                config.THUMB_BOX_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _on_fraction_committed(self, _fraction: float) -> None:
        self._update_preview()

    def _on_whole_card(self) -> None:
        self._drag_label.set_fraction(0.0)
        self._update_preview()

    # ── actions ───────────────────────────────────────────────────────────────

    def _on_confirm(self) -> None:
        if self._series is None or self._final_bgr is None:
            return
        fraction = self._drag_label.fraction()
        global _session_default_fraction
        _session_default_fraction = fraction
        config.save("VIVID_LINE_DEFAULT", str(fraction))
        self.navigate_to_team_selection.emit(
            self._series, self._final_bgr, self._name, self._price, self._rotation, fraction
        )

    def _on_cancel(self) -> None:
        if self._series is not None:
            self.navigate_to_scanning.emit(self._series)
