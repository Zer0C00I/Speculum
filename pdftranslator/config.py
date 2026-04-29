import os
import sys
from pathlib import Path

from dotenv import load_dotenv, set_key

_ENV_PATH: Path | None = None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _xdg_dir(env_name: str, fallback: str) -> Path:
    base = os.getenv(env_name, "").strip()
    if base:
        return Path(base)
    return Path.home() / fallback


def _app_config_dir() -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", ".config") / "pdftranslator"


def _app_state_dir() -> Path:
    return _xdg_dir("XDG_STATE_HOME", ".local/state") / "pdftranslator"


def _app_cache_dir() -> Path:
    return _xdg_dir("XDG_CACHE_HOME", ".cache") / "pdftranslator"


def _find_or_create_env() -> Path:
    global _ENV_PATH
    if _ENV_PATH is not None and _ENV_PATH.exists():
        return _ENV_PATH

    candidates = [
        _app_config_dir() / "config.env",
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]
    for c in candidates:
        if c.exists():
            _ENV_PATH = c
            return c

    _ENV_PATH = _app_config_dir() / "config.env"
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    def app_config_dir() -> Path:
        path = _app_config_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def app_state_dir() -> Path:
        path = _app_state_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def app_cache_dir() -> Path:
        path = _app_cache_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def deepseek_api_key() -> str:
        return os.getenv("DEEPSEEK_API_KEY", "")

    @staticmethod
    def anthropic_api_key() -> str:
        return os.getenv("ANTHROPIC_API_KEY", "")

    @staticmethod
    def openai_api_key() -> str:
        return os.getenv("OPENAI_API_KEY", "")

    @staticmethod
    def openai_base_url() -> str:
        return os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    @staticmethod
    def openai_model() -> str:
        return os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    @staticmethod
    def default_provider() -> str:
        return os.getenv("DEFAULT_PROVIDER", "babeldoc-deepseek")

    @staticmethod
    def default_source_lang() -> str:
        return os.getenv("DEFAULT_SOURCE_LANG", "en")

    @staticmethod
    def default_target_lang() -> str:
        return os.getenv("DEFAULT_TARGET_LANG", "ru")

    @staticmethod
    def verbose_logging() -> bool:
        return _env_bool("VERBOSE_LOGGING", False)

    @staticmethod
    def save_logs_to_file() -> bool:
        return _env_bool("SAVE_LOGS_TO_FILE", False)

    @staticmethod
    def save_session_copies() -> bool:
        return _env_bool("SAVE_SESSION_COPIES", False)

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
    def set_openai_api_key(key: str) -> None:
        os.environ["OPENAI_API_KEY"] = key
        env_path = _find_or_create_env()
        set_key(env_path, "OPENAI_API_KEY", key)

    @staticmethod
    def set_openai_base_url(base_url: str) -> None:
        os.environ["OPENAI_BASE_URL"] = base_url
        env_path = _find_or_create_env()
        set_key(env_path, "OPENAI_BASE_URL", base_url)

    @staticmethod
    def set_openai_model(model: str) -> None:
        os.environ["OPENAI_MODEL"] = model
        env_path = _find_or_create_env()
        set_key(env_path, "OPENAI_MODEL", model)

    @staticmethod
    def set_default_provider(provider: str) -> None:
        os.environ["DEFAULT_PROVIDER"] = provider
        env_path = _find_or_create_env()
        set_key(env_path, "DEFAULT_PROVIDER", provider)

    @staticmethod
    def set_verbose_logging(enabled: bool) -> None:
        os.environ["VERBOSE_LOGGING"] = "1" if enabled else "0"
        env_path = _find_or_create_env()
        set_key(env_path, "VERBOSE_LOGGING", os.environ["VERBOSE_LOGGING"])

    @staticmethod
    def set_save_logs_to_file(enabled: bool) -> None:
        os.environ["SAVE_LOGS_TO_FILE"] = "1" if enabled else "0"
        env_path = _find_or_create_env()
        set_key(env_path, "SAVE_LOGS_TO_FILE", os.environ["SAVE_LOGS_TO_FILE"])

    @staticmethod
    def set_save_session_copies(enabled: bool) -> None:
        os.environ["SAVE_SESSION_COPIES"] = "1" if enabled else "0"
        env_path = _find_or_create_env()
        set_key(env_path, "SAVE_SESSION_COPIES", os.environ["SAVE_SESSION_COPIES"])

    @staticmethod
    def babeldoc_python() -> str:
        configured = os.getenv("BABELDOC_PYTHON", "").strip()
        if configured:
            return configured
        venv_python = Path.cwd() / ".venv" / "bin" / "python"
        if venv_python.exists():
            return str(venv_python)
        return sys.executable

    @staticmethod
    def translation_state_path(pdf_path: str | Path) -> Path:
        pdf_path = Path(pdf_path)
        return pdf_path.with_suffix(pdf_path.suffix + ".pages.json")
