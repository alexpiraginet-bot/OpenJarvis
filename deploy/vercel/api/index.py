"""Vercel serverless entrypoint for the Life API.

Vercel's Python runtime looks for an ASGI application named ``app`` in a module
under ``api/``. Everything below ``/v1/life`` is rewritten here by
``vercel.json``; the PWA itself is served from ``public/`` by Vercel's CDN,
which is both faster and cheaper than routing static files through a function.

The database **must** be PostgreSQL here. Vercel gives each invocation a
read-only filesystem apart from ``/tmp``, and that ``/tmp`` belongs to one
instance — a SQLite deployment would let a client register on one machine and
then 401 on the next request from another. The guard below fails loudly at
import time rather than letting that ship.
"""

from __future__ import annotations

from openjarvis.life.db import POSTGRES, configured_database_target, detect_backend

_DSN = configured_database_target()

if not _DSN:
    raise RuntimeError(
        "No PostgreSQL DSN is configured. Set OPENJARVIS_LIFE_DB or connect a "
        "Vercel Postgres integration that provides POSTGRES_PRISMA_URL."
    )

if detect_backend(_DSN) != POSTGRES:
    raise RuntimeError(
        "OPENJARVIS_LIFE_DB must be a PostgreSQL DSN on Vercel. A SQLite file "
        "cannot persist here: the filesystem is read-only apart from a "
        "per-instance /tmp, so a client would register on one instance and be "
        "logged out by the next request."
    )

# Import only after validating the environment. The standalone module detects
# Vercel and creates exactly one ASGI app with this PostgreSQL DSN, no CORS
# origins, and no Python static-file handler.
from openjarvis.life.server import app as life_app  # noqa: E402


class _StripVercelFunctionPrefix:
    """Restore the public path after Vercel's internal framework rewrite."""

    _PREFIX = "/api/index"

    def __init__(self, wrapped_app):
        self._wrapped_app = wrapped_app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if path == self._PREFIX or path.startswith(f"{self._PREFIX}/"):
            scope = dict(scope)
            public_path = path[len(self._PREFIX) :] or "/"
            scope["path"] = public_path
            scope["raw_path"] = public_path.encode("utf-8")
        await self._wrapped_app(scope, receive, send)


app = _StripVercelFunctionPrefix(life_app)
