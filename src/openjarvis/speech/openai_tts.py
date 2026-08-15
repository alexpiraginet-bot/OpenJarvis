"""OpenAI TTS backend — cloud-based voice synthesis via OpenAI API."""

from __future__ import annotations

import os
from typing import List

import httpx

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult

_OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"


def _openai_tts_request(
    api_key: str,
    text: str,
    voice: str,
    model: str = _OPENAI_TTS_MODEL,
    speed: float = 1.0,
    response_format: str = "mp3",
    instructions: str = "",
) -> bytes:
    """Call the OpenAI TTS API and return raw audio bytes."""
    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "speed": speed,
        "response_format": response_format,
    }
    if instructions:
        payload["instructions"] = instructions

    resp = httpx.post(
        _OPENAI_TTS_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.content


@TTSRegistry.register("openai_tts")
class OpenAITTSBackend(TTSBackend):
    """OpenAI TTS backend — cloud synthesis."""

    backend_id = "openai_tts"

    def __init__(
        self,
        *,
        api_key: str = "",
        model: str = _OPENAI_TTS_MODEL,
        instructions: str = "",
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model
        self._instructions = instructions

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "cedar",
        speed: float = 1.0,
        output_format: str = "mp3",
    ) -> TTSResult:
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        selected_voice = voice_id or "cedar"
        audio = _openai_tts_request(
            self._api_key,
            text,
            voice=selected_voice,
            model=self._model,
            speed=speed,
            response_format=output_format,
            instructions=self._instructions,
        )

        return TTSResult(
            audio=audio,
            format=output_format,
            voice_id=selected_voice,
            metadata={"backend": "openai_tts", "model": self._model},
        )

    def available_voices(self) -> List[str]:
        return [
            "alloy",
            "ash",
            "ballad",
            "coral",
            "echo",
            "fable",
            "nova",
            "onyx",
            "sage",
            "shimmer",
            "verse",
            "marin",
            "cedar",
        ]

    def health(self) -> bool:
        return bool(self._api_key)
