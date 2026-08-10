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

import os

from openjarvis.life.db import POSTGRES, detect_backend
from openjarvis.life.server import create_life_app

_DSN = os.environ.get("OPENJARVIS_LIFE_DB", "")

if not _DSN:
    raise RuntimeError(
        "OPENJARVIS_LIFE_DB is not set. The Life API needs a PostgreSQL DSN on "
        "Vercel — set it in the project's environment variables."
    )

if detect_backend(_DSN) != POSTGRES:
    raise RuntimeError(
        "OPENJARVIS_LIFE_DB must be a PostgreSQL DSN on Vercel. A SQLite file "
        "cannot persist here: the filesystem is read-only apart from a "
        "per-instance /tmp, so a client would register on one instance and be "
        "logged out by the next request."
    )

# `serve_static=False`: Vercel's CDN serves the PWA from public/, and only
# /v1/life is rewritten to this function. Same-origin in production, so no CORS
# origins are opened — add one only if a separate frontend host appears.
app = create_life_app(db_path=_DSN, cors_origins=[], serve_static=False)
