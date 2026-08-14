"""Standalone entrypoint for the Life OS — the app, without the framework.

``openjarvis.server.app`` builds the full research server: inference engine,
telemetry, agent manager, channel bridge. A client's phone needs none of that.
This serves exactly two things — the Life API and the PWA bundle — so the
hosted deployment starts in a second, holds no model in memory, and has a much
smaller surface to secure.

Run it::

    uv run python -m openjarvis.life.server
    uv run uvicorn openjarvis.life.server:app --host 0.0.0.0 --port 8100

An inference engine is optional. Without one, ``/v1/life/ask`` answers from the
client's own records rather than going silent (see ``life_routes._fallback_answer``).
"""

from __future__ import annotations

import logging
import os
import pathlib
from types import SimpleNamespace
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from openjarvis.life.db import configured_database_target
from openjarvis.life.whatsapp import ChannelAddressVault, NewsProvider
from openjarvis.server.life_routes import create_life_router
from openjarvis.server.life_whatsapp_routes import WhatsAppSender

logger = logging.getLogger(__name__)

_CLOUD_API_KEY_ENV_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")

#: Where `npm run build` puts the PWA.
_STATIC_DIR = pathlib.Path(__file__).resolve().parents[1] / "server" / "static"


def create_life_app(
    *,
    db_path: str = "",
    cors_origins: list[str] | None = None,
    serve_static: bool = True,
    engine: Any | None = None,
    model: str | None = None,
    channel_address_vault: ChannelAddressVault | None = None,
    channel_pepper: bytes | None = None,
    whatsapp_channel: WhatsAppSender | None = None,
    whatsapp_verify_token: str | None = None,
    whatsapp_app_secret: str | None = None,
    whatsapp_news_provider: NewsProvider | None = None,
    whatsapp_inbound_handler: Callable[[Any], Any] | None = None,
) -> FastAPI:
    """Build the Life-only application.

    ``serve_static=False`` leaves the PWA to someone else — on Vercel the CDN
    serves it from ``public/`` and only ``/v1/life`` reaches this function, so
    a missing bundle there is correct rather than a misconfiguration.
    """
    app = FastAPI(
        title="Jarvis — Life OS",
        description="Assistente pessoal: finanças, treino, rotina, família e trabalho",
        version="1.0.0",
    )

    # A phone app is served from the same origin in production, so the default
    # is deliberately narrow — dev servers only. Widen it explicitly per
    # deployment rather than shipping "*" and hoping.
    resolved_cors_origins = (
        cors_origins
        if cors_origins is not None
        else ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    resolved_whatsapp_channel = whatsapp_channel
    if resolved_whatsapp_channel is None:
        access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
        phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
        if access_token and phone_number_id:
            from openjarvis.channels.whatsapp import WhatsAppChannel

            resolved_whatsapp_channel = WhatsAppChannel(
                access_token=access_token,
                phone_number_id=phone_number_id,
            )
    resolved_channel_pepper = (
        channel_pepper
        if channel_pepper is not None
        else os.environ.get("OPENJARVIS_LIFE_CHANNEL_PEPPER", "").encode("utf-8")
    )
    router = create_life_router(
        db_path or configured_database_target(),
        channel_address_vault=channel_address_vault,
        channel_pepper=resolved_channel_pepper,
        whatsapp_channel=resolved_whatsapp_channel,
        whatsapp_verify_token=(
            whatsapp_verify_token
            if whatsapp_verify_token is not None
            else os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
        ),
        whatsapp_app_secret=(
            whatsapp_app_secret
            if whatsapp_app_secret is not None
            else os.environ.get("WHATSAPP_APP_SECRET", "")
        ),
        whatsapp_news_provider=whatsapp_news_provider,
        whatsapp_inbound_handler=whatsapp_inbound_handler,
    )
    app.include_router(router)
    app.state.life_context = getattr(router, "life_context", None)

    resolved_engine = engine
    if resolved_engine is None and any(
        os.environ.get(key) for key in _CLOUD_API_KEY_ENV_VARS
    ):
        try:
            from openjarvis.engine.cloud import CloudEngine

            resolved_engine = CloudEngine()
        except Exception:  # noqa: BLE001 - the data-only fallback must stay online
            logger.exception("Jarvis AI engine initialization failed; using data mode")

    if resolved_engine is not None:
        app.state.engine = resolved_engine
        app.state.config = SimpleNamespace(
            model=model
            or os.environ.get("OPENJARVIS_LIFE_MODEL", "gpt-5-mini").strip()
            or "gpt-5-mini"
        )

    @app.get("/health")
    async def health() -> dict:
        """Liveness probe. Open by design — it exposes nothing about a client."""
        return {"status": "ok", "service": "life"}

    if serve_static and _STATIC_DIR.is_dir():
        assets = _STATIC_DIR / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            """Serve the PWA, falling back to index.html for client routes."""
            if full_path:
                candidate = (_STATIC_DIR / full_path).resolve()
                # Path-traversal guard: a crafted path must not escape static/.
                if (
                    candidate.is_relative_to(_STATIC_DIR.resolve())
                    and candidate.is_file()
                ):
                    return FileResponse(candidate)
            return FileResponse(_STATIC_DIR / "index.html")
    elif serve_static:
        logger.warning(
            "PWA bundle not found at %s — run `npm run build` in frontend/",
            _STATIC_DIR,
        )

    return app


_RUNNING_ON_VERCEL = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))
app = create_life_app(
    db_path=configured_database_target(),
    cors_origins=[] if _RUNNING_ON_VERCEL else None,
    serve_static=not _RUNNING_ON_VERCEL,
)


def main() -> None:
    """Run the app with uvicorn."""
    import uvicorn

    host = os.environ.get("OPENJARVIS_LIFE_HOST", "127.0.0.1")
    port = int(os.environ.get("OPENJARVIS_LIFE_PORT", "8100"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
