"""Tenant isolation, idempotency and credential rotation for provider sync."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.life import integrations as integrations_module
from openjarvis.life.integration_sync import (
    IntegrationItem,
    IntegrationSyncError,
    IntegrationSyncService,
)
from openjarvis.life.integrations import IntegrationsStore
from openjarvis.life.oauth_providers import OAuthCredential, OAuthProviderError

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


class _MemoryVault:
    def __init__(self) -> None:
        self.stored: dict[str, dict] = {}
        self.discarded: list[str] = []
        self.counter = 0

    def store(self, user_id, provider, payload):
        self.counter += 1
        ref = f"vault://{user_id}/{provider}/{self.counter}"
        self.stored[ref] = dict(payload)
        return ref

    def resolve(self, ref):
        return dict(self.stored[ref])

    def discard(self, ref):
        self.discarded.append(ref)
        self.stored.pop(ref, None)


class _ProviderClient:
    def __init__(self, items=None) -> None:
        self.items = list(items or [])
        self.fetch_calls = []
        self.refresh_calls = []
        self.fetch_error = None
        self.refresh_error = None

    def fetch_items(self, provider, payload, now):
        self.fetch_calls.append((provider, dict(payload), now))
        if self.fetch_error:
            raise self.fetch_error
        return list(self.items)

    def refresh(self, provider, payload):
        self.refresh_calls.append((provider, dict(payload)))
        if self.refresh_error:
            raise self.refresh_error
        return OAuthCredential(
            access_token="fresh-access",
            refresh_token="fresh-refresh",
            expires_at=(NOW + timedelta(hours=1)).isoformat(),
            token_type="Bearer",
            granted_scopes=("scope.one",),
        )


def _connect(life, user, vault, monkeypatch, *, expires_at: str) -> str:
    monkeypatch.setenv("OPENJARVIS_LIFE_GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("OPENJARVIS_LIFE_GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("OPENJARVIS_LIFE_PUBLIC_BASE_URL", "https://jarvis.example")
    monkeypatch.setattr(integrations_module, "OAUTH_CALLBACK_IMPLEMENTED", True)
    store = IntegrationsStore(life, vault=vault, now=lambda: NOW)
    begun = store.begin_authorization(user.id, "gmail")
    store.complete_authorization(
        begun["state"],
        granted_scopes=["scope.one"],
        credential_payload={
            "access_token": "initial-access",
            "refresh_token": "initial-refresh",
            "expires_at": expires_at,
            "granted_scopes": ["scope.one"],
            "token_type": "Bearer",
        },
    )
    row = life.connection.execute(
        "SELECT credential_ref FROM integration_connections"
        " WHERE user_id = ? AND provider = 'gmail'",
        (user.id,),
    ).fetchone()
    return str(row["credential_ref"])


def _mail(summary: str = "Resumo inicial") -> IntegrationItem:
    return IntegrationItem(
        external_id="mail-1",
        kind="mail",
        title="Assunto",
        summary=summary,
        occurred_at="2026-08-14T11:00:00+00:00",
        source_url="https://mail.google.com/mail/u/0/#inbox/mail-1",
        metadata={"from": "cliente@example.com", "unread": True},
    )


def test_sync_is_tenant_scoped_and_idempotently_updates_one_row(
    life, user, monkeypatch
) -> None:
    vault = _MemoryVault()
    _connect(
        life,
        user,
        vault,
        monkeypatch,
        expires_at=(NOW + timedelta(hours=1)).isoformat(),
    )
    provider = _ProviderClient([_mail()])
    service = IntegrationSyncService(life, vault, provider, now=lambda: NOW)

    first = service.sync(user.id, "gmail")
    provider.items = [_mail("Resumo atualizado")]
    second = service.sync(user.id, "gmail")

    rows = life.connection.execute(
        "SELECT * FROM integration_items WHERE user_id = ?", (user.id,)
    ).fetchall()
    assert first["synced"] == second["synced"] == 1
    assert len(rows) == 1
    assert rows[0]["summary"] == "Resumo atualizado"
    assert (
        life.connection.execute(
            "SELECT COUNT(*) AS total FROM integration_items WHERE user_id = ?",
            ("another-user",),
        ).fetchone()["total"]
        == 0
    )


def test_expired_credential_is_refreshed_and_rotated_before_fetch(
    life, user, monkeypatch
) -> None:
    vault = _MemoryVault()
    old_ref = _connect(
        life,
        user,
        vault,
        monkeypatch,
        expires_at=(NOW - timedelta(minutes=1)).isoformat(),
    )
    provider = _ProviderClient([_mail()])
    service = IntegrationSyncService(life, vault, provider, now=lambda: NOW)

    service.sync(user.id, "gmail")

    row = life.connection.execute(
        "SELECT credential_ref FROM integration_connections WHERE user_id = ?",
        (user.id,),
    ).fetchone()
    assert provider.refresh_calls[0][1]["refresh_token"] == "initial-refresh"
    assert provider.fetch_calls[0][1]["access_token"] == "fresh-access"
    assert row["credential_ref"] != old_ref
    assert old_ref in vault.discarded
    assert old_ref not in vault.stored


def test_invalid_grant_marks_connection_expired_without_leaking_tokens(
    life, user, monkeypatch
) -> None:
    vault = _MemoryVault()
    _connect(
        life,
        user,
        vault,
        monkeypatch,
        expires_at=(NOW - timedelta(minutes=1)).isoformat(),
    )
    provider = _ProviderClient()
    provider.refresh_error = OAuthProviderError("gmail", 400, "invalid_grant")
    service = IntegrationSyncService(life, vault, provider, now=lambda: NOW)

    with pytest.raises(IntegrationSyncError, match="reconectar"):
        service.sync(user.id, "gmail")

    row = life.connection.execute(
        "SELECT * FROM integration_connections WHERE user_id = ?", (user.id,)
    ).fetchone()
    assert row["status"] == "expired"
    assert row["last_error"] == "invalid_grant"
    assert "initial-access" not in tuple(str(value) for value in dict(row).values())


def test_provider_failure_records_only_a_sanitized_code(
    life, user, monkeypatch
) -> None:
    vault = _MemoryVault()
    _connect(
        life,
        user,
        vault,
        monkeypatch,
        expires_at=(NOW + timedelta(hours=1)).isoformat(),
    )
    provider = _ProviderClient()
    provider.fetch_error = OAuthProviderError("gmail", 503, "api_error")
    service = IntegrationSyncService(life, vault, provider, now=lambda: NOW)

    with pytest.raises(IntegrationSyncError, match="indisponível"):
        service.sync(user.id, "gmail")

    row = life.connection.execute(
        "SELECT * FROM integration_connections WHERE user_id = ?", (user.id,)
    ).fetchone()
    assert row["status"] == "connected"
    assert row["last_sync_status"] == "error"
    assert row["last_error"] == "api_error"


def test_sync_rejects_missing_connection_and_an_active_claim(
    life, user, monkeypatch
) -> None:
    vault = _MemoryVault()
    service = IntegrationSyncService(life, vault, _ProviderClient(), now=lambda: NOW)
    with pytest.raises(IntegrationSyncError, match="não está conectado"):
        service.sync(user.id, "gmail")

    _connect(
        life,
        user,
        vault,
        monkeypatch,
        expires_at=(NOW + timedelta(hours=1)).isoformat(),
    )
    life.connection.execute(
        "UPDATE integration_connections SET last_sync_status = 'syncing',"
        " updated_at = ? WHERE user_id = ?",
        (NOW.isoformat(), user.id),
    )
    life.connection.commit()

    with pytest.raises(IntegrationSyncError, match="já está em andamento"):
        service.sync(user.id, "gmail")
