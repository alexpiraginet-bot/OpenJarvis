"""Authenticated OpenAI voice synthesis for the Jarvis Life client."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from openjarvis.life import LifeContext
from openjarvis.life.tenancy import User
from openjarvis.speech.openai_tts import _openai_tts_request

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

JARVIS_VOICE_ID = "cedar"
JARVIS_VOICE_MODEL = "gpt-4o-mini-tts"
JARVIS_REALTIME_MODEL = "gpt-realtime-2.1"
JARVIS_REALTIME_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
JARVIS_REALTIME_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
_REALTIME_TOKEN_WINDOW_SECONDS = 60.0
_REALTIME_TOKEN_USER_LIMIT = 3
JARVIS_VOICE_INSTRUCTIONS = (
    "Fale em português do Brasil. Voz masculina adulta, registro grave moderado, "
    "calma, confiante e precisa. Ritmo natural, presença tecnológica sofisticada, "
    "calor humano discreto e pausas curtas. Sem teatralidade, sem voz robótica e "
    "sem imitar qualquer personagem, ator ou propriedade intelectual."
)
JARVIS_REALTIME_INSTRUCTIONS = (
    "Você é somente a camada de voz do Jarvis Life. Fale em português do Brasil "
    "com voz masculina adulta original, calma, precisa e natural, sem imitar "
    "personagens ou pessoas reais. Nunca decida, execute ou confirme ações. "
    "Fale somente o texto fornecido pelo backend autoritativo do Jarvis Life."
)


class VoiceSpeechRequest(BaseModel):
    """Text returned by Jarvis that should be spoken to this user."""

    text: str = Field(min_length=1, max_length=1_600)


class _RealtimeTokenRateLimiter:
    """Bound token minting per user inside one process/serverless instance.

    This guard limits accidental reconnect loops reaching the paid provider. It
    is deliberately not presented as a global distributed rate limit because
    separate serverless instances do not share this in-memory state.
    """

    def __init__(self) -> None:
        self._events: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def allow(self, user_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [
                event
                for event in self._events.get(user_id, [])
                if now - event < _REALTIME_TOKEN_WINDOW_SECONDS
            ]
            if len(recent) >= _REALTIME_TOKEN_USER_LIMIT:
                self._events[user_id] = recent
                return False
            recent.append(now)
            self._events[user_id] = recent
        return True


def create_voice_router(life: LifeContext) -> APIRouter:
    """Create the tenant-authenticated Life voice router."""
    router = APIRouter(tags=["life-voice"])
    realtime_token_limiter = _RealtimeTokenRateLimiter()

    def current_user(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    ) -> User:
        if credentials is None or not credentials.credentials:
            raise HTTPException(status_code=401, detail="Missing bearer token")
        user_id = life.users.resolve_token(credentials.credentials)
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user = life.users.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Unknown user")
        return user

    @router.post("/voice/speech", response_class=Response)
    async def synthesize_speech(
        body: VoiceSpeechRequest,
        user: User = Depends(current_user),
    ) -> Response:
        """Render one spoken reply without exposing the provider credential."""
        del user  # Authentication and tenant ownership are the gate for this route.
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Empty speech text")

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise HTTPException(status_code=503, detail="Jarvis voice unavailable")

        try:
            audio = await run_in_threadpool(
                _openai_tts_request,
                api_key,
                text,
                JARVIS_VOICE_ID,
                JARVIS_VOICE_MODEL,
                1.0,
                "mp3",
                JARVIS_VOICE_INSTRUCTIONS,
            )
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "OpenAI voice request failed with status %s",
                exc.response.status_code,
            )
            raise HTTPException(
                status_code=502, detail="Jarvis voice unavailable"
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("OpenAI voice transport failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=502, detail="Jarvis voice unavailable"
            ) from exc

        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "private, no-store",
                "X-Jarvis-Voice": JARVIS_VOICE_ID,
                "X-AI-Generated-Voice": "true",
            },
        )

    @router.post("/voice/realtime/token", response_class=JSONResponse)
    async def create_realtime_token(
        user: User = Depends(current_user),
    ) -> JSONResponse:
        """Mint one short-lived WebRTC credential for this authenticated user.

        Realtime owns microphone transport, VAD, transcription and streaming
        playback only. The regular ``/ask`` route remains the authority for
        memory, tools, proposals and confirmations.
        """
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise HTTPException(
                status_code=503,
                detail="Jarvis realtime voice unavailable",
            )
        if not realtime_token_limiter.allow(user.id):
            raise HTTPException(
                status_code=429,
                detail="Too many realtime token requests",
                headers={
                    "Retry-After": str(int(_REALTIME_TOKEN_WINDOW_SECONDS)),
                },
            )

        session = {
            "type": "realtime",
            "model": JARVIS_REALTIME_MODEL,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "noise_reduction": {"type": "near_field"},
                    "transcription": {
                        "model": JARVIS_REALTIME_TRANSCRIPTION_MODEL,
                        "language": "pt",
                    },
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500,
                        "create_response": False,
                        "interrupt_response": True,
                    },
                },
                "output": {"voice": JARVIS_VOICE_ID},
            },
            "instructions": JARVIS_REALTIME_INSTRUCTIONS,
        }
        safety_identifier = hashlib.sha256(user.id.encode("utf-8")).hexdigest()

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                provider_response = await client.post(
                    JARVIS_REALTIME_CLIENT_SECRETS_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "OpenAI-Safety-Identifier": safety_identifier,
                    },
                    json={"session": session},
                )
            provider_response.raise_for_status()
            provider_payload = provider_response.json()
            client_secret = provider_payload.get("value")
            expires_at = provider_payload.get("expires_at")
            if not isinstance(client_secret, str) or not client_secret:
                raise ValueError("missing realtime client secret")
            if not isinstance(expires_at, (int, float)):
                raise ValueError("missing realtime client secret expiry")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning(
                "OpenAI realtime credential request failed: %s",
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=502,
                detail="Jarvis realtime voice unavailable",
            ) from exc

        return JSONResponse(
            content={
                "client_secret": client_secret,
                "expires_at": expires_at,
                "model": JARVIS_REALTIME_MODEL,
                "voice": JARVIS_VOICE_ID,
            },
            headers={"Cache-Control": "private, no-store"},
        )

    return router
