from __future__ import annotations

import logging
import shutil
import sys
import tempfile
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable

from pdftranslator.config import Config

_BABELDOC_READY = False
_DOC_LAYOUT_MODEL = None
_BABELDOC_LOCK = threading.Lock()


class BabelDocError(RuntimeError):
    pass


@dataclass(frozen=True)
class BabelDocRequest:
    input_path: str
    output_path: str
    source_lang: str
    target_lang: str
    page_numbers: list[int]
    provider: "BabelDocProviderConfig"
    session_dir: str | None = None
    verbose: bool = False


@dataclass(frozen=True)
class BabelDocProviderConfig:
    code: str
    label: str
    api_key: str
    model: str
    base_url: str


def bundled_babeldoc_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "BabelDOC"


def uses_babeldoc(provider: str) -> bool:
    return provider.startswith("babeldoc-")


def run_babeldoc(
    request: BabelDocRequest,
    log: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> Path:
    api_key = request.provider.api_key.strip()
    if not api_key:
        raise BabelDocError(f"{request.provider.label} API key is not set")

    bundle_dir = bundled_babeldoc_dir()
    if not bundle_dir.is_dir():
        raise BabelDocError(f"Bundled BabelDOC directory not found: {bundle_dir}")

    if sys.version_info[:2] != (3, 12):
        raise BabelDocError(
            "In-process BabelDOC integration currently requires Python 3.12."
        )
    if is_cancelled and is_cancelled():
        raise BabelDocError("Translation cancelled")

    output_dir = Path(tempfile.mkdtemp(prefix="pdftranslator-babeldoc-out-"))
    working_root = Path(tempfile.mkdtemp(prefix="pdftranslator-babeldoc-work-"))
    stop_polling = threading.Event()
    requested_cancel = threading.Event()
    pm_cancel_event = threading.Event()

    def log_line(message: str) -> None:
        if log:
            log(f"[BabelDOC] {message}")

    log_line(
        "Preparing request: "
        f"provider={request.provider.code} model={request.provider.model} "
        f"source_lang={request.source_lang} target_lang={request.target_lang} "
        f"pages={_page_numbers_to_ranges(request.page_numbers)}"
    )
    log_line(f"Bundle dir: {bundle_dir}")
    log_line(f"Working root: {working_root}")
    log_line(f"Output dir: {output_dir}")

    poller = threading.Thread(
        target=_poll_cancel,
        args=(is_cancelled, requested_cancel, pm_cancel_event, stop_polling),
        daemon=True,
    )
    poller.start()

    try:
        log_line("Importing BabelDOC modules")
        modules = _load_babeldoc(bundle_dir)
        log_line("Creating OpenAI-compatible translator")
        translator = modules["OpenAITranslator"](
            lang_in=request.source_lang,
            lang_out=request.target_lang,
            model=request.provider.model,
            base_url=request.provider.base_url,
            api_key=api_key,
            ignore_cache=False,
            enable_json_mode_if_requested=False,
            send_temperature=True,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        working_root.mkdir(parents=True, exist_ok=True)
        log_line("Created temp output and working directories")

        log_line("Loading document layout model")
        doc_layout_model = _get_doc_layout_model(modules, log_line)
        log_line("Building TranslationConfig")
        config = modules["TranslationConfig"](
            input_file=request.input_path,
            font=None,
            pages=_page_numbers_to_ranges(request.page_numbers),
            output_dir=output_dir,
            translator=translator,
            term_extraction_translator=translator,
            # App-level "Verbose" should only affect logs, not generate debug-marked PDFs.
            debug=False,
            lang_in=request.source_lang,
            lang_out=request.target_lang,
            no_dual=True,
            no_mono=False,
            qps=5,
            doc_layout_model=doc_layout_model,
            report_interval=0.5,
            min_text_length=5,
            watermark_output_mode=modules["WatermarkOutputMode"].NoWatermark,
            working_dir=working_root,
            auto_extract_glossary=True,
            disable_same_text_fallback=False,
        )

        init_font_mapper = getattr(config.doc_layout_model, "init_font_mapper", None)
        if callable(init_font_mapper):
            log_line("Initializing font mapper")
            init_font_mapper(config)
        else:
            log_line("Font mapper init not provided by model")

        log_line("Creating ProgressMonitor")
        pm = modules["ProgressMonitor"](
            modules["get_translation_stage"](config),
            progress_change_callback=_make_progress_callback(log_line, progress),
            finish_callback=lambda **_kwargs: None,
            cancel_event=pm_cancel_event,
            report_interval=config.report_interval,
        )

        log_line("Starting in-process translation")
        result = modules["do_translate"](pm, config)
        if requested_cancel.is_set():
            raise BabelDocError("Translation cancelled")

        log_line("Translation finished, selecting output PDF")
        result_path = _pick_result_pdf(result)
        log_line(f"Selected output PDF: {result_path}")
        Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result_path, request.output_path)
        log_line(f"Copied result to requested output path: {request.output_path}")
        return Path(request.output_path)
    except Exception as exc:
        if requested_cancel.is_set():
            raise BabelDocError("Translation cancelled") from exc
        if isinstance(exc, BabelDocError):
            raise
        raise BabelDocError(
            "BabelDOC failed in-process. Ensure Python 3.12 is used and its "
            "dependencies from BabelDOC/pyproject.toml are installed."
        ) from exc
    finally:
        stop_polling.set()
        poller.join(timeout=1.0)
        log_line("Cleaning temporary BabelDOC directories")
        with suppress(Exception):
            shutil.rmtree(output_dir)
        with suppress(Exception):
            shutil.rmtree(working_root)


def _poll_cancel(
    is_cancelled: Callable[[], bool] | None,
    requested_cancel: threading.Event,
    pm_cancel_event: threading.Event,
    stop_polling: threading.Event,
) -> None:
    while not stop_polling.is_set():
        if is_cancelled and is_cancelled():
            requested_cancel.set()
            pm_cancel_event.set()
            return
        stop_polling.wait(0.2)


def _load_babeldoc(bundle_dir: Path) -> dict[str, Any]:
    global _BABELDOC_READY
    if not _BABELDOC_READY:
        sys.path.insert(0, str(bundle_dir))
        import babeldoc.format.pdf.high_level as high_level
        from babeldoc.docvision.doclayout import DocLayoutModel
        from babeldoc.format.pdf.translation_config import TranslationConfig
        from babeldoc.format.pdf.translation_config import WatermarkOutputMode
        from babeldoc.progress_monitor import ProgressMonitor
        from babeldoc.translator.translator import OpenAITranslator

        high_level.init()
        _BABELDOC_READY = True
        return {
            "DocLayoutModel": DocLayoutModel,
            "OpenAITranslator": OpenAITranslator,
            "ProgressMonitor": ProgressMonitor,
            "TranslationConfig": TranslationConfig,
            "WatermarkOutputMode": WatermarkOutputMode,
            "do_translate": high_level.do_translate,
            "get_translation_stage": high_level.get_translation_stage,
        }

    import babeldoc.format.pdf.high_level as high_level
    from babeldoc.docvision.doclayout import DocLayoutModel
    from babeldoc.format.pdf.translation_config import TranslationConfig
    from babeldoc.format.pdf.translation_config import WatermarkOutputMode
    from babeldoc.progress_monitor import ProgressMonitor
    from babeldoc.translator.translator import OpenAITranslator

    return {
        "DocLayoutModel": DocLayoutModel,
        "OpenAITranslator": OpenAITranslator,
        "ProgressMonitor": ProgressMonitor,
        "TranslationConfig": TranslationConfig,
        "WatermarkOutputMode": WatermarkOutputMode,
        "do_translate": high_level.do_translate,
        "get_translation_stage": high_level.get_translation_stage,
    }


def _get_doc_layout_model(
    modules: dict[str, Any],
    log: Callable[[str], None] | None = None,
):
    global _DOC_LAYOUT_MODEL
    with _BABELDOC_LOCK:
        if _DOC_LAYOUT_MODEL is None:
            if log:
                log("DocLayoutModel cache miss, loading ONNX model")
            _DOC_LAYOUT_MODEL = modules["DocLayoutModel"].load_onnx()
            if log:
                log("DocLayoutModel loaded and cached")
        elif log:
            log("DocLayoutModel cache hit")
        return _DOC_LAYOUT_MODEL


def _make_progress_callback(
    log: Callable[[str], None],
    progress: Callable[[int, str], None] | None = None,
) -> Callable[..., None]:
    logger = logging.getLogger(__name__)

    def callback(**event: Any) -> None:
        event_type = event.get("type")
        if event_type == "progress_start":
            if progress:
                progress(
                    int(event.get("overall_progress", 0)),
                    f"{event['stage']} started",
                )
            log(
                f"{event['stage']} started ({event['part_index']}/{event['total_parts']})"
            )
        elif event_type == "progress_update":
            if progress:
                progress(
                    int(event.get("overall_progress", 0)),
                    f"{event['stage']}: {event['stage_current']}/{event['stage_total']}",
                )
            log(
                f"{event['stage']}: {event['stage_current']}/{event['stage_total']} "
                f"overall={event['overall_progress']:.1f}%"
            )
        elif event_type == "progress_end":
            if progress:
                progress(
                    int(event.get("overall_progress", 0)),
                    f"{event['stage']} complete",
                )
            log(f"{event['stage']} complete")
        elif event_type == "error":
            logger.error("BabelDOC progress error: %s", event.get("error"))

    return callback


def _pick_result_pdf(result: Any) -> Path:
    candidates = [
        getattr(result, "no_watermark_mono_pdf_path", None),
        getattr(result, "mono_pdf_path", None),
        getattr(result, "no_watermark_dual_pdf_path", None),
        getattr(result, "dual_pdf_path", None),
    ]
    for candidate in candidates:
        if candidate:
            return Path(candidate)
    raise BabelDocError("BabelDOC did not produce an output PDF")


def babeldoc_provider_config(provider: str) -> BabelDocProviderConfig:
    if provider == "babeldoc-deepseek":
        return BabelDocProviderConfig(
            code=provider,
            label="DeepSeek",
            api_key=Config.deepseek_api_key(),
            model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
        )
    if provider == "babeldoc-openai":
        return BabelDocProviderConfig(
            code=provider,
            label="OpenAI",
            api_key=Config.openai_api_key(),
            model=Config.openai_model(),
            base_url=Config.openai_base_url(),
        )
    raise BabelDocError(f"Unsupported BabelDOC provider: {provider}")


def _page_numbers_to_ranges(page_numbers: list[int]) -> str:
    if not page_numbers:
        raise BabelDocError("No pages requested for BabelDOC translation")
    pages = sorted({p + 1 for p in page_numbers})
    ranges: list[str] = []
    start = prev = pages[0]
    for page in pages[1:]:
        if page == prev + 1:
            prev = page
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = page
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(ranges)
