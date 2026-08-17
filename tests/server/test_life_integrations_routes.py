"""Testes da API de integrações do Life (`/v1/life/integrations`).

O contrato HTTP deve ser tão honesto quanto o store: catálogo verdadeiro,
connect-intent que falha fechado com o motivo, e nenhum vazamento de material
de credencial ou de dados de outro tenant.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.life import integrations as integrations_module
from openjarvis.life.app_attest import AppAttestError
from openjarvis.life.integration_sync import IntegrationItem
from openjarvis.life.integrations import PROVIDERS, IntegrationsStore
from openjarvis.life.oauth_providers import OAuthCredential
from openjarvis.server.life_integrations_routes import _healthkit_resource_id
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

    def resolve(self, ref: str) -> dict:
        return dict(self.stored[ref])

    def discard(self, ref: str) -> None:
        self.stored.pop(ref, None)


class _FakeOAuthClient:
    def __init__(self) -> None:
        self.calls = []
        self.fetch_calls = []
        self.items = [
            IntegrationItem(
                external_id="mail-1",
                kind="mail",
                title="Assunto",
                summary="Resumo",
                occurred_at="2026-08-14T11:00:00+00:00",
                metadata={"unread": True},
            )
        ]

    def exchange(self, context, code):
        self.calls.append({"context": context, "code": code})
        return OAuthCredential(
            access_token="provider-access-token",
            refresh_token="provider-refresh-token",
            expires_at="2099-08-14T18:00:00+00:00",
            token_type="Bearer",
            granted_scopes=context.requested_scopes,
            account_label="alex@exemplo.com",
        )

    def fetch_items(self, provider, payload, now):
        self.fetch_calls.append((provider, dict(payload), now))
        return list(self.items)


class _FakeAppAttestStore:
    def __init__(self) -> None:
        self.calls = []

    def verify_assertion(self, user_id, **kwargs):
        if kwargs["assertion"] != "a" * 86:
            raise AppAttestError("invalid assertion")
        self.calls.append({"user_id": user_id, **kwargs})


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ALL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    app = FastAPI()
    app_attest = _FakeAppAttestStore()
    router = create_life_router(str(tmp_path / "life.db"), app_attest_store=app_attest)
    app.include_router(router)
    with TestClient(app) as test_client:
        test_client.life = router.life_context
        test_client.app_attest = app_attest
        yield test_client
    router.life_context.close()


@pytest.fixture()
def callback_client(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    for name, value in GOOGLE_ENV.items():
        monkeypatch.setenv(name, value)
    app = FastAPI()
    vault = _MemoryVault()
    oauth = _FakeOAuthClient()
    router = create_life_router(
        str(tmp_path / "life-oauth.db"),
        integration_vault=vault,
        oauth_client=oauth,
    )
    app.include_router(router)
    with TestClient(app) as test_client:
        test_client.life = router.life_context
        test_client.integration_vault = vault
        test_client.oauth_client = oauth
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


def _app_attest_proof() -> dict:
    return {
        "challenge_id": "calendar-challenge-1234",
        "challenge": "c" * 43,
        "key_id": "k" * 43,
        "assertion": "a" * 86,
    }


def _healthkit_payload(samples: list[dict]) -> str:
    raw = json.dumps(
        {"samples": samples},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_healthkit_payload_digest_matches_the_native_ios_contract():
    raw = (
        b'{"samples":[{"kind":"steps","observed_at":'
        b'"2026-08-14T12:00:00Z","sample_id":"steps:2026-08-14",'
        b'"unit":"count","value":8421}]}'
    )

    assert _healthkit_resource_id("ios-device-1234", raw) == (
        "health:4ab320d62432fe8067e63675cccf3e1a6e54d43e3423738a761278b96383177e"
    )


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
    assert client.post("/v1/life/integrations/gmail/sync").status_code == 401
    assert (
        client.post(
            "/v1/life/integrations/apple_calendar/device-grant",
            json={
                "granted_scopes": ["events.read", "events.write"],
                "device_id": "ios-device-1234",
                "device_label": "iPhone",
            },
        ).status_code
        == 401
    )
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


def test_catalog_hides_native_apple_connect_without_app_attest_identity(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    monkeypatch.delenv("APPLE_TEAM_ID", raising=False)
    monkeypatch.delenv("APPLE_BUNDLE_VERSION", raising=False)
    app = FastAPI()
    router = create_life_router(str(tmp_path / "life-no-app-attest.db"))
    app.include_router(router)

    try:
        with TestClient(app) as native_client:
            auth = _register(native_client, "native-setup@exemplo.com")
            for provider in ("apple_calendar", "apple_health"):
                entry = _catalog_entry(native_client, auth, provider)
                assert entry["availability"] == "needs_setup"
                assert entry["missing_config"] == [
                    "APPLE_TEAM_ID",
                    "APPLE_BUNDLE_VERSION",
                ]
    finally:
        router.life_context.close()


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


def test_connect_is_refused_without_runtime_vault_or_oauth_client(
    client, auth, monkeypatch
):
    """Env não basta quando o runtime não consegue guardar/trocar tokens."""
    for name, value in GOOGLE_ENV.items():
        monkeypatch.setenv(name, value)
    response = client.post("/v1/life/integrations/gmail/connect", headers=auth)
    assert response.status_code == 409
    assert "callback" in response.json()["detail"].lower()


def test_connect_returns_an_authorize_url_and_stays_honest(
    callback_client, monkeypatch
):
    _configure_google(monkeypatch)
    auth = _register(callback_client, "oauth-connect@exemplo.com")
    response = callback_client.post("/v1/life/integrations/gmail/connect", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["authorize_url"].startswith("https://accounts.google.com/")
    assert body["state"]
    assert body["expires_at"]
    assert "google-client-secret" not in response.text
    # Iniciar o OAuth não conecta nada: o catálogo continua sem conexão.
    entry = _catalog_entry(callback_client, auth, "gmail")
    assert entry["connection"] is None
    assert entry["pending_auth"] == {"expires_at": body["expires_at"]}


# -- Callback OAuth ---------------------------------------------------------


def test_callback_connects_without_bearer_and_returns_to_the_single_screen(
    callback_client,
):
    auth = _register(callback_client, "oauth-owner@exemplo.com")
    begun = callback_client.post(
        "/v1/life/integrations/gmail/connect", headers=auth
    ).json()

    response = callback_client.get(
        "/v1/life/integrations/gmail/callback",
        params={"state": begun["state"], "code": "single-use-code"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == ("/vida?integration=gmail&status=connected")
    entry = _catalog_entry(callback_client, auth, "gmail")
    assert entry["connection"]["status"] == "connected"
    assert entry["connection"]["account_label"] == "alex@exemplo.com"
    assert entry["pending_auth"] is None
    assert callback_client.oauth_client.calls[-1]["code"] == "single-use-code"
    stored = next(iter(callback_client.integration_vault.stored.values()))
    assert stored["access_token"] == "provider-access-token"
    assert "provider-access-token" not in response.text
    assert "single-use-code" not in response.text


def test_callback_rejects_provider_mismatch_without_consuming_the_state(
    callback_client,
):
    auth = _register(callback_client, "oauth-mismatch@exemplo.com")
    begun = callback_client.post(
        "/v1/life/integrations/gmail/connect", headers=auth
    ).json()

    mismatch = callback_client.get(
        "/v1/life/integrations/google_calendar/callback",
        params={"state": begun["state"], "code": "wrong-provider-code"},
        follow_redirects=False,
    )
    valid = callback_client.get(
        "/v1/life/integrations/gmail/callback",
        params={"state": begun["state"], "code": "right-provider-code"},
        follow_redirects=False,
    )

    assert mismatch.headers["location"].endswith("status=error")
    assert valid.headers["location"].endswith("status=connected")
    assert [call["code"] for call in callback_client.oauth_client.calls] == [
        "right-provider-code"
    ]


def test_denied_callback_consumes_state_and_never_calls_token_endpoint(
    callback_client,
):
    auth = _register(callback_client, "oauth-denied@exemplo.com")
    begun = callback_client.post(
        "/v1/life/integrations/gmail/connect", headers=auth
    ).json()

    denied = callback_client.get(
        "/v1/life/integrations/gmail/callback",
        params={"state": begun["state"], "error": "access_denied"},
        follow_redirects=False,
    )
    replay = callback_client.get(
        "/v1/life/integrations/gmail/callback",
        params={"state": begun["state"], "code": "late-code"},
        follow_redirects=False,
    )

    assert denied.headers["location"].endswith("status=error")
    assert replay.headers["location"].endswith("status=error")
    assert callback_client.oauth_client.calls == []
    assert _catalog_entry(callback_client, auth, "gmail")["pending_auth"] is None


@pytest.mark.parametrize(
    "params",
    [{}, {"state": "state-without-code"}, {"code": "code-without-state"}],
)
def test_callback_missing_parameters_fails_closed(callback_client, params):
    response = callback_client.get(
        "/v1/life/integrations/gmail/callback",
        params=params,
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("status=error")
    assert callback_client.oauth_client.calls == []


def test_callback_connection_is_tenant_bound(callback_client):
    owner = _register(callback_client, "oauth-tenant-owner@exemplo.com")
    other = _register(callback_client, "oauth-tenant-other@exemplo.com")
    begun = callback_client.post(
        "/v1/life/integrations/gmail/connect", headers=owner
    ).json()

    callback_client.get(
        "/v1/life/integrations/gmail/callback",
        params={"state": begun["state"], "code": "tenant-code"},
        follow_redirects=False,
    )

    assert _catalog_entry(callback_client, owner, "gmail")["connection"] is not None
    assert _catalog_entry(callback_client, other, "gmail")["connection"] is None


# -- Sincronização ----------------------------------------------------------


def test_connected_provider_syncs_normalized_items(callback_client):
    auth = _register(callback_client, "oauth-sync@exemplo.com")
    begun = callback_client.post(
        "/v1/life/integrations/gmail/connect", headers=auth
    ).json()
    callback_client.get(
        "/v1/life/integrations/gmail/callback",
        params={"state": begun["state"], "code": "sync-code"},
        follow_redirects=False,
    )

    response = callback_client.post("/v1/life/integrations/gmail/sync", headers=auth)

    assert response.status_code == 200
    assert response.json()["synced"] == 1
    assert callback_client.oauth_client.fetch_calls[-1][0] == "gmail"
    entry = _catalog_entry(callback_client, auth, "gmail")
    assert entry["connection"]["last_sync_status"] == "ok"
    assert entry["connection"]["last_sync_at"]


def test_sync_rejects_unknown_or_disconnected_provider(callback_client):
    auth = _register(callback_client, "oauth-nosync@exemplo.com")

    assert (
        callback_client.post(
            "/v1/life/integrations/unknown/sync", headers=auth
        ).status_code
        == 404
    )
    response = callback_client.post("/v1/life/integrations/gmail/sync", headers=auth)
    assert response.status_code == 409
    assert "conectado" in response.json()["detail"]


# -- Autorizacao nativa ------------------------------------------------------


def test_apple_calendar_device_grant_is_registered_for_the_current_tenant(client, auth):
    response = client.post(
        "/v1/life/integrations/apple_calendar/device-grant",
        headers=auth,
        json={
            "granted_scopes": ["events.read", "events.write"],
            "device_id": "ios-device-1234",
            "device_label": "iPhone",
            "app_attest": _app_attest_proof(),
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["provider"] == "apple_calendar"
    assert body["connection"]["status"] == "connected"
    assert body["connection"]["granted_scopes"] == [
        "events.read",
        "events.write",
    ]
    assert body["connection"]["has_credential"] is False
    assert client.app_attest.calls[-1]["purpose"] == "device_grant"

    other = _register(client, "bruna-calendar@exemplo.com")
    assert _catalog_entry(client, other, "apple_calendar")["connection"] is None
    assert (
        _catalog_entry(client, auth, "apple_calendar")["connection"]["status"]
        == "connected"
    )


def test_apple_health_device_grant_requires_the_complete_healthkit_scope_set(
    client, auth
):
    response = client.post(
        "/v1/life/integrations/apple_health/device-grant",
        headers=auth,
        json={
            "granted_scopes": [
                "steps.read",
                "sleep.read",
                "heart_rate.read",
                "resting_heart_rate.read",
                "active_energy.read",
                "workouts.read",
            ],
            "device_id": "ios-device-1234",
            "device_label": "iPhone",
            "app_attest": _app_attest_proof(),
        },
    )

    assert response.status_code == 201
    connection = response.json()["connection"]
    assert connection["status"] == "connected"
    assert connection["has_credential"] is False
    assert client.app_attest.calls[-1]["purpose"] == "device_grant"

    partial = client.post(
        "/v1/life/integrations/apple_health/device-grant",
        headers=auth,
        json={
            "granted_scopes": ["steps.read"],
            "device_id": "another-ios-device",
            "device_label": "iPhone",
            "app_attest": _app_attest_proof(),
        },
    )
    assert partial.status_code == 409


def test_apple_health_sync_is_attested_tenant_bound_and_idempotent(client, auth):
    grant = client.post(
        "/v1/life/integrations/apple_health/device-grant",
        headers=auth,
        json={
            "granted_scopes": [
                "steps.read",
                "sleep.read",
                "heart_rate.read",
                "resting_heart_rate.read",
                "active_energy.read",
                "workouts.read",
            ],
            "device_id": "ios-device-1234",
            "device_label": "iPhone",
            "app_attest": _app_attest_proof(),
        },
    )
    assert grant.status_code == 201
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload = {
        "device_id": "ios-device-1234",
        "payload": _healthkit_payload(
            [
                {
                    "sample_id": "steps:2026-08-14",
                    "kind": "steps",
                    "value": 8421,
                    "unit": "count",
                    "observed_at": now,
                },
                {
                    "sample_id": "heart-rate:sample-1",
                    "kind": "heart_rate",
                    "value": 72,
                    "unit": "bpm",
                    "observed_at": now,
                },
            ]
        ),
        "app_attest": _app_attest_proof(),
    }

    first = client.post(
        "/v1/life/integrations/apple_health/device-sync",
        headers=auth,
        json=payload,
    )
    second = client.post(
        "/v1/life/integrations/apple_health/device-sync",
        headers=auth,
        json=payload,
    )

    assert first.status_code == 200
    assert first.json()["synced"] == 2
    assert second.status_code == 200
    assert second.json()["synced"] == 2
    assert client.app_attest.calls[-1]["purpose"] == "health_sync"
    me = client.get("/v1/life/me", headers=auth).json()
    rows = client.life.store.list_records(
        "health_observations", me["user"]["id"], limit=10
    )
    assert len(rows) == 2
    assert {row["kind"] for row in rows} == {"steps", "heart_rate"}
    assert {row["source"] for row in rows} == {"apple_health"}

    other = _register(client, "bruna-health@exemplo.com")
    assert (
        client.post(
            "/v1/life/integrations/apple_health/device-sync",
            headers=other,
            json=payload,
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    ("kind", "value", "unit"),
    [
        ("steps", 10, "bpm"),
        ("heart_rate", 500, "bpm"),
        ("diagnosis", 1, "text"),
    ],
)
def test_apple_health_sync_rejects_untrusted_or_implausible_samples(
    client, auth, kind, value, unit
):
    response = client.post(
        "/v1/life/integrations/apple_health/device-sync",
        headers=auth,
        json={
            "device_id": "ios-device-1234",
            "payload": _healthkit_payload(
                [
                    {
                        "sample_id": "unsafe-sample",
                        "kind": kind,
                        "value": value,
                        "unit": unit,
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    }
                ]
            ),
            "app_attest": _app_attest_proof(),
        },
    )

    assert response.status_code in {409, 422}


def test_calendar_device_grant_rejects_a_partial_or_forged_scope_set(client, auth):
    response = client.post(
        "/v1/life/integrations/apple_calendar/device-grant",
        headers=auth,
        json={
            "granted_scopes": ["events.read"],
            "device_id": "ios-device-1234",
            "device_label": "iPhone",
        },
    )

    assert response.status_code == 409
    assert "completo" in response.json()["detail"].lower()
    assert _catalog_entry(client, auth, "apple_calendar")["connection"] is None


def test_device_grant_rejects_non_device_and_unknown_providers(client, auth):
    body = {
        "granted_scopes": ["events.read"],
        "device_id": "ios-device-1234",
        "device_label": "iPhone",
    }
    assert (
        client.post(
            "/v1/life/integrations/gmail/device-grant",
            headers=auth,
            json=body,
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/v1/life/integrations/unknown/device-grant",
            headers=auth,
            json=body,
        ).status_code
        == 404
    )


# -- Desconectar -------------------------------------------------------------


def test_disconnect_with_nothing_is_404(client, auth):
    response = client.delete("/v1/life/integrations/gmail", headers=auth)
    assert response.status_code == 404


def test_disconnect_cancels_a_pending_authorization(callback_client, monkeypatch):
    _configure_google(monkeypatch)
    auth = _register(callback_client, "oauth-cancel@exemplo.com")
    begun = callback_client.post("/v1/life/integrations/gmail/connect", headers=auth)
    assert begun.status_code == 200
    response = callback_client.delete("/v1/life/integrations/gmail", headers=auth)
    assert response.status_code == 200
    assert response.json()["result"] == "canceled"
    assert _catalog_entry(callback_client, auth, "gmail")["pending_auth"] is None


def test_disconnect_revokes_a_connected_provider(callback_client, monkeypatch):
    _configure_google(monkeypatch)
    auth = _register(callback_client, "oauth-revoke@exemplo.com")
    begun = callback_client.post(
        "/v1/life/integrations/gmail/connect", headers=auth
    ).json()
    callback_client.get(
        "/v1/life/integrations/gmail/callback",
        params={"state": begun["state"], "code": "revoke-code"},
        follow_redirects=False,
    )
    assert _catalog_entry(callback_client, auth, "gmail")["connection"]["status"] == (
        "connected"
    )

    response = callback_client.delete("/v1/life/integrations/gmail", headers=auth)
    assert response.status_code == 200
    assert response.json()["result"] == "revoked"
    connection = _catalog_entry(callback_client, auth, "gmail")["connection"]
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
