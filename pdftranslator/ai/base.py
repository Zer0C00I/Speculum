from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TextBlock:
    id: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font_size: float
    font_name: str


@dataclass
class TranslatedBlock:
    id: int
    original_text: str
    translated_text: str


class AbstractTranslator(ABC):
    @abstractmethod
    def translate_batch(
        self,
        blocks: list[TextBlock],
        source_lang: str,
        target_lang: str,
        context_before: str = "",
        context_after: str = "",
    ) -> list[TranslatedBlock]:
        ...

    @abstractmethod
    def estimate_cost(
        self,
        blocks: list[TextBlock],
        source_lang: str,
        target_lang: str,
    ) -> float:
        ...

    @staticmethod
    @abstractmethod
    def validate_key(key: str) -> tuple[bool, str]:
        ...
