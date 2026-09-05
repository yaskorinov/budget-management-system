from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Telegram
    bot_token: str = ""
    bot_mode: str = "polling"  # polling | webhook
    webhook_secret: str = "change-me-webhook-secret"

    # Server
    host: str = "127.0.0.1"
    port: int = 8080
    public_base_url: str = ""
    secret_key: str = "change-me-please"

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/expenses.db"

    # Locale
    tz_offset_hours: int = 3
    currency: str = "RUB"

    # LLM
    llm_provider: str = "openai_compat"  # anthropic | openai_compat | off
    llm_api_key: str = ""
    llm_model: str = "gemini-3.1-flash-lite"
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_timeout_seconds: float = 8.0
    # Модель для расшифровки голоса. У Gemini аудио принимает и flash-lite,
    # так что по умолчанию та же самая — отдельная нужна, только если
    # распознавание начнёт ошибаться
    llm_voice_model: str = "gemini-3.1-flash-lite"
    llm_voice_timeout_seconds: float = 60.0

    # Ежедневные сообщения, часы по локальному времени; -1 — выключить
    tips_hour: int = 20
    debts_hour: int = 12

    @property
    def public_base(self) -> str:
        return self.public_base_url.rstrip("/")

    @property
    def webhook_path(self) -> str:
        return "/tg/webhook"

    @property
    def webhook_url(self) -> str:
        return f"{self.public_base}{self.webhook_path}"

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "off" and bool(self.llm_api_key)

    @property
    def web_enabled(self) -> bool:
        """Мини-аппа/браузерная версия доступны только при публичном https."""
        return self.public_base.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
