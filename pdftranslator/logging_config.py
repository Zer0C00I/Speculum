from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from PySide6.QtCore import QObject, Signal

from pdftranslator.config import Config

_GUI_LOG: QTextEditLogHandler | None = None
_FILE_LOG: RotatingFileHandler | None = None


class QTextEditLogHandler(logging.Handler, QObject):
    log_signal = Signal(str)

    def __init__(self) -> None:
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        self.log_signal.emit(msg)


def setup_logging(level: int = logging.INFO) -> None:
    global _GUI_LOG, _FILE_LOG

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.WARNING)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    _GUI_LOG = QTextEditLogHandler()
    _GUI_LOG.setLevel(logging.DEBUG)
    _GUI_LOG.setFormatter(fmt)
    root.addHandler(_GUI_LOG)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("babeldoc").setLevel(logging.ERROR)
    logging.getLogger("babeldoc.format").setLevel(logging.ERROR)

    _FILE_LOG = None
    set_file_logging_enabled(Config.save_logs_to_file())
    logging.getLogger(__name__).info("Logging initialized")


def set_verbose_logging(enabled: bool) -> None:
    logging.getLogger().setLevel(logging.DEBUG if enabled else logging.INFO)
    babeldoc_level = logging.INFO if enabled else logging.ERROR
    logging.getLogger("babeldoc").setLevel(babeldoc_level)
    logging.getLogger("babeldoc.format").setLevel(babeldoc_level)


def set_file_logging_enabled(enabled: bool) -> None:
    global _FILE_LOG
    root = logging.getLogger()
    if _FILE_LOG is not None:
        root.removeHandler(_FILE_LOG)
        try:
            _FILE_LOG.close()
        except Exception:
            pass
        _FILE_LOG = None
    if not enabled:
        return
    log_dir = Config.app_state_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "pdftranslator.log"
    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=3)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(fh)
    _FILE_LOG = fh


def gui_handler() -> QTextEditLogHandler | None:
    return _GUI_LOG


def log_file_path():
    return Config.app_state_dir() / "logs" / "pdftranslator.log"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
