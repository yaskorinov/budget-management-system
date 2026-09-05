"""Расшифровка голосовых сообщений.

Основная модель (nemotron-3.5-lightning) принимает только текст, поэтому для
голоса берётся отдельная — с аудио на входе. Формат передаётся как есть:
голосовые Telegram приходят в контейнере ogg.
"""
from __future__ import annotations

import base64
import logging

from app.config import settings
from app.core.classifier import chat

log = logging.getLogger(__name__)

PROMPT = (
    "Расшифруй голосовое сообщение на русском языке. "
    "Верни только произнесённый текст, без пояснений, без кавычек и без перевода. "
    "Числа записывай цифрами."
)

# Что Telegram присылает -> как это называется у OpenAI-совместимого API
FORMATS = {
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
}


def audio_format(mime: str | None, file_name: str | None = None) -> str:
    """Формат для поля input_audio. По умолчанию ogg — так шлёт Telegram."""
    if mime and mime.split(";")[0] in FORMATS:
        return FORMATS[mime.split(";")[0]]
    if file_name and "." in file_name:
        suffix = file_name.rsplit(".", 1)[-1].lower()
        if suffix in {"ogg", "mp3", "m4a", "wav", "flac", "aac", "aiff"}:
            return suffix
    return "ogg"


async def transcribe(audio: bytes, fmt: str = "ogg") -> str | None:
    """Голос -> текст. None, если модель недоступна или ничего не разобрала."""
    if not settings.llm_enabled or not settings.llm_voice_model:
        return None

    encoded = base64.b64encode(audio).decode()
    try:
        answer = await chat(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "input_audio",
                            "input_audio": {"data": encoded, "format": fmt},
                        },
                    ],
                }
            ],
            model=settings.llm_voice_model,
            max_tokens=300,
            timeout=settings.llm_voice_timeout_seconds,
        )
    except Exception as exc:
        log.warning("Расшифровка не удалась (%s)", exc)
        return None

    text = (answer or "").strip().strip('"«»')
    return text or None
