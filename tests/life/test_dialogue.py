"""Conversation continuity, CAS and idempotency for the Life assistant."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openjarvis.life.dialogue import (
    MAX_HISTORY_MESSAGES,
    TURN_RESERVATION_TTL_SECONDS,
    DialogueConflict,
    DialogueStore,
    resolve_intent,
)
from openjarvis.life.jarvis import JarvisActionStore


def _start(
    store: DialogueStore,
    user_id: str,
    conversation_id: str,
    turn_id: str,
    question: str,
    revision: int | None,
):
    return store.start_turn(
        user_id,
        conversation_id,
        turn_id,
        {"question": question, "expected_revision": revision},
        expected_revision=revision,
    )


def _complete(store: DialogueStore, turn, question: str, answer: str):
    return store.complete_turn(
        turn,
        question=question,
        answer=answer,
        response={"answer": answer, "source": "model"},
        pending_intent=None,
    )


def test_same_turn_and_payload_replays_the_stored_response(life, user):
    store = DialogueStore(life)
    turn = _start(store, user.id, "conversation", "turn", "Olá", 0)
    first = _complete(store, turn, "Olá", "Oi, Alex.")

    replay = _start(store, user.id, "conversation", "turn", "Olá", 0)

    assert replay.replay_response == first
    assert store.get_session(user.id, "conversation")["revision"] == 1


def test_same_turn_is_reserved_before_inference_and_recovers_after_expiry(life, user):
    clock = [datetime(2026, 8, 10, 12, tzinfo=timezone.utc)]
    store = DialogueStore(life, now=lambda: clock[0])

    first = _start(store, user.id, "conversation", "turn", "Olá", 0)

    with pytest.raises(DialogueConflict, match="already in progress") as caught:
        _start(store, user.id, "conversation", "turn", "Olá", 0)
    assert caught.value.current_revision == 0

    clock[0] += timedelta(seconds=300)
    with pytest.raises(DialogueConflict, match="already in progress"):
        _start(store, user.id, "conversation", "turn", "Olá", 0)

    clock[0] += timedelta(seconds=59)
    with pytest.raises(DialogueConflict, match="already in progress"):
        _start(store, user.id, "conversation", "turn", "Olá", 0)

    clock[0] += timedelta(seconds=1)
    recovered = _start(store, user.id, "conversation", "turn", "Olá", 0)

    with pytest.raises(DialogueConflict, match="reservation"):
        _complete(store, first, "Olá", "Resposta obsoleta")
    completed = _complete(store, recovered, "Olá", "Resposta válida")
    assert completed["answer"] == "Resposta válida"


def test_reused_turn_id_with_a_different_payload_conflicts(life, user):
    store = DialogueStore(life)
    turn = _start(store, user.id, "conversation", "turn", "Olá", 0)
    _complete(store, turn, "Olá", "Oi.")

    with pytest.raises(DialogueConflict, match="different request") as caught:
        _start(store, user.id, "conversation", "turn", "Outro pedido", 0)

    assert caught.value.current_revision == 1


def test_reserved_turn_id_rejects_a_different_payload_before_inference(life, user):
    store = DialogueStore(life)
    _start(store, user.id, "conversation", "turn", "Olá", 0)

    with pytest.raises(DialogueConflict, match="different request") as caught:
        _start(store, user.id, "conversation", "turn", "Outro pedido", 0)

    assert caught.value.current_revision == 0


def test_compare_and_swap_rejects_two_writers_from_the_same_revision(life, user):
    store = DialogueStore(life)
    first = _start(store, user.id, "conversation", "turn-a", "A", 0)
    concurrent = _start(store, user.id, "conversation", "turn-b", "B", 0)
    _complete(store, first, "A", "Resposta A")

    with pytest.raises(DialogueConflict, match="changed") as caught:
        _complete(store, concurrent, "B", "Resposta B")

    assert caught.value.current_revision == 1
    assert store.get_session(user.id, "conversation")["history"][-1]["content"] == (
        "Resposta A"
    )


def test_losing_compare_and_swap_releases_its_turn_for_immediate_retry(life, user):
    store = DialogueStore(life)
    first = _start(store, user.id, "conversation", "turn-a", "A", 0)
    losing = _start(store, user.id, "conversation", "turn-b", "B", 0)
    _complete(store, first, "A", "Resposta A")

    with pytest.raises(DialogueConflict, match="changed"):
        _complete(store, losing, "B", "Resposta obsoleta")

    retry = _start(store, user.id, "conversation", "turn-b", "B", 1)
    response = _complete(store, retry, "B", "Resposta atualizada")

    assert response["revision"] == 2
    assert response["answer"] == "Resposta atualizada"


def test_stale_completion_never_releases_another_reservation_owner(life, user):
    clock = [datetime(2026, 8, 10, 12, tzinfo=timezone.utc)]
    store = DialogueStore(life, now=lambda: clock[0])
    stale = _start(store, user.id, "conversation", "turn", "Olá", 0)
    clock[0] += timedelta(seconds=TURN_RESERVATION_TTL_SECONDS)
    current = _start(store, user.id, "conversation", "turn", "Olá", 0)

    with pytest.raises(DialogueConflict, match="reservation"):
        _complete(store, stale, "Olá", "Resposta obsoleta")

    response = _complete(store, current, "Olá", "Resposta atual")
    assert response["answer"] == "Resposta atual"


def test_losing_turn_never_persists_its_deferred_action(life, user):
    store = DialogueStore(life)
    actions = JarvisActionStore(life)
    first = _start(store, user.id, "conversation", "turn-a", "A", 0)
    concurrent = _start(store, user.id, "conversation", "turn-b", "B", 0)

    def persist_expense(response):
        response["proposals"] = [
            actions.create(
                user.id,
                "life_record",
                {
                    "kind": "expense",
                    "fields": {"amount_cents": 1000, "category": "teste"},
                },
            )
        ]

    first_response = store.complete_turn(
        first,
        question="A",
        answer="Resposta A",
        response={"answer": "Resposta A", "source": "model", "proposals": []},
        pending_intent=None,
        mutate_response=persist_expense,
    )

    with pytest.raises(DialogueConflict, match="changed"):
        store.complete_turn(
            concurrent,
            question="B",
            answer="Resposta B",
            response={"answer": "Resposta B", "source": "model", "proposals": []},
            pending_intent=None,
            mutate_response=persist_expense,
        )

    pending = actions.list_pending(user.id)
    assert [item["id"] for item in pending] == [first_response["proposals"][0]["id"]]


def test_same_conversation_id_is_structurally_isolated_by_tenant(
    life, user, other_user
):
    store = DialogueStore(life)
    first = _start(store, user.id, "shared-id", "turn-a", "Meu dado", 0)
    second = _start(store, other_user.id, "shared-id", "turn-b", "Outro dado", 0)
    _complete(store, first, "Meu dado", "Resposta do Alex")
    _complete(store, second, "Outro dado", "Resposta da Bruna")

    alex = store.get_session(user.id, "shared-id")
    bruna = store.get_session(other_user.id, "shared-id")

    assert alex["history"][-1]["content"] == "Resposta do Alex"
    assert bruna["history"][-1]["content"] == "Resposta da Bruna"


def test_history_is_bounded_to_twenty_messages(life, user):
    store = DialogueStore(life)
    revision = 0
    for index in range(12):
        question = f"Pergunta {index}"
        turn = _start(
            store,
            user.id,
            "conversation",
            f"turn-{index}",
            question,
            revision,
        )
        response = _complete(store, turn, question, f"Resposta {index}")
        revision = response["revision"]

    history = store.get_session(user.id, "conversation")["history"]
    assert len(history) == MAX_HISTORY_MESSAGES
    assert history[0]["content"] == "Pergunta 2"
    assert history[-1]["content"] == "Resposta 11"


def test_expired_session_resets_and_expired_pending_intent_is_dropped(life, user):
    clock = [datetime(2026, 8, 10, 12, tzinfo=timezone.utc)]
    store = DialogueStore(life, now=lambda: clock[0])
    pending = resolve_intent(
        "Marque uma reunião para mim amanhã às 15h",
        None,
        timezone_name="America/Sao_Paulo",
        now=clock[0],
    )
    turn = _start(store, user.id, "conversation", "turn-a", "Reunião", 0)
    store.complete_turn(
        turn,
        question="Reunião",
        answer=pending.answer,
        response={"answer": pending.answer, "source": "data"},
        pending_intent=pending.pending_intent,
    )

    clock[0] += timedelta(minutes=16)
    active = _start(store, user.id, "conversation", "turn-b", "Marketing", 1)
    assert active.pending_intent is None

    clock[0] += timedelta(days=31)
    reset = _start(store, user.id, "conversation", "turn-c", "Olá", 0)
    assert reset.revision == 0
    assert reset.history == []


def test_pending_calendar_intent_supports_correction_and_cancellation():
    now = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
    first = resolve_intent(
        "Marque uma reunião para mim amanhã às 15h",
        None,
        timezone_name="America/Sao_Paulo",
        now=now,
    )
    corrected = resolve_intent(
        "Na verdade, às 16h",
        first.pending_intent,
        timezone_name="America/Sao_Paulo",
        now=now,
    )
    completed = resolve_intent(
        "Marketing",
        corrected.pending_intent,
        timezone_name="America/Sao_Paulo",
        now=now,
    )
    cancelled = resolve_intent(
        "Cancela isso",
        first.pending_intent,
        timezone_name="America/Sao_Paulo",
        now=now,
    )

    assert corrected.proposal_arguments is None
    assert corrected.pending_intent["slots"]["start_at"].endswith("T16:00:00-03:00")
    assert completed.proposal_arguments["title"] == "Marketing"
    assert completed.proposal_arguments["start_at"].endswith("T16:00:00-03:00")
    assert cancelled.pending_intent is None


def test_history_redacts_keys_and_never_has_an_audio_or_token_column(life, user):
    store = DialogueStore(life)
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    turn = _start(store, user.id, "conversation", "turn", secret, 0)
    _complete(store, turn, secret, f"Não vou repetir {secret}")

    row = life.connection.execute(
        "SELECT history_json FROM jarvis_dialog_sessions WHERE user_id = ? AND id = ?",
        (user.id, "conversation"),
    ).fetchone()
    columns = {
        item["name"]
        for item in life.connection.execute(
            "PRAGMA table_info(jarvis_dialog_sessions)"
        ).fetchall()
    }

    assert secret not in row["history_json"]
    assert "token" not in columns
    assert "audio" not in columns
