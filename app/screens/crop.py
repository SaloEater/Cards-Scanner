from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontDatabase, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.crop_detector import detect_art, detect_label
from app.label_ocr import OcrResult, OcrSettings, ocr_label_debug, preview_bitmap
from app.models import Series

Rect = tuple[int, int, int, int]

_MIN_SIZE = 20          # image px
_HANDLE_PX = 9          # view px, drawn size
_HIT_PX = 12            # view px, pick radius
_ART_COLOR = QColor("#2f80ff")
_LABEL_COLOR = QColor("#ff3b30")
# (dx, dy): -1 = moves the left/top edge, 1 = right/bottom edge, 0 = that axis untouched
_HANDLES = [(-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0)]


def _bgr_to_pixmap(bgr: np.ndarray) -> QPixmap:
    h, w, ch = bgr.shape
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    img = QImage(rgb.data, w, h, w * ch, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(img.copy())


class _CropCanvas(QWidget):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._iw = 0
        self._ih = 0
        self.art: Rect = (0, 0, 1, 1)
        self.label: Rect = (0, 0, 1, 1)
        self.label_visible = True
        self.guide_pct: float = 2.0  # OCR border inset, % of the label width, same pixels on all sides
        self._drag: tuple[str, int, tuple[float, float], Rect] | None = None  # (which, handle|-1, start, rect)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setToolTip("Drag handles/rectangles to edit. Click to snap the nearest art corner.")
        self._press: QPointF | None = None  # view pos of a left press that may still be a click
        self.setStyleSheet("background: black;")

    def set_image(self, bgr: np.ndarray) -> None:
        self._pixmap = _bgr_to_pixmap(bgr)
        self._ih, self._iw = bgr.shape[:2]
        self.update()

    # ── coordinate mapping ────────────────────────────────────────────────

    def _scale_origin(self) -> tuple[float, float, float]:
        if self._iw == 0:
            return 1.0, 0.0, 0.0
        s = min(self.width() / self._iw, self.height() / self._ih)
        return s, (self.width() - self._iw * s) / 2, (self.height() - self._ih * s) / 2

    def _to_view(self, x: float, y: float) -> QPointF:
        s, ox, oy = self._scale_origin()
        return QPointF(ox + x * s, oy + y * s)

    def _to_image(self, p: QPointF) -> tuple[float, float]:
        s, ox, oy = self._scale_origin()
        return (p.x() - ox) / s, (p.y() - oy) / s

    def _view_rect(self, r: Rect) -> QRectF:
        a = self._to_view(r[0], r[1])
        b = self._to_view(r[0] + r[2], r[1] + r[3])
        return QRectF(a, b)

    def _handle_points(self, r: Rect) -> list[QPointF]:
        vr = self._view_rect(r)
        cx, cy = vr.center().x(), vr.center().y()
        xs = {-1: vr.left(), 0: cx, 1: vr.right()}
        ys = {-1: vr.top(), 0: cy, 1: vr.bottom()}
        return [QPointF(xs[dx], ys[dy]) for dx, dy in _HANDLES]

    # ── painting ──────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("black"))
        if self._pixmap is None:
            return
        s, ox, oy = self._scale_origin()
        p.drawPixmap(QRectF(ox, oy, self._iw * s, self._ih * s), self._pixmap, QRectF(self._pixmap.rect()))
        for r, color, visible in ((self.art, _ART_COLOR, True), (self.label, _LABEL_COLOR, self.label_visible)):
            if not visible:
                continue
            p.setPen(QPen(color, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self._view_rect(r))
            p.setBrush(color)
            p.setPen(QPen(QColor("white"), 1))
            for pt in self._handle_points(r):
                p.drawRect(QRectF(pt.x() - _HANDLE_PX / 2, pt.y() - _HANDLE_PX / 2, _HANDLE_PX, _HANDLE_PX))
        if self.label_visible:  # red band = the border the OCR shaves off; inner edge = OCR input boundary
            lx, ly, lw, lh = self.label
            g = min(int(lw * self.guide_pct / 100), (lw - 1) // 2, (lh - 1) // 2)
            outer = self._view_rect(self.label)
            inner = self._view_rect((lx + g, ly + g, max(1, lw - 2 * g), max(1, lh - 2 * g)))
            band = QPainterPath()
            band.setFillRule(Qt.FillRule.OddEvenFill)
            band.addRect(outer)
            band.addRect(inner)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(226, 35, 26, 77))
            p.drawPath(band)
            p.setPen(QPen(_LABEL_COLOR, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(inner)

    # ── interaction ───────────────────────────────────────────────────────

    def _hit(self, pos: QPointF) -> tuple[str, int] | None:
        order = (["label"] if self.label_visible else []) + ["art"]
        for which in order:
            r = self.label if which == "label" else self.art
            for i, pt in enumerate(self._handle_points(r)):
                if abs(pt.x() - pos.x()) <= _HIT_PX and abs(pt.y() - pos.y()) <= _HIT_PX:
                    return which, i
        for which in order:
            r = self.label if which == "label" else self.art
            if self._view_rect(r).contains(pos):
                return which, -1
        return None

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._pixmap is None:
            return
        hit = self._hit(event.position())
        self._press = None if (hit is not None and hit[1] >= 0) else event.position()
        if hit is None:
            return
        which, handle = hit
        r = self.label if which == "label" else self.art
        self._drag = (which, handle, self._to_image(event.position()), r)

    def mouseMoveEvent(self, event) -> None:
        if self._press is not None:
            d = event.position() - self._press
            if (d.x() ** 2 + d.y() ** 2) ** 0.5 >= 4:
                self._press = None  # it is a drag, not a click
        if self._drag is None:
            hit = self._hit(event.position()) if self._pixmap is not None else None
            if hit is None:
                self.unsetCursor()
            elif hit[1] < 0:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                dx, dy = _HANDLES[hit[1]]
                self.setCursor(
                    Qt.CursorShape.SizeFDiagCursor if dx * dy == 1 else
                    Qt.CursorShape.SizeBDiagCursor if dx * dy == -1 else
                    Qt.CursorShape.SizeHorCursor if dx else Qt.CursorShape.SizeVerCursor
                )
            return
        which, handle, start, r0 = self._drag
        cx, cy = self._to_image(event.position())
        ddx, ddy = cx - start[0], cy - start[1]
        x, y, w, h = r0
        if handle < 0:
            nx = int(round(min(max(x + ddx, 0), self._iw - w)))
            ny = int(round(min(max(y + ddy, 0), self._ih - h)))
            new = (nx, ny, w, h)
        else:
            hx, hy = _HANDLES[handle]
            left, top, right, bottom = x, y, x + w, y + h
            if hx < 0:
                left = int(round(min(max(x + ddx, 0), right - _MIN_SIZE)))
            elif hx > 0:
                right = int(round(max(min(right + ddx, self._iw), left + _MIN_SIZE)))
            if hy < 0:
                top = int(round(min(max(y + ddy, 0), bottom - _MIN_SIZE)))
            elif hy > 0:
                bottom = int(round(max(min(bottom + ddy, self._ih), top + _MIN_SIZE)))
            new = (left, top, right - left, bottom - top)
        if which == "label":
            self.label = new
        else:
            self.art = new
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        press, self._press = self._press, None
        if press is None or event.button() != Qt.MouseButton.LeftButton or self._pixmap is None:
            return
        d = event.position() - press
        if (d.x() ** 2 + d.y() ** 2) ** 0.5 < 4:
            self._snap_art_corner(*self._to_image(event.position()))

    def _snap_art_corner(self, px: float, py: float) -> None:
        x, y, w, h = self.art
        px = min(max(px, 0), self._iw)
        py = min(max(py, 0), self._ih)
        left, top, right, bottom = x, y, x + w, y + h
        # nearest corner (Euclidean)
        cx = left if abs(px - left) <= abs(px - right) else right
        cy = top if abs(py - top) <= abs(py - bottom) else bottom
        nx, ny = int(round(px)), int(round(py))
        if cx == left:
            left = min(nx, right - _MIN_SIZE)
        else:
            right = max(nx, left + _MIN_SIZE)
        if cy == top:
            top = min(ny, bottom - _MIN_SIZE)
        else:
            bottom = max(ny, top + _MIN_SIZE)
        self.art = (left, top, right - left, bottom - top)
        self.update()
        self.changed.emit()


def _pad_axis(pos: int, size: int, pad: int, limit: int) -> tuple[int, int]:
    lo, hi = pos - pad, pos + size + pad
    if hi - lo < _MIN_SIZE:
        c = pos + size / 2
        lo, hi = int(round(c - _MIN_SIZE / 2)), int(round(c + _MIN_SIZE / 2))
    if lo < 0:
        lo = 0
    if hi > limit:
        hi = limit
    if hi - lo < _MIN_SIZE:  # squeezed by the image edge: keep minimum size
        if lo == 0:
            hi = min(limit, _MIN_SIZE)
        else:
            lo = max(0, limit - _MIN_SIZE)
    return lo, hi - lo


_OCR_PREFIX = "ocr_"


def _load_ocr_settings() -> OcrSettings:
    st = QSettings("MOB", "CardCrop")
    m = {k: st.value(_OCR_PREFIX + k) for k in (*OcrSettings().to_mapping(), "shave_x_pct")}
    return OcrSettings.from_mapping({k: v for k, v in m.items() if v is not None})


def _save_ocr_settings(s: OcrSettings) -> None:
    st = QSettings("MOB", "CardCrop")
    for k, v in s.to_mapping().items():
        st.setValue(_OCR_PREFIX + k, v)


# Fixed aspect (width / height) of the synthetic label slot in the preview, independent of the label
# rectangle on the photo (which only drives OCR). PSA labels are ~3.3-3.4 : 1; the frontend uses the same value.
LABEL_ASPECT = 3.35
_LABEL_RED = QColor("#e2231a")
_FONT_CANDIDATES = ("Arial Narrow", "Helvetica Neue Condensed", "Avenir Next Condensed", "Arial")


def _label_font() -> str:
    fams = set(QFontDatabase.families())
    return next((f for f in _FONT_CANDIDATES if f in fams), QFont().family())


def _snap(v: float, step: float) -> float:
    return round(v / step) * step


def _draw_synthetic_label(rows: list[list[str]], w: int, h: int) -> QPixmap:
    """Mirror of the frontend's SyntheticLabel (labelRowGeometry): white box, inset red border 7.5% of
    the height, padding 4%, 4 equal rows, font size = row height, 70/30 columns, cells clipped."""
    pix = QPixmap(w, h)
    p = QPainter(pix)
    p.fillRect(0, 0, w, h, _LABEL_RED)
    border = _snap(h * 0.075, 0.25)
    off = border + _snap(h * 0.04, 0.25)
    ix, iy = off, off
    iw, ih = max(0.0, w - 2 * off), max(0.0, h - 2 * off)
    p.fillRect(QRectF(border, border, max(0.0, w - 2 * border), max(0.0, h - 2 * border)), QColor("white"))
    row_h = ih / 4
    left_w = _snap(iw * 0.7, 0.25)
    font = QFont(_label_font())
    font.setPixelSize(max(1, int(round(_snap(row_h, 0.25)))))
    font.setWeight(QFont.Weight.Normal)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, font.pixelSize() * 0.02)
    p.setFont(font)
    p.setPen(QColor("black"))
    for i in range(4):
        top, bottom = round(iy + i * row_h), round(iy + (i + 1) * row_h)
        for col, (x, cw, align) in enumerate(((ix, left_w, Qt.AlignmentFlag.AlignLeft),
                                              (ix + left_w, iw - left_w, Qt.AlignmentFlag.AlignRight))):
            text = rows[i][col] if i < len(rows) and col < len(rows[i]) else ""
            if not text:
                continue
            cell = QRectF(x, top, cw, bottom - top)
            p.save()
            p.setClipRect(cell)  # overflow clipped, no ellipsis / shrink
            p.drawText(cell, int(align | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine), text)
            p.restore()
    p.end()
    return pix


class _OcrThread(QThread):
    done = Signal(int, object)  # (token, OcrResult)

    def __init__(self, token: int, bgr: np.ndarray, rect: Rect, settings: OcrSettings, parent=None) -> None:
        super().__init__(parent)
        self._token = token
        self._bgr = bgr
        self._rect = rect
        self._settings = settings

    def run(self) -> None:
        self.done.emit(self._token, ocr_label_debug(self._bgr, self._rect, self._settings))


class CropScreen(QWidget):
    # (Series, cropped_bgr, art_bounds, label_bounds|None, label_text|None)
    navigate_to_review = Signal(object, object, object, object, object)
    navigate_to_scanning = Signal(object)                        # Series

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: Series | None = None
        self._bgr: np.ndarray | None = None
        self._full_bgr: np.ndarray | None = None  # full-size warp for OCR (None on resume)
        self._last_label: Rect | None = None
        self._ocr_token = 0
        self._ocr_threads: set[_OcrThread] = set()
        self._saved_cells: list[str] | None = None
        self._ocr_settings = _load_ocr_settings()
        self._last_result: OcrResult | None = None
        self._ocr_runs = 0
        self._ocr_timer = QTimer(self)
        self._ocr_timer.setSingleShot(True)
        self._ocr_timer.setInterval(400)
        self._ocr_timer.timeout.connect(self._reread)
        self._pad_base: Rect | None = None  # art rect when the current padding drag started
        self._pad_timer = QTimer(self)
        self._pad_timer.setSingleShot(True)
        self._pad_timer.setInterval(400)
        self._pad_timer.timeout.connect(self._on_pad_idle)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setSpacing(12)
        outer.setContentsMargins(8, 8, 8, 8)

        self._canvas = _CropCanvas()
        self._canvas.changed.connect(self._on_canvas_changed)
        outer.addWidget(self._canvas, stretch=3)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        self._no_label = QCheckBox("No label")
        self._no_label.toggled.connect(self._on_no_label_toggled)
        rl.addWidget(self._no_label)

        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet("background: #222; border: 1px solid #555;")
        self._preview.setMinimumSize(200, 300)
        self._preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        rl.addWidget(self._preview, stretch=1)

        self._text_box = QGroupBox("Label text")
        grid = QGridLayout(self._text_box)
        grid.setColumnStretch(0, 7)
        grid.setColumnStretch(1, 3)
        self._cells: list[QLineEdit] = []
        self._base_pt = QLineEdit().font().pointSizeF()
        try:
            scale = float(QSettings("MOB", "CardCrop").value("label_text_font_scale", 1.0))
        except (TypeError, ValueError):
            scale = 1.0
        scale = min(max(round(scale * 20) / 20, 0.5), 1.5)
        for row in range(4):
            for col in range(2):
                e = QLineEdit()
                e.setMaxLength(64)  # backend rejects label_text cells longer than 64 characters
                e.setFont(self._scaled_font(e.font(), scale))
                if col == 1:
                    e.setAlignment(Qt.AlignmentFlag.AlignRight)
                grid.addWidget(e, row * 2, col)
                self._cells.append(e)
                e.textChanged.connect(lambda _=None: self._update_preview())
                clr = QToolButton()
                clr.setText("\u2715 clear")
                clr.setFixedHeight(18)
                clr.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # keep Space/Enter for Accept
                clr.clicked.connect(e.clear)
                grid.addWidget(clr, row * 2 + 1, col, alignment=Qt.AlignmentFlag.AlignRight if col else Qt.AlignmentFlag.AlignLeft)
        self._reread_btn = QPushButton("Re-read")
        self._reread_btn.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._reread_btn.clicked.connect(self._reread)
        grid.addWidget(self._reread_btn, 8, 0, 1, 2)
        rl.addWidget(self._text_box)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Text size"))
        self._font_slider = QSlider(Qt.Orientation.Horizontal)
        self._font_slider.setRange(50, 150)
        self._font_slider.setSingleStep(5)
        self._font_slider.setPageStep(5)
        self._font_slider.setValue(int(round(scale * 100)))
        self._font_slider.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # keep Space/Enter for Accept
        self._font_slider.valueChanged.connect(self._on_font_scale)
        size_row.addWidget(self._font_slider, stretch=1)
        self._font_value = QLabel(f"\u00d7{scale:.2f}")
        self._font_value.setMinimumWidth(48)
        size_row.addWidget(self._font_value)
        rl.addLayout(size_row)

        self._build_tune_panel(rl)
        self._canvas.guide_pct = self._ocr_settings.shave_pct

        pad_row = QHBoxLayout()
        pad_row.addWidget(QLabel("Padding"))
        self._pad_slider = QSlider(Qt.Orientation.Horizontal)
        self._pad_slider.setRange(-20, 20)
        self._pad_slider.setValue(0)
        self._pad_slider.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # keep Space/Enter for Accept
        self._pad_slider.valueChanged.connect(self._on_pad_changed)
        self._pad_slider.sliderReleased.connect(self._commit_padding)
        pad_row.addWidget(self._pad_slider, stretch=1)
        self._pad_value = QLabel("+0 px")
        self._pad_value.setMinimumWidth(48)
        pad_row.addWidget(self._pad_value)
        rl.addLayout(pad_row)

        accept_btn = QPushButton("Accept  [Enter / Space]")
        accept_btn.setStyleSheet("background: #4caf50; color: white; font-weight: bold; padding: 8px;")
        accept_btn.clicked.connect(self._on_accept)
        rl.addWidget(accept_btn)

        redetect_btn = QPushButton("Re-detect")
        redetect_btn.clicked.connect(lambda: self._redetect())
        rl.addWidget(redetect_btn)

        retake_btn = QPushButton("Retake  [Esc]")
        retake_btn.clicked.connect(self._on_retake)
        rl.addWidget(retake_btn)

        outer.addWidget(right, stretch=1)

        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            QShortcut(QKeySequence(key), self).activated.connect(self._on_accept)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self._on_retake)

    # ── Tune OCR panel ────────────────────────────────────────────────────

    def _slider(self, lo: int, hi: int, value: int, fmt=str) -> tuple[QSlider, QLabel, QHBoxLayout]:
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(value)
        sl.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # keep Space/Enter for Accept
        lab = QLabel(fmt(value))
        lab.setMinimumWidth(34)
        sl.valueChanged.connect(lambda v, lab=lab, fmt=fmt: lab.setText(fmt(v)))
        sl._lab = lab
        row = QHBoxLayout()
        row.addWidget(sl, stretch=1)
        row.addWidget(lab)
        return sl, lab, row

    def _spin(self, lo: float, hi: float, value: float, step: float = 1.0, dec: int = 0, suffix: str = "") -> QWidget:
        w = QDoubleSpinBox() if dec else QSpinBox()
        w.setRange(lo, hi)
        w.setSingleStep(step)
        if dec:
            w.setDecimals(dec)
        w.setValue(value)
        if suffix:
            w.setSuffix(suffix)
        w.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        return w

    def _build_tune_panel(self, rl: QVBoxLayout) -> None:
        s = self._ocr_settings
        self._tune_btn = QToolButton()
        self._tune_btn.setText("Tune OCR")
        self._tune_btn.setCheckable(True)
        self._tune_btn.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        rl.addWidget(self._tune_btn)

        box = QGroupBox("Tune OCR")
        form = QGridLayout(box)
        r = 0
        self._tune_preview = QLabel()
        self._tune_preview.setMinimumHeight(110)
        self._tune_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tune_preview.setStyleSheet("background: #222; border: 1px solid #555;")
        form.addWidget(self._tune_preview, r, 0, 1, 2); r += 1

        def add(label: str, widget) -> None:
            nonlocal r
            form.addWidget(QLabel(label), r, 0)
            (form.addLayout if isinstance(widget, QHBoxLayout) else form.addWidget)(widget, r, 1)
            r += 1

        self._mode = QComboBox()
        self._mode.addItems(["fixed", "adaptive"])
        self._mode.setCurrentText(s.mode)
        self._mode.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        add("Mode", self._mode)
        self._thr, _, row = self._slider(0, 255, s.threshold)
        add("Threshold", row)
        self._blk, _, row = self._slider(11, 75, s.adaptive_block)
        add("Adaptive block", row)
        self._adc, _, row = self._slider(-20, 20, s.adaptive_c)
        add("Adaptive C", row)
        self._conf, _, row = self._slider(0, 100, s.confidence)
        add("Confidence", row)
        self._shave = self._spin(0, 20, s.shave_pct, 0.5, 1, " %")
        add("Border %", self._shave)
        self._up = QComboBox()
        self._up.addItems(["Auto", "1x", "2x", "3x"])
        self._up.setCurrentIndex(s.upscale if s.upscale in (1, 2, 3) else 0)
        self._up.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._blur = self._spin(0, 5, s.blur, 1, 0, " px")
        ub = QHBoxLayout()
        ub.addWidget(self._up)
        ub.addWidget(QLabel("Blur"))
        ub.addWidget(self._blur)
        add("Upscale", ub)
        self._psm = QComboBox()
        for n, name in ((6, "6 block"), (4, "4 columns"), (11, "11 sparse")):
            self._psm.addItem(name, n)
        self._psm.setCurrentIndex(max(0, self._psm.findData(s.psm)))
        self._psm.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        add("Segmentation", self._psm)
        self._deskew = QCheckBox("Deskew")
        self._deskew.setChecked(s.deskew)
        self._skew_lbl = QLabel("")
        self._whitelist = QCheckBox("Whitelist A-Z 0-9 # / - . &")
        self._whitelist.setChecked(s.whitelist)
        self._best2 = QCheckBox("Best of 2 thresholds")
        self._best2.setChecked(s.best_of_two)
        self._best2_lbl = QLabel("")
        for cb in (self._deskew, self._whitelist, self._best2):
            cb.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        dk = QHBoxLayout()
        dk.addWidget(self._deskew)
        dk.addWidget(self._skew_lbl)
        form.addLayout(dk, r, 0, 1, 2); r += 1
        form.addWidget(self._whitelist, r, 0, 1, 2); r += 1
        bk = QHBoxLayout()
        bk.addWidget(self._best2)
        bk.addWidget(self._best2_lbl)
        form.addLayout(bk, r, 0, 1, 2); r += 1
        self._logo = QCheckBox("Mask logo")
        self._logo.setChecked(s.logo_mask)
        self._logo.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._lgx = self._spin(0, 100, s.logo_x_pct, 1, 0, "%")
        self._lgw = self._spin(1, 100, s.logo_w_pct, 1, 0, "%")
        self._lgy = self._spin(0, 99, s.logo_y_pct, 1, 0, "%")
        lg = QHBoxLayout()
        lg.addWidget(self._logo)
        for tag, w in (("x", self._lgx), ("w", self._lgw), ("y", self._lgy)):
            lg.addWidget(QLabel(tag))
            lg.addWidget(w)
        form.addLayout(lg, r, 0, 1, 2); r += 1
        self._raw = QPlainTextEdit()
        self._raw.setReadOnly(True)
        self._raw.setMinimumHeight(110)
        form.addWidget(QLabel("Raw output"), r, 0, 1, 2); r += 1
        form.addWidget(self._raw, r, 0, 1, 2); r += 1
        self._last_run = QLabel("")
        form.addWidget(self._last_run, r, 0, 1, 2); r += 1
        reset = QPushButton("Reset to defaults")
        reset.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        reset.clicked.connect(self._reset_ocr_settings)
        form.addWidget(reset, r, 0, 1, 2)

        self._tune_scroll = QScrollArea()
        self._tune_scroll.setWidgetResizable(True)
        self._tune_scroll.setWidget(box)
        self._tune_scroll.setMinimumHeight(260)
        self._tune_scroll.hide()
        rl.addWidget(self._tune_scroll, stretch=2)
        self._tune_btn.toggled.connect(self._on_tune_toggled)

        for w in (self._mode, self._up, self._psm):
            w.currentIndexChanged.connect(self._on_ocr_ctrl_changed)
        for w in (self._thr, self._blk, self._adc):
            w.valueChanged.connect(self._on_ocr_ctrl_changed)
        for w in (self._shave, self._blur, self._lgx, self._lgw, self._lgy):
            w.valueChanged.connect(self._on_ocr_ctrl_changed)
        for w in (self._deskew, self._whitelist, self._best2, self._logo):
            w.toggled.connect(self._on_ocr_ctrl_changed)
        self._conf.valueChanged.connect(self._on_conf_changed)

    def _collect_ocr_settings(self) -> OcrSettings:
        return OcrSettings(
            threshold=self._thr.value(),
            mode=self._mode.currentText(),
            adaptive_block=self._blk.value() | 1,
            adaptive_c=self._adc.value(),
            confidence=self._conf.value(),
            shave_pct=self._shave.value(),
            upscale=self._up.currentIndex(),
            blur=self._blur.value(),
            psm=int(self._psm.currentData()),
            deskew=self._deskew.isChecked(),
            whitelist=self._whitelist.isChecked(),
            logo_mask=self._logo.isChecked(),
            logo_x_pct=self._lgx.value(),
            logo_w_pct=self._lgw.value(),
            logo_y_pct=self._lgy.value(),
            best_of_two=self._best2.isChecked(),
        )

    def _apply_ocr_settings(self, s: OcrSettings) -> None:
        """Push settings into the controls without triggering their handlers."""
        ctrls = (self._mode, self._up, self._psm, self._thr, self._blk, self._adc, self._conf, self._shave,
                 self._blur, self._lgx, self._lgw, self._lgy, self._deskew, self._whitelist,
                 self._best2, self._logo)
        for c in ctrls:
            c.blockSignals(True)
        self._mode.setCurrentText(s.mode)
        self._up.setCurrentIndex(s.upscale if s.upscale in (1, 2, 3) else 0)
        self._psm.setCurrentIndex(max(0, self._psm.findData(s.psm)))
        self._thr.setValue(s.threshold)
        self._blk.setValue(s.adaptive_block)
        self._adc.setValue(s.adaptive_c)
        self._conf.setValue(s.confidence)
        self._shave.setValue(s.shave_pct)
        self._blur.setValue(s.blur)
        self._lgx.setValue(s.logo_x_pct)
        self._lgw.setValue(s.logo_w_pct)
        self._lgy.setValue(s.logo_y_pct)
        self._deskew.setChecked(s.deskew)
        self._whitelist.setChecked(s.whitelist)
        self._best2.setChecked(s.best_of_two)
        self._logo.setChecked(s.logo_mask)
        for c in ctrls:
            c.blockSignals(False)
        for sl in (self._thr, self._blk, self._adc, self._conf):  # their labels follow valueChanged (blocked)
            sl._lab.setText(str(sl.value()))

    def _on_tune_toggled(self, on: bool) -> None:
        self._tune_scroll.setVisible(on)
        if on:
            self._refresh_tune_preview(instant=True)

    def _on_ocr_ctrl_changed(self, *_) -> None:
        self._ocr_settings = self._collect_ocr_settings()
        _save_ocr_settings(self._ocr_settings)
        self._canvas.guide_pct = self._ocr_settings.shave_pct
        self._canvas.update()
        self._refresh_tune_preview(instant=True)
        if self._bgr is not None and not self._no_label.isChecked():
            self._ocr_timer.start()  # debounce: re-run Tesseract ~400 ms after the last change

    def _on_conf_changed(self, value: int) -> None:
        """Confidence only re-filters the words of the last run: no new Tesseract run."""
        self._ocr_settings.confidence = value
        _save_ocr_settings(self._ocr_settings)
        if self._last_result is not None and self._last_result.bitmap is not None:
            self._last_result.regrid(self._ocr_settings)
            self._show_result(self._last_result)

    def _reset_ocr_settings(self) -> None:
        self._ocr_settings = OcrSettings()
        _save_ocr_settings(self._ocr_settings)
        self._apply_ocr_settings(self._ocr_settings)
        self._canvas.guide_pct = self._ocr_settings.shave_pct
        self._canvas.update()
        self._refresh_tune_preview(instant=True)
        self._reread()

    def _ocr_input(self) -> tuple[np.ndarray, Rect] | None:
        if self._bgr is None:
            return None
        rect = tuple(int(v) for v in self._canvas.label)
        if self._full_bgr is None:
            return self._bgr, rect
        fh, fw = self._full_bgr.shape[:2]
        sh, sw = self._bgr.shape[:2]
        sx, sy = fw / sw, fh / sh
        return self._full_bgr, (int(round(rect[0] * sx)), int(round(rect[1] * sy)),
                                int(round(rect[2] * sx)), int(round(rect[3] * sy)))

    def _refresh_tune_preview(self, instant: bool = False) -> None:
        """Instant: just the preprocessed bitmap for the current controls. Otherwise the last result."""
        if not self._tune_btn.isChecked():
            return
        if instant:
            inp = self._ocr_input()
            if inp is None:
                return
            bw, _, logo = preview_bitmap(inp[0], inp[1], self._ocr_settings)
            self._paint_tune_preview(bw, [], {}, logo)
        elif self._last_result is not None and self._last_result.bitmap is not None:
            r = self._last_result
            self._paint_tune_preview(r.bitmap, r.words, r.splits, r.logo_rect)

    def _paint_tune_preview(self, bw: np.ndarray, words, splits, logo: Rect | None) -> None:
        bh, bwid = bw.shape[:2]
        pw = max(200, self._tune_preview.width() - 4)
        k = pw / bwid
        pix = _bgr_to_pixmap(cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)).scaled(
            pw, max(1, int(bh * k)), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        p = QPainter(pix)
        font = p.font()
        font.setPointSizeF(7)
        p.setFont(font)
        for w in words:
            col = QColor("#1faa3c") if w.kept else QColor("#888888")
            p.setPen(QPen(col, 1))
            p.drawRect(QRectF(w.left * k, w.top * k, w.width * k, w.height * k))
            p.drawText(QPointF(w.left * k, max(8.0, w.top * k - 1)), f"{w.conf:.0f}")
        p.setPen(QPen(QColor("#e0a800"), 1, Qt.PenStyle.DashLine))
        for x, y0, y1 in splits.values():
            p.drawLine(QPointF(x * k, y0 * k), QPointF(x * k, y1 * k))
        if logo is not None:
            lx, ly, lw, lh = logo
            p.setPen(QPen(QColor("#ff3b30"), 1))
            p.setBrush(QBrush(QColor(255, 59, 48, 160), Qt.BrushStyle.BDiagPattern))
            p.drawRect(QRectF(lx * k, ly * k, lw * k, lh * k))
        p.end()
        self._tune_preview.setPixmap(pix)
        self._tune_preview.setMinimumHeight(min(pix.height() + 4, 220))

    def _show_result(self, r: OcrResult) -> None:
        self._refresh_tune_preview()
        self._raw.setPlainText(r.error or r.raw_text)
        kept = sum(w.kept for w in r.words)
        self._last_run.setText(f"Last run: {r.elapsed_ms:.0f} ms, {len(r.words)} words ({kept} kept)")
        self._skew_lbl.setText(f"{r.skew_deg:+.1f} deg" if self._deskew.isChecked() else "")
        self._best2_lbl.setText(f"chose {r.best_of}" if r.best_of else "")

    # ── load / detection ──────────────────────────────────────────────────

    def load(
        self,
        series: Series,
        bgr: np.ndarray,
        art_bounds: Rect | None = None,
        label_bounds: Rect | None = None,
        full_bgr: np.ndarray | None = None,
        label_text: dict | None = None,
    ) -> None:
        """Show the crop editor. Saved bounds (resume) win over a fresh detection."""
        self._commit_padding()
        self._series = series
        self._bgr = bgr
        self._full_bgr = full_bgr
        self._ocr_token += 1  # drop any in-flight OCR from the previous photo
        self._saved_cells = None
        self._last_result = None
        self._set_cells(label_text)
        self._canvas.set_image(bgr)
        if art_bounds is not None:
            self._canvas.art = tuple(art_bounds)
            self._set_label(tuple(label_bounds) if label_bounds else None)
            self._apply_no_label_state()
        else:
            self._redetect(ocr=label_text is None)
            return
        self._update_preview()

    def _default_label(self) -> Rect:
        h, w = self._bgr.shape[:2]
        return (0, 0, w, max(_MIN_SIZE, int(h * 0.15)))

    def _set_label(self, label: Rect | None) -> None:
        self._last_label = label if label is not None else self._default_label()
        self._canvas.label = self._last_label
        self._no_label.blockSignals(True)
        self._no_label.setChecked(label is None)
        self._no_label.blockSignals(False)
        self._canvas.label_visible = label is not None
        self._canvas.update()

    def _redetect(self, ocr: bool = True) -> None:
        if self._bgr is None:
            return
        self._saved_cells = None
        self._commit_padding()
        art = detect_art(self._bgr)
        self._canvas.art = art
        self._set_label(detect_label(self._bgr, art))
        self._apply_no_label_state()
        self._update_preview()
        if ocr:
            self._reread()

    # ── label text (OCR) ──────────────────────────────────────────────────

    def _scaled_font(self, font, scale: float):
        font.setPointSizeF(max(self._base_pt * scale, 6.0))
        return font

    def _on_font_scale(self, value: int) -> None:
        scale = value / 100
        self._font_value.setText(f"\u00d7{scale:.2f}")
        for e in self._cells:
            e.setFont(self._scaled_font(e.font(), scale))
        QSettings("MOB", "CardCrop").setValue("label_text_font_scale", scale)

    def _set_cells(self, text: dict | None) -> None:
        rows = (text or {}).get("rows") or []
        for i, e in enumerate(self._cells):
            r, c = divmod(i, 2)
            try:
                e.setText(str(rows[r][c]))
            except (IndexError, TypeError):
                e.setText("")

    def _cell_values(self) -> list[list[str]]:
        v = [e.text().strip() for e in self._cells]
        return [[v[0], v[1]], [v[2], v[3]], [v[4], v[5]], [v[6], v[7]]]

    def _apply_no_label_state(self) -> None:
        self._text_box.setEnabled(not self._no_label.isChecked())
        if self._no_label.isChecked():
            self._stash_and_clear()

    def _stash_and_clear(self) -> None:
        if self._saved_cells is None:
            self._saved_cells = [e.text() for e in self._cells]
        for e in self._cells:
            e.clear()

    def _reread(self) -> None:
        """Run OCR on the current label rectangle (off-thread) and overwrite the fields."""
        self._ocr_timer.stop()
        if self._bgr is None or self._no_label.isChecked():
            return
        img, rect = self._ocr_input()
        self._ocr_token += 1
        self._ocr_runs += 1
        t = _OcrThread(self._ocr_token, img, rect, self._ocr_settings, self)
        t.done.connect(self._on_ocr_done)
        t.finished.connect(lambda t=t: (self._ocr_threads.discard(t), t.deleteLater()))
        self._ocr_threads.add(t)
        self._reread_btn.setEnabled(False)
        t.start()

    def _on_ocr_done(self, token: int, result) -> None:
        if token != self._ocr_token:
            return  # stale: photo changed or a newer read was started
        self._reread_btn.setEnabled(True)
        self._last_result = result
        if result.bitmap is not None:
            self._show_result(result)
        elif result.error:
            self._raw.setPlainText(result.error)
        if not result.error and not self._no_label.isChecked():
            self._set_cells({"kind": "psa", "rows": result.rows})

    def wait_ocr(self) -> None:
        for t in list(self._ocr_threads):
            t.wait()

    # ── art padding (relative slider) ─────────────────────────────────────

    def _on_pad_changed(self, value: int) -> None:
        self._pad_value.setText(f"{value:+d} px")
        if self._pad_base is None:
            self._pad_base = tuple(self._canvas.art)
        x, y, w, h = self._pad_base
        nx, nw = _pad_axis(x, w, value, self._canvas._iw)
        ny, nh = _pad_axis(y, h, value, self._canvas._ih)
        self._canvas.art = (nx, ny, nw, nh)
        self._canvas.update()
        self._update_preview()
        self._pad_timer.start()  # commit if no release ever comes (keyboard/wheel)

    def _on_pad_idle(self) -> None:
        if self._pad_slider.isSliderDown():
            self._pad_timer.start()  # still dragging, just paused
        else:
            self._commit_padding()

    def _commit_padding(self) -> None:
        """Keep the current art rect as the new bounds and snap the slider to 0."""
        self._pad_timer.stop()
        self._pad_base = None
        self._pad_slider.blockSignals(True)
        self._pad_slider.setValue(0)
        self._pad_slider.blockSignals(False)
        self._pad_value.setText("+0 px")

    # ── UI handlers ───────────────────────────────────────────────────────

    def _on_canvas_changed(self) -> None:
        self._commit_padding()  # a manual handle drag supersedes a pending padding base
        self._update_preview()

    def _on_no_label_toggled(self, checked: bool) -> None:
        self._text_box.setEnabled(not checked)
        if checked:
            self._last_label = self._canvas.label
            self._canvas.label_visible = False
            self._stash_and_clear()
        else:
            if self._saved_cells is not None:
                for e, v in zip(self._cells, self._saved_cells):
                    e.setText(v)
                self._saved_cells = None
            self._canvas.label = self._last_label or self._default_label()
            self._canvas.label_visible = True
        self._canvas.update()
        self._update_preview()

    def _crop(self, r: Rect) -> np.ndarray:
        x, y, w, h = r
        return self._bgr[y:y + h, x:x + w]

    def _update_preview(self) -> None:
        if self._bgr is None:
            return
        pw = max(50, self._preview.width() - 4)
        parts: list[QPixmap] = []
        if self._canvas.label_visible:
            parts.append(_draw_synthetic_label(self._cell_values(), pw, max(1, int(round(pw / LABEL_ASPECT)))))
        c = self._crop(self._canvas.art)
        if c.size != 0:
            ch = max(1, int(round(c.shape[0] * pw / c.shape[1])))
            parts.append(_bgr_to_pixmap(cv2.resize(c, (pw, ch), interpolation=cv2.INTER_AREA)))
        if not parts:
            return
        pix = QPixmap(pw, sum(q.height() for q in parts))
        p = QPainter(pix)
        y = 0
        for q in parts:
            p.drawPixmap(0, y, q)
            y += q.height()
        p.end()
        self._preview.setPixmap(pix.scaled(
            self._preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_preview()

    def _on_accept(self) -> None:
        if self._series is None or self._bgr is None:
            return
        self._commit_padding()
        art = tuple(int(v) for v in self._canvas.art)
        label = None if self._no_label.isChecked() else tuple(int(v) for v in self._canvas.label)
        label_text = None
        if label is not None:
            rows = self._cell_values()
            if any(c for r in rows for c in r):
                label_text = {"kind": "psa", "rows": rows}
        self.navigate_to_review.emit(self._series, self._bgr, art, label, label_text)

    def _on_retake(self) -> None:
        if self._series is not None:
            self.navigate_to_scanning.emit(self._series)
