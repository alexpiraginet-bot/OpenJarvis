"""Tests for LifeService — the domain rules behind the raw rows."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest

from openjarvis.life.service import (
    LifeServiceError,
    add_months,
    month_range,
    today_in,
)

TODAY = date(2026, 8, 10)


# -- Date helpers ------------------------------------------------------------


def test_add_months_clamps_to_month_end():
    """31 January + 1 month is end of February, not 3 March."""
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_add_months_handles_leap_year():
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)


def test_add_months_rolls_the_year():
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_month_range_spans_the_whole_month():
    span = month_range(date(2026, 2, 14))
    assert (span.first, span.last) == ("2026-02-01", "2026-02-28")


def test_today_in_falls_back_on_unknown_timezone():
    assert isinstance(today_in("Nao/Existe"), date)


# -- Finance -----------------------------------------------------------------


def test_add_transaction_debits_the_account(life, user):
    account = life.store.insert(
        "accounts", user.id, {"name": "Nubank", "balance_cents": 100000}
    )
    life.service.add_transaction(
        user.id, amount_cents=4590, category="mercado", account_id=account
    )
    assert life.store.get("accounts", user.id, account)["balance_cents"] == 95410


def test_add_transaction_credits_income(life, user):
    account = life.store.insert(
        "accounts", user.id, {"name": "Nubank", "balance_cents": 0}
    )
    life.service.add_transaction(
        user.id, amount_cents=500000, kind="income", account_id=account
    )
    assert life.store.get("accounts", user.id, account)["balance_cents"] == 500000


def test_add_transaction_applies_account_delta_atomically(life, user, monkeypatch):
    account = life.store.insert(
        "accounts", user.id, {"name": "Nubank", "balance_cents": 100000}
    )
    statements = []
    execute = life.connection.execute

    def recording_execute(sql, params=()):
        statements.append((" ".join(sql.split()), tuple(params)))
        return execute(sql, params)

    monkeypatch.setattr(life.connection, "execute", recording_execute)

    life.service.add_transaction(
        user.id,
        amount_cents=4590,
        category="mercado",
        account_id=account,
    )

    deltas = [
        params
        for sql, params in statements
        if sql.startswith(
            "UPDATE accounts SET balance_cents = balance_cents + ? "
            "WHERE id = ? AND user_id = ?"
        )
    ]
    assert deltas == [(-4590, account, user.id)]


def test_add_transaction_locks_account_row_on_postgres(life, user, monkeypatch):
    account = life.store.insert(
        "accounts", user.id, {"name": "Nubank", "balance_cents": 100000}
    )
    statements = []
    execute = life.connection.execute
    life.connection._backend = "postgres"

    def postgres_sql_over_sqlite(sql, params=()):
        statements.append(" ".join(sql.split()))
        life.connection._backend = "sqlite"
        try:
            return execute(sql.replace(" FOR UPDATE", ""), params)
        finally:
            life.connection._backend = "postgres"

    monkeypatch.setattr(life.connection, "execute", postgres_sql_over_sqlite)

    life.service.add_transaction(user.id, amount_cents=4590, account_id=account)

    assert any(
        statement.startswith("SELECT id FROM accounts")
        and statement.endswith("FOR UPDATE")
        for statement in statements
    )


def test_add_transaction_without_account_still_records(life, user):
    record = life.service.add_transaction(user.id, amount_cents=1000)
    assert record["amount_cents"] == 1000


def test_add_transaction_rejects_non_positive_amount(life, user):
    """Direction lives in ``kind``; a negative amount would double-negate."""
    with pytest.raises(LifeServiceError):
        life.service.add_transaction(user.id, amount_cents=-500)


def test_add_transaction_rejects_unknown_kind(life, user):
    with pytest.raises(LifeServiceError):
        life.service.add_transaction(user.id, amount_cents=100, kind="transferencia")


def test_add_transaction_rejects_foreign_account_without_writing(
    life, user, other_user
):
    """A cross-tenant account cannot leave an orphaned ledger entry behind."""
    foreign = life.store.insert(
        "accounts", other_user.id, {"name": "Da Bruna", "balance_cents": 100000}
    )

    with pytest.raises(LifeServiceError, match="Account not found"):
        life.service.add_transaction(user.id, amount_cents=5000, account_id=foreign)

    assert life.store.get("accounts", other_user.id, foreign)["balance_cents"] == 100000
    assert life.store.count("transactions", user.id) == 0


def test_pay_bill_marks_paid_books_expense_and_recurs(life, user):
    account = life.store.insert(
        "accounts", user.id, {"name": "Nubank", "balance_cents": 100000}
    )
    bill = life.store.insert(
        "bills",
        user.id,
        {
            "name": "Luz",
            "amount_cents": 18000,
            "due_on": "2026-08-07",
            "recurrence": "monthly",
            "category": "casa",
        },
    )
    result = life.service.pay_bill(
        user.id, bill, account_id=account, paid_on="2026-08-10"
    )

    assert result["bill"]["status"] == "paid"
    assert result["bill"]["paid_on"] == "2026-08-10"
    assert life.store.get("accounts", user.id, account)["balance_cents"] == 82000

    transaction = life.store.get("transactions", user.id, result["transaction_id"])
    assert transaction["amount_cents"] == 18000
    assert transaction["category"] == "casa"

    next_bill = life.store.get("bills", user.id, result["next_bill_id"])
    assert next_bill["due_on"] == "2026-09-07"
    assert next_bill["status"] == "pending"


def test_pay_bill_without_recurrence_creates_no_successor(life, user):
    bill = life.store.insert(
        "bills",
        user.id,
        {"name": "IPVA", "amount_cents": 90000, "due_on": "2026-08-07"},
    )
    assert life.service.pay_bill(user.id, bill)["next_bill_id"] == ""


def test_pay_bill_weekly_and_yearly_recurrence(life, user):
    weekly = life.store.insert(
        "bills",
        user.id,
        {
            "name": "Feira",
            "amount_cents": 10000,
            "due_on": "2026-08-07",
            "recurrence": "weekly",
        },
    )
    result = life.service.pay_bill(user.id, weekly)
    next_weekly = life.store.get("bills", user.id, result["next_bill_id"])
    assert next_weekly["due_on"] == "2026-08-14"

    yearly = life.store.insert(
        "bills",
        user.id,
        {
            "name": "Seguro",
            "amount_cents": 200000,
            "due_on": "2026-08-07",
            "recurrence": "yearly",
        },
    )
    result = life.service.pay_bill(user.id, yearly)
    next_yearly = life.store.get("bills", user.id, result["next_bill_id"])
    assert next_yearly["due_on"] == "2027-08-07"


def test_pay_bill_twice_is_rejected(life, user):
    bill = life.store.insert(
        "bills",
        user.id,
        {"name": "Net", "amount_cents": 9900, "due_on": "2026-08-01"},
    )
    life.service.pay_bill(user.id, bill)
    with pytest.raises(LifeServiceError):
        life.service.pay_bill(user.id, bill)


def test_pay_bill_locks_target_row_on_postgres(life, user, monkeypatch):
    bill = life.store.insert(
        "bills",
        user.id,
        {"name": "Net", "amount_cents": 9900, "due_on": "2026-08-01"},
    )
    statements = []
    execute = life.connection.execute
    life.connection._backend = "postgres"

    def postgres_sql_over_sqlite(sql, params=()):
        statements.append(" ".join(sql.split()))
        life.connection._backend = "sqlite"
        try:
            return execute(sql.replace(" FOR UPDATE", ""), params)
        finally:
            life.connection._backend = "postgres"

    monkeypatch.setattr(life.connection, "execute", postgres_sql_over_sqlite)

    life.service.pay_bill(user.id, bill)

    assert any(
        statement.startswith("SELECT * FROM bills") and statement.endswith("FOR UPDATE")
        for statement in statements
    )


def test_concurrent_bill_payment_books_one_expense(life, user):
    account = life.store.insert(
        "accounts", user.id, {"name": "Nubank", "balance_cents": 50000}
    )
    bill = life.store.insert(
        "bills", user.id, {"name": "Net", "amount_cents": 9900, "due_on": "2026-08-01"}
    )

    def pay():
        try:
            life.service.pay_bill(user.id, bill, account_id=account)
            return "paid"
        except LifeServiceError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: pay(), range(2)))

    assert sorted(outcomes) == ["paid", "rejected"]
    assert life.store.count("transactions", user.id) == 1
    assert life.store.get("accounts", user.id, account)["balance_cents"] == 40100


def test_pay_bill_rolls_back_every_change_when_recurrence_creation_fails(
    life, user, monkeypatch
):
    account = life.store.insert(
        "accounts", user.id, {"name": "Nubank", "balance_cents": 50000}
    )
    bill = life.store.insert(
        "bills",
        user.id,
        {
            "name": "Net",
            "amount_cents": 9900,
            "due_on": "2026-08-07",
            "recurrence": "monthly",
        },
    )
    original_insert = life.store.insert

    def fail_next_bill(table, user_id, data):
        if table == "bills":
            raise RuntimeError("simulated recurring bill failure")
        return original_insert(table, user_id, data)

    monkeypatch.setattr(life.store, "insert", fail_next_bill)
    with pytest.raises(RuntimeError, match="simulated"):
        life.service.pay_bill(user.id, bill, account_id=account)

    assert life.store.get("bills", user.id, bill)["status"] == "pending"
    assert life.store.get("accounts", user.id, account)["balance_cents"] == 50000
    assert life.store.count("transactions", user.id) == 0


def test_pay_bill_rejects_foreign_account_and_rolls_back(life, user, other_user):
    foreign = life.store.insert(
        "accounts", other_user.id, {"name": "Da Bruna", "balance_cents": 100000}
    )
    bill = life.store.insert(
        "bills", user.id, {"name": "Net", "amount_cents": 9900, "due_on": "2026-08-01"}
    )

    with pytest.raises(LifeServiceError, match="Account not found"):
        life.service.pay_bill(user.id, bill, account_id=foreign)

    assert life.store.get("bills", user.id, bill)["status"] == "pending"
    assert life.store.get("accounts", other_user.id, foreign)["balance_cents"] == 100000
    assert life.store.count("transactions", user.id) == 0


def test_pay_bill_of_another_tenant_is_not_found(life, user, other_user):
    bill = life.store.insert(
        "bills", user.id, {"name": "Net", "amount_cents": 9900, "due_on": "2026-08-01"}
    )
    with pytest.raises(LifeServiceError):
        life.service.pay_bill(other_user.id, bill)


def test_finance_summary_reports_flow_and_budgets(life, user):
    life.store.insert("accounts", user.id, {"name": "Nubank", "balance_cents": 250000})
    life.service.add_transaction(
        user.id, amount_cents=800000, kind="income", occurred_on="2026-08-01"
    )
    life.service.add_transaction(
        user.id, amount_cents=60000, category="mercado", occurred_on="2026-08-03"
    )
    life.service.add_transaction(
        user.id, amount_cents=20000, category="mercado", occurred_on="2026-08-05"
    )
    life.store.insert(
        "budgets", user.id, {"category": "mercado", "limit_cents": 100000}
    )

    summary = life.service.finance_summary(user.id, anchor=TODAY)
    assert summary["income_cents"] == 800000
    assert summary["expense_cents"] == 80000
    assert summary["net_cents"] == 720000
    assert summary["balance_cents"] == 250000

    budget = summary["budgets"][0]
    assert budget["spent_cents"] == 80000
    assert budget["remaining_cents"] == 20000
    assert budget["pct_used"] == 80.0


def test_finance_summary_excludes_other_months(life, user):
    life.service.add_transaction(user.id, amount_cents=50000, occurred_on="2026-07-20")
    assert life.service.finance_summary(user.id, anchor=TODAY)["expense_cents"] == 0


def test_finance_summary_survives_zero_limit_budget(life, user):
    """A zero limit must not raise ZeroDivisionError on the Today screen."""
    life.store.insert("budgets", user.id, {"category": "lazer", "limit_cents": 0})
    assert (
        life.service.finance_summary(user.id, anchor=TODAY)["budgets"][0]["pct_used"]
        == 0.0
    )


def test_finance_summary_ignores_archived_accounts(life, user):
    life.store.insert("accounts", user.id, {"name": "Ativa", "balance_cents": 1000})
    life.store.insert(
        "accounts", user.id, {"name": "Velha", "balance_cents": 9999, "archived": 1}
    )
    assert life.service.finance_summary(user.id, anchor=TODAY)["balance_cents"] == 1000


def test_refresh_bill_statuses_marks_overdue(life, user):
    life.store.insert(
        "bills", user.id, {"name": "Luz", "amount_cents": 100, "due_on": "2026-08-01"}
    )
    life.store.insert(
        "bills", user.id, {"name": "Água", "amount_cents": 100, "due_on": "2026-08-20"}
    )
    assert life.service.refresh_bill_statuses(user.id, TODAY) == 1
    statuses = {
        row["name"]: row["status"] for row in life.store.list_records("bills", user.id)
    }
    assert statuses == {"Luz": "overdue", "Água": "pending"}


# -- Fitness -----------------------------------------------------------------


def test_complete_workout_stamps_completion(life, user):
    workout = life.store.insert(
        "workouts", user.id, {"name": "Peito", "scheduled_on": TODAY.isoformat()}
    )
    result = life.service.complete_workout(user.id, workout, duration_min=52)
    assert result["completed_at"]
    assert result["duration_min"] == 52


def test_complete_missing_workout_raises(life, user):
    with pytest.raises(LifeServiceError):
        life.service.complete_workout(user.id, "nao-existe")


def test_fitness_summary_counts_week_and_volume(life, user):
    done = life.store.insert(
        "workouts",
        user.id,
        {
            "name": "Perna",
            "scheduled_on": (TODAY - timedelta(days=1)).isoformat(),
            "completed_at": "2026-08-09T10:00:00Z",
            "duration_min": 60,
        },
    )
    life.store.insert(
        "workouts", user.id, {"name": "Costas", "scheduled_on": TODAY.isoformat()}
    )
    life.store.insert(
        "exercise_sets",
        user.id,
        {"workout_id": done, "exercise": "Agachamento", "reps": 10, "weight_kg": 80},
    )

    summary = life.service.fitness_summary(user.id, anchor=TODAY)
    assert summary["week_planned"] == 2
    assert summary["week_completed"] == 1
    assert summary["week_minutes"] == 60
    assert summary["week_volume_kg"] == 800.0
    assert summary["days_since_last"] == 1


def test_fitness_summary_with_no_history(life, user):
    summary = life.service.fitness_summary(user.id, anchor=TODAY)
    assert summary["week_completed"] == 0
    assert summary["days_since_last"] is None
    assert summary["personal_records"] == []


def test_personal_records_keep_the_heaviest_set(life, user):
    workout = life.store.insert(
        "workouts", user.id, {"name": "A", "scheduled_on": TODAY.isoformat()}
    )
    for weight in (60, 100, 80):
        life.store.insert(
            "exercise_sets",
            user.id,
            {
                "workout_id": workout,
                "exercise": "Supino",
                "reps": 5,
                "weight_kg": weight,
            },
        )
    records = life.service.personal_records(user.id)
    assert records == [{"exercise": "Supino", "weight_kg": 100.0, "reps": 5}]


# -- Routine -----------------------------------------------------------------


def test_check_in_is_idempotent(life, user):
    habit = life.store.insert("habits", user.id, {"name": "Ler"})
    first = life.service.check_in_habit(user.id, habit, done_on="2026-08-10")
    second = life.service.check_in_habit(user.id, habit, done_on="2026-08-10")
    assert first["created"] is True
    assert second["created"] is False


def test_concurrent_check_in_creates_one_row(life, user):
    habit = life.store.insert("habits", user.id, {"name": "Ler"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: life.service.check_in_habit(
                    user.id, habit, done_on="2026-08-10"
                ),
                range(2),
            )
        )

    assert sorted(result["created"] for result in results) == [False, True]
    assert life.store.count("habit_checkins", user.id) == 1
    assert life.store.count("habit_checkins", user.id) == 1


def test_check_in_unknown_habit_raises(life, user):
    with pytest.raises(LifeServiceError):
        life.service.check_in_habit(user.id, "nao-existe")


def test_undo_check_in(life, user):
    habit = life.store.insert("habits", user.id, {"name": "Ler"})
    life.service.check_in_habit(user.id, habit, done_on="2026-08-10")
    assert life.service.undo_habit_check_in(user.id, habit, done_on="2026-08-10")
    assert life.store.count("habit_checkins", user.id) == 0


def test_streak_counts_consecutive_days(life, user):
    habit = life.store.insert("habits", user.id, {"name": "Ler"})
    for offset in range(4):
        life.service.check_in_habit(
            user.id, habit, done_on=(TODAY - timedelta(days=offset)).isoformat()
        )
    assert life.service.habit_streak(user.id, habit, anchor=TODAY) == 4


def test_streak_survives_a_day_not_yet_done(life, user):
    """Today is not over — a streak through yesterday is still alive."""
    habit = life.store.insert("habits", user.id, {"name": "Ler"})
    for offset in (1, 2, 3):
        life.service.check_in_habit(
            user.id, habit, done_on=(TODAY - timedelta(days=offset)).isoformat()
        )
    assert life.service.habit_streak(user.id, habit, anchor=TODAY) == 3


def test_streak_breaks_on_a_gap(life, user):
    habit = life.store.insert("habits", user.id, {"name": "Ler"})
    for offset in (0, 1, 3, 4):
        life.service.check_in_habit(
            user.id, habit, done_on=(TODAY - timedelta(days=offset)).isoformat()
        )
    assert life.service.habit_streak(user.id, habit, anchor=TODAY) == 2


def test_streak_of_untouched_habit_is_zero(life, user):
    habit = life.store.insert("habits", user.id, {"name": "Ler"})
    assert life.service.habit_streak(user.id, habit, anchor=TODAY) == 0


def test_routine_summary_reports_today(life, user):
    done = life.store.insert("habits", user.id, {"name": "Ler"})
    life.store.insert("habits", user.id, {"name": "Meditar"})
    life.store.insert("habits", user.id, {"name": "Arquivado", "archived": 1})
    life.service.check_in_habit(user.id, done, done_on=TODAY.isoformat())

    summary = life.service.routine_summary(user.id, anchor=TODAY)
    assert summary["total"] == 2
    assert summary["completed"] == 1
    assert {h["name"]: h["done_today"] for h in summary["habits"]} == {
        "Ler": True,
        "Meditar": False,
    }


# -- Family ------------------------------------------------------------------


def test_upcoming_projects_birthdays_onto_this_year(life, user):
    life.store.insert(
        "family_members",
        user.id,
        {"name": "Maria", "relation": "mãe", "birthday": "1968-08-15"},
    )
    upcoming = life.service.upcoming_family(user.id, anchor=TODAY)
    assert upcoming[0]["date"] == "2026-08-15"
    assert upcoming[0]["days_away"] == 5
    assert upcoming[0]["turning"] == 58


def test_birthday_already_past_rolls_to_next_year(life, user):
    life.store.insert(
        "family_members", user.id, {"name": "João", "birthday": "1990-01-05"}
    )
    upcoming = life.service.upcoming_family(user.id, anchor=TODAY, days=400)
    assert upcoming[0]["date"] == "2027-01-05"


def test_leap_day_birthday_observed_on_28_february(life, user):
    life.store.insert(
        "family_members", user.id, {"name": "Leap", "birthday": "2000-02-29"}
    )
    upcoming = life.service.upcoming_family(user.id, anchor=TODAY, days=400)
    assert upcoming[0]["date"] == "2027-02-28"


def test_members_without_birthday_are_skipped(life, user):
    life.store.insert("family_members", user.id, {"name": "Sem data"})
    assert life.service.upcoming_family(user.id, anchor=TODAY) == []


def test_family_events_appear_sorted_with_birthdays(life, user):
    life.store.insert(
        "family_members", user.id, {"name": "Maria", "birthday": "1968-08-20"}
    )
    life.store.insert(
        "family_events",
        user.id,
        {"title": "Jantar da família", "event_on": "2026-08-12"},
    )
    dates = [e["date"] for e in life.service.upcoming_family(user.id, anchor=TODAY)]
    assert dates == ["2026-08-12", "2026-08-20"]


# -- Work --------------------------------------------------------------------


def test_complete_task_stamps_done(life, user):
    task = life.store.insert("work_tasks", user.id, {"title": "Enviar proposta"})
    result = life.service.complete_task(user.id, task)
    assert result["status"] == "done"
    assert result["done_at"]


def test_complete_missing_task_raises(life, user):
    with pytest.raises(LifeServiceError):
        life.service.complete_task(user.id, "nao-existe")


def test_work_summary_splits_overdue_and_due_today(life, user):
    life.store.insert(
        "work_tasks", user.id, {"title": "Atrasada", "due_on": "2026-08-05"}
    )
    life.store.insert(
        "work_tasks", user.id, {"title": "Hoje", "due_on": TODAY.isoformat()}
    )
    life.store.insert(
        "work_tasks", user.id, {"title": "Futura", "due_on": "2026-08-30"}
    )
    life.store.insert(
        "work_tasks",
        user.id,
        {"title": "Feita", "due_on": "2026-08-01", "status": "done"},
    )

    summary = life.service.work_summary(user.id, anchor=TODAY)
    assert [t["title"] for t in summary["overdue"]] == ["Atrasada"]
    assert [t["title"] for t in summary["due_today"]] == ["Hoje"]
    assert summary["open_count"] == 3


# -- Health ------------------------------------------------------------------


def test_health_summary_preserves_confirmed_facts_and_today_totals(life, user):
    life.store.insert(
        "health_profiles",
        user.id,
        {"height_cm": 178, "goals": "Dormir melhor", "consent_health_memory": 1},
    )
    life.store.insert(
        "health_conditions", user.id, {"name": "Asma", "status": "active"}
    )
    life.store.insert(
        "health_conditions", user.id, {"name": "Antiga", "status": "resolved"}
    )
    life.store.insert(
        "medications", user.id, {"name": "Medicamento informado", "status": "active"}
    )
    life.store.insert("allergies", user.id, {"substance": "Látex"})
    life.store.insert(
        "hydration_logs",
        user.id,
        {"amount_ml": 500, "occurred_at": "2026-08-10T08:00:00-03:00"},
    )
    life.store.insert(
        "hydration_logs",
        user.id,
        {"amount_ml": 300, "occurred_at": "2026-08-09T20:00:00-03:00"},
    )
    life.store.insert(
        "nutrition_logs",
        user.id,
        {
            "meal_type": "breakfast",
            "description": "Pão e fruta",
            "occurred_at": "2026-08-10T07:00:00-03:00",
        },
    )
    life.store.insert(
        "health_observations",
        user.id,
        {
            "kind": "peso",
            "value": 80.1,
            "unit": "kg",
            "observed_at": "2026-08-10T07:30:00-03:00",
        },
    )
    life.store.insert(
        "health_observations",
        user.id,
        {
            "kind": "peso",
            "value": 81,
            "unit": "kg",
            "observed_at": "2026-08-01T07:30:00-03:00",
        },
    )

    summary = life.service.health_summary(
        user.id, anchor=TODAY, timezone_name=user.timezone
    )

    assert summary["profile"]["goals"] == "Dormir melhor"
    assert [item["name"] for item in summary["active_conditions"]] == ["Asma"]
    assert len(summary["active_medications"]) == 1
    assert [item["substance"] for item in summary["allergies"]] == ["Látex"]
    assert summary["hydration_today_ml"] == 500
    assert summary["nutrition_today_count"] == 1
    assert summary["latest_observations"][0]["value"] == 80.1


def test_health_summary_never_reads_another_tenant(life, user, other_user):
    life.store.insert("medications", user.id, {"name": "Privado", "status": "active"})
    life.store.insert(
        "hydration_logs",
        user.id,
        {"amount_ml": 900, "occurred_at": "2026-08-10T08:00:00-03:00"},
    )

    summary = life.service.health_summary(other_user.id, anchor=TODAY)

    assert summary["active_medications"] == []
    assert summary["hydration_today_ml"] == 0


def test_health_summary_uses_the_users_local_day_after_utc_rollover(life, user):
    life.store.insert(
        "hydration_logs",
        user.id,
        {"amount_ml": 250, "occurred_at": "2026-08-11T02:30:00+00:00"},
    )
    life.store.insert(
        "hydration_logs",
        user.id,
        {"amount_ml": 400, "occurred_at": "2026-08-10T02:30:00+00:00"},
    )
    life.store.insert(
        "nutrition_logs",
        user.id,
        {
            "meal_type": "dinner",
            "description": "Refeição confirmada",
            "occurred_at": "2026-08-11T02:45:00+00:00",
        },
    )

    summary = life.service.health_summary(
        user.id, anchor=TODAY, timezone_name="America/Sao_Paulo"
    )

    assert summary["hydration_today_ml"] == 250
    assert summary["nutrition_today_count"] == 1
