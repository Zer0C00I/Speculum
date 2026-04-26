import os
from pathlib import Path

from dotenv import load_dotenv, set_key

_ENV_PATH: Path | None = None


def _find_or_create_env() -> Path:
    global _ENV_PATH
    if _ENV_PATH is not None and _ENV_PATH.exists():
        return _ENV_PATH

    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]
    for c in candidates:
        if c.exists():
            _ENV_PATH = c
            return c

    _ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
    _ENV_PATH.touch()
    return _ENV_PATH


def load_config() -> None:
    env_path = _find_or_create_env()
    load_dotenv(env_path, override=True)


def _reload_env() -> None:
    env_path = _find_or_create_env()
    load_dotenv(env_path, override=True)


class Config:
    @staticmethod
    def deepseek_api_key() -> str:
        return os.getenv("DEEPSEEK_API_KEY", "")

    @staticmethod
    def anthropic_api_key() -> str:
        return os.getenv("ANTHROPIC_API_KEY", "")

    @staticmethod
    def default_provider() -> str:
        return os.getenv("DEFAULT_PROVIDER", "deepseek")

    @staticmethod
    def default_source_lang() -> str:
        return os.getenv("DEFAULT_SOURCE_LANG", "en")

    @staticmethod
    def default_target_lang() -> str:
        return os.getenv("DEFAULT_TARGET_LANG", "ru")

    @staticmethod
    def set_deepseek_api_key(key: str) -> None:
        os.environ["DEEPSEEK_API_KEY"] = key
        env_path = _find_or_create_env()
        set_key(env_path, "DEEPSEEK_API_KEY", key)

    @staticmethod
    def set_anthropic_api_key(key: str) -> None:
        os.environ["ANTHROPIC_API_KEY"] = key
        env_path = _find_or_create_env()
        set_key(env_path, "ANTHROPIC_API_KEY", key)

    @staticmethod
    def set_default_provider(provider: str) -> None:
        os.environ["DEFAULT_PROVIDER"] = provider
        env_path = _find_or_create_env()
        set_key(env_path, "DEFAULT_PROVIDER", provider)
