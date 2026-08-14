"""Tests for the WhatsAppChannel adapter."""

from __future__ import annotations

import hashlib
import hmac
import os
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.channels._stubs import ChannelStatus
from openjarvis.channels.whatsapp import (
    WhatsAppChannel,
    WhatsAppListSection,
    WhatsAppOutboundMessage,
    normalize_webhook_messages,
    verify_webhook_signature,
)
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.registry import ChannelRegistry
from tests.channels.channel_test_helpers import make_common_channel_tests


@pytest.fixture(autouse=True)
def _register_whatsapp():
    """Re-register after any registry clear."""
    if not ChannelRegistry.contains("whatsapp"):
        ChannelRegistry.register_value("whatsapp", WhatsAppChannel)


TestCommonChannel = make_common_channel_tests(
    WhatsAppChannel,
    "whatsapp",
    constructor_kwargs={"access_token": "test-token", "phone_number_id": "12345"},
)


class TestInit:
    def test_defaults(self):
        ch = WhatsAppChannel()
        assert ch._token == ""
        assert ch._phone_number_id == ""
        assert ch._status == ChannelStatus.DISCONNECTED

    def test_constructor_token(self):
        ch = WhatsAppChannel(access_token="my-token", phone_number_id="12345")
        assert ch._token == "my-token"
        assert ch._phone_number_id == "12345"

    def test_env_var_fallback(self):
        with patch.dict(
            os.environ,
            {
                "WHATSAPP_ACCESS_TOKEN": "env-token",
                "WHATSAPP_PHONE_NUMBER_ID": "env-id",
            },
        ):
            ch = WhatsAppChannel()
            assert ch._token == "env-token"
            assert ch._phone_number_id == "env-id"

    def test_constructor_overrides_env(self):
        with patch.dict(
            os.environ,
            {
                "WHATSAPP_ACCESS_TOKEN": "env-token",
                "WHATSAPP_PHONE_NUMBER_ID": "env-id",
            },
        ):
            ch = WhatsAppChannel(
                access_token="explicit-token",
                phone_number_id="explicit-id",
            )
            assert ch._token == "explicit-token"
            assert ch._phone_number_id == "explicit-id"

    def test_current_graph_version_is_default_and_can_be_overridden(self):
        assert WhatsAppChannel()._graph_api_version == "v25.0"
        with patch.dict(os.environ, {"WHATSAPP_GRAPH_API_VERSION": "v24.0"}):
            assert WhatsAppChannel()._graph_api_version == "v24.0"
            assert (
                WhatsAppChannel(graph_api_version="v23.0")._graph_api_version == "v23.0"
            )


class TestSend:
    def test_send_success(self):
        ch = WhatsAppChannel(access_token="test-token", phone_number_id="12345")

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = ch.send("+1234567890", "Hello!")
            assert result is True
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            url = call_args[0][0]
            assert "graph.facebook.com" in url
            assert "12345" in url
            assert call_args.kwargs["json"]["to"] == "1234567890"

    @pytest.mark.parametrize(
        "recipient",
        ["", "+0123456789", "+123", "+1234567890123456", "55 11 99999-9999"],
    )
    def test_invalid_recipient_is_rejected_before_network(self, recipient):
        ch = WhatsAppChannel(access_token="test-token", phone_number_id="12345")
        message = WhatsAppOutboundMessage(kind="text", body="Hello!")

        with patch("httpx.post") as post, pytest.raises(ValueError, match="recipient"):
            ch.send_message(recipient, message)

        post.assert_not_called()

    def test_send_failure(self):
        ch = WhatsAppChannel(access_token="test-token", phone_number_id="12345")

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        with patch("httpx.post", return_value=mock_response):
            result = ch.send("+1234567890", "Hello!")
            assert result is False

    def test_send_exception(self):
        ch = WhatsAppChannel(access_token="test-token", phone_number_id="12345")

        with patch("httpx.post", side_effect=ConnectionError("refused")):
            result = ch.send("+1234567890", "Hello!")
            assert result is False

    def test_send_no_token(self):
        ch = WhatsAppChannel()
        result = ch.send("+1234567890", "Hello!")
        assert result is False

    def test_send_publishes_event(self):
        bus = EventBus(record_history=True)
        ch = WhatsAppChannel(
            access_token="test-token",
            phone_number_id="12345",
            bus=bus,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.post", return_value=mock_response):
            ch.send("+1234567890", "Hello!")

        event_types = [e.event_type for e in bus.history]
        assert EventType.CHANNEL_MESSAGE_SENT in event_types

    def test_send_reply_buttons_returns_provider_message_id(self):
        channel = WhatsAppChannel(
            access_token="test-token",
            phone_number_id="12345",
        )
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"messages": [{"id": "wamid.accepted-1"}]}
        message = WhatsAppOutboundMessage(
            kind="reply_buttons",
            body="Confirmar a criação da reunião?",
            buttons=(
                ("action:proposal-123:confirm", "Confirmar"),
                ("action:proposal-123:cancel", "Cancelar"),
            ),
        )

        with patch("httpx.post", return_value=response) as post:
            result = channel.send_message("5511999999999", message)

        assert result.accepted is True
        assert result.message_id == "wamid.accepted-1"
        payload = post.call_args.kwargs["json"]
        assert payload == {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": "5511999999999",
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": "Confirmar a criação da reunião?"},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {
                                "id": "action:proposal-123:confirm",
                                "title": "Confirmar",
                            },
                        },
                        {
                            "type": "reply",
                            "reply": {
                                "id": "action:proposal-123:cancel",
                                "title": "Cancelar",
                            },
                        },
                    ]
                },
            },
        }

    def test_send_list_builds_sections_without_model_generated_ids(self):
        channel = WhatsAppChannel(
            access_token="test-token",
            phone_number_id="12345",
        )
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"messages": [{"id": "wamid.list-1"}]}
        message = WhatsAppOutboundMessage(
            kind="list",
            body="O que você quer abrir?",
            list_button="Abrir menu",
            sections=(
                WhatsAppListSection(
                    title="Hoje",
                    rows=(
                        ("menu:today:agenda", "Agenda", "Compromissos de hoje"),
                        ("menu:today:tasks", "Prioridades", "Tarefas em aberto"),
                    ),
                ),
            ),
        )

        with patch("httpx.post", return_value=response) as post:
            result = channel.send_message("5511999999999", message)

        assert result.accepted is True
        rows = post.call_args.kwargs["json"]["interactive"]["action"]["sections"][0][
            "rows"
        ]
        assert [row["id"] for row in rows] == [
            "menu:today:agenda",
            "menu:today:tasks",
        ]

    def test_send_template_includes_body_parameters_and_quick_reply_payloads(self):
        channel = WhatsAppChannel(
            access_token="test-token",
            phone_number_id="12345",
        )
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"messages": [{"id": "wamid.template-1"}]}
        message = WhatsAppOutboundMessage(
            kind="template",
            body="Resumo central do dia",
            template_name="jarvis_daily_briefing",
            template_language="pt_BR",
            template_parameters=("Resumo central do dia",),
            template_quick_replies=(
                "command:priorities",
                "command:fitness",
            ),
        )

        with patch("httpx.post", return_value=response) as post:
            result = channel.send_message("5511999999999", message)

        assert result.accepted is True
        template = post.call_args.kwargs["json"]["template"]
        assert template == {
            "name": "jarvis_daily_briefing",
            "language": {"code": "pt_BR"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": "Resumo central do dia"}],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "0",
                    "parameters": [
                        {"type": "payload", "payload": "command:priorities"}
                    ],
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": "1",
                    "parameters": [{"type": "payload", "payload": "command:fitness"}],
                },
            ],
        }

    @pytest.mark.parametrize(
        "message",
        [
            WhatsAppOutboundMessage(kind="text", body=""),
            WhatsAppOutboundMessage(
                kind="reply_buttons",
                body="Escolha",
                buttons=(("duplicated", "Sim"), ("duplicated", "Não")),
            ),
            WhatsAppOutboundMessage(kind="list", body="Menu", list_button="Abrir"),
        ],
    )
    def test_invalid_interactive_message_is_rejected_before_network(self, message):
        channel = WhatsAppChannel(
            access_token="test-token",
            phone_number_id="12345",
        )

        with patch("httpx.post") as post, pytest.raises(ValueError):
            channel.send_message("5511999999999", message)

        post.assert_not_called()


class TestWebhookContract:
    def test_signature_validation_fails_closed(self):
        body = b'{"object":"whatsapp_business_account"}'
        digest = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()

        assert verify_webhook_signature("app-secret", body, f"sha256={digest}") is True
        assert verify_webhook_signature("app-secret", body, "sha256=wrong") is False
        assert verify_webhook_signature("", body, f"sha256={digest}") is False
        assert verify_webhook_signature("app-secret", body, "") is False

    def test_text_audio_document_and_interactive_messages_are_normalized(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "phone-1"},
                                "messages": [
                                    {
                                        "from": "5511999999999",
                                        "id": "wamid.text",
                                        "type": "text",
                                        "text": {"body": "Bom dia"},
                                    },
                                    {
                                        "from": "5511999999999",
                                        "id": "wamid.audio",
                                        "type": "audio",
                                        "audio": {
                                            "id": "media-audio",
                                            "mime_type": "audio/ogg",
                                        },
                                    },
                                    {
                                        "from": "5511999999999",
                                        "id": "wamid.document",
                                        "type": "document",
                                        "document": {
                                            "id": "media-document",
                                            "mime_type": "application/pdf",
                                            "filename": "comprovante.pdf",
                                        },
                                    },
                                    {
                                        "from": "5511999999999",
                                        "id": "wamid.button",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": {
                                                "id": "action:proposal-123:confirm",
                                                "title": "Confirmar",
                                            },
                                        },
                                    },
                                    {
                                        "from": "5511999999999",
                                        "id": "wamid.list",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "list_reply",
                                            "list_reply": {
                                                "id": "menu:today:agenda",
                                                "title": "Agenda",
                                                "description": "Compromissos de hoje",
                                            },
                                        },
                                    },
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        messages = normalize_webhook_messages(payload)

        assert [(message.message_id, message.content) for message in messages] == [
            ("wamid.text", "Bom dia"),
            ("wamid.audio", ""),
            ("wamid.document", ""),
            ("wamid.button", "Confirmar"),
            ("wamid.list", "Agenda"),
        ]
        assert {message.sender for message in messages} == {"+5511999999999"}
        assert {message.conversation_id for message in messages} == {"+5511999999999"}
        assert messages[1].metadata == {
            "kind": "audio",
            "media_id": "media-audio",
            "mime_type": "audio/ogg",
            "phone_number_id": "phone-1",
        }
        assert messages[2].metadata["filename"] == "comprovante.pdf"
        assert messages[3].metadata["action_id"] == ("action:proposal-123:confirm")
        assert messages[4].metadata["action_id"] == "menu:today:agenda"

    def test_delivery_status_is_normalized_without_a_sender_message(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"phone_number_id": "phone-1"},
                                "statuses": [
                                    {
                                        "id": "wamid.outbound-1",
                                        "status": "delivered",
                                        "timestamp": "1786100000",
                                        "recipient_id": "5511999999999",
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        messages = normalize_webhook_messages(payload)

        assert len(messages) == 1
        assert messages[0].message_id == "wamid.outbound-1"
        assert messages[0].sender == ""
        assert messages[0].metadata == {
            "kind": "delivery_status",
            "status": "delivered",
            "timestamp": "1786100000",
            "recipient_id": "5511999999999",
            "phone_number_id": "phone-1",
        }


class TestStatus:
    def test_no_token_connect_error(self):
        ch = WhatsAppChannel()
        ch.connect()
        assert ch.status() == ChannelStatus.ERROR
