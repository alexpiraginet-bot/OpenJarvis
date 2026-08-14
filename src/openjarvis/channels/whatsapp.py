"""WhatsAppChannel — WhatsApp Cloud API adapter."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

from openjarvis.channels._stubs import (
    BaseChannel,
    ChannelHandler,
    ChannelMessage,
    ChannelStatus,
)
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.registry import ChannelRegistry

logger = logging.getLogger(__name__)

_GRAPH_VERSION_RE = re.compile(r"^v\d+\.\d+$")
_META_SENDER_RE = re.compile(r"^[1-9]\d{7,14}$")


@dataclass(frozen=True, slots=True)
class WhatsAppListSection:
    """A deterministic section for an interactive WhatsApp list."""

    title: str
    rows: Tuple[Tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class WhatsAppOutboundMessage:
    """Provider-neutral outbound message accepted by the Cloud API adapter."""

    kind: Literal["text", "reply_buttons", "list", "template"]
    body: str
    buttons: Tuple[Tuple[str, str], ...] = ()
    sections: Tuple[WhatsAppListSection, ...] = ()
    list_button: str = ""
    template_name: str = ""
    template_language: str = "pt_BR"
    template_parameters: Tuple[str, ...] = ()
    template_quick_replies: Tuple[str, ...] = ()
    reply_to_message_id: str = ""


@dataclass(frozen=True, slots=True)
class WhatsAppSendResult:
    """Sanitized provider acceptance result; secrets and bodies never appear."""

    accepted: bool
    message_id: str = ""
    error_code: str = ""


def verify_webhook_signature(app_secret: str, body: bytes, signature: str) -> bool:
    """Validate Meta's ``X-Hub-Signature-256`` header, failing closed."""
    if not app_secret or not signature.startswith("sha256="):
        return False
    expected = (
        "sha256="
        + hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(signature, expected)


def _message_metadata(
    message_type: str,
    message: Mapping[str, Any],
    *,
    phone_number_id: str,
) -> tuple[str, Dict[str, Any]]:
    """Return visible content plus a bounded metadata envelope."""
    metadata: Dict[str, Any] = {
        "kind": message_type or "unknown",
        "phone_number_id": phone_number_id,
    }
    if message_type == "text":
        text = message.get("text")
        body = text.get("body", "") if isinstance(text, Mapping) else ""
        return str(body), metadata
    if message_type in {"audio", "image", "document", "video"}:
        media = message.get(message_type)
        if isinstance(media, Mapping):
            metadata["media_id"] = str(media.get("id", ""))
            metadata["mime_type"] = str(media.get("mime_type", ""))
            if message_type == "document":
                metadata["filename"] = str(media.get("filename", ""))
            caption = media.get("caption", "")
            return str(caption) if isinstance(caption, str) else "", metadata
        return "", metadata
    if message_type == "interactive":
        interactive = message.get("interactive")
        if not isinstance(interactive, Mapping):
            return "", metadata
        interactive_type = str(interactive.get("type", ""))
        reply = interactive.get(interactive_type)
        metadata["interactive_type"] = interactive_type
        if isinstance(reply, Mapping):
            metadata["action_id"] = str(reply.get("id", ""))
            description = reply.get("description")
            if isinstance(description, str) and description:
                metadata["description"] = description
            return str(reply.get("title", "")), metadata
        return "", metadata
    return "", metadata


def _normalize_meta_sender(sender: object) -> str:
    """Convert Meta's digits-only WhatsApp ID to internal E.164 format."""
    raw = str(sender).strip()
    digits = raw.removeprefix("+")
    return f"+{digits}" if _META_SENDER_RE.fullmatch(digits) else raw


def _meta_recipient(recipient: object) -> str:
    """Convert internal E.164 to the digits-only Cloud API recipient."""
    raw = str(recipient).strip()
    digits = raw.removeprefix("+")
    if not _META_SENDER_RE.fullmatch(digits):
        raise ValueError("WhatsApp recipient must be a valid international number")
    return digits


def normalize_webhook_messages(
    payload: Mapping[str, Any],
) -> List[ChannelMessage]:
    """Normalize Meta messages and delivery receipts into channel envelopes."""
    normalized: List[ChannelMessage] = []
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        return normalized
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        changes = entry.get("changes", [])
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, Mapping):
                continue
            value = change.get("value", {})
            if not isinstance(value, Mapping):
                continue
            provider_metadata = value.get("metadata", {})
            phone_number_id = (
                str(provider_metadata.get("phone_number_id", ""))
                if isinstance(provider_metadata, Mapping)
                else ""
            )
            messages = value.get("messages", [])
            if isinstance(messages, list):
                for message in messages:
                    if not isinstance(message, Mapping):
                        continue
                    sender = _normalize_meta_sender(message.get("from", ""))
                    message_type = str(message.get("type", "unknown"))
                    content, metadata = _message_metadata(
                        message_type,
                        message,
                        phone_number_id=phone_number_id,
                    )
                    normalized.append(
                        ChannelMessage(
                            channel="whatsapp",
                            sender=sender,
                            content=content,
                            message_id=str(message.get("id", "")),
                            conversation_id=sender,
                            metadata=metadata,
                        )
                    )
            statuses = value.get("statuses", [])
            if isinstance(statuses, list):
                for status in statuses:
                    if not isinstance(status, Mapping):
                        continue
                    recipient = str(status.get("recipient_id", ""))
                    normalized.append(
                        ChannelMessage(
                            channel="whatsapp",
                            sender="",
                            content="",
                            message_id=str(status.get("id", "")),
                            conversation_id=recipient,
                            metadata={
                                "kind": "delivery_status",
                                "status": str(status.get("status", "")),
                                "timestamp": str(status.get("timestamp", "")),
                                "recipient_id": recipient,
                                "phone_number_id": phone_number_id,
                            },
                        )
                    )
    return normalized


def _validate_outbound(message: WhatsAppOutboundMessage) -> None:
    if not message.body.strip():
        raise ValueError("WhatsApp message body is required")
    if message.kind == "reply_buttons":
        if not message.buttons:
            raise ValueError("Reply-button messages require at least one button")
        identifiers = [identifier.strip() for identifier, _ in message.buttons]
        has_blank_id = any(not identifier for identifier in identifiers)
        has_duplicate_id = len(set(identifiers)) != len(identifiers)
        if has_blank_id or has_duplicate_id:
            raise ValueError("Reply-button IDs must be non-empty and unique")
        if any(not title.strip() for _, title in message.buttons):
            raise ValueError("Reply-button titles are required")
    elif message.kind == "list":
        if not message.list_button.strip() or not message.sections:
            raise ValueError("List messages require a button and sections")
        rows = [row for section in message.sections for row in section.rows]
        identifiers = [identifier.strip() for identifier, _, _ in rows]
        if not rows or any(not identifier for identifier in identifiers):
            raise ValueError("List rows require IDs")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("List row IDs must be unique")
        if any(not title.strip() for _, title, _ in rows):
            raise ValueError("List row titles are required")
    elif message.kind == "template":
        if not message.template_name.strip():
            raise ValueError("Template messages require a template name")
        if any(not value.strip() for value in message.template_parameters):
            raise ValueError("Template body parameters cannot be blank")
        if any(not value.strip() for value in message.template_quick_replies):
            raise ValueError("Template quick-reply payloads cannot be blank")


def _outbound_payload(
    recipient: str, message: WhatsAppOutboundMessage
) -> Dict[str, Any]:
    _validate_outbound(message)
    payload: Dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _meta_recipient(recipient),
    }
    if message.reply_to_message_id:
        payload["context"] = {"message_id": message.reply_to_message_id}
    if message.kind == "text":
        payload.update({"type": "text", "text": {"body": message.body}})
    elif message.kind == "reply_buttons":
        payload.update(
            {
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": message.body},
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {"id": identifier, "title": title},
                            }
                            for identifier, title in message.buttons
                        ]
                    },
                },
            }
        )
    elif message.kind == "list":
        payload.update(
            {
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": message.body},
                    "action": {
                        "button": message.list_button,
                        "sections": [
                            {
                                "title": section.title,
                                "rows": [
                                    {
                                        "id": identifier,
                                        "title": title,
                                        **(
                                            {"description": description}
                                            if description
                                            else {}
                                        ),
                                    }
                                    for identifier, title, description in section.rows
                                ],
                            }
                            for section in message.sections
                        ],
                    },
                },
            }
        )
    else:
        components: List[Dict[str, Any]] = []
        if message.template_parameters:
            components.append(
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": value}
                        for value in message.template_parameters
                    ],
                }
            )
        components.extend(
            {
                "type": "button",
                "sub_type": "quick_reply",
                "index": str(index),
                "parameters": [{"type": "payload", "payload": payload_value}],
            }
            for index, payload_value in enumerate(message.template_quick_replies)
        )
        payload.update(
            {
                "type": "template",
                "template": {
                    "name": message.template_name,
                    "language": {"code": message.template_language},
                    **({"components": components} if components else {}),
                },
            }
        )
    return payload


@ChannelRegistry.register("whatsapp")
class WhatsAppChannel(BaseChannel):
    """WhatsApp Cloud API channel adapter (send-only).

    Parameters
    ----------
    access_token:
        WhatsApp Cloud API access token.  Falls back to
        ``WHATSAPP_ACCESS_TOKEN`` env var.
    phone_number_id:
        WhatsApp phone number ID.  Falls back to
        ``WHATSAPP_PHONE_NUMBER_ID`` env var.
    bus:
        Optional event bus for publishing channel events.
    """

    channel_id = "whatsapp"

    def __init__(
        self,
        access_token: str = "",
        *,
        phone_number_id: str = "",
        graph_api_version: str = "",
        bus: Optional[EventBus] = None,
    ) -> None:
        self._token = access_token or os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
        self._phone_number_id = phone_number_id or os.environ.get(
            "WHATSAPP_PHONE_NUMBER_ID",
            "",
        )
        self._bus = bus
        resolved_graph_version = (
            graph_api_version
            or os.environ.get("WHATSAPP_GRAPH_API_VERSION", "")
            or "v25.0"
        )
        if not _GRAPH_VERSION_RE.fullmatch(resolved_graph_version):
            raise ValueError("graph_api_version must look like v25.0")
        self._graph_api_version = resolved_graph_version
        self._handlers: List[ChannelHandler] = []
        self._status = ChannelStatus.DISCONNECTED

    # -- connection lifecycle ---------------------------------------------------

    def connect(self) -> None:
        """Mark as connected (send-only — no persistent connection)."""
        if not self._token:
            logger.warning("No WhatsApp access token configured")
            self._status = ChannelStatus.ERROR
            return
        self._status = ChannelStatus.CONNECTED

    def disconnect(self) -> None:
        """Mark as disconnected."""
        self._status = ChannelStatus.DISCONNECTED

    # -- send / receive --------------------------------------------------------

    def send(
        self,
        channel: str,
        content: str,
        *,
        conversation_id: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> bool:
        """Send a message via the WhatsApp Cloud API."""
        result = self.send_message(
            channel,
            WhatsAppOutboundMessage(
                kind="text",
                body=content,
                reply_to_message_id=conversation_id,
            ),
        )
        if result.accepted:
            self._publish_sent(channel, content, conversation_id)
        return result.accepted

    def send_message(
        self,
        recipient: str,
        message: WhatsAppOutboundMessage,
    ) -> WhatsAppSendResult:
        """Send a structured Cloud API message and return its provider receipt."""
        payload = _outbound_payload(recipient, message)
        if not self._token or not self._phone_number_id:
            logger.warning("Cannot send WhatsApp message: channel is not configured")
            return WhatsAppSendResult(False, error_code="not_configured")
        try:
            import httpx

            response = httpx.post(
                "https://graph.facebook.com/"
                f"{self._graph_api_version}/{self._phone_number_id}/messages",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )
            data = response.json() if response.content is not None else {}
            if response.status_code < 300:
                messages = data.get("messages", []) if isinstance(data, dict) else []
                message_id = (
                    str(messages[0].get("id", ""))
                    if messages and isinstance(messages[0], dict)
                    else ""
                )
                return WhatsAppSendResult(True, message_id=message_id)
            error = data.get("error", {}) if isinstance(data, dict) else {}
            code = str(error.get("code", response.status_code))
            logger.warning("WhatsApp API rejected a message with code %s", code)
            return WhatsAppSendResult(False, error_code=code)
        except Exception:
            logger.debug("WhatsApp send failed", exc_info=True)
            return WhatsAppSendResult(False, error_code="transport_error")

    def status(self) -> ChannelStatus:
        """Return the current connection status."""
        return self._status

    def list_channels(self) -> List[str]:
        """Return available channel identifiers."""
        return ["whatsapp"]

    def on_message(self, handler: ChannelHandler) -> None:
        """Register a callback for incoming messages."""
        self._handlers.append(handler)

    # -- internal helpers -------------------------------------------------------

    def _publish_sent(self, channel: str, content: str, conversation_id: str) -> None:
        """Publish a CHANNEL_MESSAGE_SENT event on the bus."""
        if self._bus is not None:
            self._bus.publish(
                EventType.CHANNEL_MESSAGE_SENT,
                {
                    "channel": channel,
                    "content": content,
                    "conversation_id": conversation_id,
                },
            )


__all__ = [
    "WhatsAppChannel",
    "WhatsAppListSection",
    "WhatsAppOutboundMessage",
    "WhatsAppSendResult",
    "normalize_webhook_messages",
    "verify_webhook_signature",
]
