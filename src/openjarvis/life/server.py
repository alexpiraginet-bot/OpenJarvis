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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from openjarvis.server.life_routes import create_life_router

logger = logging.getLogger(__name__)

#: Where `npm run build` puts the PWA.
_STATIC_DIR = pathlib.Path(__file__).resolve().parents[1] / "server" / "static"


def create_life_app(
    *,
    db_path: str = "",
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Build the Life-only application."""
    app = FastAPI(
        title="Jarvis — Life OS",
        description="Assistente pessoal: finanças, treino, rotina, família e trabalho",
        version="1.0.0",
    )

    # A phone app is served from the same origin in production, so the default
    # is deliberately narrow — dev servers only. Widen it explicitly per
    # deployment rather than shipping "*" and hoping.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins
        or ["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    router = create_life_router(db_path or os.environ.get("OPENJARVIS_LIFE_DB", ""))
    app.include_router(router)
    app.state.life_context = getattr(router, "life_context", None)

    @app.get("/health")
    async def health() -> dict:
        """Liveness probe. Open by design — it exposes nothing about a client."""
        return {"status": "ok", "service": "life"}

    if _STATIC_DIR.is_dir():
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
    else:
        logger.warning(
            "PWA bundle not found at %s — run `npm run build` in frontend/",
            _STATIC_DIR,
        )

    return app


app = create_life_app()


def main() -> None:
    """Run the app with uvicorn."""
    import uvicorn

    host = os.environ.get("OPENJARVIS_LIFE_HOST", "127.0.0.1")
    port = int(os.environ.get("OPENJARVIS_LIFE_PORT", "8100"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
