from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from pdftranslator.config import load_config
from pdftranslator.logging_config import setup_logging
from pdftranslator.gui.main_window import MainWindow


def run_gui() -> None:
    setup_logging()
    load_config()

    app = QApplication(sys.argv)
    app.setApplicationName("PDF Translator")
    app.setOrganizationName("pdftranslator")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())
