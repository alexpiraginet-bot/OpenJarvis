"""Tenant isolation and replay safety for the Life WhatsApp control plane."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import openjarvis.life.whatsapp as whatsapp_module
from openjarvis.channels._stubs import ChannelMessage
from openjarvis.life.whatsapp import (
    AddressInUseError,
    ChannelConfigurationError,
    LinkVerificationError,
    SupabaseVaultAddressVault,
    WhatsAppLifeStore,
)


class _MemoryAddressVault:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.discarded: list[str] = []
        self._counter = 0

    def store(self, user_id: str, channel: str, address: str) -> str:
        self._counter += 1
        ref = f"vault://{user_id}/{channel}/{self._counter}"
        self.values[ref] = address
        return ref

    def resolve(self, ref: str) -> str:
        return self.values[ref]

    def discard(self, ref: str) -> None:
        self.discarded.append(ref)
        self.values.pop(ref, None)


@pytest.fixture()
def clock():
    return [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]


@pytest.fixture()
def address_vault():
    return _MemoryAddressVault()


@pytest.fixture()
def whatsapp_store(life, address_vault, clock):
    return WhatsAppLifeStore(
        life,
        pepper=b"test-only-channel-pepper",
        vault=address_vault,
        now=lambda: clock[0],
        code_factory=lambda: "731904",
    )


def _link(store: WhatsAppLifeStore, user_id: str, phone: str, *, channel="whatsapp"):
    challenge = store.begin_link(user_id, phone, channel=channel)
    return store.verify_link(phone, challenge.code, channel=channel)


def test_linking_requires_pepper_and_external_address_vault(life, address_vault):
    no_pepper = WhatsAppLifeStore(life, pepper=b"", vault=address_vault)
    with pytest.raises(ChannelConfigurationError):
        no_pepper.begin_link("user-1", "+5527999990001")

    no_vault = WhatsAppLifeStore(life, pepper=b"valid-pepper", vault=None)
    with pytest.raises(ChannelConfigurationError):
        no_vault.begin_link("user-1", "+5527999990001")


def test_link_persists_neither_raw_phone_nor_verification_code(
    life, user, whatsapp_store
):
    challenge = whatsapp_store.begin_link(user.id, "+55 (27) 99999-0001")

    row = life.connection.execute("SELECT * FROM channel_links").fetchone()
    persisted = " ".join(str(value) for value in tuple(row))
    assert "+5527999990001" not in persisted
    assert "731904" not in persisted
    assert challenge.code == "731904"
    assert row["status"] == "pending"


def test_link_code_limit_is_durable_scoped_and_never_persists_phone(
    life, user, other_user, whatsapp_store, address_vault, clock
):
    """Cooldown/window state survives store instances without retaining PII."""
    consume = getattr(whatsapp_store, "consume_link_code_attempt", None)
    assert callable(consume)
    consume(user.id, "+5527999990001")
    reopened = WhatsAppLifeStore(
        life,
        pepper=b"test-only-channel-pepper",
        vault=address_vault,
        now=lambda: clock[0],
        code_factory=lambda: "731904",
    )

    with pytest.raises(whatsapp_module.LinkRateLimitedError) as cooldown:
        reopened.consume_link_code_attempt(user.id, "+5527999990001")

    assert 1 <= cooldown.value.retry_after_seconds <= 60
    reopened.consume_link_code_attempt(other_user.id, "+5527999990001")
    for _ in range(4):
        clock[0] += timedelta(seconds=61)
        reopened.consume_link_code_attempt(user.id, "+5527999990001")
    clock[0] += timedelta(seconds=61)
    with pytest.raises(whatsapp_module.LinkRateLimitedError) as window:
        reopened.consume_link_code_attempt(user.id, "+5527999990001")
    assert window.value.retry_after_seconds > 0

    persisted = life.connection.execute(
        "SELECT * FROM channel_link_rate_limits WHERE user_id = ?",
        (user.id,),
    ).fetchall()
    assert persisted
    assert "+5527999990001" not in " ".join(
        str(value) for row in persisted for value in tuple(row)
    )


def test_link_code_user_window_cannot_be_bypassed_by_rotating_phones(
    user, whatsapp_store, clock
):
    """One tenant cannot turn arbitrary target numbers into an SMS cannon."""
    consume = getattr(whatsapp_store, "consume_link_code_attempt", None)
    assert callable(consume)
    for index in range(10):
        consume(user.id, f"+5527999900{index:02d}")
        clock[0] += timedelta(seconds=61)

    with pytest.raises(whatsapp_module.LinkRateLimitedError):
        consume(user.id, "+552799990099")


def test_two_users_cannot_link_the_same_phone(life, user, whatsapp_store):
    second_user = life.users.create_user("second@example.com", "second-password")
    _link(whatsapp_store, user.id, "+5527999990001")

    with pytest.raises(AddressInUseError):
        whatsapp_store.begin_link(second_user.id, "+5527999990001")

    resolved = whatsapp_store.resolve_sender("+5527999990001")
    assert resolved is not None
    assert resolved.user_id == user.id


def test_unknown_wrong_expired_and_reused_codes_fail_closed(
    user, whatsapp_store, clock
):
    with pytest.raises(LinkVerificationError):
        whatsapp_store.verify_link("+5527999990999", "731904")

    challenge = whatsapp_store.begin_link(user.id, "+5527999990001")
    with pytest.raises(LinkVerificationError):
        whatsapp_store.verify_link("+5527999990001", "000000")

    clock[0] += timedelta(minutes=11)
    with pytest.raises(LinkVerificationError):
        whatsapp_store.verify_link("+5527999990001", challenge.code)

    clock[0] -= timedelta(minutes=11)
    link = whatsapp_store.verify_link("+5527999990001", challenge.code)
    assert link.status == "verified"
    with pytest.raises(LinkVerificationError):
        whatsapp_store.verify_link("+5527999990001", challenge.code)


def test_revoked_link_no_longer_resolves(user, whatsapp_store, address_vault):
    link = _link(whatsapp_store, user.id, "+5527999990001")

    assert whatsapp_store.revoke_link(user.id, link.id)
    assert whatsapp_store.resolve_sender("+5527999990001") is None
    assert address_vault.discarded


def test_vault_cleanup_failure_does_not_restore_revoked_link(life, user):
    class FailingDiscardVault(_MemoryAddressVault):
        def discard(self, ref: str) -> None:
            raise RuntimeError("vault temporarily unavailable")

    vault = FailingDiscardVault()
    store = WhatsAppLifeStore(
        life,
        pepper=b"test-only-channel-pepper",
        vault=vault,
        code_factory=lambda: "731904",
    )
    challenge = store.begin_link(user.id, "+5527999990001")
    link = store.verify_link("+5527999990001", challenge.code)

    assert store.revoke_link(user.id, link.id) is True
    assert store.resolve_sender("+5527999990001") is None


def test_duplicate_inbound_provider_id_creates_one_receipt(user, whatsapp_store, life):
    _link(whatsapp_store, user.id, "+5527999990001")
    message = ChannelMessage(
        channel="whatsapp",
        sender="+5527999990001",
        content="Resumo de hoje",
        message_id="wamid.same",
        conversation_id="+5527999990001",
        metadata={"kind": "text", "phone_number_id": "pn-1"},
    )

    first = whatsapp_store.accept_inbound(message)
    second = whatsapp_store.accept_inbound(message)

    assert first.duplicate is False
    assert second.duplicate is True
    count = life.connection.execute(
        "SELECT COUNT(*) AS total FROM channel_messages"
    ).fetchone()["total"]
    assert count == 1


def test_provider_message_ids_are_namespaced_by_channel(user, whatsapp_store, life):
    _link(whatsapp_store, user.id, "+5527999990001", channel="whatsapp")
    _link(whatsapp_store, user.id, "+5527999990001", channel="sms")

    whatsapp = ChannelMessage(
        channel="whatsapp",
        sender="+5527999990001",
        content="Oi",
        message_id="provider-1",
    )
    sms = ChannelMessage(
        channel="sms",
        sender="+5527999990001",
        content="Oi",
        message_id="provider-1",
    )

    assert whatsapp_store.accept_inbound(whatsapp).duplicate is False
    assert whatsapp_store.accept_inbound(sms).duplicate is False
    count = life.connection.execute(
        "SELECT COUNT(*) AS total FROM channel_messages"
    ).fetchone()["total"]
    assert count == 2


def test_outbound_is_idempotent_per_user(user, whatsapp_store, life):
    link = _link(whatsapp_store, user.id, "+5527999990001")
    payload = {"kind": "text", "body": "Seu briefing está pronto."}

    first = whatsapp_store.enqueue_outbound(
        user.id,
        link.id,
        idempotency_key="briefing:2026-08-13",
        payload=payload,
    )
    second = whatsapp_store.enqueue_outbound(
        user.id,
        link.id,
        idempotency_key="briefing:2026-08-13",
        payload=payload,
    )

    assert first.id == second.id
    assert first.duplicate is False
    assert second.duplicate is True
    count = life.connection.execute(
        "SELECT COUNT(*) AS total FROM message_outbox"
    ).fetchone()["total"]
    assert count == 1


def test_daily_briefing_is_opt_in_and_due_in_the_users_timezone(user, whatsapp_store):
    link = _link(whatsapp_store, user.id, "+5527999990001")

    disabled = whatsapp_store.get_briefing_preference(user.id)
    assert disabled.enabled is False
    assert (
        whatsapp_store.list_due_briefings(
            now=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
        )
        == []
    )

    enabled = whatsapp_store.set_briefing_preference(
        user.id,
        enabled=True,
        local_time="08:00",
        sections=("priorities", "finance", "news"),
        news_topics=("mobilidade", "carros por assinatura"),
        delivery_days=(0, 3, 4),
        custom_instructions="Destaque movimentos que afetam locadoras.",
    )

    assert enabled.enabled is True
    assert enabled.local_time == "08:00"
    assert enabled.link_id == link.id
    assert enabled.sections == ("priorities", "finance", "news")
    assert enabled.news_topics == ("mobilidade", "carros por assinatura")
    assert enabled.delivery_days == (0, 3, 4)
    assert enabled.custom_instructions == ("Destaque movimentos que afetam locadoras.")
    assert (
        whatsapp_store.list_due_briefings(
            now=datetime(2026, 8, 13, 10, 59, tzinfo=timezone.utc)
        )
        == []
    )
    due = whatsapp_store.list_due_briefings(
        now=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    )
    assert [(item.user_id, item.local_date) for item in due] == [
        (user.id, "2026-08-13")
    ]
    assert due[0].sections == ("priorities", "finance", "news")
    assert due[0].news_topics == ("mobilidade", "carros por assinatura")


def test_daily_briefing_requires_a_verified_link_and_valid_time(user, whatsapp_store):
    whatsapp_store.begin_link(user.id, "+5527999990001")

    with pytest.raises(ChannelConfigurationError, match="vínculo ativo"):
        whatsapp_store.set_briefing_preference(
            user.id,
            enabled=True,
            local_time="08:00",
        )
    with pytest.raises(ChannelConfigurationError, match="HH:MM"):
        whatsapp_store.set_briefing_preference(
            user.id,
            enabled=False,
            local_time="25:90",
        )
    _link(whatsapp_store, user.id, "+5527999990001")
    with pytest.raises(ChannelConfigurationError, match="seção"):
        whatsapp_store.set_briefing_preference(
            user.id,
            enabled=True,
            local_time="08:00",
            sections=("finance", "horóscopo_inventado"),
        )
    with pytest.raises(ChannelConfigurationError, match="dias"):
        whatsapp_store.set_briefing_preference(
            user.id,
            enabled=True,
            local_time="08:00",
            delivery_days=(7,),
        )


def test_disabling_briefing_cancels_only_pending_briefing_deliveries(
    life, user, whatsapp_store, clock
):
    """Opt-out must revoke every unsent briefing without touching replies."""
    link = _link(whatsapp_store, user.id, "+5527999990001")
    whatsapp_store.set_briefing_preference(
        user.id,
        enabled=True,
        local_time="08:00",
    )
    queued = whatsapp_store.enqueue_outbound(
        user.id,
        link.id,
        idempotency_key="whatsapp-briefing:2026-08-13",
        payload={"kind": "template", "body": "queued"},
    )
    retrying = whatsapp_store.enqueue_outbound(
        user.id,
        link.id,
        idempotency_key="whatsapp-briefing:2026-08-14",
        payload={"kind": "template", "body": "retry"},
    )
    preparing = whatsapp_store.reserve_briefing_preparation(
        user.id,
        link.id,
        idempotency_key="whatsapp-briefing:2026-08-15",
        payload={"kind": "template", "body": "preparing"},
        now=clock[0],
        stale_before=clock[0] - timedelta(minutes=5),
    )
    reply = whatsapp_store.enqueue_outbound(
        user.id,
        link.id,
        idempotency_key="whatsapp-reply:wamid.keep",
        payload={"kind": "text", "body": "keep"},
    )
    life.connection.execute(
        "UPDATE message_outbox SET status = 'retry', lease_token = 'retry-lease',"
        " next_attempt_at = ? WHERE id = ?",
        ((clock[0] + timedelta(minutes=1)).isoformat(), retrying.id),
    )
    life.connection.commit()

    disabled = whatsapp_store.set_briefing_preference(
        user.id,
        enabled=False,
        local_time="08:00",
    )

    assert disabled.enabled is False
    rows = life.connection.execute(
        "SELECT id, status, lease_token, next_attempt_at, last_error"
        " FROM message_outbox ORDER BY id"
    ).fetchall()
    states = {row["id"]: row for row in rows}
    for item in (queued, retrying, preparing):
        assert states[item.id]["status"] == "cancelled"
        assert states[item.id]["lease_token"] == ""
        assert states[item.id]["next_attempt_at"] is None
        assert states[item.id]["last_error"] == "briefing_opt_out"
    assert states[reply.id]["status"] == "queued"


def test_briefing_runner_enqueues_one_cross_domain_message_per_local_day(
    life, user, whatsapp_store
):
    runner_class = getattr(whatsapp_module, "WhatsAppBriefingRunner", None)
    assert runner_class is not None
    _link(whatsapp_store, user.id, "+5527999990001")
    whatsapp_store.set_briefing_preference(
        user.id,
        enabled=True,
        local_time="08:00",
    )
    life.store.insert(
        "accounts",
        user.id,
        {"name": "Principal", "balance_cents": 350000},
    )
    life.store.insert(
        "bills",
        user.id,
        {"name": "Internet", "amount_cents": 12990, "due_on": "2026-08-12"},
    )
    life.store.insert(
        "workouts",
        user.id,
        {"name": "Corrida Z2", "scheduled_on": "2026-08-13"},
    )
    life.store.insert("habits", user.id, {"name": "Beber água"})
    life.store.insert(
        "work_tasks",
        user.id,
        {"title": "Enviar relatório", "due_on": "2026-08-13"},
    )
    runner = runner_class(life, whatsapp_store)
    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    first = runner.enqueue_due(now=now)
    second = runner.enqueue_due(now=now)

    assert first.to_dict() == {"due": 1, "enqueued": 1, "duplicates": 0}
    assert second.to_dict() == {"due": 1, "enqueued": 0, "duplicates": 1}
    row = life.connection.execute(
        "SELECT payload_json FROM message_outbox WHERE user_id = ?",
        (user.id,),
    ).fetchone()
    payload = json.loads(row["payload_json"])
    assert payload["kind"] == "template"
    assert payload["template_name"] == "jarvis_daily_briefing"
    assert "Internet está vencida" in payload["body"]
    assert "Corrida Z2" in payload["body"]
    assert "Enviar relatório" in payload["body"]
    assert "Beber água" in payload["body"]
    assert payload["template_parameters"] == [payload["body"]]
    assert payload["template_quick_replies"] == [
        "command:priorities",
        "command:fitness",
        "command:finance",
    ]


def test_briefing_runner_honors_deadline_and_microbatch_without_starvation(
    life, user, other_user, whatsapp_store
):
    """Completed duplicates must not consume the next cron's bounded batch."""
    _link(whatsapp_store, user.id, "+5527999990001")
    _link(whatsapp_store, other_user.id, "+5527999990002")
    for target in (user, other_user):
        whatsapp_store.set_briefing_preference(
            target.id,
            enabled=True,
            local_time="08:00",
        )
    monotonic = [100.0]
    runner = whatsapp_module.WhatsAppBriefingRunner(
        life,
        whatsapp_store,
        monotonic=lambda: monotonic[0],
    )
    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    expired = runner.enqueue_due(now=now, limit=1, deadline=100.0)
    first = runner.enqueue_due(now=now, limit=1, deadline=200.0)
    second = runner.enqueue_due(now=now, limit=1, deadline=200.0)

    assert expired.to_dict() == {"due": 2, "enqueued": 0, "duplicates": 0}
    assert first.to_dict() == {"due": 2, "enqueued": 1, "duplicates": 0}
    assert second.to_dict() == {"due": 2, "enqueued": 1, "duplicates": 1}
    total = life.connection.execute(
        "SELECT COUNT(*) AS total FROM message_outbox"
        " WHERE idempotency_key = 'whatsapp-briefing:2026-08-13'"
    ).fetchone()["total"]
    assert total == 2


def test_personalized_news_briefing_uses_grounded_provider_sources(
    life, user, whatsapp_store
):
    runner_class = getattr(whatsapp_module, "WhatsAppBriefingRunner", None)
    digest_class = getattr(whatsapp_module, "NewsDigest", None)
    source_class = getattr(whatsapp_module, "NewsSource", None)
    assert runner_class is not None
    assert digest_class is not None
    assert source_class is not None

    class NewsProvider:
        def __init__(self):
            self.calls = []

        def fetch(self, **kwargs):
            self.calls.append(kwargs)
            return digest_class(
                text=(
                    "Locadoras ampliam assinatura elétrica — 13/08/2026 — "
                    "movimento relevante para mobilidade."
                ),
                sources=(
                    source_class(
                        title="Mercado de mobilidade",
                        url="https://example.com/mobilidade",
                    ),
                ),
            )

    _link(whatsapp_store, user.id, "+5527999990001")
    whatsapp_store.set_briefing_preference(
        user.id,
        enabled=True,
        local_time="08:00",
        sections=("finance", "news"),
        news_topics=("mobilidade", "carros por assinatura"),
        custom_instructions="Priorize impacto no mercado brasileiro.",
    )
    provider = NewsProvider()
    runner = runner_class(life, whatsapp_store, news_provider=provider)

    result = runner.enqueue_due(now=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc))

    assert result.enqueued == 1
    assert provider.calls[0]["topics"] == (
        "mobilidade",
        "carros por assinatura",
    )
    assert provider.calls[0]["custom_instructions"] == (
        "Priorize impacto no mercado brasileiro."
    )
    row = life.connection.execute(
        "SELECT payload_json FROM message_outbox WHERE user_id = ?",
        (user.id,),
    ).fetchone()
    body = json.loads(row["payload_json"])["body"]
    assert "NOTÍCIAS" in body
    assert "Locadoras ampliam assinatura elétrica" in body
    assert "https://example.com/mobilidade" in body
    assert "TREINO" not in body
    assert "ROTINA" not in body


def test_news_composition_finishes_before_outbox_can_be_claimed(
    life, user, whatsapp_store
):
    """A delivery worker must never observe the temporary base briefing."""
    runner_class = whatsapp_module.WhatsAppBriefingRunner
    digest_class = whatsapp_module.NewsDigest
    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    class RacingNewsProvider:
        def __init__(self):
            self.claimed_during_fetch = None

        def fetch(self, **_kwargs):
            self.claimed_during_fetch = whatsapp_store.claim_outbox(
                limit=1,
                now=now,
                max_attempts=5,
                stale_before=now - timedelta(minutes=5),
            )
            return digest_class(text="Notícia final e verificada.")

    _link(whatsapp_store, user.id, "+5527999990001")
    whatsapp_store.set_briefing_preference(
        user.id,
        enabled=True,
        local_time="08:00",
        sections=("finance", "news"),
        news_topics=("mobilidade",),
    )
    provider = RacingNewsProvider()
    runner = runner_class(life, whatsapp_store, news_provider=provider)

    result = runner.enqueue_due(now=now)

    assert provider.claimed_during_fetch == []
    assert result.to_dict() == {"due": 1, "enqueued": 1, "duplicates": 0}
    row = life.connection.execute(
        "SELECT payload_json, status, attempts FROM message_outbox WHERE user_id = ?",
        (user.id,),
    ).fetchone()
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert "Notícia final e verificada." in json.loads(row["payload_json"])["body"]


def test_concurrent_briefing_runner_reserves_before_paid_news_fetch(
    life, user, whatsapp_store
):
    """A second cron entering during fetch must not buy the same user-day twice."""
    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    class SecondaryNewsProvider:
        def __init__(self):
            self.calls = 0

        def fetch(self, **_kwargs):
            self.calls += 1
            return whatsapp_module.NewsDigest(text="Compra duplicada indevida.")

    secondary_provider = SecondaryNewsProvider()
    secondary_runner = whatsapp_module.WhatsAppBriefingRunner(
        life,
        whatsapp_store,
        news_provider=secondary_provider,
    )

    class PrimaryNewsProvider:
        def __init__(self):
            self.concurrent_result = None

        def fetch(self, **_kwargs):
            self.concurrent_result = secondary_runner.enqueue_due(now=now)
            return whatsapp_module.NewsDigest(text="Notícia comprada uma vez.")

    _link(whatsapp_store, user.id, "+5527999990001")
    whatsapp_store.set_briefing_preference(
        user.id,
        enabled=True,
        local_time="08:00",
        sections=("finance", "news"),
        news_topics=("mobilidade",),
    )
    primary_provider = PrimaryNewsProvider()
    primary_runner = whatsapp_module.WhatsAppBriefingRunner(
        life,
        whatsapp_store,
        news_provider=primary_provider,
    )

    result = primary_runner.enqueue_due(now=now)

    assert result.to_dict() == {"due": 1, "enqueued": 1, "duplicates": 0}
    assert primary_provider.concurrent_result.to_dict() == {
        "due": 1,
        "enqueued": 0,
        "duplicates": 1,
    }
    assert secondary_provider.calls == 0
    row = life.connection.execute(
        "SELECT payload_json, status FROM message_outbox WHERE user_id = ?",
        (user.id,),
    ).fetchone()
    assert row["status"] == "queued"
    assert "Notícia comprada uma vez." in json.loads(row["payload_json"])["body"]


def test_crashed_news_preparation_lease_is_recovered_after_five_minutes(
    life, user, whatsapp_store
):
    """A dead cron retains a lease that a later cron can recover after expiry."""
    started_at = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    class SimulatedWorkerCrash(BaseException):
        pass

    class CrashingNewsProvider:
        def fetch(self, **_kwargs):
            raise SimulatedWorkerCrash

    _link(whatsapp_store, user.id, "+5527999990001")
    whatsapp_store.set_briefing_preference(
        user.id,
        enabled=True,
        local_time="08:00",
        sections=("finance", "news"),
        news_topics=("mobilidade",),
    )
    crashed_runner = whatsapp_module.WhatsAppBriefingRunner(
        life,
        whatsapp_store,
        news_provider=CrashingNewsProvider(),
    )

    with pytest.raises(SimulatedWorkerCrash):
        crashed_runner.enqueue_due(now=started_at)

    stranded = life.connection.execute(
        "SELECT status, lease_token, updated_at FROM message_outbox WHERE user_id = ?",
        (user.id,),
    ).fetchone()
    assert stranded["status"] == "preparing"
    assert stranded["lease_token"]
    assert stranded["updated_at"] == started_at.isoformat()

    class RecoveryNewsProvider:
        def __init__(self):
            self.calls = 0

        def fetch(self, **_kwargs):
            self.calls += 1
            return whatsapp_module.NewsDigest(text="Notícia recuperada.")

    recovery_provider = RecoveryNewsProvider()
    recovery_runner = whatsapp_module.WhatsAppBriefingRunner(
        life,
        whatsapp_store,
        news_provider=recovery_provider,
    )
    recovered = recovery_runner.enqueue_due(
        now=started_at + timedelta(minutes=5, seconds=1)
    )

    assert recovered.to_dict() == {"due": 1, "enqueued": 1, "duplicates": 0}
    assert recovery_provider.calls == 1
    row = life.connection.execute(
        "SELECT payload_json, status, lease_token FROM message_outbox"
        " WHERE user_id = ?",
        (user.id,),
    ).fetchone()
    assert row["status"] == "queued"
    assert row["lease_token"] == ""
    assert "Notícia recuperada." in json.loads(row["payload_json"])["body"]


def test_expired_briefing_preparation_owner_cannot_overwrite_recovery(
    life, user, whatsapp_store
):
    """CAS rejects a stale owner after another cron has recovered its lease."""
    started_at = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    link = _link(whatsapp_store, user.id, "+5527999990001")
    base_payload = {"kind": "template", "body": "Briefing base"}
    recovered_payload = {"kind": "template", "body": "Briefing recuperado"}

    first = whatsapp_store.reserve_briefing_preparation(
        user.id,
        link.id,
        idempotency_key="whatsapp-briefing:2026-08-13",
        payload=base_payload,
        now=started_at,
        stale_before=started_at - timedelta(minutes=5),
    )
    second = whatsapp_store.reserve_briefing_preparation(
        user.id,
        link.id,
        idempotency_key="whatsapp-briefing:2026-08-13",
        payload=base_payload,
        now=started_at + timedelta(minutes=5, seconds=1),
        stale_before=started_at + timedelta(seconds=1),
    )

    assert first.duplicate is False
    assert second.duplicate is False
    assert first.lease_token != second.lease_token
    assert (
        whatsapp_store.finalize_briefing_preparation(
            first.id,
            lease_token=first.lease_token,
            payload={"kind": "template", "body": "Resultado antigo"},
            now=started_at + timedelta(minutes=5, seconds=2),
        )
        is False
    )
    assert (
        whatsapp_store.finalize_briefing_preparation(
            second.id,
            lease_token=second.lease_token,
            payload=recovered_payload,
            now=started_at + timedelta(minutes=5, seconds=3),
        )
        is True
    )
    row = life.connection.execute(
        "SELECT payload_json, status FROM message_outbox WHERE user_id = ?",
        (user.id,),
    ).fetchone()
    assert row["status"] == "queued"
    assert json.loads(row["payload_json"]) == recovered_payload


def test_news_failure_enqueues_base_briefing_once(life, user, whatsapp_store):
    runner_class = whatsapp_module.WhatsAppBriefingRunner
    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    class FailingNewsProvider:
        def __init__(self):
            self.calls = 0

        def fetch(self, **_kwargs):
            self.calls += 1
            raise RuntimeError("news unavailable")

    _link(whatsapp_store, user.id, "+5527999990001")
    whatsapp_store.set_briefing_preference(
        user.id,
        enabled=True,
        local_time="08:00",
        sections=("finance", "news"),
        news_topics=("mobilidade",),
    )
    provider = FailingNewsProvider()
    runner = runner_class(life, whatsapp_store, news_provider=provider)

    first = runner.enqueue_due(now=now)
    second = runner.enqueue_due(now=now)

    assert first.to_dict() == {"due": 1, "enqueued": 1, "duplicates": 0}
    assert second.to_dict() == {"due": 1, "enqueued": 0, "duplicates": 1}
    assert provider.calls == 1
    rows = life.connection.execute(
        "SELECT payload_json, status, attempts FROM message_outbox WHERE user_id = ?",
        (user.id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "queued"
    assert rows[0]["attempts"] == 0
    assert "NOTÍCIAS" not in json.loads(rows[0]["payload_json"])["body"]


def test_openai_news_provider_uses_web_search_citations_and_ai_budget():
    provider_class = getattr(whatsapp_module, "OpenAIWebNewsProvider", None)
    assert provider_class is not None

    class Budget:
        def __init__(self):
            self.calls = []

        def reserve(self, user_id, model, maximum):
            self.calls.append(("reserve", user_id, model, maximum))
            return "reservation-1"

        def finalize(self, reservation_id, actual):
            self.calls.append(("finalize", reservation_id, actual))

        def release(self, reservation_id):
            self.calls.append(("release", reservation_id))

    annotation = SimpleNamespace(
        type="url_citation",
        title="Fonte automotiva",
        url="https://example.com/noticia",
    )
    response = SimpleNamespace(
        output_text="Mercado de assinatura cresce — 13/08/2026.",
        usage=SimpleNamespace(input_tokens=120, output_tokens=80),
        output=[
            SimpleNamespace(type="web_search_call"),
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(
                        type="output_text",
                        text="Mercado de assinatura cresce — 13/08/2026.",
                        annotations=[annotation],
                    )
                ],
            ),
        ],
    )

    class Responses:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return response

    class Client:
        def __init__(self):
            self.responses = Responses()
            self.options = []

        def with_options(self, **kwargs):
            self.options.append(kwargs)
            return self

    client = Client()
    responses = client.responses
    budget = Budget()
    provider = provider_class(budget, client=client, model="gpt-5-mini")

    digest = provider.fetch(
        user_id="user-1",
        topics=("mobilidade", "carros por assinatura"),
        custom_instructions="Priorize o Brasil.",
        local_date="2026-08-13",
        timezone_name="America/Sao_Paulo",
    )

    assert digest.text == "Mercado de assinatura cresce — 13/08/2026."
    assert digest.sources == (
        whatsapp_module.NewsSource(
            title="Fonte automotiva",
            url="https://example.com/noticia",
        ),
    )
    assert budget.calls[0][:3] == ("reserve", "user-1", "gpt-5-mini")
    assert budget.calls[1][0] == "finalize"
    assert budget.calls[1][2] >= 10_000
    assert responses.calls[0]["tools"] == [
        {"type": "web_search", "search_context_size": "low"}
    ]
    assert 0 < responses.calls[0]["timeout"] < 240
    assert client.options == [{"max_retries": 0}]


def test_supabase_vault_is_not_assumed_on_sqlite(life):
    assert SupabaseVaultAddressVault.from_database(life.connection) is None


def test_supabase_vault_uses_opaque_refs_and_decrypted_view_only():
    class Rows:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Postgres:
        backend = "postgres"

        def __init__(self):
            self.calls = []
            self.commits = 0

        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            if "to_regclass" in sql:
                return Rows({"table_name": "vault.secrets"})
            if "vault.create_secret" in sql:
                return Rows({"secret_id": "2ab9274e-69f9-4c50-a84f-256f48ad802e"})
            if "vault.decrypted_secrets" in sql:
                return Rows({"decrypted_secret": "+5527999990001"})
            return Rows()

        def commit(self):
            self.commits += 1

    database = Postgres()
    vault = SupabaseVaultAddressVault.from_database(database)

    assert vault is not None
    ref = vault.store("user-1", "whatsapp", "+5527999990001")
    assert ref == "supabase-vault:2ab9274e-69f9-4c50-a84f-256f48ad802e"
    assert vault.resolve(ref) == "+5527999990001"
    vault.discard(ref)

    sql = "\n".join(call[0] for call in database.calls)
    assert "vault.create_secret" in sql
    assert "vault.decrypted_secrets" in sql
    assert "DELETE FROM vault.secrets" in sql
    assert database.commits == 2
