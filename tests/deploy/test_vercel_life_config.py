"""Production contract for the Jarvis Life Vercel deployment."""

from __future__ import annotations

import json
from pathlib import Path

VERCEL_CONFIG = (
    Path(__file__).resolve().parents[2] / "deploy" / "vercel" / "vercel.json"
)


def _config() -> dict:
    return json.loads(VERCEL_CONFIG.read_text(encoding="utf-8"))


def test_whatsapp_briefing_scheduler_runs_each_minute():
    """Removing the minute scheduler would silently skip user-selected times."""
    assert {
        "path": "/v1/life/internal/whatsapp/briefings",
        "schedule": "* * * * *",
    } in _config()["crons"]


def test_life_function_allows_a_five_minute_briefing_batch():
    """Restoring the old 30s cap would terminate multi-user AI briefings."""
    assert _config()["functions"]["api/index.py"]["maxDuration"] >= 300


def test_realtime_webrtc_origin_is_allowed_by_content_security_policy():
    """The browser must be able to exchange SDP with OpenAI Realtime."""
    root_headers = next(
        entry["headers"] for entry in _config()["headers"] if entry["source"] == "/(.*)"
    )
    csp = next(
        header["value"]
        for header in root_headers
        if header["key"] == "Content-Security-Policy"
    )
    connect_src = next(
        directive
        for directive in csp.split(";")
        if directive.strip().startswith("connect-src ")
    )

    assert "https://api.openai.com" in connect_src.split()
