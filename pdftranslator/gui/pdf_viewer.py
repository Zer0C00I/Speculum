from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPixmap, QTransform, QWheelEvent
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from pdftranslator.gui.render_worker import RenderWorker
from pdftranslator.logging_config import get_logger
from pdftranslator.pdf.engine import PDFEngine

_log = get_logger(__name__)

PreviewKey = tuple[int, int]
TileKey = tuple[int, int, int, int]
TileSpec = tuple[int, int, int, tuple[float, float, float, float]]


@dataclass
class _TileItem:
    row: int
    col: int
    clip_rect: QRectF
    item: QGraphicsPixmapItem
    dpi_x10: int | None = None

    def set_pixmap(self, pixmap: QPixmap, dpi_x10: int) -> None:
        self.item.setPixmap(pixmap)
        self.item.setScale(72.0 / (dpi_x10 / 10.0))
        self.dpi_x10 = dpi_x10

    def clear(self) -> None:
        self.item.setPixmap(QPixmap())
        self.dpi_x10 = None


@dataclass
class _PageItem:
    page_num: int
    rect: QRectF
    frame: QGraphicsRectItem
    preview_item: QGraphicsPixmapItem
    label: QGraphicsSimpleTextItem
    tiles: dict[tuple[int, int], _TileItem] = field(default_factory=dict)
    pending_tiles: dict[tuple[int, int], _TileItem] = field(default_factory=dict)
    pending_expected: set[tuple[int, int]] = field(default_factory=set)
    pending_loaded: set[tuple[int, int]] = field(default_factory=set)
    pending_dpi_x10: int | None = None
    preview_dpi_x10: int | None = None

    def set_preview(self, pixmap: QPixmap, dpi_x10: int) -> None:
        self.preview_item.setPixmap(pixmap)
        self.preview_item.setScale(72.0 / (dpi_x10 / 10.0))
        self.preview_dpi_x10 = dpi_x10

    def clear_preview(self) -> None:
        self.preview_item.setPixmap(QPixmap())
        self.preview_dpi_x10 = None

    def clear_tiles(self) -> None:
        for tile in self.tiles.values():
            tile.clear()
        for tile in self.pending_tiles.values():
            tile.clear()

    def drop_tiles(self) -> None:
        for tile in self.tiles.values():
            scene = tile.item.scene()
            if scene is not None:
                scene.removeItem(tile.item)
        self.tiles.clear()
        self.drop_pending_tiles()

    def drop_pending_tiles(self) -> None:
        for tile in self.pending_tiles.values():
            scene = tile.item.scene()
            if scene is not None:
                scene.removeItem(tile.item)
        self.pending_tiles.clear()
        self.pending_expected.clear()
        self.pending_loaded.clear()
        self.pending_dpi_x10 = None


class PDFViewer(QGraphicsView):
    page_changed = Signal(int)
    zoom_requested = Signal(float, int, float, float)

    BASE_DPI = 150.0
    MIN_DPI = 60.0
    MAX_DPI = 480.0
    PAGE_GAP = 24.0
    PAGE_MARGIN = 24.0
    TILE_PIXEL_SIZE = 768
    TILE_BUFFER = 1
    CACHE_LIMIT = 512
    PREFETCH_PAGE_AHEAD = 3
    PREFETCH_PAGE_BEHIND = 1
    PREVIEW_DPI = 96.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QColor("#efefef"))
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

        self._engine: PDFEngine | None = None
        self._dpi = self.BASE_DPI
        self._page_items: list[_PageItem] = []
        self._page_rects: list[QRectF] = []
        self._page_count = 0
        self._max_page_width = 1.0
        self._session_id = 0
        self._current_page = 0
        self._sync_target: PDFViewer | None = None
        self._sync_guard = False
        self._last_center_scene = QPointF()
        self._scroll_dx = 0.0
        self._scroll_dy = 0.0

        self._preview_cache: OrderedDict[PreviewKey, QPixmap] = OrderedDict()
        self._tile_cache: OrderedDict[TileKey, QPixmap] = OrderedDict()
        self._render_worker = RenderWorker(self)
        self._render_worker.preview_rendered.connect(self._on_preview_rendered)
        self._render_worker.tile_rendered.connect(self._on_tile_rendered)
        self._render_worker.render_failed.connect(self._on_render_failed)
        self._render_worker.start()

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(35)
        self._render_timer.timeout.connect(self._queue_visible_tiles)

        self.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.horizontalScrollBar().valueChanged.connect(self._on_scroll_changed)

        self.viewport().installEventFilter(self)
        self._apply_view_transform()

    def set_engine(self, engine: PDFEngine | None) -> None:
        self._engine = engine
        self._session_id += 1
        self._page_count = 0
        self._current_page = 0
        self._last_center_scene = QPointF()
        self._scroll_dx = 0.0
        self._scroll_dy = 0.0
        self._preview_cache.clear()
        self._tile_cache.clear()
        self._scene.clear()
        self._page_items.clear()
        self._page_rects.clear()

        path = engine._path if engine and engine._path else None
        self._render_worker.set_pdf_path(path)

        if not engine:
            self._scene.setSceneRect(QRectF())
            return

        self._build_scene()
        self._apply_view_transform()
        center = self._viewport_center_scene()
        if center is not None:
            self._last_center_scene = center
        self._emit_current_page_if_changed(force=True)
        self._render_timer.start()
        _log.info(
            "PDFViewer: set_engine pages=%d session=%d dpi=%.1f",
            self._page_count,
            self._session_id,
            self._dpi,
        )

    def shutdown(self) -> None:
        self._render_timer.stop()
        self._render_worker.stop()
        self._render_worker.wait(5000)

    def sync_with(self, other: PDFViewer) -> None:
        self._sync_target = other
        other._sync_target = self

    def dpi(self) -> float:
        return self._dpi

    def set_dpi(self, dpi: float) -> None:
        self.set_dpi_with_anchor(dpi, self._viewport_anchor())

    def set_dpi_with_anchor(
        self,
        dpi: float,
        anchor: tuple[int, float, float] | None,
    ) -> None:
        dpi = max(self.MIN_DPI, min(self.MAX_DPI, dpi))
        if abs(dpi - self._dpi) < 0.1:
            return
        self._session_id += 1
        self._dpi = dpi
        self._apply_view_transform()
        if anchor is not None:
            self._apply_anchor(anchor)
        else:
            center = self._viewport_center_scene()
            if center is not None:
                self.centerOn(center)
                self._last_center_scene = center
        self._scroll_dx = 0.0
        self._scroll_dy = 0.0
        self._render_timer.start()

    def fit_width(self) -> None:
        if not self._page_items:
            return
        center = self._viewport_center_scene()
        available = max(120, self.viewport().width() - 32)
        self._session_id += 1
        self._dpi = max(
            self.MIN_DPI,
            min(self.MAX_DPI, available * 72.0 / max(self._max_page_width, 1.0)),
        )
        self._apply_view_transform()
        if center is not None:
            self.centerOn(center)
            self._last_center_scene = center
        self._scroll_dx = 0.0
        self._scroll_dy = 0.0
        self._render_timer.start()

    def current_page(self) -> int:
        return self._current_page

    def scroll_to_page(self, page_num: int) -> None:
        if not (0 <= page_num < len(self._page_items)):
            return
        target_rect = self._page_items[page_num].rect
        self.centerOn(QPointF(target_rect.center().x(), target_rect.top() + 20))
        center = self._viewport_center_scene()
        if center is not None:
            self._last_center_scene = center
        self._scroll_dx = 0.0
        self._scroll_dy = 0.0

    def refresh_page(self, page_num: int) -> None:
        self.refresh_pages([page_num])

    def refresh_pages(self, page_numbers: list[int]) -> None:
        if not self._engine or not page_numbers:
            return
        touched = sorted({page for page in page_numbers if 0 <= page < self._page_count})
        if not touched:
            return
        self._session_id += 1
        self._render_worker.set_pdf_path(self._engine._path if self._engine._path else None)
        for page_num in touched:
            self._drop_page_cache(page_num)
        self._request_previews(self._prioritize_pages(touched))
        self._request_tiles_for_pages(self._prioritize_pages(touched))

    def refresh_document(self, changed_pages: list[int] | None = None) -> None:
        if not self._engine:
            return
        if self._engine.page_count != self._page_count:
            self.set_engine(self._engine)
            return
        self._session_id += 1
        self._render_worker.set_pdf_path(self._engine._path if self._engine._path else None)
        if changed_pages is None:
            self._preview_cache.clear()
            self._tile_cache.clear()
            for page in self._page_items:
                page.clear_preview()
                page.drop_tiles()
            self._render_timer.start()
            return
        self.refresh_pages(changed_pages)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                anchor = self._anchor_for_viewport_pos(event.position())
                if anchor is None:
                    anchor = self._viewport_anchor()
                if anchor is None:
                    anchor = (self._current_page, 0.5, 0.5)
                self.zoom_requested.emit(
                    1.15 if delta > 0 else 1 / 1.15,
                    anchor[0],
                    anchor[1],
                    anchor[2],
                )
                event.accept()
                return
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        bar = self.verticalScrollBar()
        single_step = max(40, self.viewport().height() // 10)
        page_step = max(80, self.viewport().height() - 80)

        if key == Qt.Key.Key_Space and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            bar.setValue(min(bar.maximum(), bar.value() + page_step))
            event.accept()
            return
        if key == Qt.Key.Key_Backspace or (
            key == Qt.Key.Key_Space and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ):
            bar.setValue(max(bar.minimum(), bar.value() - page_step))
            event.accept()
            return
        if key == Qt.Key.Key_PageDown:
            bar.setValue(min(bar.maximum(), bar.value() + page_step))
            event.accept()
            return
        if key == Qt.Key.Key_PageUp:
            bar.setValue(max(bar.minimum(), bar.value() - page_step))
            event.accept()
            return
        if key == Qt.Key.Key_Down:
            bar.setValue(min(bar.maximum(), bar.value() + single_step))
            event.accept()
            return
        if key == Qt.Key.Key_Up:
            bar.setValue(max(bar.minimum(), bar.value() - single_step))
            event.accept()
            return
        if key == Qt.Key.Key_Home:
            bar.setValue(bar.minimum())
            event.accept()
            return
        if key == Qt.Key.Key_End:
            bar.setValue(bar.maximum())
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render_timer.start()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.viewport() and event.type() == QEvent.Type.Paint:
            self._emit_current_page_if_changed()
        return super().eventFilter(watched, event)

    def _build_scene(self) -> None:
        assert self._engine is not None
        self._page_count = self._engine.page_count
        sizes = [self._engine._doc[page_num].rect for page_num in range(self._page_count)]
        self._max_page_width = max((rect.width for rect in sizes), default=1.0)

        y = self.PAGE_MARGIN
        scene_width = self._max_page_width + self.PAGE_MARGIN * 2.0
        for page_num, page_rect in enumerate(sizes):
            x = self.PAGE_MARGIN + (self._max_page_width - page_rect.width) / 2.0
            scene_rect = QRectF(x, y, page_rect.width, page_rect.height)

            frame = QGraphicsRectItem(0, 0, page_rect.width, page_rect.height)
            frame.setPos(scene_rect.topLeft())
            frame.setBrush(QColor("#ffffff"))
            frame.setPen(QColor("#d6d6d6"))
            frame.setZValue(0)
            self._scene.addItem(frame)

            preview_item = QGraphicsPixmapItem(frame)
            preview_item.setPos(0, 0)
            preview_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            preview_item.setZValue(0.5)

            label = QGraphicsSimpleTextItem(str(page_num + 1), frame)
            label.setBrush(QColor("#888888"))
            label.setPos(8, 6)
            label.setZValue(2)

            self._page_items.append(_PageItem(page_num, scene_rect, frame, preview_item, label))
            self._page_rects.append(scene_rect)
            y += page_rect.height + self.PAGE_GAP

        self._scene.setSceneRect(0, 0, scene_width, max(y, 1.0))

    def _apply_view_transform(self) -> None:
        scale = self._dpi / 72.0
        self.setTransform(QTransform().scale(scale, scale))

    def _on_scroll_changed(self, _value: int) -> None:
        center = self._viewport_center_scene()
        if center is not None:
            self._scroll_dx = center.x() - self._last_center_scene.x()
            self._scroll_dy = center.y() - self._last_center_scene.y()
            self._last_center_scene = center
        self._emit_current_page_if_changed()
        self._render_timer.start()
        self._sync_scrollbars()

    def _sync_scrollbars(self) -> None:
        other = self._sync_target
        if not other or self._sync_guard or other._sync_guard:
            return
        anchor = self._viewport_anchor()
        if anchor is None:
            return
        other._sync_guard = True
        try:
            other._apply_anchor(anchor)
        finally:
            other._sync_guard = False

    def _emit_current_page_if_changed(self, force: bool = False) -> None:
        page_num = self._page_at_view_center()
        if page_num < 0:
            return
        if force or page_num != self._current_page:
            self._current_page = page_num
            self.page_changed.emit(page_num)

    def _page_at_view_center(self) -> int:
        if not self._page_rects:
            return -1
        center_scene = self._viewport_center_scene()
        if center_scene is None:
            return -1
        return self._page_at_scene_y(center_scene.y())

    def _page_at_scene_y(self, y: float) -> int:
        if not self._page_rects:
            return -1
        for index, rect in enumerate(self._page_rects):
            if rect.top() <= y <= rect.bottom():
                return index
            if y < rect.top():
                return max(0, index - 1)
        if y < self._page_rects[0].top():
            return 0
        return len(self._page_rects) - 1

    def _viewport_center_scene(self) -> QPointF | None:
        if self.viewport().width() <= 0 or self.viewport().height() <= 0:
            return None
        return self.mapToScene(self.viewport().rect().center())

    def _viewport_scene_rect(self) -> QRectF | None:
        if self.viewport().width() <= 0 or self.viewport().height() <= 0:
            return None
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def _viewport_anchor(self) -> tuple[int, float, float] | None:
        if not self._page_rects:
            return None
        center = self._viewport_center_scene()
        if center is None:
            return None
        return self._anchor_for_scene_point(center)

    def _anchor_for_viewport_pos(self, pos: QPointF) -> tuple[int, float, float] | None:
        if self.viewport().width() <= 0 or self.viewport().height() <= 0:
            return None
        return self._anchor_for_scene_point(self.mapToScene(pos.toPoint()))

    def _anchor_for_scene_point(self, point: QPointF) -> tuple[int, float, float] | None:
        if not self._page_rects:
            return None
        page_num = self._page_at_scene_y(point.y())
        if not (0 <= page_num < len(self._page_rects)):
            return None
        rect = self._page_rects[page_num]
        height = max(rect.height(), 1.0)
        width = max(rect.width(), 1.0)
        rel_y = (point.y() - rect.top()) / height
        rel_x = (point.x() - rect.left()) / width
        return (
            page_num,
            max(0.0, min(1.0, rel_y)),
            max(0.0, min(1.0, rel_x)),
        )

    def _apply_anchor(self, anchor: tuple[int, float, float]) -> None:
        page_num, rel_y, rel_x = anchor
        if not (0 <= page_num < len(self._page_rects)):
            return
        rect = self._page_rects[page_num]
        target = QPointF(
            rect.left() + rect.width() * rel_x,
            rect.top() + rect.height() * rel_y,
        )
        self.centerOn(target)
        center = self._viewport_center_scene()
        if center is not None:
            self._last_center_scene = center
        self._emit_current_page_if_changed()

    def _queue_visible_tiles(self) -> None:
        if not self._engine or not self._page_items or not self._engine._path:
            return
        page_order = self._priority_visible_pages()
        self._request_previews(page_order)
        self._request_tiles_for_pages(page_order)

    def _priority_visible_pages(self) -> list[int]:
        visible = self._visible_pages()
        if not visible:
            if self._page_count == 0:
                return []
            visible = [self._current_page]
        expanded: set[int] = set()
        page_bias = self._page_prefetch_bias()
        for page_num in visible:
            behind = self.PREFETCH_PAGE_BEHIND + max(0, -page_bias)
            ahead = self.PREFETCH_PAGE_AHEAD + max(0, page_bias)
            for candidate in range(page_num - behind, page_num + ahead + 1):
                if 0 <= candidate < self._page_count:
                    expanded.add(candidate)
        return self._prioritize_pages(expanded)

    def _visible_pages(self) -> list[int]:
        if not self._page_rects:
            return []
        scene_rect = self._viewport_scene_rect()
        if scene_rect is None:
            return []
        return [index for index, rect in enumerate(self._page_rects) if rect.intersects(scene_rect)]

    def _prioritize_pages(self, pages) -> list[int]:
        unique = sorted(set(pages))
        current = self._current_page
        visible = set(self._visible_pages())
        return sorted(
            unique,
            key=lambda page: (
                0 if page == current else 1,
                0 if page in visible else 1,
                abs(page - current),
                page,
            ),
        )

    def _request_tiles_for_pages(self, ordered_pages: list[int]) -> None:
        if not ordered_pages or not self._engine or not self._engine._path:
            return
        request_specs: list[TileSpec] = []
        for page_num in ordered_pages:
            request_specs.extend(self._missing_tile_specs(page_num))
        if not request_specs:
            return
        self._render_worker.request_tiles(self._session_id, self._dpi_x10(), request_specs)

    def _request_previews(self, ordered_pages: list[int]) -> None:
        if not ordered_pages or not self._engine or not self._engine._path:
            return
        preview_dpi_x10 = self._preview_dpi_x10()
        missing = [
            page_num
            for page_num in ordered_pages
            if (page_num, preview_dpi_x10) not in self._preview_cache
        ]
        if not missing:
            return
        self._render_worker.request_previews(self._session_id, preview_dpi_x10, missing)

    def _missing_tile_specs(self, page_num: int) -> list[TileSpec]:
        if not (0 <= page_num < len(self._page_items)):
            return []
        page = self._page_items[page_num]
        tile_span = self._tile_span_points()
        visible_clip = self._visible_clip_for_page(page.rect)
        if visible_clip is None:
            return []
        visible_clip = self._expanded_prefetch_clip(page.rect, visible_clip, tile_span)

        start_col = max(0, int(visible_clip.left() // tile_span) - self.TILE_BUFFER)
        end_col = int(visible_clip.right() // tile_span) + self.TILE_BUFFER
        start_row = max(0, int(visible_clip.top() // tile_span) - self.TILE_BUFFER)
        end_row = int(visible_clip.bottom() // tile_span) + self.TILE_BUFFER

        center = self._viewport_center_scene()
        center_x = center.x() - page.rect.left() if center is not None else page.rect.width() / 2.0
        center_y = center.y() - page.rect.top() if center is not None else page.rect.height() / 2.0
        center_col = int(center_x // tile_span)
        center_row = int(center_y // tile_span)
        dx_dir = 1 if self._scroll_dx > 1.0 else -1 if self._scroll_dx < -1.0 else 0
        dy_dir = 1 if self._scroll_dy > 1.0 else -1 if self._scroll_dy < -1.0 else 0

        expected_keys: set[tuple[int, int]] = set()
        pending: list[tuple[tuple[int, int, int, int, int], TileSpec]] = []
        for row in range(start_row, end_row + 1):
            top = row * tile_span
            if top >= page.rect.height():
                continue
            for col in range(start_col, end_col + 1):
                left = col * tile_span
                if left >= page.rect.width():
                    continue
                expected_keys.add((row, col))
                clip_rect = (
                    left,
                    top,
                    min(page.rect.width(), left + tile_span),
                    min(page.rect.height(), top + tile_span),
                )
                forward_row_penalty = 0
                if dy_dir > 0:
                    forward_row_penalty = 0 if row >= center_row else 2
                elif dy_dir < 0:
                    forward_row_penalty = 0 if row <= center_row else 2
                forward_col_penalty = 0
                if dx_dir > 0:
                    forward_col_penalty = 0 if col >= center_col else 1
                elif dx_dir < 0:
                    forward_col_penalty = 0 if col <= center_col else 1
                pending.append(
                    (
                        (
                            0 if page_num == self._current_page else 1,
                            forward_row_penalty,
                            forward_col_penalty,
                            abs(row - center_row) + abs(col - center_col),
                            row + col,
                        ),
                        (page_num, row, col, clip_rect),
                    )
                )
        self._prepare_pending_tiles(page, expected_keys, tile_span)
        for row, col in expected_keys:
            clip_rect = self._clip_rect_for_tile(page_num, row, col, self._dpi_x10())
            if clip_rect is None:
                continue
            key = (page_num, self._dpi_x10(), row, col)
            if key in self._tile_cache:
                tile = self._ensure_pending_tile_item(page, row, col, clip_rect)
                tile.set_pixmap(self._tile_cache[key], self._dpi_x10())
                tile.item.setVisible(False)
                page.pending_loaded.add((row, col))
        pending.sort(key=lambda item: item[0])
        self._commit_pending_tiles_if_ready(page)
        return [spec for _, spec in pending]

    def _visible_clip_for_page(self, page_rect: QRectF) -> QRectF | None:
        scene_rect = self._viewport_scene_rect()
        if scene_rect is None:
            return None
        if not page_rect.intersects(scene_rect):
            return None
        clipped = page_rect.intersected(scene_rect)
        local = QRectF(
            max(0.0, clipped.left() - page_rect.left()),
            max(0.0, clipped.top() - page_rect.top()),
            clipped.width(),
            clipped.height(),
        )
        if local.isEmpty():
            return None
        return local

    def _expanded_prefetch_clip(
        self,
        page_rect: QRectF,
        local_clip: QRectF,
        tile_span: float,
    ) -> QRectF:
        extra_left = tile_span * (1 + max(0, -self._horizontal_prefetch_bias()))
        extra_right = tile_span * (1 + max(0, self._horizontal_prefetch_bias()))
        extra_top = tile_span * (1 + max(0, -self._vertical_prefetch_bias()))
        extra_bottom = tile_span * (2 + max(0, self._vertical_prefetch_bias()))
        left = max(0.0, local_clip.left() - extra_left)
        top = max(0.0, local_clip.top() - extra_top)
        right = min(page_rect.width(), local_clip.right() + extra_right)
        bottom = min(page_rect.height(), local_clip.bottom() + extra_bottom)
        return QRectF(left, top, max(0.0, right - left), max(0.0, bottom - top))

    def _tile_span_points(self) -> float:
        return self.TILE_PIXEL_SIZE * 72.0 / self._dpi

    def _vertical_prefetch_bias(self) -> int:
        if self._scroll_dy > 1.0:
            return 2
        if self._scroll_dy < -1.0:
            return -1
        return 0

    def _horizontal_prefetch_bias(self) -> int:
        if self._scroll_dx > 1.0:
            return 1
        if self._scroll_dx < -1.0:
            return -1
        return 0

    def _page_prefetch_bias(self) -> int:
        if self._scroll_dy > 1.0:
            return 2
        if self._scroll_dy < -1.0:
            return -1
        return 0

    def _ensure_tile_item(
        self,
        page: _PageItem,
        row: int,
        col: int,
        clip_rect: tuple[float, float, float, float],
    ) -> _TileItem:
        key = (row, col)
        if key in page.tiles:
            return page.tiles[key]
        left, top, right, bottom = clip_rect
        local_rect = QRectF(left, top, right - left, bottom - top)
        item = QGraphicsPixmapItem(page.frame)
        item.setPos(local_rect.topLeft())
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setZValue(1)
        tile = _TileItem(row, col, local_rect, item)
        page.tiles[key] = tile
        return tile

    def _ensure_pending_tile_item(
        self,
        page: _PageItem,
        row: int,
        col: int,
        clip_rect: tuple[float, float, float, float],
    ) -> _TileItem:
        key = (row, col)
        if key in page.pending_tiles:
            return page.pending_tiles[key]
        left, top, right, bottom = clip_rect
        local_rect = QRectF(left, top, right - left, bottom - top)
        item = QGraphicsPixmapItem(page.frame)
        item.setPos(local_rect.topLeft())
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setZValue(1.25)
        item.setVisible(False)
        tile = _TileItem(row, col, local_rect, item)
        page.pending_tiles[key] = tile
        return tile

    def _prepare_pending_tiles(
        self,
        page: _PageItem,
        expected_keys: set[tuple[int, int]],
        tile_span: float,
    ) -> None:
        target_dpi = self._dpi_x10()
        if (
            page.pending_dpi_x10 == target_dpi
            and page.pending_expected == expected_keys
        ):
            return
        page.drop_pending_tiles()
        page.pending_dpi_x10 = target_dpi
        page.pending_expected = set(expected_keys)
        page.pending_loaded = set()

    def _commit_pending_tiles_if_ready(self, page: _PageItem) -> None:
        if not page.pending_expected:
            return
        if page.pending_loaded != page.pending_expected:
            return
        for tile in page.tiles.values():
            scene = tile.item.scene()
            if scene is not None:
                scene.removeItem(tile.item)
        page.tiles = {}
        for key, tile in page.pending_tiles.items():
            tile.item.setVisible(True)
            page.tiles[key] = tile
        page.pending_tiles = {}
        page.pending_expected = set()
        page.pending_loaded = set()
        page.pending_dpi_x10 = None

    def _on_preview_rendered(
        self,
        session_id: int,
        page_num: int,
        dpi_x10: int,
        image: QImage,
    ) -> None:
        if session_id != self._session_id:
            return
        if not (0 <= page_num < len(self._page_items)):
            return
        pixmap = QPixmap.fromImage(image)
        cache_key = (page_num, dpi_x10)
        self._preview_cache.pop(cache_key, None)
        self._preview_cache[cache_key] = pixmap
        while len(self._preview_cache) > max(32, self.CACHE_LIMIT // 8):
            self._preview_cache.popitem(last=False)
        self._page_items[page_num].set_preview(pixmap, dpi_x10)
        self.viewport().update()

    def _on_tile_rendered(
        self,
        session_id: int,
        page_num: int,
        dpi_x10: int,
        row: int,
        col: int,
        image: QImage,
    ) -> None:
        if session_id != self._session_id:
            return
        if not (0 <= page_num < len(self._page_items)):
            return
        clip_rect = self._clip_rect_for_tile(page_num, row, col, dpi_x10)
        if clip_rect is None:
            return
        page = self._page_items[page_num]
        if page.pending_dpi_x10 != dpi_x10:
            return
        tile = self._ensure_pending_tile_item(page, row, col, clip_rect)
        pixmap = QPixmap.fromImage(image)
        cache_key = (page_num, dpi_x10, row, col)
        self._cache_touch(cache_key, pixmap)
        tile.set_pixmap(pixmap, dpi_x10)
        tile.item.setVisible(False)
        page.pending_loaded.add((row, col))
        self._commit_pending_tiles_if_ready(page)
        self.viewport().update()

    def _clip_rect_for_tile(
        self,
        page_num: int,
        row: int,
        col: int,
        dpi_x10: int,
    ) -> tuple[float, float, float, float] | None:
        if not (0 <= page_num < len(self._page_items)):
            return None
        page_rect = self._page_items[page_num].rect
        tile_span = self.TILE_PIXEL_SIZE * 72.0 / (dpi_x10 / 10.0)
        left = col * tile_span
        top = row * tile_span
        if left >= page_rect.width() or top >= page_rect.height():
            return None
        return (
            left,
            top,
            min(page_rect.width(), left + tile_span),
            min(page_rect.height(), top + tile_span),
        )

    def _on_render_failed(
        self,
        session_id: int,
        page_num: int,
        row: int,
        col: int,
        kind: str,
        error: str,
    ) -> None:
        if session_id != self._session_id:
            return
        _log.warning(
            "PDFViewer: render failed kind=%s page=%s row=%s col=%s error=%s",
            kind,
            page_num + 1,
            row,
            col,
            error,
        )

    def _dpi_x10(self) -> int:
        return int(round(self._dpi * 10))

    def _preview_dpi_x10(self) -> int:
        return int(round(min(self._dpi, self.PREVIEW_DPI) * 10))

    def _cache_touch(self, key: TileKey, pixmap: QPixmap) -> None:
        self._tile_cache.pop(key, None)
        self._tile_cache[key] = pixmap
        while len(self._tile_cache) > self.CACHE_LIMIT:
            self._tile_cache.popitem(last=False)

    def _drop_page_cache(self, page_num: int) -> None:
        preview_keys = [key for key in self._preview_cache if key[0] == page_num]
        for key in preview_keys:
            self._preview_cache.pop(key, None)
        keys = [key for key in self._tile_cache if key[0] == page_num]
        for key in keys:
            self._tile_cache.pop(key, None)
        if 0 <= page_num < len(self._page_items):
            self._page_items[page_num].clear_preview()
            self._page_items[page_num].drop_tiles()
