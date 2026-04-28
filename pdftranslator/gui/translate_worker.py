from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import fitz
from PySide6.QtCore import QThread, Signal

from pdftranslator.babeldoc_runner import BabelDocError, BabelDocRequest, run_babeldoc, uses_babeldoc
from pdftranslator.logging_config import get_logger
from pdftranslator.translation_job import TranslationJob

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
        src_engine: PDFEngine | None,
        dst_engine: PDFEngine | None,
        translator: AbstractTranslator | None,
        job: TranslationJob,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._src = src_engine
        self._dst = dst_engine
        self._translator = translator
        self._job = job
        self._cancelled = False

    def run(self) -> None:
        _log.info("TranslateWorker: starting provider=%s", self._job.provider)
        try:
            if uses_babeldoc(self._job.provider):
                self._run_babeldoc()
            else:
                self._run_legacy_overlay()

            _log.info("TranslateWorker: finished all pages")
            self.translation_finished.emit()
        except Exception as exc:
            _log.exception("TranslateWorker: error")
            self.error_occurred.emit(str(exc))
        finally:
            try:
                if self._src:
                    self._src.close()
            except Exception as exc:
                _log.warning("TranslateWorker: failed to close src engine: %s", exc)

    def _run_babeldoc(self) -> None:
        if not self._src or not self._src._path or not self._dst:
            raise BabelDocError("BabelDOC backend requires a source PDF path")
        if not self._job.babeldoc_provider:
            raise BabelDocError("Missing BabelDOC provider configuration")
        if self._cancelled:
            raise BabelDocError("Translation cancelled")

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        request = BabelDocRequest(
            input_path=self._src._path,
            output_path=tmp.name,
            source_lang=self._job.source_lang,
            target_lang=self._job.target_lang,
            page_numbers=self._job.page_numbers,
            provider=self._job.babeldoc_provider,
            session_dir=self._job.session_dir,
            verbose=self._job.verbose,
        )
        try:
            result_path = run_babeldoc(
                request,
                log=_log.info,
                is_cancelled=lambda: self._cancelled,
            )
            translated_doc = fitz.open(str(result_path))
            try:
                self._dst.replace_pages_from_document(translated_doc, self._job.page_numbers)
            finally:
                translated_doc.close()
            self._save_to_disk()
            total = len(self._job.page_numbers)
            for idx, page_num in enumerate(self._job.page_numbers):
                self.page_progress.emit(idx + 1, total, page_num)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    def _run_legacy_overlay(self) -> None:
        if not self._src or not self._dst or not self._translator:
            raise RuntimeError("Legacy overlay backend requires source, destination, and translator")

        total = len(self._job.page_numbers)
        for idx, page_num in enumerate(self._job.page_numbers):
            if self._cancelled:
                _log.info("TranslateWorker: cancelled at page %d", page_num)
                break

            try:
                self._dst.build_translated_page(
                    self._src,
                    page_num,
                    self._translator,
                    self._job.source_lang,
                    self._job.target_lang,
                    verbose=self._job.verbose,
                )
            except Exception as exc:
                _log.exception("Page %d build FAILED", page_num)
                self.error_occurred.emit(f"Page {page_num}: {exc}")
                continue

            self._save_to_disk()
            self.page_progress.emit(idx + 1, total, page_num)

    def _save_to_disk(self) -> None:
        if not self._dst:
            return
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.close()
            self._dst._doc.save(tmp.name, garbage=4, deflate=True)
            shutil.move(tmp.name, self._job.output_path)
            if self._job.session_dir:
                shutil.copy2(self._job.output_path, str(Path(self._job.session_dir) / Path(self._job.output_path).name))
            _log.debug("TranslateWorker: saved to %s", self._job.output_path)
        except Exception as exc:
            _log.warning("TranslateWorker: save failed: %s", exc)

    def cancel(self) -> None:
        _log.info("TranslateWorker: cancel requested")
        self._cancelled = True
