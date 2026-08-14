"""Jarvis action proposals: no silent writes and exactly-once confirmation."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.life import open_life
from openjarvis.life.integrations import IntegrationsStore
from openjarvis.life.jarvis import (
    JarvisActionError,
    JarvisActionStore,
    JarvisRuntime,
    _InteractiveLifeEngine,
)
from openjarvis.life.schema import CURRENT_SCHEMA_VERSION

DEVICE_ID = "device-calendar-1234"
CLAIM_TOKEN = "calendarclaimtoken_abcdefghijklmnopqrstuvwxyz012345"


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


def _connect_calendar(life, user_id: str, device_id: str = DEVICE_ID) -> None:
    IntegrationsStore(life).register_device_grant(
        user_id,
        "apple_calendar",
        granted=["events.read", "events.write"],
        device_id=device_id,
        device_label="iPhone de teste",
    )


def _calendar(actions: JarvisActionStore, user_id: str, **overrides):
    payload = {
        "title": "Reunião com o time",
        "start_at": "2026-08-12T14:00:00-03:00",
        "end_at": "2026-08-12T15:00:00-03:00",
        "location": "Escritório",
    }
    payload.update(overrides)
    return actions.create(user_id, "calendar_create", payload)


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


@pytest.mark.parametrize(
    ("kind", "fields", "expected"),
    [
        ("training_profile", {"primary_sport": "canoeing"}, "perfil de treino"),
        ("nutrition", {"description": "Almoço"}, "registro de alimentação"),
        ("health_document", {"name": "Hemograma"}, "documento de saúde"),
        ("family_event", {"title": "Aniversário"}, "evento familiar"),
    ],
)
def test_specialist_proposals_use_human_labels(life, user, kind, fields, expected):
    proposal = JarvisActionStore(life).create(
        user.id,
        "life_record",
        {"kind": kind, "fields": fields},
    )

    assert f"Confirmar novo {expected}" in proposal["summary"]


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


def test_confirmation_claims_proposal_before_domain_write(life, user, monkeypatch):
    actions = JarvisActionStore(life)
    proposal = _expense(actions, user.id)
    statements = []
    execute = life.connection.execute

    def recording_execute(sql, params=()):
        statements.append(" ".join(sql.split()))
        return execute(sql, params)

    monkeypatch.setattr(life.connection, "execute", recording_execute)

    actions.confirm(user.id, proposal["id"], confirmation_method="face_id")

    claim_indexes = [
        index
        for index, statement in enumerate(statements)
        if "UPDATE jarvis_action_proposals SET status = 'executing'" in statement
    ]
    write_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("INSERT INTO transactions")
    )
    assert claim_indexes, statements
    assert claim_indexes[0] < write_index


def test_confirmation_locks_approved_target_after_claim_on_postgres(
    life, user, monkeypatch
):
    bill_id = life.store.insert(
        "bills",
        user.id,
        {"name": "Aluguel", "amount_cents": 500000, "due_on": "2026-08-10"},
    )
    actions = JarvisActionStore(life)
    proposal = actions.create(
        user.id,
        "life_complete",
        {"kind": "bill", "record_id": bill_id},
    )
    statements = []
    execute = life.connection.execute
    life.connection._backend = "postgres"

    def postgres_sql_over_sqlite(sql, params=()):
        normalized = " ".join(sql.split())
        statements.append(normalized)
        life.connection._backend = "sqlite"
        try:
            return execute(sql.replace(" FOR UPDATE", ""), params)
        finally:
            life.connection._backend = "postgres"

    monkeypatch.setattr(life.connection, "execute", postgres_sql_over_sqlite)

    actions.confirm(user.id, proposal["id"], confirmation_method="face_id")

    claim_index = next(
        index
        for index, statement in enumerate(statements)
        if "UPDATE jarvis_action_proposals SET status = 'executing'" in statement
    )
    lock_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("SELECT * FROM bills")
        and statement.endswith("FOR UPDATE")
    )
    assert claim_index < lock_index


def test_confirmation_does_not_execute_after_losing_atomic_claim(
    life, user, monkeypatch
):
    actions = JarvisActionStore(life)
    proposal = _expense(actions, user.id)
    execute = life.connection.execute

    class LostClaimCursor:
        rowcount = 0

    def lose_claim(sql, params=()):
        if "UPDATE jarvis_action_proposals SET status = 'executing'" in " ".join(
            sql.split()
        ):
            return LostClaimCursor()
        return execute(sql, params)

    monkeypatch.setattr(life.connection, "execute", lose_claim)

    with pytest.raises(JarvisActionError, match="claimed by another request"):
        actions.confirm(user.id, proposal["id"], confirmation_method="face_id")

    assert life.store.count("transactions", user.id) == 0


def test_two_database_connections_confirm_one_proposal_exactly_once(tmp_path):
    path = tmp_path / "concurrent-life.db"
    first_life = open_life(path)
    second_life = None
    try:
        user = first_life.users.create_user(
            "concorrencia@exemplo.com",
            "senha-forte-123",
        )
        proposal = _expense(JarvisActionStore(first_life), user.id)
        second_life = open_life(path)
        barrier = threading.Barrier(2)

        def confirm(actions):
            barrier.wait(timeout=5)
            return actions.confirm(
                user.id,
                proposal["id"],
                confirmation_method="face_id",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(confirm, JarvisActionStore(first_life)),
                executor.submit(confirm, JarvisActionStore(second_life)),
            ]
            results = [future.result(timeout=10) for future in futures]

        assert sorted(result["replayed"] for result in results) == [False, True]
        assert first_life.store.count("transactions", user.id) == 1
    finally:
        if second_life is not None:
            second_life.close()
        first_life.close()


def test_failed_finalization_rolls_back_claim_and_domain_write(life, user, monkeypatch):
    actions = JarvisActionStore(life)
    proposal = _expense(actions, user.id)
    execute = life.connection.execute

    class LostFinalizationCursor:
        rowcount = 0

    def lose_finalization(sql, params=()):
        normalized = " ".join(sql.split())
        if (
            "UPDATE jarvis_action_proposals SET status = ?, result_json = ?"
            in normalized
        ):
            return LostFinalizationCursor()
        return execute(sql, params)

    monkeypatch.setattr(life.connection, "execute", lose_finalization)

    with pytest.raises(JarvisActionError, match="finalized atomically"):
        actions.confirm(user.id, proposal["id"], confirmation_method="face_id")

    assert actions.get(user.id, proposal["id"])["status"] == "pending"
    assert life.store.count("transactions", user.id) == 0


def test_failed_nested_domain_action_rolls_back_every_financial_effect(life, user):
    bill_id = life.store.insert(
        "bills",
        user.id,
        {
            "name": "Contrato inconsistente",
            "amount_cents": 12500,
            "due_on": "2026-08-10",
            "recurrence": "invalid",
        },
    )
    actions = JarvisActionStore(life)
    proposal = actions.create(
        user.id,
        "life_complete",
        {"kind": "bill", "record_id": bill_id},
    )

    result = actions.confirm(
        user.id,
        proposal["id"],
        confirmation_method="face_id",
    )

    assert result["proposal"]["status"] == "failed"
    assert life.store.get("bills", user.id, bill_id)["status"] == "pending"
    assert life.store.count("transactions", user.id) == 0


def test_cancel_does_not_claim_success_after_losing_atomic_update(
    life, user, monkeypatch
):
    actions = JarvisActionStore(life)
    proposal = _expense(actions, user.id)
    execute = life.connection.execute

    class LostCancelCursor:
        rowcount = 0

    def lose_cancel(sql, params=()):
        if "SET status = 'canceled'" in " ".join(sql.split()):
            return LostCancelCursor()
        return execute(sql, params)

    monkeypatch.setattr(life.connection, "execute", lose_cancel)

    with pytest.raises(JarvisActionError, match="resolved by another request"):
        actions.cancel(user.id, proposal["id"])

    assert actions.get(user.id, proposal["id"])["status"] == "pending"


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


def test_expiration_does_not_overwrite_a_concurrent_cancellation(
    life, user, monkeypatch
):
    actions = JarvisActionStore(life)
    proposal = _expense(actions, user.id)
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    life.connection.execute(
        "UPDATE jarvis_action_proposals SET expires_at = ? WHERE id = ?",
        (expired_at, proposal["id"]),
    )
    life.connection.commit()
    execute = life.connection.execute

    def cancel_before_expiration_cas(sql, params=()):
        normalized = " ".join(sql.split())
        if "SET status = 'expired', resolved_at = ?" in normalized:
            execute(
                "UPDATE jarvis_action_proposals SET status = 'canceled'"
                " WHERE id = ? AND user_id = ? AND status = 'pending'",
                (proposal["id"], user.id),
            )
        return execute(sql, params)

    monkeypatch.setattr(life.connection, "execute", cancel_before_expiration_cas)

    with pytest.raises(JarvisActionError, match="resolved by another request"):
        actions.confirm(user.id, proposal["id"], confirmation_method="explicit")

    assert actions.get(user.id, proposal["id"])["status"] == "canceled"
    assert life.store.count("transactions", user.id) == 0


def test_calendar_proposal_requires_a_connected_iphone_calendar(life, user):
    actions = JarvisActionStore(life)

    with pytest.raises(JarvisActionError, match="not connected"):
        _calendar(actions, user.id)


def test_calendar_proposal_is_normalized_and_summarized(life, user):
    _connect_calendar(life, user.id)
    actions = JarvisActionStore(life)

    proposal = _calendar(
        actions,
        user.id,
        title="  Consulta médica  ",
        start_at="2026-08-12T17:00:00Z",
        end_at="2026-08-12T18:00:00Z",
        notes="  Levar exames  ",
    )

    assert proposal["status"] == "pending"
    assert proposal["summary"] == (
        "Confirmar evento no Calendário: Consulta médica em 12/08/2026 às 17:00"
    )
    assert proposal["arguments"] == {
        "title": "Consulta médica",
        "start_at": "2026-08-12T17:00:00+00:00",
        "end_at": "2026-08-12T18:00:00+00:00",
        "is_all_day": False,
        "location": "Escritório",
        "notes": "Levar exames",
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"title": "   "}, "title must be a non-empty string"),
        ({"title": "x" * 161}, "title must be at most 160"),
        ({"start_at": "2026-08-12T14:00:00"}, "start_at must be an ISO"),
        (
            {"end_at": "2026-08-12T14:00:00-03:00"},
            "end_at must be after start_at",
        ),
        (
            {"end_at": "2026-09-13T14:00:00-03:00"},
            "cannot last more than 31 days",
        ),
        ({"is_all_day": "yes"}, "is_all_day must be a boolean"),
        ({"is_all_day": True}, "all-day events must start and end at midnight"),
        ({"location": "x" * 201}, "location must be at most 200"),
        ({"notes": "x" * 1001}, "notes must be at most 1000"),
        ({"alarm": "08:00"}, "Unknown calendar fields: alarm"),
    ],
)
def test_calendar_proposal_rejects_invalid_arguments(life, user, overrides, message):
    _connect_calendar(life, user.id)
    actions = JarvisActionStore(life)

    with pytest.raises(JarvisActionError, match=message):
        _calendar(actions, user.id, **overrides)


def test_server_confirmation_refuses_a_native_calendar_action(life, user):
    _connect_calendar(life, user.id)
    actions = JarvisActionStore(life)
    proposal = _calendar(actions, user.id)

    with pytest.raises(JarvisActionError, match="completed on the iPhone"):
        actions.confirm(user.id, proposal["id"], confirmation_method="explicit")

    assert actions.get(user.id, proposal["id"])["status"] == "pending"


def test_native_calendar_resolution_is_tenant_safe_and_exactly_once(
    life, user, other_user
):
    _connect_calendar(life, user.id)
    actions = JarvisActionStore(life)
    proposal = _calendar(actions, user.id)
    event = {
        "id": "event-123",
        "title": "Reunião com o time",
        "startAt": "2026-08-12T14:00:00-03:00",
        "endAt": "2026-08-12T15:00:00-03:00",
        "isAllDay": False,
        "location": "Escritório",
        "calendarTitle": "Pessoal",
    }

    with pytest.raises(JarvisActionError, match="not found"):
        actions.prepare_native(
            other_user.id,
            proposal["id"],
            device_id=DEVICE_ID,
            claim_token=CLAIM_TOKEN,
            confirmation_method="explicit",
        )

    prepared = actions.prepare_native(
        user.id,
        proposal["id"],
        device_id=DEVICE_ID,
        claim_token=CLAIM_TOKEN,
        confirmation_method="voice_explicit",
    )
    assert prepared["proposal"]["status"] == "executing"

    divergent = {**event, "title": "Outro evento"}
    with pytest.raises(JarvisActionError, match="does not match the proposal"):
        actions.resolve_native(
            user.id,
            proposal["id"],
            result={"event": divergent},
            device_id=DEVICE_ID,
            claim_token=CLAIM_TOKEN,
        )
    assert actions.get(user.id, proposal["id"])["status"] == "executing"

    IntegrationsStore(life).disconnect(user.id, "apple_calendar")
    with pytest.raises(JarvisActionError, match="não está autorizado"):
        actions.resolve_native(
            user.id,
            proposal["id"],
            result={"event": event},
            device_id=DEVICE_ID,
            claim_token=CLAIM_TOKEN,
        )
    assert actions.get(user.id, proposal["id"])["status"] == "executing"
    _connect_calendar(life, user.id)

    first = actions.resolve_native(
        user.id,
        proposal["id"],
        result={"event": event},
        device_id=DEVICE_ID,
        claim_token=CLAIM_TOKEN,
    )
    second = actions.resolve_native(
        user.id,
        proposal["id"],
        result={"event": {"id": "different"}},
        device_id="different-device-1234",
        claim_token="differentclaimtoken_abcdefghijklmnopqrstuvwxyz123456",
    )

    assert first["replayed"] is False
    assert first["proposal"]["status"] == "confirmed"
    assert first["proposal"]["confirmation_method"] == "voice_explicit"
    assert first["proposal"]["result"]["success"] is True
    assert first["proposal"]["result"]["metadata"] == {
        "event": event,
        "device_id": DEVICE_ID,
    }
    assert second["replayed"] is True
    assert second["proposal"]["result"]["metadata"]["event"] == event


def test_native_claim_is_device_scoped_atomic_and_idempotent(life, user):
    _connect_calendar(life, user.id)
    actions = JarvisActionStore(life)
    proposal = _calendar(actions, user.id)

    with pytest.raises(JarvisActionError, match="neste aparelho"):
        actions.prepare_native(
            user.id,
            proposal["id"],
            device_id="unregistered-device-1234",
            claim_token=CLAIM_TOKEN,
            confirmation_method="explicit",
        )
    assert actions.get(user.id, proposal["id"])["status"] == "pending"

    first = actions.prepare_native(
        user.id,
        proposal["id"],
        device_id=DEVICE_ID,
        claim_token=CLAIM_TOKEN,
        confirmation_method="explicit",
    )
    replay = actions.prepare_native(
        user.id,
        proposal["id"],
        device_id=DEVICE_ID,
        claim_token=CLAIM_TOKEN,
        confirmation_method="explicit",
    )
    with pytest.raises(JarvisActionError, match="already claimed"):
        actions.prepare_native(
            user.id,
            proposal["id"],
            device_id=DEVICE_ID,
            claim_token="anotherclaimtoken_abcdefghijklmnopqrstuvwxyz012345",
            confirmation_method="explicit",
        )

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert actions.list_pending(user.id)[0]["status"] == "executing"


def test_native_resolution_requires_the_claim_token(life, user):
    _connect_calendar(life, user.id)
    actions = JarvisActionStore(life)
    proposal = _calendar(actions, user.id)
    actions.prepare_native(
        user.id,
        proposal["id"],
        device_id=DEVICE_ID,
        claim_token=CLAIM_TOKEN,
        confirmation_method="explicit",
    )
    event = {
        "id": "event-123",
        "title": "Reunião com o time",
        "startAt": "2026-08-12T14:00:00-03:00",
        "endAt": "2026-08-12T15:00:00-03:00",
        "isAllDay": False,
        "location": "Escritório",
        "calendarTitle": "Pessoal",
    }

    with pytest.raises(JarvisActionError, match="claim token does not match"):
        actions.resolve_native(
            user.id,
            proposal["id"],
            result={"event": event},
            device_id=DEVICE_ID,
            claim_token="differentclaimtoken_abcdefghijklmnopqrstuvwxyz123456",
        )
    assert actions.get(user.id, proposal["id"])["status"] == "executing"


def test_expired_native_claim_returns_proposal_to_pending(life, user):
    _connect_calendar(life, user.id)
    actions = JarvisActionStore(life)
    proposal = _calendar(actions, user.id)
    actions.prepare_native(
        user.id,
        proposal["id"],
        device_id=DEVICE_ID,
        claim_token=CLAIM_TOKEN,
        confirmation_method="explicit",
    )
    with life.store.transaction():
        life.connection.execute(
            "UPDATE jarvis_native_action_claims SET expires_at = ?"
            " WHERE proposal_id = ? AND user_id = ?",
            ("2020-01-01T00:00:00+00:00", proposal["id"], user.id),
        )
    event = {
        "id": "event-expired",
        "title": "Reunião com o time",
        "startAt": "2026-08-12T14:00:00-03:00",
        "endAt": "2026-08-12T15:00:00-03:00",
        "isAllDay": False,
        "location": "Escritório",
        "calendarTitle": "Pessoal",
    }

    with pytest.raises(JarvisActionError, match="claim expired"):
        actions.resolve_native(
            user.id,
            proposal["id"],
            result={"event": event},
            device_id=DEVICE_ID,
            claim_token=CLAIM_TOKEN,
        )

    assert actions.get(user.id, proposal["id"])["status"] == "pending"


def test_native_expiration_does_not_overwrite_a_concurrent_resolution(
    life, user, monkeypatch
):
    _connect_calendar(life, user.id)
    actions = JarvisActionStore(life)
    proposal = _calendar(actions, user.id)
    actions.prepare_native(
        user.id,
        proposal["id"],
        device_id=DEVICE_ID,
        claim_token=CLAIM_TOKEN,
        confirmation_method="explicit",
    )
    with life.store.transaction():
        life.connection.execute(
            "UPDATE jarvis_action_proposals SET expires_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", proposal["id"]),
        )
    event = {
        "id": "event-expired-race",
        "title": "Reunião com o time",
        "startAt": "2026-08-12T14:00:00-03:00",
        "endAt": "2026-08-12T15:00:00-03:00",
        "isAllDay": False,
        "location": "Escritório",
        "calendarTitle": "Pessoal",
    }
    execute = life.connection.execute

    def resolve_before_expiration_cas(sql, params=()):
        normalized = " ".join(sql.split())
        if "SET status = 'expired', resolved_at = ?" in normalized:
            execute(
                "UPDATE jarvis_action_proposals SET status = 'confirmed',"
                " result_json = '{}'"
                " WHERE id = ? AND user_id = ? AND status = 'executing'",
                (proposal["id"], user.id),
            )
        return execute(sql, params)

    monkeypatch.setattr(life.connection, "execute", resolve_before_expiration_cas)

    with pytest.raises(JarvisActionError, match="resolved by another request"):
        actions.resolve_native(
            user.id,
            proposal["id"],
            result={"event": event},
            device_id=DEVICE_ID,
            claim_token=CLAIM_TOKEN,
        )

    assert actions.get(user.id, proposal["id"])["status"] == "confirmed"


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
    assert result["proposals"] == []
    assert result["proposal_intents"] == [
        {
            "tool_name": "life_record",
            "arguments": {
                "kind": "expense",
                "fields": {"amount_cents": 4590, "category": "mercado"},
            },
        }
    ]
    assert JarvisActionStore(life).list_pending(user.id) == []
    assert life.store.count("transactions", user.id) == 0


def test_runtime_only_exposes_calendar_create_after_device_connection(life, user):
    _connect_calendar(life, user.id)

    class CalendarEngine:
        def __init__(self):
            self.calls = 0

        def generate(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                assert {tool["function"]["name"] for tool in kwargs["tools"]} == {
                    "life_overview",
                    "life_record",
                    "life_complete",
                    "calendar_create",
                }
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_calendar",
                            "name": "calendar_create",
                            "arguments": (
                                '{"title":"Dentista","start_at":'
                                '"2026-08-12T14:00:00-03:00","end_at":'
                                '"2026-08-12T15:00:00-03:00"}'
                            ),
                        }
                    ],
                    "usage": {},
                }
            return {"content": "Confirme o evento no iPhone.", "usage": {}}

    runtime = JarvisRuntime(life, user.id, CalendarEngine(), "test-model")
    result = runtime.run("Marque dentista amanhã às 14h", "Você é o Jarvis.")

    assert result["proposals"] == []
    assert result["proposal_intents"][0]["tool_name"] == "calendar_create"
    assert result["proposal_intents"][0]["arguments"]["title"] == "Dentista"
    assert JarvisActionStore(life).list_pending(user.id) == []


def test_runtime_builds_system_plus_persisted_history_before_current_question(
    life, user
):
    class CapturingEngine:
        def __init__(self):
            self.messages = []

        def generate(self, messages, **kwargs):
            self.messages = messages
            return {"content": "Continuamos.", "usage": {}}

    engine = CapturingEngine()
    runtime = JarvisRuntime(life, user.id, engine, "test-model")
    result = runtime.run(
        "Marketing",
        "Você é o Jarvis.",
        [
            {"role": "user", "content": "Marque uma reunião amanhã às 15h"},
            {"role": "assistant", "content": "Qual é o título?"},
        ],
        {
            "type": "calendar_create",
            "slots": {"start_at": "2026-08-11T15:00:00-03:00"},
            "missing_slots": ["title"],
        },
    )

    assert [message.role.value for message in engine.messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert "ESTADO ESTRUTURADO DA CONVERSA" in engine.messages[0].content
    assert engine.messages[-1].content == "Marketing"
    assert result["history"][-1] == {
        "role": "assistant",
        "content": "Continuamos.",
    }


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
