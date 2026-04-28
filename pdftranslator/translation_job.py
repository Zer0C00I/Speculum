from __future__ import annotations

from dataclasses import dataclass

from pdftranslator.babeldoc_runner import BabelDocProviderConfig


@dataclass(frozen=True)
class TranslationJob:
    provider: str
    source_lang: str
    target_lang: str
    page_numbers: list[int]
    output_path: str
    babeldoc_provider: BabelDocProviderConfig | None = None
    session_dir: str | None = None
    verbose: bool = False
    page_window_label: str = ""
