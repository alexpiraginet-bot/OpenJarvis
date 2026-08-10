"""Jarvis action proposals: no silent writes and exactly-once confirmation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.life.jarvis import (
    JarvisActionError,
    JarvisActionStore,
    JarvisRuntime,
    _InteractiveLifeEngine,
)
from openjarvis.life.schema import CURRENT_SCHEMA_VERSION


def _expense(actions: JarvisActionStore, user_id: str):
    return actions.create(
        user_id,
        "life_record",
        {
            "kind": "expense",
            "fields": {
                "amount_cents": 4590,
                "category": "mercado",
                "description": "Feira",
            },
        },
    )


def test_schema_version_and_internal_action_table_exist(life):
    version = life.connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == CURRENT_SCHEMA_VERSION
    table = life.connection.execute(
        "SELECT name FROM sqlite_master"
        " WHERE type = 'table' AND name = 'jarvis_action_proposals'"
    ).fetchone()
    assert table["name"] == "jarvis_action_proposals"


def test_proposal_does_not_write_before_confirmation(life, user):
    actions = JarvisActionStore(life)
    proposal = _expense(actions, user.id)
    assert proposal["status"] == "pending"
    assert proposal["summary"] == "Confirmar novo gasto de R$ 45,90: Feira"
    assert life.store.count("transactions", user.id) == 0
    assert actions.list_pending(user.id)[0]["id"] == proposal["id"]


def test_proposal_normalizes_amount_before_summary_and_execution(life, user):
    actions = JarvisActionStore(life)
    proposal = actions.create(
        user.id,
        "life_record",
        {
            "kind": "expense",
            "fields": {
                "amount_cents": "4590",
                "category": "mercado",
                "description": "Feira",
            },
        },
    )

    assert proposal["arguments"]["fields"]["amount_cents"] == 4590
    assert proposal["summary"] == "Confirmar novo gasto de R$ 45,90: Feira"

    actions.confirm(user.id, proposal["id"], confirmation_method="explicit")
    transaction = life.store.list_records("transactions", user.id)[0]
    assert transaction["amount_cents"] == 4590


@pytest.mark.parametrize("amount", [True, 4590.0, "45.90", None])
def test_non_integer_amount_is_rejected_before_proposal(life, user, amount):
    actions = JarvisActionStore(life)
    with pytest.raises(JarvisActionError, match="amount_cents must be an integer"):
        actions.create(
            user.id,
            "life_record",
            {"kind": "expense", "fields": {"amount_cents": amount}},
        )
    count = life.connection.execute(
        "SELECT COUNT(*) AS n FROM jarvis_action_proposals WHERE user_id = ?",
        (user.id,),
    ).fetchone()["n"]
    assert count == 0
    assert life.store.count("transactions", user.id) == 0


def test_bill_completion_proposal_includes_title_value_and_snapshot(life, user):
    bill = life.store.insert(
        "bills",
        user.id,
        {"name": "Aluguel", "amount_cents": 500000, "due_on": "2026-08-10"},
    )
    actions = JarvisActionStore(life)
    proposal = actions.create(
        user.id,
        "life_complete",
        {"kind": "bill", "record_id": bill},
    )

    assert proposal["summary"] == (
        "Confirmar pagamento da conta de R$ 5.000,00: Aluguel"
    )
    snapshot = proposal["arguments"]["_target_snapshot"]
    assert snapshot["table"] == "bills"
    assert snapshot["record"]["id"] == bill
    assert snapshot["record"]["name"] == "Aluguel"
    assert snapshot["record"]["amount_cents"] == 500000
    assert life.store.count("transactions", user.id) == 0

    actions.confirm(user.id, proposal["id"], confirmation_method="explicit")
    assert life.store.get("bills", user.id, bill)["status"] == "paid"
    transaction = life.store.list_records("transactions", user.id)[0]
    assert transaction["amount_cents"] == 500000
    assert transaction["source"] == "bill"


def test_confirmation_rejects_a_changed_target_snapshot(life, user):
    bill = life.store.insert(
        "bills",
        user.id,
        {"name": "Aluguel", "amount_cents": 500000, "due_on": "2026-08-10"},
    )
    actions = JarvisActionStore(life)
    proposal = actions.create(
        user.id,
        "life_complete",
        {"kind": "bill", "record_id": bill},
    )
    life.store.update("bills", user.id, bill, {"amount_cents": 600000})

    with pytest.raises(JarvisActionError, match="target changed"):
        actions.confirm(user.id, proposal["id"], confirmation_method="explicit")

    assert actions.get(user.id, proposal["id"])["status"] == "pending"
    assert life.store.get("bills", user.id, bill)["status"] == "pending"
    assert life.store.count("transactions", user.id) == 0


def test_completion_proposal_cannot_snapshot_another_tenant(life, user, other_user):
    bill = life.store.insert(
        "bills",
        other_user.id,
        {"name": "Conta da Bruna", "amount_cents": 100, "due_on": "2026-08-10"},
    )
    actions = JarvisActionStore(life)
    with pytest.raises(JarvisActionError, match="not found"):
        actions.create(
            user.id,
            "life_complete",
            {"kind": "bill", "record_id": bill},
        )
    assert life.store.get("bills", other_user.id, bill)["status"] == "pending"
    assert life.store.count("transactions", other_user.id) == 0


def test_proposal_cannot_create_a_paid_bill_without_domain_action(life, user):
    actions = JarvisActionStore(life)
    with pytest.raises(JarvisActionError, match="Fields require a domain action"):
        actions.create(
            user.id,
            "life_record",
            {
                "kind": "bill",
                "fields": {
                    "name": "Luz",
                    "amount_cents": 18000,
                    "status": "paid",
                    "paid_on": "2026-08-10",
                },
            },
        )
    assert life.store.count("bills", user.id) == 0
    assert life.store.count("transactions", user.id) == 0


def test_confirmation_executes_exactly_once(life, user):
    actions = JarvisActionStore(life)
    proposal = _expense(actions, user.id)

    first = actions.confirm(user.id, proposal["id"], confirmation_method="face_id")
    second = actions.confirm(user.id, proposal["id"], confirmation_method="face_id")

    assert first["replayed"] is False
    assert first["proposal"]["status"] == "confirmed"
    assert first["proposal"]["confirmation_method"] == "face_id"
    assert second["replayed"] is True
    assert life.store.count("transactions", user.id) == 1


def test_another_tenant_cannot_confirm_a_proposal(life, user, other_user):
    actions = JarvisActionStore(life)
    proposal = _expense(actions, user.id)
    with pytest.raises(JarvisActionError, match="not found"):
        actions.confirm(other_user.id, proposal["id"], confirmation_method="explicit")
    assert life.store.count("transactions", user.id) == 0


def test_canceled_proposal_cannot_execute(life, user):
    actions = JarvisActionStore(life)
    proposal = _expense(actions, user.id)
    canceled = actions.cancel(user.id, proposal["id"])
    assert canceled["status"] == "canceled"
    with pytest.raises(JarvisActionError, match="canceled"):
        actions.confirm(user.id, proposal["id"], confirmation_method="explicit")
    assert life.store.count("transactions", user.id) == 0


def test_expired_proposal_is_persisted_as_expired(life, user):
    actions = JarvisActionStore(life)
    proposal = _expense(actions, user.id)
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    life.connection.execute(
        "UPDATE jarvis_action_proposals SET expires_at = ? WHERE id = ?",
        (expired_at, proposal["id"]),
    )
    life.connection.commit()

    with pytest.raises(JarvisActionError, match="expired"):
        actions.confirm(user.id, proposal["id"], confirmation_method="explicit")
    assert actions.get(user.id, proposal["id"])["status"] == "expired"
    assert life.store.count("transactions", user.id) == 0


def test_runtime_turns_a_model_write_into_a_proposal(life, user):
    class FakeEngine:
        def __init__(self):
            self.calls = 0

        def generate(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                assert {tool["function"]["name"] for tool in kwargs["tools"]} == {
                    "life_overview",
                    "life_record",
                    "life_complete",
                }
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "name": "life_record",
                            "arguments": (
                                '{"kind":"expense","fields":'
                                '{"amount_cents":4590,"category":"mercado"}}'
                            ),
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                }
            return {
                "content": "Preparei o gasto; confirme para registrar.",
                "usage": {"prompt_tokens": 20, "completion_tokens": 8},
            }

    runtime = JarvisRuntime(life, user.id, FakeEngine(), "test-model")
    result = runtime.run("Gastei 45,90 no mercado", "Você é o Jarvis.")

    assert "confirme" in result["answer"]
    assert result["proposals"][0]["status"] == "pending"
    assert life.store.count("transactions", user.id) == 0


@pytest.mark.parametrize(
    ("model", "expected_effort"),
    [
        ("gpt-5-mini", "minimal"),
        ("gpt-5.6-luna", "none"),
    ],
)
def test_interactive_life_engine_uses_low_latency_gpt_controls(model, expected_effort):
    class FakeEngine:
        def __init__(self):
            self.kwargs = None

        def generate(self, messages, **kwargs):
            self.kwargs = kwargs
            return {"content": "Olá"}

    delegate = FakeEngine()
    engine = _InteractiveLifeEngine(delegate)

    result = engine.generate([], model=model, max_tokens=100)

    assert result["content"] == "Olá"
    assert delegate.kwargs["reasoning_effort"] == expected_effort
    assert delegate.kwargs["verbosity"] == "low"


def test_interactive_life_engine_does_not_send_openai_controls_to_anthropic():
    class FakeEngine:
        def __init__(self):
            self.kwargs = None

        def generate(self, messages, **kwargs):
            self.kwargs = kwargs
            return {"content": "Olá"}

    delegate = FakeEngine()
    engine = _InteractiveLifeEngine(delegate)

    engine.generate([], model="claude-sonnet-4-6")

    assert "reasoning_effort" not in delegate.kwargs
    assert "verbosity" not in delegate.kwargs
