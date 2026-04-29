from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Condition
from threading import Lock
from typing import TYPE_CHECKING

import fitz
from PySide6.QtCore import QThread
from PySide6.QtCore import Signal
from PySide6.QtGui import QImage

from pdftranslator.logging_config import get_logger
from pdftranslator.pdf.engine import PDFEngine

if TYPE_CHECKING:
    from collections.abc import Iterable

_log = get_logger(__name__)


@dataclass(frozen=True)
class RenderTask:
    session_id: int
    page_num: int
    dpi_x10: int
    kind: str
    row: int = -1
    col: int = -1
    clip_rect: tuple[float, float, float, float] | None = None


class RenderWorker(QThread):
    preview_rendered = Signal(int, int, int, QImage)
    tile_rendered = Signal(int, int, int, int, int, QImage)
    render_failed = Signal(int, int, int, int, str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cv = Condition(Lock())
        self._pending: OrderedDict[tuple[int, int, int, int, str], RenderTask] = OrderedDict()
        self._running = True
        self._pdf_path: str | None = None
        self._engine: PDFEngine | None = None

    def set_pdf_path(self, path: str | None) -> None:
        with self._cv:
            self._pdf_path = path
            self._pending.clear()
            self._close_engine_locked()
            self._cv.notify_all()

    def request_tiles(
        self,
        session_id: int,
        dpi_x10: int,
        tiles: Iterable[tuple[int, int, int, tuple[float, float, float, float]]],
    ) -> None:
        with self._cv:
            for page_num, row, col, clip_rect in tiles:
                key = (session_id, page_num, row, col, "tile")
                self._pending.pop(key, None)
                self._pending[key] = RenderTask(
                    session_id=session_id,
                    page_num=page_num,
                    dpi_x10=dpi_x10,
                    kind="tile",
                    row=row,
                    col=col,
                    clip_rect=clip_rect,
                )
            self._cv.notify_all()

    def request_previews(
        self,
        session_id: int,
        dpi_x10: int,
        pages: Iterable[int],
    ) -> None:
        with self._cv:
            for page_num in pages:
                key = (session_id, page_num, -1, -1, "preview")
                self._pending.pop(key, None)
                self._pending[key] = RenderTask(
                    session_id=session_id,
                    page_num=page_num,
                    dpi_x10=dpi_x10,
                    kind="preview",
                )
            self._cv.notify_all()

    def drop_session(self, session_id: int) -> None:
        with self._cv:
            keys = [key for key in self._pending if key[0] == session_id]
            for key in keys:
                self._pending.pop(key, None)

    def stop(self) -> None:
        with self._cv:
            self._running = False
            self._pending.clear()
            self._cv.notify_all()

    def run(self) -> None:
        while True:
            task = self._next_task()
            if task is None:
                break
            try:
                if task.kind == "preview":
                    image = self._render_preview(task)
                    self.preview_rendered.emit(
                        task.session_id,
                        task.page_num,
                        task.dpi_x10,
                        image,
                    )
                else:
                    image = self._render_tile(task)
                    self.tile_rendered.emit(
                        task.session_id,
                        task.page_num,
                        task.dpi_x10,
                        task.row,
                        task.col,
                        image,
                    )
            except Exception as exc:
                _log.exception(
                    "RenderWorker: failed kind=%s page=%s row=%s col=%s dpi=%s path=%s",
                    task.kind,
                    task.page_num,
                    task.row,
                    task.col,
                    task.dpi_x10 / 10.0,
                    self._pdf_path,
                )
                self.render_failed.emit(
                    task.session_id,
                    task.page_num,
                    task.row,
                    task.col,
                    task.kind,
                    str(exc),
                )

        with self._cv:
            self._close_engine_locked()

    def _next_task(self) -> RenderTask | None:
        with self._cv:
            while self._running and not self._pending:
                self._cv.wait()
            if not self._running:
                return None
            _, task = self._pending.popitem(last=False)
            return task

    def _render_preview(self, task: RenderTask) -> QImage:
        engine = self._ensure_engine()
        pixmap = engine.render_page_pixmap(task.page_num, task.dpi_x10 / 10.0)
        return self._pixmap_to_qimage(pixmap)

    def _render_tile(self, task: RenderTask) -> QImage:
        engine = self._ensure_engine()
        if task.clip_rect is None:
            raise RuntimeError("Tile render task missing clip_rect")
        clip = fitz.Rect(*task.clip_rect)
        pixmap = engine.render_page_pixmap(task.page_num, task.dpi_x10 / 10.0, clip=clip)
        return self._pixmap_to_qimage(pixmap)

    @staticmethod
    def _pixmap_to_qimage(pixmap: fitz.Pixmap) -> QImage:
        fmt = (
            QImage.Format.Format_RGBA8888
            if pixmap.n >= 4
            else QImage.Format.Format_RGB888
        )
        image = QImage(
            pixmap.samples,
            pixmap.width,
            pixmap.height,
            pixmap.stride,
            fmt,
        )
        return image.copy()

    def _ensure_engine(self) -> PDFEngine:
        with self._cv:
            if not self._pdf_path:
                raise RuntimeError("RenderWorker: PDF path is not configured")
            if self._engine is None:
                _log.info("RenderWorker: opening PDF %s", self._pdf_path)
                self._engine = PDFEngine(self._pdf_path)
            return self._engine

    def _close_engine_locked(self) -> None:
        if self._engine is not None:
            try:
                self._engine.close()
            except Exception as exc:
                _log.warning("RenderWorker: engine close failed: %s", exc)
            self._engine = None
