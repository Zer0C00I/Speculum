from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QImage, QPixmap, QColor, QPainter
from PySide6.QtWidgets import QLabel, QScrollArea, QWidget

from pdftranslator.logging_config import get_logger

if TYPE_CHECKING:
    from pdftranslator.pdf.engine import PDFEngine

_log = get_logger(__name__)

RENDER_BUFFER = 4


class PDFViewer(QScrollArea):
    page_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._container = QWidget()
        self.setWidget(self._container)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._engine: PDFEngine | None = None
        self._dpi: float = 150
        self._page_labels: list[QLabel] = []
        self._page_offsets: list[int] = []
        self._page_heights: list[int] = []
        self._rendered: set[int] = set()
        self._synced_viewer: PDFViewer | None = None
        self._last_reported_page: int = -1
        self._page_width: int = 0
        self._spacing: int = 6

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(100)
        self._render_timer.timeout.connect(self._render_visible)

        self.verticalScrollBar().valueChanged.connect(self._on_scroll_tick)

    def set_engine(self, engine: PDFEngine | None) -> None:
        _log.info("set_engine engine=0x%x", id(engine) if engine else 0)
        self._engine = engine
        self._destroy_all()
        if engine and engine.page_count > 0:
            self._build_placeholders()
            self._container.adjustSize()
            self.verticalScrollBar().setValue(0)
            sb = self.verticalScrollBar()
            _log.info("Ready: %d pages, sb=[%d..%d], container=%dx%d",
                      engine.page_count, sb.minimum(), sb.maximum(),
                      self._container.width(), self._container.height())
            self._render_visible()
            self._container.update()
            self.viewport().update()

    def _destroy_all(self) -> None:
        for lbl in self._page_labels:
            lbl.deleteLater()
        self._page_labels.clear()
        self._page_offsets.clear()
        self._page_heights.clear()
        self._rendered.clear()
        self._last_reported_page = -1
        self._page_width = 0

    def _build_placeholders(self) -> None:
        if not self._engine:
            return
        n = self._engine.page_count
        _log.info("Building %d placeholders...", n)

        max_w = 0
        total_h = self._spacing
        for pg in range(n):
            w, h = self._engine.page_pixel_size(pg, self._dpi)
            self._page_offsets.append(total_h)
            self._page_heights.append(h)
            if w > max_w:
                max_w = w

            lbl = QLabel(self._container)
            lbl.setGeometry((max_w - w) // 2, total_h, w, h)
            lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            lbl.show()
            self._page_labels.append(lbl)

            total_h += h + self._spacing

        self._page_width = max_w
        self._container.setFixedSize(self._page_width, total_h)

        _log.info("Container=%dx%d, labels=%d, h[0]=%d h[%d]=%d",
                  self._page_width, total_h, n, self._page_heights[0], n - 1, self._page_heights[-1])

    def _on_scroll_tick(self, _value: int) -> None:
        pg = self.page_at_scroll()
        if pg != self._last_reported_page:
            self._last_reported_page = pg
            self.page_changed.emit(pg)
        self._render_timer.start()

    def _render_visible(self) -> None:
        if not self._engine or not self._page_offsets:
            return
        n = min(self._engine.page_count, len(self._page_offsets))
        vp = self.viewport()
        if not vp:
            return
        vp_h = vp.height()
        scroll_v = self.verticalScrollBar().value()

        if vp_h <= 0:
            first, last = 0, min(n - 1, RENDER_BUFFER)
            _log.debug("Viewport height=0, rendering first %d pages", last - first + 1)
        else:
            first_v = n - 1
            last_v = 0
            for i in range(n):
                y = self._page_offsets[i]
                h = self._page_heights[i]
                if y + h > scroll_v and y <= scroll_v + vp_h:
                    if i < first_v:
                        first_v = i
                    if i > last_v:
                        last_v = i

            if first_v > last_v:
                first, last = 0, min(n - 1, RENDER_BUFFER)
            else:
                first = max(0, first_v - RENDER_BUFFER)
                last = min(n - 1, last_v + RENDER_BUFFER)

        to_cull = self._rendered - set(range(first, last + 1))
        for pg in to_cull:
            if pg < len(self._page_labels):
                self._page_labels[pg].clear()
                self._rendered.discard(pg)

        for pg in range(first, last + 1):
            if pg not in self._rendered:
                self._render_single_page(pg)

    def _render_single_page(self, pg: int) -> None:
        if not self._engine or pg >= len(self._page_labels):
            return
        lbl = self._page_labels[pg]
        pixmap = self._engine.render_page_pixmap(pg, self._dpi)

        if pixmap.width != self._page_width or pixmap.height != self._page_heights[pg]:
            self._page_heights[pg] = pixmap.height
            lbl.setGeometry((self._page_width - pixmap.width) // 2, self._page_offsets[pg], pixmap.width, pixmap.height)
            self._recalc_offsets_from(pg + 1)

        if pixmap.n >= 4:
            fmt = QImage.Format.Format_RGBA8888
        else:
            fmt = QImage.Format.Format_RGB888

        img = QImage(pixmap.samples, pixmap.width, pixmap.height, pixmap.stride, fmt)
        qpix = QPixmap.fromImage(img)

        painter = QPainter(qpix)
        painter.setPen(QColor(200, 200, 200))
        painter.drawRect(0, 0, qpix.width() - 1, qpix.height() - 1)
        painter.end()

        lbl.setPixmap(qpix)
        lbl.update()
        self._rendered.add(pg)

    def _recalc_offsets_from(self, start: int) -> None:
        if start <= 0 or start >= len(self._page_offsets):
            return
        y = self._page_offsets[start - 1] + self._page_heights[start - 1] + self._spacing
        for i in range(start, len(self._page_offsets)):
            self._page_offsets[i] = y
            lbl = self._page_labels[i]
            lbl.move(lbl.x(), y)
            y += self._page_heights[i] + self._spacing
        total_h = y
        self._container.setFixedSize(self._page_width, total_h)

    def page_at_scroll(self, value: int | None = None) -> int:
        if not self._page_offsets:
            return 0
        if value is None:
            value = self.verticalScrollBar().value()
        vp_h = self.viewport().height() if self.viewport() else 1
        center = value + vp_h // 2
        for i in range(len(self._page_offsets) - 1, -1, -1):
            if center >= self._page_offsets[i]:
                return i
        return 0

    def current_page(self) -> int:
        return self.page_at_scroll()

    def scroll_to_page(self, page_num: int) -> None:
        if 0 <= page_num < len(self._page_offsets):
            self.verticalScrollBar().setValue(self._page_offsets[page_num])

    def refresh_page(self, page_num: int) -> None:
        if not self._engine or page_num >= len(self._page_labels):
            return
        _log.info("Refresh page %d (engine=0x%x)", page_num, id(self._engine))
        self._rendered.discard(page_num)
        self._render_single_page(page_num)

    def sync_with(self, other: PDFViewer) -> None:
        self._synced_viewer = other
        other._synced_viewer = self

        def _sync_a_to_b(v: int) -> None:
            other.verticalScrollBar().setValue(v)

        def _sync_b_to_a(v: int) -> None:
            self.verticalScrollBar().setValue(v)

        self.verticalScrollBar().valueChanged.connect(_sync_a_to_b)
        other.verticalScrollBar().valueChanged.connect(_sync_b_to_a)

        _log.info("Synced: 0x%x <-> 0x%x", id(self), id(other))
