"""Authenticated OpenAI voice synthesis for the Jarvis Life client."""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
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
JARVIS_VOICE_INSTRUCTIONS = (
    "Fale em português do Brasil. Voz masculina adulta, registro grave moderado, "
    "calma, confiante e precisa. Ritmo natural, presença tecnológica sofisticada, "
    "calor humano discreto e pausas curtas. Sem teatralidade, sem voz robótica e "
    "sem imitar qualquer personagem, ator ou propriedade intelectual."
)


class VoiceSpeechRequest(BaseModel):
    """Text returned by Jarvis that should be spoken to this user."""

    text: str = Field(min_length=1, max_length=1_600)


def create_voice_router(life: LifeContext) -> APIRouter:
    """Create the tenant-authenticated Life voice router."""
    router = APIRouter(tags=["life-voice"])

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

    return router
