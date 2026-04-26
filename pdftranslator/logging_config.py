from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, Signal

LOG_DIR = Path.home() / ".pdftranslator" / "logs"
LOG_FILE = LOG_DIR / "pdftranslator.log"

_GUI_LOG: QTextEditLogHandler | None = None


class QTextEditLogHandler(logging.Handler, QObject):
    log_signal = Signal(str)

    def __init__(self) -> None:
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        self.log_signal.emit(msg)


def setup_logging(level: int = logging.DEBUG) -> None:
    global _GUI_LOG
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    fh = RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

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

    logging.getLogger(__name__).info("Logging initialized — file: %s", LOG_FILE)


def gui_handler() -> QTextEditLogHandler | None:
    return _GUI_LOG


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
