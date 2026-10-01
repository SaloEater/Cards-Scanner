from __future__ import annotations

import re
import cv2
import numpy as np
from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import config, state
from app.models import Photo, Series
from app.teams import ICONS_BASE, MARKETS, SPORTS

_ACTIVE_STYLE = "background: #4caf50; color: white; font-weight: bold;"
_INACTIVE_STYLE = ""
_ROW_H = 80  # px — drives both grid row height and icon size


def _icon_for(sport: str, team_name: str) -> QIcon | None:
    slug = re.sub(r"[^a-z0-9]+", "_", team_name.lower()).strip("_")
    for ext in ("png", "jpg", "svg", "webp"):
        p = ICONS_BASE / "images" / sport / f"{slug}.{ext}"
        if p.exists():
            return QIcon(str(p))
    for ext in ("png", "jpg", "svg", "webp"):
        p = ICONS_BASE / "images" / f"default-team.{ext}"
        if p.exists():
            return QIcon(str(p))
    return None


def _scale_bounds(b, sx: float, sy: float, max_w: int, max_h: int):
    if b is None:
        return None
    x, y, w, h = b
    x0, y0 = int(round(x * sx)), int(round(y * sy))
    x1, y1 = int(round((x + w) * sx)), int(round((y + h) * sy))
    x0, y0 = max(0, min(x0, max_w - 1)), max(0, min(y0, max_h - 1))
    x1, y1 = max(x0 + 1, min(x1, max_w)), max(y0 + 1, min(y1, max_h))
    return (x0, y0, x1 - x0, y1 - y0)


class TeamSelectionScreen(QWidget):
    navigate_to_scanning = Signal(object)  # Series

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: Series | None = None
        self._final_bgr: np.ndarray | None = None
        self._name: str = ""
        self._price: str = ""
        self._rotation: int = 0
        self._art_bounds: tuple[int, int, int, int] | None = None
        self._label_bounds: tuple[int, int, int, int] | None = None
        self._label_text: dict | None = None
        self._active_sport: str = "nfl"
        self._sport_btns: dict[str, QPushButton] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)

        title = QLabel("Select team — double-click to confirm  [Esc: discard]")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 15px; color: #ccc;")
        layout.addWidget(title)

        cancel_btn = QPushButton("Cancel  [Esc]")
        cancel_btn.clicked.connect(self._on_cancel)
        layout.addWidget(cancel_btn)

        sport_row = QHBoxLayout()
        sport_row.setSpacing(6)
        for sport in SPORTS:
            btn = QPushButton(sport.upper())
            btn.setCheckable(False)
            btn.clicked.connect(lambda checked, s=sport: self._on_sport_changed(s))
            self._sport_btns[sport] = btn
            sport_row.addWidget(btn)
        sport_row.addStretch()
        layout.addLayout(sport_row)

        self._list = QListWidget()
        self._list.setFlow(QListWidget.Flow.LeftToRight)
        self._list.setWrapping(True)
        self._list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._list.setIconSize(QSize(_ROW_H, _ROW_H))
        self._list.setStyleSheet("font-size: 21px; QListWidget::item { padding: 0px; }")
        self._list.itemDoubleClicked.connect(self._on_team_selected)
        layout.addWidget(self._list)

        self._error_label = QLabel()
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setStyleSheet("color: red; font-size: 13px;")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self._on_cancel)

        self._populate_list("nfl")

    def _on_sport_changed(self, sport: str) -> None:
        self._active_sport = sport
        self._populate_list(sport)
        QTimer.singleShot(0, self._update_grid)

    def _populate_list(self, sport: str) -> None:
        for s, btn in self._sport_btns.items():
            btn.setStyleSheet(_ACTIVE_STYLE if s == sport else _INACTIVE_STYLE)

        self._list.clear()
        visible = sorted(
            (m for m in MARKETS if m.get(sport)),
            key=lambda m: m[sport].lower(),
        )
        for market in visible:
            display_name = market[sport]
            item = QListWidgetItem(display_name)
            item.setData(Qt.ItemDataRole.UserRole, market)
            icon = _icon_for(sport, display_name)
            if icon:
                item.setIcon(icon)
            self._list.addItem(item)

    def load(
        self,
        series: Series,
        final_bgr: np.ndarray,
        name: str,
        price: str = "",
        rotation: int = 0,
        art_bounds: tuple[int, int, int, int] | None = None,
        label_bounds: tuple[int, int, int, int] | None = None,
        label_text: dict | None = None,
    ) -> None:
        self._series = series
        self._final_bgr = final_bgr
        self._name = name
        self._price = price
        self._rotation = rotation
        self._art_bounds = art_bounds
        self._label_bounds = label_bounds
        self._label_text = label_text
        self._error_label.hide()
        self._list.clearSelection()

    def _on_team_selected(self, item: QListWidgetItem) -> None:
        if self._series is None or self._final_bgr is None:
            return
        market = item.data(Qt.ItemDataRole.UserRole)
        team = market["nfl"]
        index = self._series.next_photo_seq()
        series_dir = state.ensure_series_dir(self._series.series_id)
        img = self._final_bgr
        h, w = img.shape[:2]
        scale = min(config.CARD_OUTPUT_W / w, config.CARD_OUTPUT_H / h)
        art_bounds, label_bounds = self._art_bounds, self._label_bounds
        if scale < 1.0:
            nw, nh = int(w * scale), int(h * scale)
            img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
            # Bounds must stay in the stored file's pixel space.
            art_bounds = _scale_bounds(art_bounds, nw / w, nh / h, nw, nh)
            label_bounds = _scale_bounds(label_bounds, nw / w, nh / h, nw, nh)
        if config.RAW:
            filename = f"c{index}.png"
            ok = cv2.imwrite(str(series_dir / filename), img)
        else:
            filename = f"c{index}.jpg"
            ok = cv2.imwrite(str(series_dir / filename), img, [cv2.IMWRITE_JPEG_QUALITY, config.JPEG_QUALITY])
        if not ok:
            self._error_label.setText(f"Failed to save {series_dir / filename}")
            self._error_label.show()
            return
        self._series.photos.append(
            Photo(
                index=index,
                filename=filename,
                name=self._name,
                team=team,
                price=self._price,
                rotation=self._rotation,
                art_bounds=art_bounds,
                label_bounds=label_bounds,
                label_text=self._label_text,
            )
        )
        state.save_series(self._series)
        self.navigate_to_scanning.emit(self._series)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_grid)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._update_grid)

    def _update_grid(self) -> None:
        vp_w = self._list.viewport().width()
        if vp_w <= 0:
            return
        fm = self._list.fontMetrics()
        max_text_w = max(
            (fm.horizontalAdvance(self._list.item(i).text()) for i in range(self._list.count())),
            default=0,
        )
        needed_w = _ROW_H + 8 + max_text_w  # icon + gap + text
        self._list.setGridSize(QSize(min(vp_w // 3, needed_w), _ROW_H))

    def _on_cancel(self) -> None:
        if self._series is not None:
            self.navigate_to_scanning.emit(self._series)
