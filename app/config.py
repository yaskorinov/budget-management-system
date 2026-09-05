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

    # Выходной прокси, например socks5://user:pass@host:1080.
    # proxy_url — на весь исходящий трафик; llm_proxy_url перекрывает его
    # для запросов к модели: часто через прокси нужен только он
    proxy_url: str = ""
    llm_proxy_url: str = ""

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
    def telegram_proxy(self) -> str | None:
        return self.proxy_url or None

    @property
    def llm_proxy(self) -> str | None:
        return self.llm_proxy_url or self.proxy_url or None

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "off" and bool(self.llm_api_key)

    @property
    def web_enabled(self) -> bool:
        """Мини-аппа/браузерная версия доступны только при публичном https."""
        return self.public_base.startswith("https://")


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def in_host_network() -> bool:
    """Контейнер запущен с network_mode: host?

    В своей сети контейнер видит только lo и eth0; docker0 — интерфейс хоста,
    так что его появление означает, что сетевой стек общий с хостом и
    127.0.0.1 указывает туда же, куда и на хосте.
    """
    return Path("/sys/class/net/docker0").exists()


def proxy_warning(settings: "Settings") -> str | None:
    """Предупреждение про прокси на loopback внутри контейнера.

    В контейнере 127.0.0.1 — это сам контейнер, и прокси с хоста туда не виден.
    Ошибка при этом выглядит как «Connection refused», хотя на хосте всё
    работает, — подсказываем сразу, чтобы не искать.
    """
    if not Path("/.dockerenv").exists() or in_host_network():
        return None

    hosts = {
        url.split("://")[-1].split("@")[-1].split(":")[0]
        for url in (settings.proxy_url, settings.llm_proxy_url)
        if url
    }
    if not hosts & LOOPBACK_HOSTS:
        return None

    return (
        "Прокси указан на localhost, а бот работает в контейнере: там это адрес "
        "самого контейнера. Если прокси слушает только loopback хоста, поднимите "
        "бота с deploy/docker-compose.proxy.yml (сеть хоста); если он слушает все "
        "интерфейсы — впишите host.docker.internal вместо 127.0.0.1."
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
