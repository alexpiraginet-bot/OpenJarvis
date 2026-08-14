"""Normalized records synchronized from tenant-bound external providers."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from openjarvis.life import LifeContext
from openjarvis.life.integrations import (
    PROVIDERS,
    CredentialVault,
    IntegrationsError,
    UnknownProviderError,
)
from openjarvis.life.oauth_providers import OAuthProviderClient, OAuthProviderError

logger = logging.getLogger(__name__)

_SYNC_LEASE_SECONDS = 300
_EXPIRY_SKEW_SECONDS = 60
_ALLOWED_KINDS = frozenset({"activity", "calendar", "mail"})


@dataclass(frozen=True, slots=True)
class IntegrationItem:
    """One bounded provider record safe to persist as untrusted context."""

    external_id: str
    kind: str
    title: str
    summary: str
    occurred_at: str
    source_url: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


class IntegrationSyncError(IntegrationsError):
    """A sanitized, actionable failure for one provider synchronization."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class IntegrationSyncService:
    """Synchronize one tenant/provider under a durable database lease."""

    def __init__(
        self,
        life: LifeContext,
        vault: CredentialVault,
        provider_client: OAuthProviderClient,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = life.connection
        self._vault = vault
        self._provider = provider_client
        self._now = now or (lambda: datetime.now(timezone.utc))

    def sync(self, user_id: str, provider: str) -> dict[str, Any]:
        spec = PROVIDERS.get(provider)
        if spec is None:
            raise UnknownProviderError(f"Provedor desconhecido: {provider!r}")
        if spec.auth_kind != "oauth":
            raise IntegrationSyncError(
                f"{spec.label} não usa sincronização OAuth do servidor.",
                reason="unsupported",
            )
        now = self._now()
        self._claim(user_id, provider, now)
        try:
            row = self._connection_row(user_id, provider)
            old_ref = str(row["credential_ref"] or "")
            credential = dict(self._vault.resolve(old_ref))
            refreshed = False
            if _credential_expired(credential, now):
                credential = self._refresh_and_rotate(
                    user_id, provider, old_ref, credential
                )
                refreshed = True
            try:
                items = self._provider.fetch_items(provider, credential, now)
            except OAuthProviderError as exc:
                if exc.status_code != 401 or refreshed:
                    raise
                row = self._connection_row(user_id, provider)
                credential = self._refresh_and_rotate(
                    user_id,
                    provider,
                    str(row["credential_ref"] or ""),
                    credential,
                )
                items = self._provider.fetch_items(provider, credential, now)
            normalized = [_normalize_item(item) for item in items]
            synced_at = now.isoformat()
            with self._db.transaction():
                for item in normalized:
                    self._upsert(user_id, provider, item, synced_at)
                updated = self._db.execute(
                    "UPDATE integration_connections SET last_sync_at = ?,"
                    " last_sync_status = 'ok', last_error = '', updated_at = ?"
                    " WHERE user_id = ? AND provider = ? AND status = 'connected'",
                    (synced_at, synced_at, user_id, provider),
                ).rowcount
                if updated != 1:
                    raise IntegrationSyncError(
                        "A conexão foi alterada durante a sincronização.",
                        reason="connection_changed",
                    )
            return {
                "provider": provider,
                "synced": len(normalized),
                "last_sync_at": synced_at,
            }
        except OAuthProviderError as exc:
            self._record_provider_error(user_id, provider, exc)
            if exc.error_code == "invalid_grant" or exc.status_code == 401:
                raise IntegrationSyncError(
                    "A autorização expirou; é necessário reconectar este provedor.",
                    reason="reauthorize",
                ) from exc
            raise IntegrationSyncError(
                "O provedor está indisponível para sincronização.",
                reason="provider_error",
            ) from exc
        except IntegrationSyncError:
            self._release_with_error(user_id, provider, "sync_error")
            raise
        except Exception as exc:  # noqa: BLE001 - never expose credential details
            logger.warning("Integration sync failed for %s", provider)
            self._release_with_error(user_id, provider, "internal_error")
            raise IntegrationSyncError(
                "Não foi possível sincronizar este provedor.",
                reason="internal_error",
            ) from exc

    def _claim(self, user_id: str, provider: str, now: datetime) -> None:
        stale_before = (now - timedelta(seconds=_SYNC_LEASE_SECONDS)).isoformat()
        claimed = self._db.execute(
            "UPDATE integration_connections SET last_sync_status = 'syncing',"
            " last_error = '', updated_at = ?"
            " WHERE user_id = ? AND provider = ? AND status = 'connected'"
            " AND credential_ref <> ''"
            " AND (last_sync_status <> 'syncing' OR updated_at < ?)",
            (now.isoformat(), user_id, provider, stale_before),
        ).rowcount
        self._db.commit()
        if claimed == 1:
            return
        row = self._db.execute(
            "SELECT status, credential_ref, last_sync_status"
            " FROM integration_connections WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        ).fetchone()
        if (
            row is None
            or str(row["status"]) != "connected"
            or not str(row["credential_ref"] or "")
        ):
            raise IntegrationSyncError(
                "Este provedor não está conectado.", reason="not_connected"
            )
        raise IntegrationSyncError(
            "Uma sincronização deste provedor já está em andamento.",
            reason="busy",
        )

    def _connection_row(self, user_id: str, provider: str) -> Any:
        row = self._db.execute(
            "SELECT * FROM integration_connections"
            " WHERE user_id = ? AND provider = ? AND status = 'connected'",
            (user_id, provider),
        ).fetchone()
        if row is None or not row["credential_ref"]:
            raise IntegrationSyncError(
                "Este provedor não está conectado.", reason="not_connected"
            )
        return row

    def _refresh_and_rotate(
        self,
        user_id: str,
        provider: str,
        old_ref: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        refreshed = self._provider.refresh(provider, payload)
        refreshed_payload = refreshed.to_vault_payload()
        new_ref = self._vault.store(user_id, provider, refreshed_payload)
        try:
            with self._db.transaction():
                changed = self._db.execute(
                    "UPDATE integration_connections SET credential_ref = ?"
                    " WHERE user_id = ? AND provider = ?"
                    " AND status = 'connected' AND credential_ref = ?",
                    (new_ref, user_id, provider, old_ref),
                ).rowcount
                if changed != 1:
                    raise IntegrationSyncError(
                        "A conexão foi alterada durante a renovação.",
                        reason="connection_changed",
                    )
        except Exception:
            try:
                self._vault.discard(new_ref)
            except Exception:  # noqa: BLE001 - orphan cleanup is best effort
                logger.warning("Failed to discard unclaimed OAuth credential")
            raise
        try:
            self._vault.discard(old_ref)
        except Exception:  # noqa: BLE001 - DB already points only to the new ref
            logger.warning("Failed to discard rotated OAuth credential")
        return refreshed_payload

    def _upsert(
        self,
        user_id: str,
        provider: str,
        item: IntegrationItem,
        now_iso: str,
    ) -> None:
        metadata_json = json.dumps(
            dict(item.metadata),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        self._db.execute(
            "INSERT INTO integration_items"
            " (id, user_id, provider, external_id, kind, title, summary,"
            " occurred_at, source_url, metadata_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(user_id, provider, external_id) DO UPDATE SET"
            " kind = excluded.kind, title = excluded.title,"
            " summary = excluded.summary, occurred_at = excluded.occurred_at,"
            " source_url = excluded.source_url,"
            " metadata_json = excluded.metadata_json,"
            " updated_at = excluded.updated_at",
            (
                uuid.uuid4().hex,
                user_id,
                provider,
                item.external_id,
                item.kind,
                item.title,
                item.summary,
                item.occurred_at,
                item.source_url,
                metadata_json,
                now_iso,
                now_iso,
            ),
        )

    def _record_provider_error(
        self,
        user_id: str,
        provider: str,
        error: OAuthProviderError,
    ) -> None:
        expired = error.error_code == "invalid_grant" or error.status_code == 401
        status_sql = ", status = 'expired'" if expired else ""
        self._db.execute(
            "UPDATE integration_connections SET last_sync_status = 'error',"
            f" last_error = ?, updated_at = ?{status_sql}"
            " WHERE user_id = ? AND provider = ?",
            (error.error_code, self._now().isoformat(), user_id, provider),
        )
        self._db.commit()

    def _release_with_error(self, user_id: str, provider: str, code: str) -> None:
        self._db.execute(
            "UPDATE integration_connections SET last_sync_status = 'error',"
            " last_error = ?, updated_at = ?"
            " WHERE user_id = ? AND provider = ? AND last_sync_status = 'syncing'",
            (code, self._now().isoformat(), user_id, provider),
        )
        self._db.commit()


def _credential_expired(payload: Mapping[str, Any], now: datetime) -> bool:
    try:
        expires_at = datetime.fromisoformat(str(payload.get("expires_at") or ""))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= now + timedelta(seconds=_EXPIRY_SKEW_SECONDS)


def _normalize_item(item: Any) -> IntegrationItem:
    if not isinstance(item, IntegrationItem):
        raise IntegrationSyncError(
            "O provedor devolveu um registro inválido.", reason="invalid_item"
        )
    external_id = item.external_id.strip()
    if not external_id or len(external_id) > 256 or item.kind not in _ALLOWED_KINDS:
        raise IntegrationSyncError(
            "O provedor devolveu um registro inválido.", reason="invalid_item"
        )
    try:
        occurred = datetime.fromisoformat(item.occurred_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntegrationSyncError(
            "O provedor devolveu uma data inválida.", reason="invalid_item"
        ) from exc
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    metadata_json = json.dumps(dict(item.metadata), ensure_ascii=False)
    if len(metadata_json.encode("utf-8")) > 4096:
        raise IntegrationSyncError(
            "O provedor devolveu metadados excessivos.", reason="invalid_item"
        )
    source_url = item.source_url.strip()
    if source_url and not source_url.startswith("https://"):
        source_url = ""
    return IntegrationItem(
        external_id=external_id,
        kind=item.kind,
        title=" ".join(item.title.split())[:300],
        summary=" ".join(item.summary.split())[:800],
        occurred_at=occurred.isoformat(),
        source_url=source_url[:1000],
        metadata=dict(item.metadata),
    )
