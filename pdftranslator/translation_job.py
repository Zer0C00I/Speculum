from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TranslationJob:
    provider: str
    source_lang: str
    target_lang: str
    page_numbers: list[int]
    output_path: str
    session_dir: str | None = None
    verbose: bool = False
    page_window_label: str = ""
