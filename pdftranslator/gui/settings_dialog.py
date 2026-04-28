from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from openai import OpenAI

from pdftranslator.config import Config


class _ValidateWorker(QThread):
    result = Signal(bool, str)

    def __init__(self, provider: str, key: str, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._key = key

    def run(self) -> None:
        try:
            if self._provider == "deepseek":
                from pdftranslator.ai.deepseek import DeepSeekTranslator
                ok, msg = DeepSeekTranslator.validate_key(self._key)
            elif self._provider == "openai":
                client = OpenAI(
                    api_key=self._key,
                    base_url=Config.openai_base_url(),
                )
                client.models.list()
                ok, msg = True, "OpenAI-compatible API key is valid"
            elif self._provider == "anthropic":
                from pdftranslator.ai.anthropic import AnthropicTranslator
                ok, msg = AnthropicTranslator.validate_key(self._key)
            else:
                ok, msg = False, f"Unknown provider: {self._provider}"
        except Exception as exc:
            ok, msg = False, str(exc)
        self.result.emit(ok, msg)


class SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings — API Keys")
        self.setMinimumWidth(520)

        self._tabs = QTabWidget()
        self._ds_tab = self._create_provider_tab(
            "DeepSeek",
            Config.deepseek_api_key(),
            self._on_validate_deepseek,
        )
        self._an_tab = self._create_provider_tab(
            "Anthropic Claude",
            Config.anthropic_api_key(),
            self._on_validate_anthropic,
        )
        self._oa_tab = self._create_openai_tab()
        self._tabs.addTab(self._ds_tab, "DeepSeek")
        self._tabs.addTab(self._oa_tab, "OpenAI")
        self._tabs.addTab(self._an_tab, "Anthropic")

        save_btn = QPushButton("Save All")
        save_btn.clicked.connect(self._on_save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)
        layout.addLayout(btn_layout)

    def _create_provider_tab(
        self, name: str, current_key: str, validate_cb
    ) -> QWidget:
        widget = QWidget()
        layout = QFormLayout(widget)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        key_field = QLineEdit()
        key_field.setEchoMode(QLineEdit.EchoMode.Password)
        key_field.setPlaceholderText(f"Enter {name} API key...")
        key_field.setText(current_key)
        key_field.setMinimumWidth(380)
        widget._key_field = key_field

        validate_btn = QPushButton("Validate")
        validate_btn.clicked.connect(validate_cb)
        widget._validate_btn = validate_btn

        status_label = QLabel("Not tested")
        status_label.setStyleSheet("color: gray;")
        widget._status_label = status_label

        key_row = QHBoxLayout()
        key_row.addWidget(key_field, 1)
        key_row.addWidget(validate_btn)

        layout.addRow("API Key:", key_row)
        layout.addRow("Status:", status_label)
        layout.addRow(QLabel(""))  # spacer

        hint = QLabel(
            "Get your key at:<br>"
            "<b>DeepSeek</b>: <a href='https://platform.deepseek.com/api_keys'>platform.deepseek.com</a><br>"
            "<b>Anthropic</b>: <a href='https://console.anthropic.com/settings/keys'>console.anthropic.com</a>"
        )
        hint.setOpenExternalLinks(True)
        hint.setWordWrap(True)
        layout.addRow(hint)

        return widget

    def _create_openai_tab(self) -> QWidget:
        widget = QWidget()
        layout = QFormLayout(widget)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        key_field = QLineEdit()
        key_field.setEchoMode(QLineEdit.EchoMode.Password)
        key_field.setPlaceholderText("Enter OpenAI-compatible API key...")
        key_field.setText(Config.openai_api_key())
        key_field.setMinimumWidth(380)
        widget._key_field = key_field

        base_url_field = QLineEdit()
        base_url_field.setText(Config.openai_base_url())
        base_url_field.setPlaceholderText("https://api.openai.com/v1")
        widget._base_url_field = base_url_field

        model_field = QLineEdit()
        model_field.setText(Config.openai_model())
        model_field.setPlaceholderText("gpt-4o-mini")
        widget._model_field = model_field

        validate_btn = QPushButton("Validate")
        validate_btn.clicked.connect(self._on_validate_openai)
        widget._validate_btn = validate_btn

        status_label = QLabel("Not tested")
        status_label.setStyleSheet("color: gray;")
        widget._status_label = status_label

        key_row = QHBoxLayout()
        key_row.addWidget(key_field, 1)
        key_row.addWidget(validate_btn)

        layout.addRow("API Key:", key_row)
        layout.addRow("Base URL:", base_url_field)
        layout.addRow("Model:", model_field)
        layout.addRow("Status:", status_label)

        hint = QLabel(
            "Used by the BabelDOC backend via OpenAI-compatible APIs.<br>"
            "Examples: OpenAI, OpenRouter, or another compatible endpoint."
        )
        hint.setWordWrap(True)
        layout.addRow(hint)

        return widget

    def _on_validate_deepseek(self) -> None:
        self._validate("deepseek", self._ds_tab)

    def _on_validate_anthropic(self) -> None:
        self._validate("anthropic", self._an_tab)

    def _on_validate_openai(self) -> None:
        Config.set_openai_base_url(self._oa_tab._base_url_field.text().strip())
        self._validate("openai", self._oa_tab)

    def _validate(self, provider: str, tab: QWidget) -> None:
        key = tab._key_field.text().strip()
        lbl: QLabel = tab._status_label
        btn: QPushButton = tab._validate_btn

        if not key:
            lbl.setText("Please enter an API key")
            lbl.setStyleSheet("color: red;")
            return

        lbl.setText("Validating...")
        lbl.setStyleSheet("color: #2196F3;")
        btn.setEnabled(False)

        self._worker = _ValidateWorker(provider, key)
        self._worker.result.connect(
            lambda ok, msg: self._on_validation_result(tab, ok, msg)
        )
        self._worker.start()

    def _on_validation_result(self, tab: QWidget, ok: bool, msg: str) -> None:
        lbl: QLabel = tab._status_label
        btn: QPushButton = tab._validate_btn
        btn.setEnabled(True)
        if ok:
            lbl.setText(f"✓ {msg}")
            lbl.setStyleSheet("color: green; font-weight: bold;")
        else:
            lbl.setText(f"✗ {msg}")
            lbl.setStyleSheet("color: red;")

    def _on_save(self) -> None:
        Config.set_deepseek_api_key(self._ds_tab._key_field.text().strip())
        Config.set_openai_api_key(self._oa_tab._key_field.text().strip())
        Config.set_openai_base_url(self._oa_tab._base_url_field.text().strip())
        Config.set_openai_model(self._oa_tab._model_field.text().strip())
        Config.set_anthropic_api_key(self._an_tab._key_field.text().strip())
        self.accept()
