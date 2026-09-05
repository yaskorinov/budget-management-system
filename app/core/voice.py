"""Расшифровка голосовых сообщений.

Основная модель (nemotron-3.5-lightning) принимает только текст, поэтому для
голоса берётся отдельная — с аудио на входе. Формат передаётся как есть:
голосовые Telegram приходят в контейнере ogg.
"""
from __future__ import annotations

import base64
import io
import logging

from app.config import settings
from app.core.classifier import chat

log = logging.getLogger(__name__)


class VoiceUnavailable(RuntimeError):
    """Модель не ответила. Отличается от «не разобрал слова»: там виновата
    запись, здесь — доступ к модели, и человеку нужно сказать разное."""

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


SPEECH_RATE = 16000  # речевые модели работают с 16 кГц, больше только утяжеляет


def to_mp3(raw: bytes) -> tuple[bytes, str]:
    """Голосовое Telegram (ogg/opus) -> mono mp3 16 кГц.

    Модели принимают ogg не всегда, а mp3 понимают все. Заодно запись худеет
    в несколько раз: она уходит в запрос как base64, и лишний вес — это
    лишние секунды ожидания.

    Если сконвертировать не вышло, возвращаем исходник как есть — пусть
    попробует сервер.
    """
    try:
        import numpy
        import soundfile

        data, rate = soundfile.read(io.BytesIO(raw), dtype="float32")
        mono = data if data.ndim == 1 else data.mean(axis=1)

        if rate != SPEECH_RATE:
            length = int(len(mono) * SPEECH_RATE / rate)
            points = numpy.linspace(0, len(mono) - 1, length)
            mono = numpy.interp(points, numpy.arange(len(mono)), mono).astype("float32")

        out = io.BytesIO()
        soundfile.write(out, mono, SPEECH_RATE, format="MP3")
        return out.getvalue(), "mp3"
    except Exception as exc:
        log.warning("Не удалось перекодировать запись (%s), отправляю как есть", exc)
        return raw, ""


async def transcribe(audio: bytes, fmt: str = "ogg") -> str | None:
    """Голос -> текст. None, если модель недоступна или ничего не разобрала."""
    if not settings.llm_enabled or not settings.llm_voice_model:
        return None

    audio, converted = to_mp3(audio)
    if converted:
        fmt = converted
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
        raise VoiceUnavailable(str(exc)) from exc

    text = (answer or "").strip().strip('"«»')
    return text or None
