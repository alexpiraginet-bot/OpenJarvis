"""Proactive nudges — the difference between a diary and an assistant.

An app the client must *remember to open* cannot tell them a bill is due. This
turns the Today feed into an outbound message and hands it to any registered
channel, so the assistant reaches the client where they already are — WhatsApp
and Telegram in particular, which need no app install at all.

Push notifications are deliberately not the first mechanism here. Web Push
needs VAPID keys, a subscription store and a service-worker handler, and on
iOS it only works once the PWA is on the home screen. The repo already ships
34 channel adapters that work today; using them is the same guidance
`capability-master` gives — reach for what exists before building.

Delivery is deduplicated per alert per day, structurally: the `notifications`
table has a UNIQUE constraint, so a scheduler firing hourly cannot tell a
client four times that the same bill is late.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, Sequence

from openjarvis.life.service import LifeService, today_in
from openjarvis.life.tenancy import User
from openjarvis.life.today import build_today

logger = logging.getLogger(__name__)

#: Only these severities are worth interrupting someone for. An `info` alert
#: belongs on the Today screen, not in their pocket at 8am.
NOTIFIABLE_SEVERITIES = ("critical", "warning")

#: Cap on how many items a single message carries. Past this it stops being a
#: nudge and becomes a wall of text people learn to ignore.
MAX_ITEMS = 5


class MessageSender(Protocol):
    """The part of ``BaseChannel`` this module needs.

    Typing the dependency this narrowly keeps the notifier testable without a
    live Telegram connection, and documents that nothing here needs a channel's
    lifecycle — only its ``send``.
    """

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Deliver *content* to *channel*, returning success."""


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """What one notification run did."""

    sent: bool
    message: str
    alerts: int
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API responses and logs."""
        return {
            "sent": self.sent,
            "message": self.message,
            "alerts": self.alerts,
            "reason": self.reason,
        }


def alert_fingerprint(alert: Dict[str, Any]) -> str:
    """Stable identity for an alert, for deduplication.

    Built from app, record and action rather than the rendered title: a bill
    whose title changes from "vence em 2 dias" to "vence hoje" is the same
    obligation, and re-notifying every day as the wording shifts is exactly
    the nagging this guards against.
    """
    raw = (
        f"{alert.get('app', '')}|{alert.get('record_id', '')}|{alert.get('action', '')}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _money(cents: int, currency: str) -> str:
    symbol = {"BRL": "R$", "USD": "$", "EUR": "€"}.get(currency, currency + " ")
    return f"{symbol}{cents / 100:,.2f}"


def render_digest(alerts: Sequence[Dict[str, Any]], user: User) -> str:
    """Compose the outbound message. Empty string when nothing is worth saying."""
    if not alerts:
        return ""
    first_name = user.name.split()[0] if user.name else ""
    lines = [f"Oi{', ' + first_name if first_name else ''} — resumo rápido:"]
    for alert in alerts[:MAX_ITEMS]:
        marker = "🔴" if alert["severity"] == "critical" else "🟡"
        amount = alert.get("amount_cents") or 0
        suffix = f" ({_money(int(amount), user.currency)})" if amount else ""
        lines.append(f"{marker} {alert['title']}{suffix}")
    remaining = len(alerts) - MAX_ITEMS
    if remaining > 0:
        lines.append(f"…e mais {remaining} item(ns).")
    return "\n".join(lines)


class LifeNotifier:
    """Selects what deserves a nudge, sends it, and remembers that it did."""

    def __init__(self, service: LifeService) -> None:
        self._service = service
        self._conn = service.store.connection

    def pending_alerts(
        self, user: User, *, anchor: Optional[date] = None
    ) -> List[Dict[str, Any]]:
        """Today's notifiable alerts that have not been sent yet today."""
        anchor = anchor or today_in(user.timezone)
        briefing = build_today(self._service, user, anchor=anchor)
        already = self._sent_fingerprints(user.id, anchor)
        return [
            alert
            for alert in briefing["alerts"]
            if alert["severity"] in NOTIFIABLE_SEVERITIES
            and alert_fingerprint(alert) not in already
        ]

    def notify(
        self,
        user: User,
        sender: MessageSender,
        destination: str,
        *,
        channel_name: str = "",
        anchor: Optional[date] = None,
    ) -> DeliveryResult:
        """Send the pending digest to one client, then record what was sent.

        Nothing is recorded when the send fails, so a channel outage means the
        client is told late rather than never.
        """
        anchor = anchor or today_in(user.timezone)
        alerts = self.pending_alerts(user, anchor=anchor)
        if not alerts:
            return DeliveryResult(
                sent=False, message="", alerts=0, reason="nothing-pending"
            )

        message = render_digest(alerts, user)
        try:
            delivered = sender.send(destination, message)
        except Exception as exc:  # noqa: BLE001 — a channel must not crash a cron
            logger.warning("Life notification failed for %s: %s", user.id, exc)
            return DeliveryResult(
                sent=False, message=message, alerts=len(alerts), reason=str(exc)
            )

        if not delivered:
            return DeliveryResult(
                sent=False,
                message=message,
                alerts=len(alerts),
                reason="channel-refused",
            )

        self._record(user.id, alerts, anchor, channel_name)
        return DeliveryResult(sent=True, message=message, alerts=len(alerts))

    # -- Internals -----------------------------------------------------------

    def _sent_fingerprints(self, user_id: str, anchor: date) -> set:
        rows = self._conn.execute(
            "SELECT fingerprint FROM notifications WHERE user_id = ? AND sent_on = ?",
            (user_id, anchor.isoformat()),
        ).fetchall()
        return {row["fingerprint"] for row in rows}

    def _record(
        self,
        user_id: str,
        alerts: Sequence[Dict[str, Any]],
        anchor: date,
        channel_name: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.executemany(
            "INSERT OR IGNORE INTO notifications"
            " (id, user_id, fingerprint, sent_on, channel, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    uuid.uuid4().hex,
                    user_id,
                    alert_fingerprint(alert),
                    anchor.isoformat(),
                    channel_name,
                    now,
                )
                for alert in alerts
            ],
        )
        self._conn.commit()


__all__ = [
    "DeliveryResult",
    "LifeNotifier",
    "MessageSender",
    "alert_fingerprint",
    "render_digest",
]
