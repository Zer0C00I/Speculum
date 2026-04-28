from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QFileDialog, QHBoxLayout,
    QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSpinBox, QSplitter, QStatusBar, QTextEdit, QVBoxLayout, QWidget,
)

from pdftranslator.ai.anthropic import AnthropicTranslator
from pdftranslator.ai.deepseek import DeepSeekTranslator
from pdftranslator.ai.base import AbstractTranslator
from pdftranslator.babeldoc_runner import babeldoc_provider_config, uses_babeldoc
from pdftranslator.config import Config
from pdftranslator.gui.pdf_viewer import PDFViewer
from pdftranslator.gui.settings_dialog import SettingsDialog
from pdftranslator.gui.translate_worker import TranslateWorker
from pdftranslator.logging_config import get_logger, gui_handler
from pdftranslator.pdf.engine import PDFEngine
from pdftranslator.translation_job import TranslationJob

_log = get_logger(__name__)

LANGUAGE_NAMES = {
    "en": "English", "ru": "Russian", "de": "German", "fr": "French",
    "es": "Spanish", "it": "Italian", "zh": "Chinese", "ja": "Japanese",
    "ko": "Korean", "pt": "Portuguese", "ar": "Arabic", "tr": "Turkish",
    "pl": "Polish", "uk": "Ukrainian", "nl": "Dutch",
}

PROVIDER_NAMES = {
    "babeldoc-deepseek": "BabelDOC + DeepSeek",
    "babeldoc-openai": "BabelDOC + OpenAI",
    "deepseek": "DeepSeek (Legacy Overlay)",
    "anthropic": "Anthropic Claude (Legacy Overlay)",
}

MODE_NAMES = {
    "window": "Window ±1",
    "single": "Single Page",
    "track": "Tracking Mode",
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        _log.info("=== Starting PDF Translator GUI ===")
        self.setWindowTitle("PDF Translator")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)

        self._engine_orig: PDFEngine | None = None
        self._engine_trans: PDFEngine | None = None
        self._display_trans: PDFEngine | None = None
        self._translator: AbstractTranslator | None = None
        self._worker: TranslateWorker | None = None
        self._output_path: Path | None = None
        self._session_dir: Path | None = None
        self._last_batch_done: float = 0
        self._translated_pages: set[int] = set()
        self._tracking_active = False
        self._active_job_pages: list[int] = []

        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(800)
        self._settle_timer.timeout.connect(self._on_settle)
        self._settle_page: int = 0

        self._setup_toolbar()
        self._setup_panels()
        self._setup_statusbar()
        self._setup_log_dock()

        _log.info("GUI initialized")

    def _setup_toolbar(self) -> None:
        toolbar = QHBoxLayout()

        self._btn_load = QPushButton("Load PDF")
        self._btn_load.clicked.connect(self._on_load)

        self._source_lang = QComboBox()
        self._target_lang = QComboBox()
        for code, name in LANGUAGE_NAMES.items():
            if code != Config.default_target_lang():
                self._source_lang.addItem(name, code)
        self._target_lang.addItem(LANGUAGE_NAMES.get(Config.default_target_lang(), "Russian"), Config.default_target_lang())
        self._target_lang.addItems([n for c, n in LANGUAGE_NAMES.items() if c != Config.default_target_lang()])
        self._source_lang.setCurrentIndex(0)

        self._provider = QComboBox()
        for code, name in PROVIDER_NAMES.items():
            self._provider.addItem(name, code)
        default_idx = self._provider.findData(Config.default_provider())
        if default_idx >= 0:
            self._provider.setCurrentIndex(default_idx)

        self._mode = QComboBox()
        for code, name in MODE_NAMES.items():
            self._mode.addItem(name, code)
        self._mode.setCurrentIndex(0)
        self._mode.currentIndexChanged.connect(self._on_mode_changed)

        self._chk_verbose = QCheckBox("Verbose")
        self._chk_original = QCheckBox("Original")
        self._chk_original.setChecked(True)
        self._chk_translated = QCheckBox("Translated")
        self._chk_translated.setChecked(True)

        self._btn_translate = QPushButton("Translate")
        self._btn_translate.setEnabled(False)
        self._btn_translate.clicked.connect(self._on_translate)

        self._btn_stop = QPushButton("Stop")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._on_stop)

        self._btn_save = QPushButton("Save As...")
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._on_save)

        self._btn_settings = QPushButton("Settings")
        self._btn_settings.clicked.connect(self._on_settings)

        self._btn_log = QPushButton("Log")
        self._btn_log.setCheckable(True)
        self._btn_log.toggled.connect(self._on_log_toggle)

        toolbar.addWidget(self._btn_load)
        toolbar.addWidget(QLabel("  From:"))
        toolbar.addWidget(self._source_lang)
        toolbar.addWidget(QLabel("To:"))
        toolbar.addWidget(self._target_lang)
        toolbar.addWidget(QLabel("  AI:"))
        toolbar.addWidget(self._provider)
        toolbar.addWidget(QLabel("  Mode:"))
        toolbar.addWidget(self._mode)
        toolbar.addSpacing(20)
        toolbar.addWidget(self._btn_translate)
        toolbar.addWidget(self._btn_stop)
        toolbar.addWidget(self._btn_save)
        toolbar.addStretch()
        toolbar.addWidget(self._chk_verbose)
        toolbar.addWidget(self._btn_log)
        toolbar.addWidget(self._btn_settings)
        toolbar.addSpacing(8)
        toolbar.addWidget(self._chk_original)
        toolbar.addWidget(self._chk_translated)

        toolbar_widget = QWidget()
        toolbar_widget.setLayout(toolbar)
        top_layout = QVBoxLayout()
        top_layout.setContentsMargins(6, 6, 6, 0)
        top_layout.addWidget(toolbar_widget)

        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addLayout(top_layout)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self._splitter, 1)
        self.setCentralWidget(main_widget)

    def _setup_panels(self) -> None:
        self._original_viewer = PDFViewer()
        self._translated_viewer = PDFViewer()
        self._original_viewer.sync_with(self._translated_viewer)
        self._splitter.addWidget(self._original_viewer)
        self._splitter.addWidget(self._translated_viewer)
        self._splitter.setSizes([600, 600])
        self._chk_original.toggled.connect(self._original_viewer.setVisible)
        self._chk_translated.toggled.connect(self._translated_viewer.setVisible)

    def _setup_statusbar(self) -> None:
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._btn_prev = QPushButton("<"); self._btn_prev.setFixedWidth(28)
        self._btn_prev.clicked.connect(self._on_page_prev); self._btn_prev.setEnabled(False)
        self._btn_next = QPushButton(">"); self._btn_next.setFixedWidth(28)
        self._btn_next.clicked.connect(self._on_page_next); self._btn_next.setEnabled(False)
        self._page_spin = QSpinBox(); self._page_spin.setMinimum(1); self._page_spin.setMaximum(1)
        self._page_spin.setValue(1); self._page_spin.setFixedWidth(60)
        self._page_spin.valueChanged.connect(self._on_page_spin)
        self._page_total_label = QLabel("/ —"); self._page_label = QLabel("Page:")
        self._progress_bar = QProgressBar(); self._progress_bar.setMaximumWidth(250)
        self._progress_bar.setVisible(False)
        self._status_bar.addWidget(self._page_label)
        self._status_bar.addWidget(self._btn_prev)
        self._status_bar.addWidget(self._page_spin)
        self._status_bar.addWidget(self._page_total_label)
        self._status_bar.addWidget(self._btn_next)
        self._status_bar.addPermanentWidget(self._progress_bar)
        self._original_viewer.page_changed.connect(self._on_viewer_page_changed)

    def _setup_log_dock(self) -> None:
        self._log_widget = QTextEdit(); self._log_widget.setReadOnly(True)
        self._log_dock = QDockWidget("Log", self); self._log_dock.setWidget(self._log_widget)
        self._log_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._log_dock)
        self._log_dock.hide()
        handler = gui_handler()
        if handler:
            handler.log_signal.connect(self._on_log_line)
            _log.info("Log dock connected")

    def _on_log_line(self, line: str) -> None:
        self._log_widget.append(line)
        sb = self._log_widget.verticalScrollBar(); sb.setValue(sb.maximum())

    def _on_log_toggle(self, checked: bool) -> None:
        self._log_dock.setVisible(checked)

    def _on_mode_changed(self, _index: int) -> None:
        if self._mode.currentData() != "track":
            self._tracking_active = False

    def _on_page_prev(self) -> None:
        cur = self._original_viewer.current_page()
        if cur > 0: self._navigate_to(cur - 1)

    def _on_page_next(self) -> None:
        if self._engine_orig and self._original_viewer.current_page() < self._engine_orig.page_count - 1:
            self._navigate_to(self._original_viewer.current_page() + 1)

    def _on_page_spin(self, value: int) -> None:
        self._navigate_to(value - 1)

    def _navigate_to(self, page_num: int) -> None:
        self._original_viewer.scroll_to_page(page_num)
        self._translated_viewer.scroll_to_page(page_num)
        self._update_page_display(page_num)
        self._settle_timer.stop()
        self._settle_page = page_num
        self._maybe_translate_ahead(page_num)

    def _on_viewer_page_changed(self, page_num: int) -> None:
        self._update_page_display(page_num)
        self._settle_timer.stop()
        self._settle_page = page_num
        self._settle_timer.start()

    def _update_page_display(self, page_num: int) -> None:
        self._page_spin.blockSignals(True)
        self._page_spin.setValue(page_num + 1)
        self._page_spin.blockSignals(False)

    def _on_settle(self) -> None:
        self._maybe_translate_ahead(self._settle_page)

    def _maybe_translate_ahead(self, page_num: int) -> None:
        if self._mode.currentData() != "track": return
        if not self._tracking_active: return
        if self._worker and self._worker.isRunning(): return
        if not self._engine_orig: return
        import time
        if time.time() - self._last_batch_done < 1.0: return
        if page_num in self._translated_pages:
            return

        pages = [page_num]
        if pages:
            _log.info("Tracking mode: auto-translating page %s", pages)
            self._start_worker(pages)

    # ── MAIN OPERATIONS ──

    def _on_load(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf);;All Files (*)")
        if not file_path: return

        _log.info("Loading PDF: %s", file_path)
        try:
            self._cleanup_engines()
            self._engine_orig = PDFEngine(file_path)
            self._output_path = Path(file_path).with_suffix(".translated.pdf")
            self._make_session_dir(Path(file_path))
            self._tracking_active = False

            self._original_viewer.set_engine(self._engine_orig)
            self._connect_right_viewer()
            self._load_translation_state()

            n = self._engine_orig.page_count
            self._status_bar.showMessage(f"Loaded: {n} pages | session: {self._session_dir.name}", 5000)
            self._btn_translate.setEnabled(True)
            self._btn_save.setEnabled(False)
            self._btn_prev.setEnabled(True)
            self._btn_next.setEnabled(True)
            self._page_spin.setMaximum(n)
            self._page_total_label.setText(f"/ {n}")
            self._progress_bar.setVisible(False)
            self.setWindowTitle(f"PDF Translator — {Path(file_path).name}")
            self._update_page_display(0)
        except Exception as exc:
            _log.exception("Failed to load PDF")
            QMessageBox.critical(self, "Error", f"Failed to open PDF:\n{exc}")

    def _connect_right_viewer(self) -> None:
        self._engine_trans = None
        self._close_display_engine()
        if self._output_path and self._output_path.exists():
            try:
                self._engine_trans = PDFEngine(str(self._output_path))
                self._display_trans = PDFEngine(str(self._output_path))
            except Exception as exc:
                _log.warning("Failed to load existing translation: %s", exc)
                self._engine_trans = None
                self._display_trans = None

        engine = self._display_trans or self._engine_orig
        self._translated_viewer.set_engine(engine)
        self._translated_viewer.scroll_to_page(0)

    def _cleanup_engines(self) -> None:
        seen: set[int] = set()
        for eng in [self._engine_orig, self._engine_trans, self._display_trans]:
            if eng:
                eng_id = id(eng)
                if eng_id in seen:
                    continue
                seen.add(eng_id)
                try: eng.close()
                except Exception as exc: _log.warning("Engine close failed: %s", exc)
        self._engine_orig = None
        self._engine_trans = None
        self._display_trans = None
        self._translated_pages.clear()
        self._active_job_pages = []
        self._tracking_active = False

    def _close_display_engine(self) -> None:
        if not self._display_trans:
            return
        try:
            self._display_trans.close()
        except Exception as exc:
            _log.warning("Display engine close failed: %s", exc)
        self._display_trans = None

    def _make_session_dir(self, src_path: Path) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base = Path.home() / ".pdftranslator" / "output" / f"{src_path.stem}_{ts}"
        base.mkdir(parents=True, exist_ok=True)
        self._session_dir = base
        _log.info("Session dir: %s", base)

    def _save_output(self) -> None:
        if not self._engine_trans: return
        try:
            import tempfile, shutil
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False); tmp.close()
            self._engine_trans._doc.save(tmp.name, garbage=4, deflate=True)
            shutil.move(tmp.name, str(self._output_path))
            if self._session_dir:
                shutil.copy2(str(self._output_path), str(self._session_dir / self._output_path.name))
        except Exception as exc:
            _log.warning("Save failed: %s", exc)

    def _save_session_log(self) -> None:
        if not self._session_dir: return
        try:
            from pdftranslator.logging_config import LOG_FILE
            import shutil
            if LOG_FILE.exists():
                shutil.copy2(str(LOG_FILE), str(self._session_dir / "session.log"))
        except Exception as exc:
            _log.warning("Log copy failed: %s", exc)

    def _get_provider_key(self, provider: str) -> str:
        try:
            if uses_babeldoc(provider): return babeldoc_provider_config(provider).api_key
            if provider == "deepseek": return Config.deepseek_api_key()
            if provider == "anthropic": return Config.anthropic_api_key()
        except Exception as exc:
            _log.warning("Failed to get provider key for %s: %s", provider, exc)
        return ""

    def _on_translate(self) -> None:
        if not self._engine_orig: return

        provider_code = self._provider.currentData()
        if not self._get_provider_key(provider_code):
            name = PROVIDER_NAMES.get(provider_code, provider_code)
            if QMessageBox.question(self, "API Key Missing", f"No key for {name}. Open Settings?") == QMessageBox.StandardButton.Yes:
                self._on_settings()
            return

        try:
            self._translator = self._build_translator(provider_code)
            self._ensure_output_engine(provider_code)
        except Exception as exc:
            if uses_babeldoc(provider_code):
                QMessageBox.critical(self, "Error", str(exc))
            else:
                _log.exception("Failed to create translation backend")
                QMessageBox.critical(self, "Error", f"Failed to create output document:\n{exc}")
            return

        pages = self._determine_pages(provider_code)
        self._tracking_active = self._mode.currentData() == "track"

        if not pages:
            if self._tracking_active:
                self._status_bar.showMessage("Tracking mode armed. Open a page that is not translated yet.", 4000)
            else:
                self._status_bar.showMessage("All selected pages are already translated", 3000)
            self._btn_translate.setEnabled(True)
            self._btn_load.setEnabled(True)
            return

        self._start_worker(pages)

    def _ensure_output_engine(self, provider_code: str) -> None:
        if self._engine_trans and self._engine_trans.page_count > 0:
            _log.info("Reusing output engine: %d pages", self._engine_trans.page_count)
            return

        if self._engine_trans:
            try: self._engine_trans.close()
            except Exception as exc: _log.warning("Output engine close failed: %s", exc)

        if uses_babeldoc(provider_code):
            if not self._engine_orig or not self._output_path:
                raise RuntimeError("Missing source PDF for BabelDOC output initialization")
            import shutil
            shutil.copy2(self._engine_orig._path, self._output_path)
            self._engine_trans = PDFEngine(str(self._output_path))
            self._save_translation_state()
            _log.info("Initialized BabelDOC output from source PDF")
            return

        import fitz
        self._engine_trans = PDFEngine(doc=fitz.open())
        _log.info("Created new output document")

    def _build_translator(self, provider_code: str) -> AbstractTranslator | None:
        if provider_code == "deepseek":
            return DeepSeekTranslator()
        if provider_code == "anthropic":
            return AnthropicTranslator()
        if uses_babeldoc(provider_code):
            return None
        raise ValueError(f"Unknown provider: {provider_code}")

    def _determine_pages(self, provider_code: str) -> list[int]:
        if not self._engine_orig:
            return []
        current = self._original_viewer.current_page()
        mode = self._mode.currentData()
        if mode == "single":
            candidates = [current]
        elif mode == "window":
            candidates = [p for p in [current - 1, current, current + 1] if 0 <= p < self._engine_orig.page_count]
        else:
            candidates = [current]
        return [page for page in candidates if page not in self._translated_pages]

    def _start_worker(self, pages: list[int]) -> None:
        if not self._engine_orig:
            _log.error("Cannot start worker: missing source engine")
            return

        self._settle_timer.stop()

        provider_code = self._provider.currentData()
        use_babeldoc = uses_babeldoc(provider_code)
        self._active_job_pages = list(pages)
        self._btn_translate.setEnabled(False)
        self._btn_load.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._progress_bar.setVisible(True)
        self._progress_bar.setMaximum(len(pages))
        self._progress_bar.setValue(0)
        self._status_bar.showMessage(
            f"Translating {len(pages)} page(s): {self._page_label_text(pages)}"
        )

        worker_src = PDFEngine(self._engine_orig._path) if self._engine_orig._path else self._engine_orig
        job = TranslationJob(
            provider=provider_code,
            source_lang=self._source_lang.currentData(),
            target_lang=self._target_lang.currentData(),
            page_numbers=pages,
            output_path=str(self._output_path),
            babeldoc_provider=babeldoc_provider_config(provider_code) if use_babeldoc else None,
            session_dir=str(self._session_dir) if self._session_dir else None,
            verbose=self._chk_verbose.isChecked(),
            page_window_label=self._page_label_text(pages),
        )
        self._worker = TranslateWorker(
            worker_src,
            self._engine_trans,
            self._translator,
            job,
        )
        self._worker.page_progress.connect(self._on_page_done)
        self._worker.translation_finished.connect(self._on_translation_done)
        self._worker.error_occurred.connect(self._on_translation_error)
        self._worker.start()

    def _on_page_done(self, done: int, total: int, page_num: int) -> None:
        self._progress_bar.setValue(done)
        n = self._engine_orig.page_count if self._engine_orig else total
        self._page_total_label.setText(f"/ {n}  [{done}/{total}]")
        self._mark_pages_translated([page_num])

        self._reload_translated_output(refresh_output_engine=uses_babeldoc(self._provider.currentData()))
        self._status_bar.showMessage(f"Built page {page_num + 1}", 2000)

    def _on_translation_done(self) -> None:
        _log.info("Translation batch complete")
        self._btn_translate.setEnabled(True)
        self._btn_load.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_save.setEnabled(True)
        self._progress_bar.setVisible(False)
        import time
        self._last_batch_done = time.time()
        self._reload_translated_output(refresh_output_engine=uses_babeldoc(self._provider.currentData()))
        self._save_translation_state()
        if self._tracking_active:
            self._status_bar.showMessage("Tracking mode active — open another page to translate it.", 0)
        elif self._engine_trans and self._engine_trans.page_count > 0:
            self._status_bar.showMessage("Done — scroll, translate more, or Save As...", 0)
        else:
            self._status_bar.showMessage("Done", 0)
        self._active_job_pages = []

    def _reload_translated_output(self, refresh_output_engine: bool = False) -> None:
        if not self._output_path or not self._output_path.exists():
            return
        try:
            if refresh_output_engine and self._engine_trans:
                try:
                    self._engine_trans.close()
                except Exception as exc:
                    _log.warning("Output engine close failed: %s", exc)
            if refresh_output_engine:
                self._engine_trans = PDFEngine(str(self._output_path))
            self._close_display_engine()
            self._display_trans = PDFEngine(str(self._output_path))
            saved = self._original_viewer.verticalScrollBar().value()
            tv = self._translated_viewer
            tv.verticalScrollBar().blockSignals(True)
            tv.set_engine(self._display_trans)
            tv.verticalScrollBar().setValue(saved)
            tv.verticalScrollBar().blockSignals(False)
        except Exception as exc:
            _log.warning("Display reload failed: %s", exc)

    def _on_translation_error(self, error: str) -> None:
        _log.error("Translation error: %s", error)
        self._btn_translate.setEnabled(True)
        self._btn_load.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._progress_bar.setVisible(False)
        self._tracking_active = False
        self._active_job_pages = []
        self._status_bar.showMessage(f"Error: {error}", 10000)
        QMessageBox.critical(self, "Translation Error", error)

    def _on_stop(self) -> None:
        self._tracking_active = False
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._status_bar.showMessage("Stopping...", 3000)
        else:
            self._status_bar.showMessage("Stopped", 2000)

    def _on_save(self) -> None:
        engine = self._engine_trans or self._engine_orig
        if not engine: return
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Translated PDF",
            str(self._output_path or "translated.pdf"), "PDF Files (*.pdf)")
        if not file_path: return
        try:
            import tempfile, shutil
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.close()
            engine._doc.save(tmp.name, garbage=4, deflate=True)
            shutil.move(tmp.name, file_path)
            self._status_bar.showMessage(f"Saved: {file_path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{exc}")

    def _on_settings(self) -> None:
        SettingsDialog(self).exec()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if (
            not Config.deepseek_api_key()
            and not Config.openai_api_key()
            and not Config.anthropic_api_key()
        ):
            if QMessageBox.warning(self, "API Keys Missing",
                "No API keys configured.\nOpen Settings now?") == QMessageBox.StandardButton.Yes:
                self._on_settings()

    def closeEvent(self, event) -> None:
        if self._worker:
            self._worker.cancel()
            if self._worker.isRunning():
                self._worker.wait(5000)
            self._worker = None
        self._save_translation_state()
        self._save_session_log()
        self._cleanup_engines()
        super().closeEvent(event)

    def _page_label_text(self, pages: list[int]) -> str:
        return ", ".join(str(page + 1) for page in pages)

    def _mark_pages_translated(self, pages: list[int]) -> None:
        self._translated_pages.update(page for page in pages if page >= 0)

    def _load_translation_state(self) -> None:
        self._translated_pages.clear()
        if not self._engine_orig or not self._output_path:
            return
        state_path = Config.translation_state_path(self._output_path)
        if state_path.exists():
            try:
                data = json.loads(state_path.read_text())
                pages = data.get("translated_pages", [])
                self._translated_pages = {int(page) for page in pages if 0 <= int(page) < self._engine_orig.page_count}
                return
            except Exception as exc:
                _log.warning("Failed to load translation state: %s", exc)
        if self._output_path.exists():
            self._translated_pages = set(range(self._engine_orig.page_count))

    def _save_translation_state(self) -> None:
        if not self._output_path:
            return
        state_path = Config.translation_state_path(self._output_path)
        try:
            state_path.write_text(json.dumps({
                "translated_pages": sorted(self._translated_pages),
            }, indent=2))
        except Exception as exc:
            _log.warning("Failed to save translation state: %s", exc)
