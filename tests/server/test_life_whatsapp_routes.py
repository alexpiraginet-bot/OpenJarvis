"""Authenticated linking and signed Meta webhooks for the Life channel."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.channels.whatsapp import WhatsAppSendResult
from openjarvis.life.integrations import IntegrationsStore
from openjarvis.life.jarvis import JarvisActionStore
from openjarvis.life.whatsapp import NewsDigest, NewsSource, WhatsAppLifeStore
from openjarvis.server.life_routes import create_life_router


class _MemoryAddressVault:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self._counter = 0

    def store(self, user_id: str, channel: str, address: str) -> str:
        self._counter += 1
        ref = f"vault://{user_id}/{channel}/{self._counter}"
        self.values[ref] = address
        return ref

    def resolve(self, ref: str) -> str:
        return self.values[ref]

    def discard(self, ref: str) -> None:
        self.values.pop(ref, None)


class _FakeWhatsAppChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, channel: str, content: str, **_kwargs) -> bool:
        self.sent.append((channel, content))
        return True


class _StructuredFakeWhatsAppChannel(_FakeWhatsAppChannel):
    def __init__(self) -> None:
        super().__init__()
        self.structured_sent = []

    def send_message(self, recipient, message):
        self.structured_sent.append((recipient, message))
        return WhatsAppSendResult(
            True,
            message_id=f"wamid.out-{len(self.structured_sent)}",
        )


def _register(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/v1/life/auth/register",
        json={"email": email, "password": "senha-forte-123"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _signed_post(client: TestClient, payload: dict):
    body = json.dumps(payload).encode()
    signature = hmac.new(b"meta-app-secret", body, hashlib.sha256).hexdigest()
    return client.post(
        "/v1/life/webhooks/whatsapp",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )


def _message_payload(
    *, sender: str, message_id: str, body: str = "", interactive_id: str = ""
) -> dict:
    if interactive_id:
        message = {
            "from": sender,
            "id": message_id,
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": interactive_id, "title": "Confirmar"},
            },
        }
    else:
        message = {
            "from": sender,
            "id": message_id,
            "type": "text",
            "text": {"body": body},
        }
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "phone-id-1"},
                            "messages": [message],
                        }
                    }
                ]
            }
        ]
    }


def _status_payload(message_id: str, status: str) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "phone-id-1"},
                            "statuses": [
                                {
                                    "id": message_id,
                                    "recipient_id": "+5527999990001",
                                    "status": status,
                                    "timestamp": "1786622400",
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


def test_linking_and_signed_activation_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    vault = _MemoryAddressVault()
    channel = _FakeWhatsAppChannel()
    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
    )
    app.include_router(router)

    with TestClient(app) as client:
        auth = _register(client, "alex@example.com")
        assert (
            client.post(
                "/v1/life/channels/whatsapp/link",
                json={"phone": "+5527999990001"},
            ).status_code
            == 401
        )

        begun = client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=auth,
        )
        assert begun.status_code == 202
        assert "code" not in begun.json()
        assert begun.json()["status"] == "pending"
        assert channel.sent[0][0] == "+5527999990001"
        code = channel.sent[0][1].split()[0]

        pending = client.get("/v1/life/channels/whatsapp", headers=auth)
        assert pending.json()["status"] == "pending"

        activated = _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.activation",
                body=code,
            ),
        )
        assert activated.status_code == 200
        assert activated.text == "OK"
        status = client.get("/v1/life/channels/whatsapp", headers=auth)
        assert status.json()["status"] == "verified"

        revoked = client.delete("/v1/life/channels/whatsapp", headers=auth)
        assert revoked.status_code == 200
        assert revoked.json() == {"status": "revoked"}
        assert client.get("/v1/life/channels/whatsapp", headers=auth).status_code == 404

    router.life_context.close()


def test_link_code_endpoint_returns_429_without_sending_a_second_code(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    vault = _MemoryAddressVault()
    channel = _FakeWhatsAppChannel()
    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
    )
    app.include_router(router)

    with TestClient(app) as client:
        auth = _register(client, "rate-limit@example.com")
        first = client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=auth,
        )
        limited = client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=auth,
        )

        assert first.status_code == 202
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) > 0
        assert len(channel.sent) == 1

    router.life_context.close()


def test_meta_verification_signature_receipts_and_tenant_isolation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    vault = _MemoryAddressVault()
    channel = _FakeWhatsAppChannel()
    captured = []
    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
        whatsapp_inbound_handler=captured.append,
    )
    app.include_router(router)

    with TestClient(app) as client:
        alex = _register(client, "alex@example.com")
        bia = _register(client, "bia@example.com")
        challenge = client.get(
            "/v1/life/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "meta-verify-token",
                "hub.challenge": "challenge-42",
            },
        )
        assert challenge.status_code == 200
        assert challenge.text == "challenge-42"

        invalid = client.post(
            "/v1/life/webhooks/whatsapp",
            content=b"{}",
            headers={"X-Hub-Signature-256": "sha256=invalid"},
        )
        assert invalid.status_code == 403

        begun = client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=alex,
        )
        assert begun.status_code == 202
        code = channel.sent[-1][1].split()[0]
        assert (
            _signed_post(
                client,
                _message_payload(
                    sender="5527999990001",
                    message_id="wamid.activate",
                    body=code,
                ),
            ).status_code
            == 200
        )

        assert client.get("/v1/life/channels/whatsapp", headers=bia).status_code == 404
        conflict = client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=bia,
        )
        assert conflict.status_code == 409

        interactive = _message_payload(
            sender="5527999990001",
            message_id="wamid.action",
            interactive_id="confirm:proposal-1",
        )
        assert _signed_post(client, interactive).status_code == 200
        assert _signed_post(client, interactive).status_code == 200
        assert len(captured) == 1
        assert captured[0].sender == "+5527999990001"
        assert captured[0].metadata["action_id"] == "confirm:proposal-1"

        unknown = _signed_post(
            client,
            _message_payload(
                sender="+5527999990999",
                message_id="wamid.unknown",
                body="Olá",
            ),
        )
        assert unknown.status_code == 200

    router.life_context.close()


def test_whatsapp_turn_keeps_calendar_context_and_queues_action_buttons(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    vault = _MemoryAddressVault()
    channel = _FakeWhatsAppChannel()
    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
    )
    app.include_router(router)

    with TestClient(app) as client:
        auth = _register(client, "context@example.com")
        user_id = client.get("/v1/life/me", headers=auth).json()["user"]["id"]
        IntegrationsStore(router.life_context).register_device_grant(
            user_id,
            "apple_calendar",
            granted=["events.read", "events.write"],
            device_id="iphone-context-1234",
            device_label="iPhone de teste",
        )
        assert (
            client.post(
                "/v1/life/channels/whatsapp/link",
                json={"phone": "+5527999990001"},
                headers=auth,
            ).status_code
            == 202
        )
        code = channel.sent[-1][1].split()[0]
        assert (
            _signed_post(
                client,
                _message_payload(
                    sender="+5527999990001",
                    message_id="wamid.activate-context",
                    body=code,
                ),
            ).status_code
            == 200
        )

        first = _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.context-1",
                body="Marque uma reunião para mim amanhã às 15 horas",
            ),
        )
        second = _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.context-2",
                body="Marketing",
            ),
        )

        assert first.status_code == 200
        assert second.status_code == 200
        rows = router.life_context.connection.execute(
            "SELECT id, payload_json FROM message_outbox"
            " WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()
        assert len(rows) == 2
        first_payload = json.loads(rows[0]["payload_json"])
        second_payload = json.loads(rows[1]["payload_json"])
        assert first_payload["body"] == "Qual é o título da reunião?"
        assert first_payload["kind"] == "text"
        assert second_payload["kind"] == "reply_buttons"
        assert second_payload["buttons"][0][1] == "Confirmar"

        delivery_store = WhatsAppLifeStore(
            router.life_context,
            pepper=b"channel-pepper",
            vault=vault,
        )
        claimed = delivery_store.claim_outbox(
            limit=1,
            now=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc),
            max_attempts=5,
            stale_before=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
            - timedelta(minutes=5),
        )
        assert claimed
        delivery_store.finalize_outbox(
            claimed[0].id,
            lease_token=claimed[0].lease_token,
            status="sent",
            provider_message_id="wamid.outbound-status",
        )
        assert (
            _signed_post(
                client,
                _status_payload("wamid.outbound-status", "delivered"),
            ).status_code
            == 200
        )
        delivery_state = router.life_context.connection.execute(
            "SELECT status FROM message_outbox WHERE id = ?",
            (claimed[0].id,),
        ).fetchone()["status"]
        assert delivery_state == "delivered"

        proposal = router.life_context.connection.execute(
            "SELECT arguments_json FROM jarvis_action_proposals WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        assert json.loads(proposal["arguments_json"])["title"] == "Marketing"
        sessions = router.life_context.connection.execute(
            "SELECT id, revision FROM jarvis_dialog_sessions WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        assert len(sessions) == 1
        assert sessions[0]["revision"] == 2

    router.life_context.close()


def test_structured_channel_flushes_reply_outbox_in_the_webhook_turn(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    vault = _MemoryAddressVault()
    channel = _StructuredFakeWhatsAppChannel()
    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
    )
    app.include_router(router)

    with TestClient(app) as client:
        auth = _register(client, "delivery@example.com")
        user_id = client.get("/v1/life/me", headers=auth).json()["user"]["id"]
        assert (
            client.post(
                "/v1/life/channels/whatsapp/link",
                json={"phone": "+5527999990001"},
                headers=auth,
            ).status_code
            == 202
        )
        code = channel.sent[-1][1].split()[0]
        _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.activate-delivery",
                body=code,
            ),
        )

        response = _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.delivery-turn",
                body="O que tenho hoje?",
            ),
        )

        assert response.status_code == 200
        assert len(channel.structured_sent) == 1
        assert channel.structured_sent[0][0] == "+5527999990001"
        state = router.life_context.connection.execute(
            "SELECT status, provider_message_id FROM message_outbox WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        assert state["status"] == "sent"
        assert state["provider_message_id"] == "wamid.out-1"

    router.life_context.close()


def test_failed_turn_releases_receipt_and_requests_meta_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    vault = _MemoryAddressVault()
    channel = _FakeWhatsAppChannel()
    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
    )
    app.include_router(router)

    with TestClient(app) as client:
        auth = _register(client, "retry@example.com")
        client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=auth,
        )
        code = channel.sent[-1][1].split()[0]
        _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.activate-retry",
                body=code,
            ),
        )
        router.life_turn_service.run_turn = AsyncMock(
            side_effect=RuntimeError("temporary inference failure")
        )
        payload = _message_payload(
            sender="+5527999990001",
            message_id="wamid.retry-turn",
            body="Meu resumo",
        )

        failed = _signed_post(client, payload)

        assert failed.status_code == 503
        receipt = router.life_context.connection.execute(
            "SELECT id FROM channel_messages"
            " WHERE channel = 'whatsapp' AND provider_message_id = ?",
            ("wamid.retry-turn",),
        ).fetchone()
        assert receipt is None

    router.life_context.close()


def test_confirm_button_executes_a_low_risk_action_exactly_once(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    vault = _MemoryAddressVault()
    channel = _StructuredFakeWhatsAppChannel()
    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
    )
    app.include_router(router)

    with TestClient(app) as client:
        auth = _register(client, "action-confirm@example.com")
        user_id = client.get("/v1/life/me", headers=auth).json()["user"]["id"]
        client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=auth,
        )
        code = channel.sent[-1][1].split()[0]
        _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.activate-action-confirm",
                body=code,
            ),
        )
        actions = JarvisActionStore(router.life_context)
        proposal = actions.create(
            user_id,
            "life_record",
            {"kind": "task", "fields": {"title": "Enviar proposta"}},
        )
        payload = _message_payload(
            sender="+5527999990001",
            message_id="wamid.action-confirm",
            interactive_id=f"action:{proposal['id']}:confirm",
        )

        assert _signed_post(client, payload).status_code == 200
        assert _signed_post(client, payload).status_code == 200
        assert actions.get(user_id, proposal["id"])["status"] == "confirmed"
        assert router.life_context.store.count("work_tasks", user_id) == 1
        replies = router.life_context.connection.execute(
            "SELECT payload_json FROM message_outbox"
            " WHERE user_id = ? AND idempotency_key = ?",
            (user_id, "whatsapp-action:wamid.action-confirm"),
        ).fetchall()
        assert len(replies) == 1
        assert "executada" in json.loads(replies[0]["payload_json"])["body"]

    router.life_context.close()


def test_financial_record_buttons_require_the_authenticated_app(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    vault = _MemoryAddressVault()
    channel = _StructuredFakeWhatsAppChannel()
    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
    )
    app.include_router(router)

    with TestClient(app) as client:
        auth = _register(client, "action-financial-records@example.com")
        user_id = client.get("/v1/life/me", headers=auth).json()["user"]["id"]
        client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=auth,
        )
        code = channel.sent[-1][1].split()[0]
        _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.activate-financial-records",
                body=code,
            ),
        )
        actions = JarvisActionStore(router.life_context)
        cases = (
            (
                "expense",
                "transactions",
                {"amount_cents": 4590, "category": "mercado"},
            ),
            (
                "income",
                "transactions",
                {"amount_cents": 500000, "category": "salario"},
            ),
            (
                "bill",
                "bills",
                {"name": "Luz", "amount_cents": 18000, "due_on": "2026-08-15"},
            ),
            ("account", "accounts", {"name": "Nubank"}),
            ("budget", "budgets", {"category": "lazer", "limit_cents": 100000}),
            ("goal", "goals", {"name": "Reserva", "target_cents": 1000000}),
        )

        for index, (kind, table, fields) in enumerate(cases):
            proposal = actions.create(
                user_id,
                "life_record",
                {"kind": kind, "fields": fields},
            )
            message_id = f"wamid.financial-record-{index}"
            response = _signed_post(
                client,
                _message_payload(
                    sender="+5527999990001",
                    message_id=message_id,
                    interactive_id=f"action:{proposal['id']}:confirm",
                ),
            )

            assert response.status_code == 200
            assert actions.get(user_id, proposal["id"])["status"] == "pending"
            assert router.life_context.store.count(table, user_id) == 0
            reply = router.life_context.connection.execute(
                "SELECT payload_json FROM message_outbox"
                " WHERE user_id = ? AND idempotency_key = ?",
                (user_id, f"whatsapp-action:{message_id}"),
            ).fetchone()
            assert "no app" in json.loads(reply["payload_json"])["body"]

    router.life_context.close()


def test_cancel_button_keeps_the_proposed_write_unexecuted(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    vault = _MemoryAddressVault()
    channel = _StructuredFakeWhatsAppChannel()
    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
    )
    app.include_router(router)

    with TestClient(app) as client:
        auth = _register(client, "action-cancel@example.com")
        user_id = client.get("/v1/life/me", headers=auth).json()["user"]["id"]
        client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=auth,
        )
        code = channel.sent[-1][1].split()[0]
        _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.activate-action-cancel",
                body=code,
            ),
        )
        actions = JarvisActionStore(router.life_context)
        proposal = actions.create(
            user_id,
            "life_record",
            {"kind": "task", "fields": {"title": "Não executar"}},
        )

        response = _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.action-cancel",
                interactive_id=f"action:{proposal['id']}:cancel",
            ),
        )

        assert response.status_code == 200
        assert actions.get(user_id, proposal["id"])["status"] == "canceled"
        assert router.life_context.store.count("work_tasks", user_id) == 0

    router.life_context.close()


def test_bill_payment_button_requires_the_authenticated_app(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    vault = _MemoryAddressVault()
    channel = _StructuredFakeWhatsAppChannel()
    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
    )
    app.include_router(router)

    with TestClient(app) as client:
        auth = _register(client, "action-finance@example.com")
        user_id = client.get("/v1/life/me", headers=auth).json()["user"]["id"]
        client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=auth,
        )
        code = channel.sent[-1][1].split()[0]
        _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.activate-action-finance",
                body=code,
            ),
        )
        bill_id = router.life_context.store.insert(
            "bills",
            user_id,
            {"name": "Aluguel", "amount_cents": 500000, "due_on": "2026-08-15"},
        )
        actions = JarvisActionStore(router.life_context)
        proposal = actions.create(
            user_id,
            "life_complete",
            {"kind": "bill", "record_id": bill_id},
        )

        response = _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.action-finance",
                interactive_id=f"action:{proposal['id']}:confirm",
            ),
        )

        assert response.status_code == 200
        assert actions.get(user_id, proposal["id"])["status"] == "pending"
        assert router.life_context.store.get("bills", user_id, bill_id)["status"] == (
            "pending"
        )
        reply = router.life_context.connection.execute(
            "SELECT payload_json FROM message_outbox"
            " WHERE user_id = ? AND idempotency_key = ?",
            (user_id, "whatsapp-action:wamid.action-finance"),
        ).fetchone()
        assert "Face ID" in json.loads(reply["payload_json"])["body"]

    router.life_context.close()


def test_briefing_shortcut_runs_the_shared_jarvis_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    vault = _MemoryAddressVault()
    channel = _StructuredFakeWhatsAppChannel()
    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
    )
    app.include_router(router)

    with TestClient(app) as client:
        auth = _register(client, "briefing-command@example.com")
        user_id = client.get("/v1/life/me", headers=auth).json()["user"]["id"]
        client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=auth,
        )
        code = channel.sent[-1][1].split()[0]
        _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.activate-briefing-command",
                body=code,
            ),
        )

        response = _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.briefing-finance",
                interactive_id="command:finance",
            ),
        )

        assert response.status_code == 200
        row = router.life_context.connection.execute(
            "SELECT payload_json FROM message_outbox"
            " WHERE user_id = ? AND idempotency_key = ?",
            (user_id, "whatsapp-reply:wamid.briefing-finance"),
        ).fetchone()
        assert "Seu saldo" in json.loads(row["payload_json"])["body"]
        session = router.life_context.connection.execute(
            "SELECT revision FROM jarvis_dialog_sessions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        assert session["revision"] == 1

    router.life_context.close()


def test_briefing_preference_and_authenticated_cron_enqueue_and_deliver_once(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    monkeypatch.setenv("CRON_SECRET", "cron-test-secret-with-16-chars")
    vault = _MemoryAddressVault()
    channel = _StructuredFakeWhatsAppChannel()
    news_calls = []

    class NewsProvider:
        def fetch(self, **kwargs):
            news_calls.append(kwargs)
            return NewsDigest(
                text="Mobilidade em destaque — 13/08/2026.",
                sources=(
                    NewsSource(
                        title="Fonte verificada",
                        url="https://example.com/mobilidade",
                    ),
                ),
            )

    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
        whatsapp_news_provider=NewsProvider(),
    )
    app.include_router(router)

    with TestClient(app) as client:
        auth = _register(client, "briefing-cron@example.com")
        user_id = client.get("/v1/life/me", headers=auth).json()["user"]["id"]
        assert (
            client.put(
                "/v1/life/channels/whatsapp/briefing",
                headers=auth,
                json={"enabled": True, "time": "00:00"},
            ).status_code
            == 409
        )
        client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=auth,
        )
        code = channel.sent[-1][1].split()[0]
        _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.activate-briefing-cron",
                body=code,
            ),
        )
        preference = client.put(
            "/v1/life/channels/whatsapp/briefing",
            headers=auth,
            json={
                "enabled": True,
                "time": "00:00",
                "sections": ["priorities", "finance", "news"],
                "news_topics": ["mobilidade", "carros por assinatura"],
                "delivery_days": [0, 1, 2, 3, 4, 5, 6],
                "custom_instructions": "Priorize o impacto no Brasil.",
            },
        )
        assert preference.status_code == 200
        assert preference.json()["briefing"] == {
            "enabled": True,
            "time": "00:00",
            "sections": ["priorities", "finance", "news"],
            "news_topics": ["mobilidade", "carros por assinatura"],
            "delivery_days": [0, 1, 2, 3, 4, 5, 6],
            "custom_instructions": "Priorize o impacto no Brasil.",
        }
        saved = client.get("/v1/life/channels/whatsapp/briefing", headers=auth).json()[
            "briefing"
        ]
        assert saved["enabled"] is True
        assert saved["news_topics"] == ["mobilidade", "carros por assinatura"]
        router.life_context.store.insert(
            "work_tasks",
            user_id,
            {"title": "Fechar relatório", "due_on": "2020-01-01"},
        )

        assert client.get("/v1/life/internal/whatsapp/briefings").status_code == 401
        first = client.get(
            "/v1/life/internal/whatsapp/briefings",
            headers={"Authorization": "Bearer cron-test-secret-with-16-chars"},
        )
        second = client.get(
            "/v1/life/internal/whatsapp/briefings",
            headers={"Authorization": "Bearer cron-test-secret-with-16-chars"},
        )

        assert first.status_code == 200
        assert first.json()["enqueued"] == 1
        assert first.json()["sent"] == 1
        assert second.json()["duplicates"] == 1
        assert len(channel.structured_sent) == 1
        assert len(news_calls) == 1
        assert (
            "Mobilidade em destaque"
            in (channel.structured_sent[0][1].template_parameters[0])
        )
        assert news_calls[0]["topics"] == (
            "mobilidade",
            "carros por assinatura",
        )

    router.life_context.close()


def test_authenticated_cron_delivers_only_one_outbox_microbatch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_LIFE_OPEN_SIGNUP", "1")
    monkeypatch.setenv("CRON_SECRET", "cron-test-secret-with-16-chars")
    vault = _MemoryAddressVault()
    channel = _StructuredFakeWhatsAppChannel()
    app = FastAPI()
    router = create_life_router(
        str(tmp_path / "life.db"),
        channel_address_vault=vault,
        channel_pepper=b"channel-pepper",
        whatsapp_channel=channel,
        whatsapp_verify_token="meta-verify-token",
        whatsapp_app_secret="meta-app-secret",
    )
    app.include_router(router)

    with TestClient(app) as client:
        auth = _register(client, "cron-microbatch@example.com")
        user_id = client.get("/v1/life/me", headers=auth).json()["user"]["id"]
        client.post(
            "/v1/life/channels/whatsapp/link",
            json={"phone": "+5527999990001"},
            headers=auth,
        )
        code = channel.sent[-1][1].split()[0]
        _signed_post(
            client,
            _message_payload(
                sender="+5527999990001",
                message_id="wamid.activate-cron-microbatch",
                body=code,
            ),
        )
        store = WhatsAppLifeStore(
            router.life_context,
            pepper=b"channel-pepper",
            vault=vault,
        )
        link = store.get_link(user_id)
        assert link is not None
        for index in range(12):
            store.enqueue_outbound(
                user_id,
                link.id,
                idempotency_key=f"cron-microbatch:{index}",
                payload={"kind": "text", "body": f"Mensagem {index}"},
            )

        response = client.get(
            "/v1/life/internal/whatsapp/briefings",
            headers={"Authorization": "Bearer cron-test-secret-with-16-chars"},
        )

        assert response.status_code == 200
        assert response.json()["claimed"] == 10
        assert response.json()["sent"] == 10
        assert len(channel.structured_sent) == 10
        remaining = router.life_context.connection.execute(
            "SELECT COUNT(*) AS total FROM message_outbox WHERE status = 'queued'"
        ).fetchone()["total"]
        assert remaining == 2

    router.life_context.close()
