"""Credential-vault tests for tenant OAuth integrations."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from openjarvis.life.integration_credentials import SupabaseVaultCredentialVault
from openjarvis.life.integrations import IntegrationsError


class _Rows:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Postgres:
    backend = "postgres"

    def __init__(self, *, vault_available: bool = True) -> None:
        self.vault_available = vault_available
        self.calls: list[tuple[str, tuple]] = []
        self.commits = 0
        self.transactions = 0
        self.transaction_depth = 0
        self.decrypted_secret = ""

    def execute(self, sql, params=()):
        params = tuple(params)
        self.calls.append((sql, params))
        if "to_regclass" in sql:
            table = "vault.secrets" if self.vault_available else None
            return _Rows({"table_name": table})
        if "vault.create_secret" in sql:
            self.decrypted_secret = params[0]
            return _Rows({"secret_id": "2ab9274e-69f9-4c50-a84f-256f48ad802e"})
        if "vault.decrypted_secrets" in sql:
            return _Rows({"decrypted_secret": self.decrypted_secret})
        return _Rows()

    def commit(self):
        self.commits += 1

    @contextmanager
    def transaction(self):
        self.transactions += 1
        self.transaction_depth += 1
        try:
            yield
        finally:
            self.transaction_depth -= 1


def test_vault_is_available_only_on_postgres_with_supabase_vault(life) -> None:
    assert SupabaseVaultCredentialVault.from_database(life.connection) is None
    assert (
        SupabaseVaultCredentialVault.from_database(_Postgres(vault_available=False))
        is None
    )
    assert SupabaseVaultCredentialVault.from_database(_Postgres()) is not None


def test_vault_round_trip_uses_canonical_json_and_opaque_reference() -> None:
    database = _Postgres()
    vault = SupabaseVaultCredentialVault.from_database(database)

    assert vault is not None
    ref = vault.store(
        "user-1",
        "gmail",
        {"refresh_token": "refresh-secret", "access_token": "access-secret"},
    )

    assert ref == "supabase-oauth-vault:2ab9274e-69f9-4c50-a84f-256f48ad802e"
    assert json.loads(database.decrypted_secret) == {
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
    }
    assert database.decrypted_secret == (
        '{"access_token":"access-secret","refresh_token":"refresh-secret"}'
    )
    assert vault.resolve(ref) == {
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
    }
    vault.discard(ref)

    sql = "\n".join(call[0] for call in database.calls)
    assert "vault.create_secret" in sql
    assert "vault.decrypted_secrets" in sql
    assert "DELETE FROM vault.secrets" in sql
    assert database.transactions == 4
    assert database.transaction_depth == 0


@pytest.mark.parametrize(
    "ref",
    ["", "vault://wrong", "supabase-oauth-vault:not-a-uuid"],
)
def test_vault_rejects_invalid_references(ref: str) -> None:
    vault = SupabaseVaultCredentialVault(_Postgres())

    with pytest.raises(IntegrationsError, match="Referência de cofre inválida"):
        vault.resolve(ref)


def test_vault_rejects_invalid_or_non_object_json() -> None:
    database = _Postgres()
    vault = SupabaseVaultCredentialVault(database)

    database.decrypted_secret = "not-json"
    with pytest.raises(IntegrationsError, match="Credencial inválida no cofre"):
        vault.resolve("supabase-oauth-vault:2ab9274e-69f9-4c50-a84f-256f48ad802e")

    database.decrypted_secret = "[]"
    with pytest.raises(IntegrationsError, match="Credencial inválida no cofre"):
        vault.resolve("supabase-oauth-vault:2ab9274e-69f9-4c50-a84f-256f48ad802e")


def test_vault_rejects_unserializable_and_oversized_payloads() -> None:
    vault = SupabaseVaultCredentialVault(_Postgres())

    with pytest.raises(IntegrationsError, match="Credencial inválida"):
        vault.store("user-1", "gmail", {"bad": object()})
    with pytest.raises(IntegrationsError, match="Credencial excede o limite"):
        vault.store("user-1", "gmail", {"access_token": "x" * 65_536})
