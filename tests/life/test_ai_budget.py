"""Remote inference must reserve room inside the monthly hard cap."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.core.events import EventBus
from openjarvis.core.types import Message, Role, ToolCall
from openjarvis.life import ai_budget as ai_budget_module
from openjarvis.life.ai_budget import (
    MONTHLY_AI_CAP_MICRO_USD,
    AiBudgetExceededError,
    AiBudgetStore,
    BudgetedEngine,
)
from openjarvis.telemetry.instrumented_engine import InstrumentedEngine


class _RemoteEngine:
    engine_id = "cloud"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def generate(self, messages, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider unavailable")
        return {
            "content": "ok",
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            "cost_usd": 0.000027,
        }


def test_remote_call_is_reserved_then_accounted(life, user):
    budget = AiBudgetStore(life)
    remote = _RemoteEngine()
    engine = BudgetedEngine(remote, budget, user.id)

    result = engine.generate(
        [Message(role=Role.USER, content="oi")],
        model="gpt-4o-mini",
        max_tokens=100,
    )

    assert result["content"] == "ok"
    snapshot = budget.snapshot()
    assert snapshot["spent_microusd"] == 27
    assert snapshot["reserved_microusd"] == 0


def test_exhausted_budget_blocks_before_provider_call(life, user):
    budget = AiBudgetStore(life)
    reservation = budget.reserve(user.id, "gpt-4o-mini", MONTHLY_AI_CAP_MICRO_USD)
    budget.finalize(reservation, MONTHLY_AI_CAP_MICRO_USD)
    remote = _RemoteEngine()
    engine = BudgetedEngine(remote, budget, user.id)

    with pytest.raises(AiBudgetExceededError, match="not called"):
        engine.generate(
            [Message(role=Role.USER, content="oi")],
            model="gpt-4o-mini",
            max_tokens=100,
        )
    assert remote.calls == 0


def test_exhausted_budget_blocks_instrumented_remote_engine(life, user):
    budget = AiBudgetStore(life)
    reservation = budget.reserve(user.id, "gpt-4o-mini", MONTHLY_AI_CAP_MICRO_USD)
    budget.finalize(reservation, MONTHLY_AI_CAP_MICRO_USD)
    remote = _RemoteEngine()
    engine = BudgetedEngine(InstrumentedEngine(remote, EventBus()), budget, user.id)

    with pytest.raises(AiBudgetExceededError, match="not called"):
        engine.generate(
            [Message(role=Role.USER, content="oi")],
            model="gpt-4o-mini",
            max_tokens=100,
        )
    assert remote.calls == 0


def test_unknown_remote_model_is_blocked_without_a_price_ceiling(life, user):
    remote = _RemoteEngine()
    engine = BudgetedEngine(remote, AiBudgetStore(life), user.id)
    with pytest.raises(AiBudgetExceededError, match="no verified price"):
        engine.generate(
            [Message(role=Role.USER, content="oi")],
            model="provider/new-model",
            max_tokens=100,
        )
    assert remote.calls == 0


def test_openai_compat_is_treated_as_remote_even_for_unknown_models(life, user):
    remote = _RemoteEngine()
    remote.engine_id = "openai-compat"
    engine = BudgetedEngine(remote, AiBudgetStore(life), user.id)

    with pytest.raises(AiBudgetExceededError, match="no verified price"):
        engine.generate(
            [Message(role=Role.USER, content="oi")],
            model="provider/new-model",
            max_tokens=100,
        )
    assert remote.calls == 0


def test_postgres_budget_uses_a_transaction_advisory_lock():
    class _Cursor:
        def fetchone(self):
            return {"locked": True}

    class _Connection:
        backend = "postgres"

        def __init__(self):
            self.calls = []

        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            return _Cursor()

    class _Life:
        connection = _Connection()

    budget = AiBudgetStore(_Life())
    budget._lock_budget_month("2026-08")

    assert _Life.connection.calls == [
        (
            "SELECT pg_advisory_xact_lock(hashtext(?))",
            ("openjarvis:life:ai-budget:2026-08",),
        )
    ]


def test_failed_provider_call_releases_its_reservation(life, user):
    budget = AiBudgetStore(life)
    engine = BudgetedEngine(_RemoteEngine(fail=True), budget, user.id)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        engine.generate(
            [Message(role=Role.USER, content="oi")],
            model="gpt-4o-mini",
            max_tokens=100,
        )
    snapshot = budget.snapshot()
    assert snapshot["spent_microusd"] == 0
    assert snapshot["reserved_microusd"] == 0


def test_cost_ceiling_includes_serialized_message_tool_calls():
    plain_messages = [Message(role=Role.ASSISTANT, content="")]
    tool_messages = [
        Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=[
                ToolCall(
                    id="call_weather",
                    name="get_weather",
                    arguments='{"city":"' + "Vitoria" * 2_000 + '"}',
                )
            ],
        )
    ]

    plain_ceiling = BudgetedEngine._max_cost_microusd(
        plain_messages, "gpt-4o-mini", 1, {}
    )
    tool_ceiling = BudgetedEngine._max_cost_microusd(
        tool_messages, "gpt-4o-mini", 1, {}
    )

    assert tool_ceiling > plain_ceiling


def test_finalize_rejects_cost_above_reservation_and_keeps_budget_locked(life, user):
    budget = AiBudgetStore(life)
    reservation = budget.reserve(user.id, "gpt-4o-mini", MONTHLY_AI_CAP_MICRO_USD)

    with pytest.raises(AiBudgetExceededError, match="reserved ceiling"):
        budget.finalize(reservation, MONTHLY_AI_CAP_MICRO_USD + 1)

    snapshot = budget.snapshot()
    assert snapshot["spent_microusd"] == 0
    assert snapshot["reserved_microusd"] == MONTHLY_AI_CAP_MICRO_USD
    assert snapshot["remaining_microusd"] == 0


def test_stale_reservation_is_not_reclaimed_while_call_can_be_in_flight(
    life, user, monkeypatch
):
    started_at = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(ai_budget_module, "_now", lambda: started_at)
    budget = AiBudgetStore(life)
    reservation = budget.reserve(user.id, "gpt-4o-mini", MONTHLY_AI_CAP_MICRO_USD)

    monkeypatch.setattr(
        ai_budget_module,
        "_now",
        lambda: (
            started_at + timedelta(minutes=ai_budget_module.RESERVATION_TTL_MINUTES + 1)
        ),
    )
    with pytest.raises(AiBudgetExceededError, match="not called"):
        budget.reserve(user.id, "gpt-4o-mini", 1)

    assert budget.snapshot()["reserved_microusd"] == MONTHLY_AI_CAP_MICRO_USD
    budget.finalize(reservation, MONTHLY_AI_CAP_MICRO_USD)
    snapshot = budget.snapshot()
    assert snapshot["spent_microusd"] == MONTHLY_AI_CAP_MICRO_USD
    assert snapshot["reserved_microusd"] == 0


def test_budget_month_uses_sao_paulo_calendar(life, user, monkeypatch):
    # 2026-03-01 01:30 UTC is still 2026-02-28 22:30 in Sao Paulo.
    now = datetime(2026, 3, 1, 1, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(ai_budget_module, "_now", lambda: now)
    budget = AiBudgetStore(life)

    reservation = budget.reserve(user.id, "gpt-4o-mini", 1)

    row = life.connection.execute(
        "SELECT month FROM jarvis_ai_cost_events WHERE id = ?", (reservation,)
    ).fetchone()
    assert row["month"] == "2026-02"
    assert budget.snapshot()["month"] == "2026-02"
