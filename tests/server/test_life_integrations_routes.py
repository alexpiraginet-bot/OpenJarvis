"""Testes da API de integrações do Life (`/v1/life/integrations`).

O contrato HTTP deve ser tão honesto quanto o store: catálogo verdadeiro,
connect-intent que falha fechado com o motivo, e nenhum vazamento de material
de credencial ou de dados de outro tenant.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.life import integrations as integrations_module
from openjarvis.life.integrations import PROVIDERS, IntegrationsStore
from openjarvis.server.life_routes import create_life_router

GOOGLE_ENV = {
    "OPENJARVIS_LIFE_GOOGLE_CLIENT_ID": "google-client-id",
    "OPENJARVIS_LIFE_GOOGLE_CLIENT_SECRET": "google-client-secret",
    "OPENJARVIS_LIFE_PUBLIC_BASE_URL": "https://life.exemplo.com",
}

ALL_ENV_VARS = sorted(
    {name for spec in PROVIDERS.values() for name in spec.env_vars}
    | {"OPENJARVIS_LIFE_PUBLIC_BASE_URL"}
)


class _MemoryVault:
    """Cofre em memória para simular conexões concluídas nos testes."""

    def __init__(self) -> None:
        self.stored: dict[str, dict] = {}

    def store(self, user_id: str, provider: str, payload) -> str:
        ref = f"vault://{user_id}/{provider}/{len(self.stored) + 1}"
        self.stored[ref] = dict(payload)
        return ref

    def discard(self, ref: str) -> None:
        self.stored.pop(ref, None)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ALL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    app = FastAPI()
    router = create_life_router(str(tmp_path / "life.db"))
    app.include_router(router)
    with TestClient(app) as test_client:
        test_client.life = router.life_context
        yield test_client
    router.life_context.close()


def _register(client, email: str) -> dict:
    response = client.post(
        "/v1/life/auth/register",
        json={"email": email, "password": "senha-forte-123"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture()
def auth(client):
    return _register(client, "alex@exemplo.com")


def _configure_google(monkeypatch) -> None:
    """Env + gate do callback ligado, para exercer o contrato completo."""
    for name, value in GOOGLE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(integrations_module, "OAUTH_CALLBACK_IMPLEMENTED", True)


def _catalog_entry(client, auth, provider: str) -> dict:
    body = client.get("/v1/life/integrations", headers=auth).json()
    return next(item for item in body["providers"] if item["id"] == provider)


def _seed_connected_gmail(client, auth, monkeypatch) -> None:
    """Conecta o Gmail do usuário autenticado direto pelo store compartilhado."""
    _configure_google(monkeypatch)
    me = client.get("/v1/life/me", headers=auth).json()
    store = IntegrationsStore(client.life, vault=_MemoryVault())
    begun = store.begin_authorization(me["user"]["id"], "gmail")
    store.complete_authorization(
        begun["state"],
        granted_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        account_label="alex@exemplo.com",
        credential_payload={"access_token": "segredo-vivo"},
    )


# -- Autenticação ------------------------------------------------------------


def test_every_integration_route_requires_a_bearer_token(client):
    assert client.get("/v1/life/integrations").status_code == 401
    assert client.post("/v1/life/integrations/gmail/connect").status_code == 401
    assert client.delete("/v1/life/integrations/gmail").status_code == 401


# -- Catálogo ----------------------------------------------------------------


def test_catalog_lists_the_curated_providers_truthfully(client, auth):
    body = client.get("/v1/life/integrations", headers=auth).json()
    ids = [item["id"] for item in body["providers"]]
    assert ids == list(PROVIDERS)
    for item in body["providers"]:
        assert item["connection"] is None
        assert item["availability"] in (
            "available",
            "needs_setup",
            "device_only",
            "coming_soon",
        )
    assert body["summary"] == {"connected": 0, "attention": 0, "pending": 0}


def test_catalog_names_missing_config_without_leaking_values(client, auth, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_GOOGLE_CLIENT_ID", "valor-sensivel")
    entry = _catalog_entry(client, auth, "gmail")
    assert entry["availability"] == "needs_setup"
    assert "OPENJARVIS_LIFE_GOOGLE_CLIENT_SECRET" in entry["missing_config"]
    assert "valor-sensivel" not in str(entry)


# -- Connect-intent ----------------------------------------------------------


def test_connect_unknown_provider_is_404(client, auth):
    response = client.post("/v1/life/integrations/telegram/connect", headers=auth)
    assert response.status_code == 404


def test_connect_fails_closed_when_the_provider_is_not_ready(client, auth):
    for provider in ("whatsapp", "open_finance", "apple_health"):
        response = client.post(
            f"/v1/life/integrations/{provider}/connect", headers=auth
        )
        assert response.status_code == 409, provider

    unconfigured = client.post("/v1/life/integrations/gmail/connect", headers=auth)
    assert unconfigured.status_code == 409
    assert "OPENJARVIS_LIFE_GOOGLE_CLIENT_ID" in unconfigured.json()["detail"]


def test_connect_is_refused_until_the_callback_ships(client, auth, monkeypatch):
    """Só env não libera o fluxo: sem callback publicado, conectar é 409."""
    for name, value in GOOGLE_ENV.items():
        monkeypatch.setenv(name, value)
    response = client.post("/v1/life/integrations/gmail/connect", headers=auth)
    assert response.status_code == 409
    assert "callback" in response.json()["detail"].lower()


def test_connect_returns_an_authorize_url_and_stays_honest(client, auth, monkeypatch):
    _configure_google(monkeypatch)
    response = client.post("/v1/life/integrations/gmail/connect", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["authorize_url"].startswith("https://accounts.google.com/")
    assert body["state"]
    assert body["expires_at"]
    assert "google-client-secret" not in response.text
    # Iniciar o OAuth não conecta nada: o catálogo continua sem conexão.
    entry = _catalog_entry(client, auth, "gmail")
    assert entry["connection"] is None
    assert entry["pending_auth"] == {"expires_at": body["expires_at"]}


# -- Desconectar -------------------------------------------------------------


def test_disconnect_with_nothing_is_404(client, auth):
    response = client.delete("/v1/life/integrations/gmail", headers=auth)
    assert response.status_code == 404


def test_disconnect_cancels_a_pending_authorization(client, auth, monkeypatch):
    _configure_google(monkeypatch)
    client.post("/v1/life/integrations/gmail/connect", headers=auth)
    response = client.delete("/v1/life/integrations/gmail", headers=auth)
    assert response.status_code == 200
    assert response.json()["result"] == "canceled"
    assert _catalog_entry(client, auth, "gmail")["pending_auth"] is None


def test_disconnect_revokes_a_connected_provider(client, auth, monkeypatch):
    _seed_connected_gmail(client, auth, monkeypatch)
    assert _catalog_entry(client, auth, "gmail")["connection"]["status"] == (
        "connected"
    )

    response = client.delete("/v1/life/integrations/gmail", headers=auth)
    assert response.status_code == 200
    assert response.json()["result"] == "revoked"
    connection = _catalog_entry(client, auth, "gmail")["connection"]
    assert connection["status"] == "revoked"
    assert connection["has_credential"] is False


# -- Isolamento por tenant ---------------------------------------------------


def test_integrations_are_isolated_between_tenants(client, auth, monkeypatch):
    _seed_connected_gmail(client, auth, monkeypatch)
    other = _register(client, "bruna@exemplo.com")

    entry = _catalog_entry(client, other, "gmail")
    assert entry["connection"] is None
    assert client.delete("/v1/life/integrations/gmail", headers=other).status_code == (
        404
    )
    # E a conexão do titular sobrevive à tentativa alheia.
    assert _catalog_entry(client, auth, "gmail")["connection"]["status"] == (
        "connected"
    )


def test_no_credential_material_ever_leaves_the_api(client, auth, monkeypatch):
    _seed_connected_gmail(client, auth, monkeypatch)
    response = client.get("/v1/life/integrations", headers=auth)
    assert "segredo-vivo" not in response.text
    assert "credential_ref" not in response.text
    assert "vault://" not in response.text
