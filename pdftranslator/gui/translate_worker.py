from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

from pdftranslator.logging_config import get_logger

if TYPE_CHECKING:
    from pdftranslator.ai.base import AbstractTranslator
    from pdftranslator.pdf.engine import PDFEngine

_log = get_logger(__name__)


class TranslateWorker(QThread):
    page_progress = Signal(int, int, int)
    translation_finished = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        src_engine: PDFEngine,
        dst_engine: PDFEngine,
        translator: AbstractTranslator,
        source_lang: str,
        target_lang: str,
        provider: str,
        page_numbers: list[int],
        output_path: str,
        session_dir: str | None = None,
        verbose: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._src = src_engine
        self._dst = dst_engine
        self._translator = translator
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._provider = provider
        self._page_numbers = page_numbers
        self._output_path = output_path
        self._session_dir = session_dir
        self._verbose = verbose
        self._cancelled = False

    def run(self) -> None:
        _log.info("TranslateWorker: starting %d pages", len(self._page_numbers))
        try:
            total = len(self._page_numbers)
            for idx, page_num in enumerate(self._page_numbers):
                if self._cancelled:
                    _log.info("TranslateWorker: cancelled at page %d", page_num)
                    break

                try:
                    self._dst.build_translated_page(
                        self._src, page_num,
                        self._translator, self._source_lang, self._target_lang,
                        verbose=self._verbose,
                    )
                except Exception as exc:
                    _log.exception("Page %d build FAILED", page_num)
                    self.error_occurred.emit(f"Page {page_num}: {exc}")
                    continue

                self._save_to_disk()
                self.page_progress.emit(idx + 1, total, page_num)

            _log.info("TranslateWorker: finished all pages")
            self.translation_finished.emit()
        except Exception as exc:
            _log.exception("TranslateWorker: error")
            self.error_occurred.emit(str(exc))
        finally:
            try:
                self._src.close()
            except Exception as exc:
                _log.warning("TranslateWorker: failed to close src engine: %s", exc)

    def _save_to_disk(self) -> None:
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.close()
            self._dst._doc.save(tmp.name, garbage=4, deflate=True)
            shutil.move(tmp.name, self._output_path)
            if self._session_dir:
                shutil.copy2(self._output_path, str(Path(self._session_dir) / Path(self._output_path).name))
            _log.debug("TranslateWorker: saved to %s", self._output_path)
        except Exception as exc:
            _log.warning("TranslateWorker: save failed: %s", exc)

    def cancel(self) -> None:
        _log.info("TranslateWorker: cancel requested")
        self._cancelled = True
