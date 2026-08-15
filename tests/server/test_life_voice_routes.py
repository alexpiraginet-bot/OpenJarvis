"""Tests for the authenticated Jarvis Life voice endpoint."""

from __future__ import annotations

import re

import httpx
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


def test_realtime_token_requires_user_token(client):
    response = client.post("/v1/life/voice/realtime/token")

    assert response.status_code == 401


def test_realtime_token_is_tenant_bound_and_keeps_standard_key_server_side(
    client,
    auth,
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-only-test")
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["request_kwargs"] = kwargs
            return httpx.Response(
                200,
                json={"value": "ek_test_ephemeral", "expires_at": 1_800_000_000},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(
        "openjarvis.server.life_voice_routes.httpx.AsyncClient",
        FakeAsyncClient,
    )

    response = client.post("/v1/life/voice/realtime/token", headers=auth)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "client_secret": "ek_test_ephemeral",
        "expires_at": 1_800_000_000,
        "model": "gpt-realtime-2.1",
        "voice": "cedar",
    }
    assert captured["url"] == "https://api.openai.com/v1/realtime/client_secrets"
    request_headers = captured["request_kwargs"]["headers"]
    assert request_headers["Authorization"] == "Bearer sk-server-only-test"
    assert re.fullmatch(r"[0-9a-f]{64}", request_headers["OpenAI-Safety-Identifier"])
    assert request_headers["OpenAI-Safety-Identifier"] not in {
        "voz@exemplo.com",
        "Alex",
    }
    session = captured["request_kwargs"]["json"]["session"]
    assert session["type"] == "realtime"
    assert session["model"] == "gpt-realtime-2.1"
    assert session["output_modalities"] == ["audio"]
    assert session["audio"]["output"]["voice"] == "cedar"
    assert session["audio"]["input"]["transcription"] == {
        "model": "gpt-4o-mini-transcribe",
        "language": "pt",
    }
    assert session["audio"]["input"]["turn_detection"] == {
        "type": "server_vad",
        "threshold": 0.5,
        "prefix_padding_ms": 300,
        "silence_duration_ms": 500,
        "create_response": False,
        "interrupt_response": True,
    }
    assert "sk-server-only-test" not in response.text


def test_realtime_token_is_unavailable_without_provider_key(client, auth, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    response = client.post("/v1/life/voice/realtime/token", headers=auth)

    assert response.status_code == 503
    assert response.json()["detail"] == "Jarvis realtime voice unavailable"


def test_realtime_token_sanitizes_provider_failure(client, auth, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-only-test")

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **_kwargs):
            return httpx.Response(
                429,
                json={"error": {"message": "provider-secret-detail"}},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(
        "openjarvis.server.life_voice_routes.httpx.AsyncClient",
        FakeAsyncClient,
    )

    response = client.post("/v1/life/voice/realtime/token", headers=auth)

    assert response.status_code == 502
    assert response.json()["detail"] == "Jarvis realtime voice unavailable"
    assert "provider-secret-detail" not in response.text


def test_realtime_token_is_rate_limited_per_user_before_provider_call(
    client,
    auth,
    monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-only-test")
    provider_calls = 0

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **_kwargs):
            nonlocal provider_calls
            provider_calls += 1
            return httpx.Response(
                200,
                json={
                    "value": f"ek_test_ephemeral_{provider_calls}",
                    "expires_at": 1_800_000_000,
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(
        "openjarvis.server.life_voice_routes.httpx.AsyncClient",
        FakeAsyncClient,
    )

    for _ in range(3):
        response = client.post("/v1/life/voice/realtime/token", headers=auth)
        assert response.status_code == 200

    blocked = client.post("/v1/life/voice/realtime/token", headers=auth)

    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "60"
    assert blocked.json()["detail"] == "Too many realtime token requests"
    assert provider_calls == 3

    other_user = client.post(
        "/v1/life/auth/register",
        json={
            "email": "outra-voz@exemplo.com",
            "password": "senha-forte-456",
            "name": "Bia",
        },
    )
    assert other_user.status_code == 201
    other_auth = {"Authorization": f"Bearer {other_user.json()['token']}"}

    allowed = client.post(
        "/v1/life/voice/realtime/token",
        headers=other_auth,
    )
    assert allowed.status_code == 200
    assert provider_calls == 4
