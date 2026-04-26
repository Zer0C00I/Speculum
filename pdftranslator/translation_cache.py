from __future__ import annotations

import json
import hashlib
from pathlib import Path

from pdftranslator.ai.base import TranslatedBlock
from pdftranslator.logging_config import get_logger

_log = get_logger(__name__)


class TranslationCache:
    def __init__(self, pdf_path: str, source_lang: str, target_lang: str, provider: str) -> None:
        self._cache_path = Path(pdf_path).with_suffix(".cache.json")
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._provider = provider
        self._data: dict[str, str] = {}
        self._loaded = False
        _log.info("TranslationCache: %s", self._cache_path)

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._cache_path.exists():
            try:
                self._data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _make_key(self, page: int, block_id: int) -> str:
        raw = f"{page}:{block_id}:{self._source_lang}:{self._target_lang}:{self._provider}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, page: int, block_id: int) -> str | None:
        self._load()
        return self._data.get(self._make_key(page, block_id))

    def set(self, page: int, block_id: int, text: str) -> None:
        self._load()
        self._data[self._make_key(page, block_id)] = text

    def flush(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    def mark_page_complete(self, page: int) -> None:
        self._load()
        self._data[f"__page_{page}_done__"] = "1"
        self.flush()

    def is_page_complete(self, page: int) -> bool:
        self._load()
        return self._data.get(f"__page_{page}_done__") == "1"
