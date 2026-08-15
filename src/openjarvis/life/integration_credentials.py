"""Encrypted OAuth credential storage backed by Supabase Vault."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Mapping, Optional

from openjarvis.life.db import Database
from openjarvis.life.integrations import IntegrationsError

logger = logging.getLogger(__name__)

_MAX_CREDENTIAL_BYTES = 64 * 1024


class SupabaseVaultCredentialVault:
    """Keep OAuth payloads encrypted and expose only opaque UUID references."""

    _PREFIX = "supabase-oauth-vault:"

    def __init__(self, database: Database) -> None:
        self._db = database

    @classmethod
    def from_database(
        cls,
        database: Database,
    ) -> Optional["SupabaseVaultCredentialVault"]:
        """Return an adapter only for PostgreSQL projects with Vault enabled."""
        if database.backend != "postgres":
            return None
        try:
            with database.transaction():
                row = database.execute(
                    "SELECT to_regclass('vault.secrets') AS table_name"
                ).fetchone()
        except Exception:  # noqa: BLE001 - any probe failure must fail closed
            logger.warning("Supabase OAuth Vault availability probe failed")
            return None
        if row is None or not row["table_name"]:
            return None
        return cls(database)

    @classmethod
    def _id_from_ref(cls, ref: str) -> str:
        if not isinstance(ref, str) or not ref.startswith(cls._PREFIX):
            raise IntegrationsError("Referência de cofre inválida.")
        candidate = ref.removeprefix(cls._PREFIX)
        try:
            return str(uuid.UUID(candidate))
        except ValueError as exc:
            raise IntegrationsError("Referência de cofre inválida.") from exc

    @staticmethod
    def _serialize(payload: Mapping[str, Any]) -> str:
        try:
            serialized = json.dumps(
                dict(payload),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise IntegrationsError("Credencial inválida para armazenamento.") from exc
        if len(serialized.encode("utf-8")) > _MAX_CREDENTIAL_BYTES:
            raise IntegrationsError("Credencial excede o limite seguro do cofre.")
        return serialized

    def store(
        self,
        user_id: str,
        provider: str,
        payload: Mapping[str, Any],
    ) -> str:
        serialized = self._serialize(payload)
        name = f"jarvis-oauth-{provider}-{uuid.uuid4().hex}"
        try:
            with self._db.transaction():
                row = self._db.execute(
                    "SELECT vault.create_secret(?, ?, ?) AS secret_id",
                    (
                        serialized,
                        name,
                        f"Jarvis encrypted OAuth credential for {provider}",
                    ),
                ).fetchone()
        except Exception as exc:  # noqa: BLE001 - never leak provider responses
            raise IntegrationsError(
                "Não foi possível armazenar a credencial no cofre."
            ) from exc
        if row is None or not row["secret_id"]:
            raise IntegrationsError(
                "O cofre não devolveu uma referência válida para a credencial."
            )
        return f"{self._PREFIX}{row['secret_id']}"

    def resolve(self, ref: str) -> dict[str, Any]:
        secret_id = self._id_from_ref(ref)
        try:
            with self._db.transaction():
                row = self._db.execute(
                    "SELECT decrypted_secret FROM vault.decrypted_secrets"
                    " WHERE id = CAST(? AS uuid)",
                    (secret_id,),
                ).fetchone()
        except Exception as exc:  # noqa: BLE001 - fail closed with sanitized error
            raise IntegrationsError("Credencial não encontrada no cofre.") from exc
        if row is None or not row["decrypted_secret"]:
            raise IntegrationsError("Credencial não encontrada no cofre.")
        raw = str(row["decrypted_secret"])
        if len(raw.encode("utf-8")) > _MAX_CREDENTIAL_BYTES:
            raise IntegrationsError("Credencial inválida no cofre.")
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise IntegrationsError("Credencial inválida no cofre.") from exc
        if not isinstance(payload, dict):
            raise IntegrationsError("Credencial inválida no cofre.")
        return payload

    def discard(self, ref: str) -> None:
        secret_id = self._id_from_ref(ref)
        try:
            with self._db.transaction():
                self._db.execute(
                    "DELETE FROM vault.secrets WHERE id = CAST(? AS uuid)",
                    (secret_id,),
                )
        except Exception as exc:  # noqa: BLE001 - no secret details in the API error
            raise IntegrationsError(
                "Não foi possível remover a credencial do cofre."
            ) from exc
