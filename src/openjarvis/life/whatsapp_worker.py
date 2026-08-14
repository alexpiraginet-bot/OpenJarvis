"""Lease, deliver and finalize durable Life WhatsApp outbox messages."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol

from openjarvis.channels.whatsapp import (
    WhatsAppListSection,
    WhatsAppOutboundMessage,
    WhatsAppSendResult,
)
from openjarvis.life.whatsapp import OutboxError, OutboxItem, WhatsAppLifeStore

MAX_DELIVERY_ATTEMPTS = 5
CLAIM_STALE_MINUTES = 5
BASE_RETRY_SECONDS = 30
MAX_RETRY_SECONDS = 3_600
DELIVERY_ATTEMPT_BUDGET_SECONDS = 15.0
_PERMANENT_META_CODES = frozenset({"100", "190"})
_SAFE_ERROR_RE = re.compile(r"[^A-Za-z0-9_.:-]")


class StructuredWhatsAppSender(Protocol):
    def send_message(
        self,
        recipient: str,
        message: WhatsAppOutboundMessage,
    ) -> WhatsAppSendResult: ...


@dataclass(frozen=True, slots=True)
class OutboxRunResult:
    claimed: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0


def _bounded_error_code(value: str) -> str:
    sanitized = _SAFE_ERROR_RE.sub("_", value.strip())[:64]
    return sanitized or "provider_error"


def _sections(value: Any) -> tuple[WhatsAppListSection, ...]:
    if not isinstance(value, list):
        return ()
    sections = []
    for section in value:
        if not isinstance(section, Mapping):
            continue
        rows_value = section.get("rows", [])
        rows = []
        if isinstance(rows_value, list):
            for row in rows_value:
                if isinstance(row, (list, tuple)) and len(row) == 3:
                    rows.append(tuple(str(item) for item in row))
        sections.append(
            WhatsAppListSection(
                title=str(section.get("title", "")),
                rows=tuple(rows),
            )
        )
    return tuple(sections)


def _outbound_message(item: OutboxItem) -> WhatsAppOutboundMessage:
    payload = item.payload
    kind = str(payload.get("kind", ""))
    if kind not in {"text", "reply_buttons", "list", "template"}:
        raise OutboxError("Tipo de mensagem da outbox inválido.")
    raw_buttons = payload.get("buttons", [])
    buttons = tuple(
        (str(button[0]), str(button[1]))
        for button in raw_buttons
        if isinstance(button, (list, tuple)) and len(button) == 2
    )
    return WhatsAppOutboundMessage(
        kind=kind,  # type: ignore[arg-type]
        body=str(payload.get("body", "")),
        buttons=buttons,
        sections=_sections(payload.get("sections")),
        list_button=str(payload.get("list_button", "")),
        template_name=str(payload.get("template_name", "")),
        template_language=str(payload.get("template_language", "pt_BR")),
        template_parameters=tuple(
            str(value) for value in payload.get("template_parameters", [])
        ),
        template_quick_replies=tuple(
            str(value) for value in payload.get("template_quick_replies", [])
        ),
        reply_to_message_id=str(payload.get("reply_to_message_id", "")),
    )


class WhatsAppOutboxWorker:
    """Deliver bounded batches without holding a database lock over the network.

    Claims are intentionally acquired one at a time. A process crash after Meta
    accepts a message but before the local CAS finalizes it remains an
    at-least-once delivery window: the current sender contract carries no
    provider idempotency key, and the message ID is in the response that may be
    lost.
    """

    def __init__(
        self,
        store: WhatsAppLifeStore,
        channel: StructuredWhatsAppSender,
        *,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._channel = channel
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic

    def run_once(
        self,
        limit: int = 50,
        *,
        deadline: float | None = None,
    ) -> OutboxRunResult:
        bounded_limit = max(1, min(int(limit), 100))
        claimed = 0
        attempted_ids: list[str] = []
        sent = retried = failed = 0
        for _ in range(bounded_limit):
            if (
                deadline is not None
                and self._monotonic() + DELIVERY_ATTEMPT_BUDGET_SECONDS > deadline
            ):
                break
            claim_now = self._now()
            items = self._store.claim_outbox(
                limit=1,
                now=claim_now,
                max_attempts=MAX_DELIVERY_ATTEMPTS,
                stale_before=claim_now - timedelta(minutes=CLAIM_STALE_MINUTES),
                exclude_ids=attempted_ids,
            )
            if not items:
                break
            item = items[0]
            claimed += 1
            attempted_ids.append(item.id)
            error_code = ""
            result = WhatsAppSendResult(False, error_code="transport_error")
            try:
                if not self._store.briefing_delivery_allowed(item):
                    error_code = "briefing_opt_out"
                else:
                    destination = self._store.resolve_destination(
                        item.user_id,
                        item.channel_link_id,
                    )
                    result = self._channel.send_message(
                        destination,
                        _outbound_message(item),
                    )
            except OutboxError:
                error_code = "link_unavailable"
            except Exception:  # noqa: BLE001 - raw provider details are not persisted
                error_code = "transport_error"

            finalize_now = self._now()
            if error_code == "briefing_opt_out":
                finalized = self._store.finalize_outbox(
                    item.id,
                    lease_token=item.lease_token,
                    status="cancelled",
                    error_code=error_code,
                    now=finalize_now,
                )
                failed += int(finalized)
                continue
            if result.accepted:
                finalized = self._store.finalize_outbox(
                    item.id,
                    lease_token=item.lease_token,
                    status="sent",
                    provider_message_id=result.message_id,
                    now=finalize_now,
                )
                sent += int(finalized)
                continue

            code = _bounded_error_code(error_code or result.error_code)
            permanent = code in _PERMANENT_META_CODES or code == "link_unavailable"
            if permanent or item.attempts >= MAX_DELIVERY_ATTEMPTS:
                finalized = self._store.finalize_outbox(
                    item.id,
                    lease_token=item.lease_token,
                    status="failed",
                    error_code=code,
                    now=finalize_now,
                )
                failed += int(finalized)
                continue
            retry_seconds = min(
                BASE_RETRY_SECONDS * (2 ** max(0, item.attempts - 1)),
                MAX_RETRY_SECONDS,
            )
            finalized = self._store.finalize_outbox(
                item.id,
                lease_token=item.lease_token,
                status="retry",
                error_code=code,
                next_attempt_at=finalize_now + timedelta(seconds=retry_seconds),
                now=finalize_now,
            )
            retried += int(finalized)
        return OutboxRunResult(
            claimed=claimed,
            sent=sent,
            retried=retried,
            failed=failed,
        )

    def record_delivery(self, provider_message_id: str, status: str) -> bool:
        """Apply one delivery webhook using the same monotonic store contract."""
        return self._store.record_delivery_status(
            provider_message_id,
            status,
            now=self._now(),
        )


__all__ = [
    "OutboxRunResult",
    "StructuredWhatsAppSender",
    "WhatsAppOutboxWorker",
]
