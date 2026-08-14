"""Tests for the standalone Jarvis Life application."""

from __future__ import annotations

from fastapi.testclient import TestClient

from openjarvis.engine import cloud as cloud_module
from openjarvis.life.server import create_life_app


class _FakeEngine:
    engine_id = "test"


def test_standalone_app_attaches_injected_engine_and_model(tmp_path) -> None:
    engine = _FakeEngine()

    app = create_life_app(
        db_path=str(tmp_path / "life.db"),
        serve_static=False,
        engine=engine,
        model="test-model",
    )

    assert app.state.engine is engine
    assert app.state.config.model == "test-model"


def test_standalone_health_without_ai_key(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app = create_life_app(
        db_path=str(tmp_path / "life.db"),
        serve_static=False,
    )

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "life"}
    assert not hasattr(app.state, "engine")


def test_standalone_app_accepts_anthropic_as_only_cloud_provider(
    monkeypatch, tmp_path
) -> None:
    engine = _FakeEngine()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setattr(cloud_module, "CloudEngine", lambda: engine)

    app = create_life_app(
        db_path=str(tmp_path / "life.db"),
        serve_static=False,
        model="claude-haiku-4-5",
    )

    assert app.state.engine is engine
    assert app.state.config.model == "claude-haiku-4-5"


def test_standalone_app_wires_injected_whatsapp_dependencies(tmp_path) -> None:
    class Vault:
        def store(self, user_id, channel, address):
            return f"vault://{user_id}/{channel}/1"

        def resolve(self, ref):
            return "+5527999990001"

        def discard(self, ref):
            return None

    class Channel:
        def send(self, channel, content, **kwargs):
            return True

    app = create_life_app(
        db_path=str(tmp_path / "life.db"),
        serve_static=False,
        channel_address_vault=Vault(),
        channel_pepper=b"test-channel-pepper",
        whatsapp_channel=Channel(),
        whatsapp_verify_token="verify-token",
        whatsapp_app_secret="app-secret",
    )

    paths = {route.path for route in app.routes}
    assert "/v1/life/channels/whatsapp/link" in paths
    assert "/v1/life/webhooks/whatsapp" in paths
