"""Tests for the authenticated Jarvis Life voice endpoint."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.server.life_routes import create_life_router


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    app = FastAPI()
    router = create_life_router(str(tmp_path / "life.db"))
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client
    router.life_context.close()


@pytest.fixture()
def auth(client):
    response = client.post(
        "/v1/life/auth/register",
        json={
            "email": "voz@exemplo.com",
            "password": "senha-forte-123",
            "name": "Alex",
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_voice_requires_user_token(client):
    response = client.post("/v1/life/voice/speech", json={"text": "Bom dia"})

    assert response.status_code == 401


def test_voice_returns_openai_audio(client, auth, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    captured = {}

    def fake_tts(*args):
        captured["args"] = args
        return b"realistic-mp3-bytes"

    monkeypatch.setattr(
        "openjarvis.server.life_voice_routes._openai_tts_request",
        fake_tts,
    )

    response = client.post(
        "/v1/life/voice/speech",
        headers=auth,
        json={"text": "  Bom dia, Alex.  "},
    )

    assert response.status_code == 200
    assert response.content == b"realistic-mp3-bytes"
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-jarvis-voice"] == "cedar"
    assert response.headers["x-ai-generated-voice"] == "true"
    assert captured["args"][1] == "Bom dia, Alex."
    assert captured["args"][2] == "cedar"
    assert captured["args"][3] == "gpt-4o-mini-tts"


def test_voice_is_unavailable_without_provider_key(client, auth, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = client.post(
        "/v1/life/voice/speech",
        headers=auth,
        json={"text": "Bom dia"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Jarvis voice unavailable"


def test_voice_rejects_oversized_text(client, auth):
    response = client.post(
        "/v1/life/voice/speech",
        headers=auth,
        json={"text": "x" * 1_601},
    )

    assert response.status_code == 422
