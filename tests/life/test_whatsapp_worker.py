"""Durable WhatsApp outbox delivery and monotonic provider receipts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openjarvis.channels.whatsapp import WhatsAppSendResult
from openjarvis.life.whatsapp import WhatsAppLifeStore
from openjarvis.life.whatsapp_worker import OutboxRunResult, WhatsAppOutboxWorker


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


class _FakeChannel:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    def send_message(self, recipient, message):
        self.calls.append((recipient, message))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _store(life, user, clock):
    vault = _MemoryAddressVault()
    store = WhatsAppLifeStore(
        life,
        pepper=b"worker-test-pepper",
        vault=vault,
        now=lambda: clock[0],
        code_factory=lambda: "731904",
    )
    challenge = store.begin_link(user.id, "+5527999990001")
    link = store.verify_link("+5527999990001", challenge.code)
    return store, link


def _enqueue(store, user, link, key="reply-1"):
    return store.enqueue_outbound(
        user.id,
        link.id,
        idempotency_key=key,
        payload={"kind": "text", "body": "Seu briefing está pronto."},
    )


def _outbox_row(life, item_id):
    return life.connection.execute(
        "SELECT * FROM message_outbox WHERE id = ?", (item_id,)
    ).fetchone()


def test_accepted_send_records_provider_id_and_sent_state(life, user):
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    item = _enqueue(store, user, link)
    channel = _FakeChannel([WhatsAppSendResult(True, message_id="wamid.sent-1")])

    result = WhatsAppOutboxWorker(
        store,
        channel,
        now=lambda: clock[0],
    ).run_once()

    row = _outbox_row(life, item.id)
    assert result.sent == 1
    assert row["status"] == "sent"
    assert row["provider_message_id"] == "wamid.sent-1"
    assert row["attempts"] == 1
    assert channel.calls[0][0] == "+5527999990001"


def test_permanent_provider_rejection_fails_without_retry(life, user):
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    item = _enqueue(store, user, link)
    channel = _FakeChannel([WhatsAppSendResult(False, error_code="100")])

    result = WhatsAppOutboxWorker(store, channel, now=lambda: clock[0]).run_once()

    row = _outbox_row(life, item.id)
    assert result.failed == 1
    assert row["status"] == "failed"
    assert row["last_error"] == "100"
    assert row["next_attempt_at"] is None


def test_transport_exception_schedules_exponential_retry(life, user):
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    item = _enqueue(store, user, link)
    channel = _FakeChannel([RuntimeError("socket included secret details")])

    result = WhatsAppOutboxWorker(store, channel, now=lambda: clock[0]).run_once()

    row = _outbox_row(life, item.id)
    assert result.retried == 1
    assert row["status"] == "retry"
    assert row["last_error"] == "transport_error"
    assert datetime.fromisoformat(row["next_attempt_at"]) == clock[0] + timedelta(
        seconds=30
    )
    assert "secret" not in row["last_error"]


def test_max_attempts_turns_transient_failure_terminal(life, user):
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    item = _enqueue(store, user, link)
    life.connection.execute(
        "UPDATE message_outbox SET attempts = 4 WHERE id = ?", (item.id,)
    )
    life.connection.commit()
    channel = _FakeChannel([RuntimeError("offline")])

    result = WhatsAppOutboxWorker(store, channel, now=lambda: clock[0]).run_once()

    row = _outbox_row(life, item.id)
    assert result.failed == 1
    assert row["status"] == "failed"
    assert row["attempts"] == 5


def test_second_worker_cannot_claim_already_sent_item(life, user):
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    _enqueue(store, user, link)
    channel = _FakeChannel([WhatsAppSendResult(True, message_id="wamid.only")])
    first = WhatsAppOutboxWorker(store, channel, now=lambda: clock[0])
    second = WhatsAppOutboxWorker(store, channel, now=lambda: clock[0])

    assert first.run_once().claimed == 1
    assert second.run_once().claimed == 0
    assert len(channel.calls) == 1


def test_worker_revalidates_briefing_opt_in_immediately_before_send(life, user):
    """A claimed briefing must not escape after the user opts out."""
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    store.set_briefing_preference(
        user.id,
        enabled=True,
        local_time="08:00",
    )
    item = _enqueue(
        store,
        user,
        link,
        key="whatsapp-briefing:2026-08-13",
    )
    claimed = store.claim_outbox(
        limit=1,
        now=clock[0],
        max_attempts=5,
        stale_before=clock[0] - timedelta(minutes=5),
    )
    assert claimed[0].id == item.id
    store.set_briefing_preference(
        user.id,
        enabled=False,
        local_time="08:00",
    )
    clock[0] += timedelta(minutes=6)
    channel = _FakeChannel([WhatsAppSendResult(True, message_id="wamid.must-not-send")])

    result = WhatsAppOutboxWorker(
        store,
        channel,
        now=lambda: clock[0],
    ).run_once()

    assert result.claimed == 1
    assert result.sent == 0
    assert result.failed == 1
    assert channel.calls == []
    row = _outbox_row(life, item.id)
    assert row["status"] == "cancelled"
    assert row["last_error"] == "briefing_opt_out"


def test_worker_claims_one_item_at_a_time_so_queued_leases_do_not_expire(life, user):
    """A batch may run for >5 min while every individual send stays lease-safe."""
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    _enqueue(store, user, link, key="slow-1")
    _enqueue(store, user, link, key="slow-2")
    competing_channel = _FakeChannel(
        [WhatsAppSendResult(True, message_id="wamid.duplicate")]
    )
    competing_worker = WhatsAppOutboxWorker(
        store,
        competing_channel,
        now=lambda: clock[0],
    )

    class SlowSequentialChannel:
        def __init__(self):
            self.calls = []
            self.competing_result = None

        def send_message(self, recipient, message):
            self.calls.append((recipient, message))
            if len(self.calls) == 1:
                clock[0] += timedelta(minutes=4)
                return WhatsAppSendResult(True, message_id="wamid.slow-1")
            clock[0] += timedelta(minutes=2)
            self.competing_result = competing_worker.run_once(limit=2)
            return WhatsAppSendResult(True, message_id="wamid.slow-2")

    channel = SlowSequentialChannel()
    result = WhatsAppOutboxWorker(
        store,
        channel,
        now=lambda: clock[0],
    ).run_once(limit=2)

    assert result.claimed == 2
    assert result.sent == 2
    assert channel.competing_result.claimed == 0
    assert len(channel.calls) == 2
    assert competing_channel.calls == []
    rows = life.connection.execute(
        "SELECT status, provider_message_id FROM message_outbox ORDER BY created_at"
    ).fetchall()
    assert [(row["status"], row["provider_message_id"]) for row in rows] == [
        ("sent", "wamid.slow-1"),
        ("sent", "wamid.slow-2"),
    ]


def test_worker_attempts_each_outbox_item_at_most_once_per_run(life, user):
    """A retry becoming due mid-batch waits for the next worker invocation."""
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    first = _enqueue(store, user, link, key="retry-once")
    second = _enqueue(store, user, link, key="other-item")

    class AdvancingChannel:
        def __init__(self):
            self.calls = 0

        def send_message(self, _recipient, _message):
            self.calls += 1
            clock[0] += timedelta(seconds=31)
            if self.calls == 1:
                return WhatsAppSendResult(False, error_code="temporary")
            return WhatsAppSendResult(True, message_id="wamid.other")

    channel = AdvancingChannel()
    result = WhatsAppOutboxWorker(
        store,
        channel,
        now=lambda: clock[0],
    ).run_once(limit=3)

    assert result == OutboxRunResult(
        claimed=2,
        sent=1,
        retried=1,
        failed=0,
    )
    assert channel.calls == 2
    first_row = _outbox_row(life, first.id)
    second_row = _outbox_row(life, second.id)
    assert first_row["status"] == "retry"
    assert first_row["attempts"] == 1
    assert second_row["status"] == "sent"


def test_worker_stops_before_starting_an_attempt_outside_shared_deadline(life, user):
    """A cron leaves enough time for one Meta request before claiming more work."""
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    monotonic = [100.0]
    store, link = _store(life, user, clock)
    _enqueue(store, user, link, key="budget-1")
    _enqueue(store, user, link, key="budget-2")

    class TimedChannel:
        def __init__(self):
            self.calls = []

        def send_message(self, recipient, message):
            self.calls.append((recipient, message))
            monotonic[0] += 10.0
            return WhatsAppSendResult(True, message_id="wamid.budget")

    channel = TimedChannel()
    result = WhatsAppOutboxWorker(
        store,
        channel,
        now=lambda: clock[0],
        monotonic=lambda: monotonic[0],
    ).run_once(limit=2, deadline=120.0)

    assert result.claimed == 1
    assert result.sent == 1
    assert len(channel.calls) == 1
    remaining = life.connection.execute(
        "SELECT COUNT(*) AS total FROM message_outbox WHERE status = 'queued'"
    ).fetchone()["total"]
    assert remaining == 1


def test_stale_worker_cannot_finalize_lease_reclaimed_by_another_worker(life, user):
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    item = _enqueue(store, user, link)

    first_claim = store.claim_outbox(
        limit=1,
        now=clock[0],
        max_attempts=5,
        stale_before=clock[0] - timedelta(minutes=5),
    )[0]
    clock[0] += timedelta(minutes=6)
    second_claim = store.claim_outbox(
        limit=1,
        now=clock[0],
        max_attempts=5,
        stale_before=clock[0] - timedelta(minutes=5),
    )[0]

    assert second_claim.lease_token != first_claim.lease_token
    assert (
        store.finalize_outbox(
            item.id,
            lease_token=first_claim.lease_token,
            status="sent",
            provider_message_id="wamid.stale-worker",
            now=clock[0],
        )
        is False
    )
    assert _outbox_row(life, item.id)["status"] == "sending"
    assert (
        store.finalize_outbox(
            item.id,
            lease_token=second_claim.lease_token,
            status="sent",
            provider_message_id="wamid.current-worker",
            now=clock[0],
        )
        is True
    )
    row = _outbox_row(life, item.id)
    assert row["status"] == "sent"
    assert row["provider_message_id"] == "wamid.current-worker"


def test_finalize_outbox_keeps_update_and_commit_in_one_transaction(
    life, user, monkeypatch
):
    """A rollback cannot enter after UPDATE and make a successful CAS disappear."""
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    item = _enqueue(store, user, link)
    claim = store.claim_outbox(
        limit=1,
        now=clock[0],
        max_attempts=5,
        stale_before=clock[0] - timedelta(minutes=5),
    )[0]
    original_commit = life.connection.commit

    def competing_rollback_before_commit():
        life.connection.rollback()
        original_commit()

    monkeypatch.setattr(life.connection, "commit", competing_rollback_before_commit)

    assert (
        store.finalize_outbox(
            item.id,
            lease_token=claim.lease_token,
            status="sent",
            provider_message_id="wamid.atomic",
            now=clock[0],
        )
        is True
    )
    row = _outbox_row(life, item.id)
    assert row["status"] == "sent"
    assert row["provider_message_id"] == "wamid.atomic"


def test_expired_final_lease_becomes_failed_instead_of_staying_sending(life, user):
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    item = _enqueue(store, user, link)
    life.connection.execute(
        "UPDATE message_outbox SET attempts = 4 WHERE id = ?", (item.id,)
    )
    life.connection.commit()

    final_claim = store.claim_outbox(
        limit=1,
        now=clock[0],
        max_attempts=5,
        stale_before=clock[0] - timedelta(minutes=5),
    )
    assert final_claim[0].attempts == 5
    clock[0] += timedelta(minutes=6)

    assert (
        store.claim_outbox(
            limit=1,
            now=clock[0],
            max_attempts=5,
            stale_before=clock[0] - timedelta(minutes=5),
        )
        == []
    )
    row = _outbox_row(life, item.id)
    assert row["status"] == "failed"
    assert row["last_error"] == "lease_expired_after_max_attempts"
    assert row["lease_token"] == ""


def test_delivery_receipts_do_not_regress_when_out_of_order(life, user):
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    item = _enqueue(store, user, link)
    channel = _FakeChannel([WhatsAppSendResult(True, message_id="wamid.receipt")])
    worker = WhatsAppOutboxWorker(store, channel, now=lambda: clock[0])
    worker.run_once()

    assert worker.record_delivery("wamid.receipt", "read") is True
    assert worker.record_delivery("wamid.receipt", "delivered") is True
    assert worker.record_delivery("unknown", "read") is False
    assert _outbox_row(life, item.id)["status"] == "read"


def test_delivery_receipt_update_cannot_regress_after_concurrent_read(
    life, user, monkeypatch
):
    """The SQL update itself must reject a stale lower-rank webhook."""
    clock = [datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)]
    store, link = _store(life, user, clock)
    item = _enqueue(store, user, link)
    channel = _FakeChannel([WhatsAppSendResult(True, message_id="wamid.race")])
    worker = WhatsAppOutboxWorker(store, channel, now=lambda: clock[0])
    worker.run_once()
    original_execute = life.connection.execute
    injected = False

    def execute_with_read_interleaving(sql, params=()):
        nonlocal injected
        if (
            not injected
            and sql.startswith("UPDATE message_outbox SET status")
            and params
            and params[0] == "delivered"
        ):
            injected = True
            assert store.record_delivery_status(
                "wamid.race",
                "read",
                now=clock[0] + timedelta(seconds=1),
            )
        return original_execute(sql, params)

    monkeypatch.setattr(life.connection, "execute", execute_with_read_interleaving)

    assert worker.record_delivery("wamid.race", "delivered") is True
    assert injected is True
    assert _outbox_row(life, item.id)["status"] == "read"
