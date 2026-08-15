"""Life WhatsApp wiring through the main OpenJarvis application factory."""

from __future__ import annotations

from fastapi.testclient import TestClient

from openjarvis.core.config import JarvisConfig
from openjarvis.server.app import create_app


class _Engine:
    engine_id = "test"


class _Vault:
    def __init__(self) -> None:
        self._addresses: dict[str, str] = {}

    def store(self, user_id: str, channel: str, address: str) -> str:
        ref = f"vault://{user_id}/{channel}"
        self._addresses[ref] = address
        return ref

    def resolve(self, ref: str) -> str:
        return self._addresses[ref]

    def discard(self, ref: str) -> None:
        self._addresses.pop(ref, None)


class _AcceptedMetaResponse:
    status_code = 200
    content = b"{}"

    @staticmethod
    def json() -> dict:
        return {"messages": [{"id": "wamid.test-link"}]}


def _config() -> JarvisConfig:
    config = JarvisConfig()
    config.analytics.enabled = False
    config.traces.enabled = False
    return config


def _isolate_life_database(monkeypatch, tmp_path) -> None:
    openjarvis_home = tmp_path / "openjarvis-home"
    openjarvis_home.mkdir()
    monkeypatch.setenv("OPENJARVIS_HOME", str(openjarvis_home))
    monkeypatch.delenv("OPENJARVIS_LIFE_DB", raising=False)
    monkeypatch.delenv("POSTGRES_PRISMA_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)


def _authorization(app) -> dict[str, str]:
    user = app.state.life_context.users.create_user(
        "life-main-runtime@example.com",
        "correct-horse-battery-staple",
    )
    token = app.state.life_context.users.issue_token(user.id, label="test")
    return {"Authorization": f"Bearer {token}"}


def test_create_app_wires_meta_env_into_life_whatsapp(monkeypatch, tmp_path) -> None:
    _isolate_life_database(monkeypatch, tmp_path)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test-phone-number-id")
    monkeypatch.setenv("OPENJARVIS_LIFE_CHANNEL_PEPPER", "test-channel-pepper")

    vault = _Vault()
    monkeypatch.setattr(
        "openjarvis.server.life_routes.SupabaseVaultAddressVault.from_database",
        lambda database: vault,
    )
    monkeypatch.setattr(
        "httpx.post",
        lambda *args, **kwargs: _AcceptedMetaResponse(),
    )
    app = create_app(
        _Engine(),
        "test-model",
        config=_config(),
        webhook_config={
            "whatsapp_verify_token": "test-verify-token",
            "whatsapp_app_secret": "test-app-secret",
        },
    )

    with TestClient(app) as client:
        authorization = _authorization(app)
        link = client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=authorization,
        )
        verification = client.get(
            "/v1/life/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test-verify-token",
                "hub.challenge": "main-runtime-ready",
            },
        )

    assert link.status_code == 202
    assert link.json()["status"] == "pending"
    assert verification.status_code == 200
    assert verification.text == "main-runtime-ready"
    app.state.life_context.close()


def test_create_app_starts_with_life_whatsapp_fail_closed_without_meta_env(
    monkeypatch, tmp_path
) -> None:
    _isolate_life_database(monkeypatch, tmp_path)
    for name in (
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
        "OPENJARVIS_LIFE_CHANNEL_PEPPER",
        "WHATSAPP_VERIFY_TOKEN",
        "WHATSAPP_APP_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    app = create_app(_Engine(), "test-model", config=_config())

    with TestClient(app) as client:
        authorization = _authorization(app)
        link = client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=authorization,
        )
        verification = client.get(
            "/v1/life/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "anything",
                "hub.challenge": "must-not-leak",
            },
        )

    assert app.state.life_context is not None
    assert link.status_code == 503
    assert verification.status_code == 403
    app.state.life_context.close()


def test_create_app_uses_meta_webhook_env_without_explicit_config(
    monkeypatch, tmp_path
) -> None:
    _isolate_life_database(monkeypatch, tmp_path)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "env-verify-token")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "env-app-secret")
    app = create_app(_Engine(), "test-model", config=_config())

    with TestClient(app) as client:
        verification = client.get(
            "/v1/life/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "env-verify-token",
                "hub.challenge": "env-runtime-ready",
            },
        )

    assert verification.status_code == 200
    assert verification.text == "env-runtime-ready"
    app.state.life_context.close()


def test_create_app_uses_meta_env_when_webhook_config_is_partial(
    monkeypatch, tmp_path
) -> None:
    _isolate_life_database(monkeypatch, tmp_path)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "env-verify-token")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "env-app-secret")
    app = create_app(
        _Engine(),
        "test-model",
        config=_config(),
        webhook_config={"twilio_auth_token": "twilio-only"},
    )

    with TestClient(app) as client:
        verification = client.get(
            "/v1/life/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "env-verify-token",
                "hub.challenge": "partial-config-ready",
            },
        )

    assert verification.status_code == 200
    assert verification.text == "partial-config-ready"
    app.state.life_context.close()
