"""Tests for proactive nudges — selection, wording, dedupe and failure."""

from __future__ import annotations

from datetime import date, timedelta

from openjarvis.life.notify import (
    LifeNotifier,
    alert_fingerprint,
    render_digest,
)

TODAY = date(2026, 8, 10)


class FakeSender:
    """A channel that records what it was asked to send."""

    def __init__(self, *, succeed: bool = True) -> None:
        self.succeed = succeed
        self.sent: list[tuple[str, str]] = []

    def send(self, channel, content, *, conversation_id="", metadata=None):
        self.sent.append((channel, content))
        return self.succeed


class ExplodingSender:
    """A channel that is down."""

    def send(self, channel, content, *, conversation_id="", metadata=None):
        raise RuntimeError("gateway fora do ar")


def _overdue_bill(life, user, name="Luz", cents=18000):
    return life.store.insert(
        "bills",
        user.id,
        {"name": name, "amount_cents": cents, "due_on": "2026-08-01"},
    )


# -- Selection ---------------------------------------------------------------


def test_nothing_pending_sends_nothing(life, user):
    notifier = LifeNotifier(life.service)
    sender = FakeSender()
    result = notifier.notify(user, sender, "5511999999999", anchor=TODAY)
    assert result.sent is False
    assert result.reason == "nothing-pending"
    assert sender.sent == []


def test_info_alerts_are_not_worth_interrupting_for(life, user):
    """A pending habit belongs on the Today screen, not in someone's pocket."""
    life.store.insert("habits", user.id, {"name": "Ler"})
    pending = LifeNotifier(life.service).pending_alerts(user, anchor=TODAY)
    assert pending == []


def test_streak_at_risk_does_reach_the_client(life, user):
    """Momentum about to break is escalated to warning, so it qualifies."""
    habit = life.store.insert("habits", user.id, {"name": "Ler"})
    for offset in (1, 2, 3):
        life.service.check_in_habit(
            user.id, habit, done_on=(TODAY - timedelta(days=offset)).isoformat()
        )
    pending = LifeNotifier(life.service).pending_alerts(user, anchor=TODAY)
    assert [a["app"] for a in pending] == ["routine"]


def test_overdue_bill_is_notified(life, user):
    _overdue_bill(life, user)
    notifier = LifeNotifier(life.service)
    sender = FakeSender()
    result = notifier.notify(user, sender, "5511999999999", anchor=TODAY)

    assert result.sent is True
    assert result.alerts == 1
    destination, content = sender.sent[0]
    assert destination == "5511999999999"
    assert "Luz está vencida" in content
    assert "R$180.00" in content


# -- Wording -----------------------------------------------------------------


def test_digest_is_empty_when_there_is_nothing_to_say(user):
    assert render_digest([], user) == ""


def test_digest_greets_by_first_name(life, user):
    _overdue_bill(life, user)
    alerts = LifeNotifier(life.service).pending_alerts(user, anchor=TODAY)
    assert render_digest(alerts, user).startswith("Oi, Alex")


def test_digest_truncates_long_lists(life, user):
    for index in range(8):
        _overdue_bill(life, user, name=f"Conta {index}", cents=1000)
    alerts = LifeNotifier(life.service).pending_alerts(user, anchor=TODAY)
    message = render_digest(alerts, user)
    assert "e mais 3 item(ns)" in message
    assert message.count("🔴") == 5


def test_digest_omits_amount_when_there_is_none(life, user):
    life.store.insert(
        "work_tasks", user.id, {"title": "Enviar proposta", "due_on": "2026-08-01"}
    )
    alerts = LifeNotifier(life.service).pending_alerts(user, anchor=TODAY)
    assert "R$0.00" not in render_digest(alerts, user)


# -- Deduplication -----------------------------------------------------------


def test_the_same_alert_is_not_sent_twice_in_a_day(life, user):
    """A scheduler firing hourly must not nag four times about one bill."""
    _overdue_bill(life, user)
    notifier = LifeNotifier(life.service)
    sender = FakeSender()

    first = notifier.notify(user, sender, "dest", anchor=TODAY)
    second = notifier.notify(user, sender, "dest", anchor=TODAY)

    assert first.sent is True
    assert second.sent is False
    assert second.reason == "nothing-pending"
    assert len(sender.sent) == 1


def test_a_new_alert_still_gets_through_the_same_day(life, user):
    _overdue_bill(life, user, name="Luz")
    notifier = LifeNotifier(life.service)
    sender = FakeSender()
    notifier.notify(user, sender, "dest", anchor=TODAY)

    _overdue_bill(life, user, name="Água", cents=9000)
    result = notifier.notify(user, sender, "dest", anchor=TODAY)

    assert result.sent is True
    assert "Água" in sender.sent[1][1]
    assert "Luz" not in sender.sent[1][1]


def test_a_new_day_allows_the_reminder_again(life, user):
    _overdue_bill(life, user)
    notifier = LifeNotifier(life.service)
    sender = FakeSender()
    notifier.notify(user, sender, "dest", anchor=TODAY)

    tomorrow = notifier.notify(user, sender, "dest", anchor=TODAY + timedelta(days=1))
    assert tomorrow.sent is True
    assert len(sender.sent) == 2


def test_fingerprint_survives_a_reworded_title(life, user):
    """Same obligation, different wording as the due date nears."""
    base = {"app": "finance", "record_id": "abc", "action": "pay_bill"}
    assert alert_fingerprint({**base, "title": "vence em 2 dias"}) == (
        alert_fingerprint({**base, "title": "vence hoje"})
    )


def test_different_records_get_different_fingerprints():
    base = {"app": "finance", "action": "pay_bill", "title": "x"}
    assert alert_fingerprint({**base, "record_id": "a"}) != alert_fingerprint(
        {**base, "record_id": "b"}
    )


# -- Failure handling --------------------------------------------------------


def test_a_refused_send_is_not_recorded(life, user):
    """A channel outage means the client is told late, never not at all."""
    _overdue_bill(life, user)
    notifier = LifeNotifier(life.service)
    failing = FakeSender(succeed=False)

    first = notifier.notify(user, failing, "dest", anchor=TODAY)
    assert first.sent is False
    assert first.reason == "channel-refused"

    working = FakeSender()
    retry = notifier.notify(user, working, "dest", anchor=TODAY)
    assert retry.sent is True


def test_a_crashing_channel_does_not_crash_the_run(life, user):
    """This runs from a scheduler; an exception must not kill the job."""
    _overdue_bill(life, user)
    result = LifeNotifier(life.service).notify(
        user, ExplodingSender(), "dest", anchor=TODAY
    )
    assert result.sent is False
    assert "gateway fora do ar" in result.reason


def test_notifications_never_cross_clients(life, user, other_user):
    _overdue_bill(life, user)
    notifier = LifeNotifier(life.service)
    notifier.notify(user, FakeSender(), "dest", anchor=TODAY)

    assert notifier.pending_alerts(other_user, anchor=TODAY) == []
