"""Tests for the Today feed — the cross-domain briefing and its alert ranking."""

from __future__ import annotations

from datetime import date, timedelta

from openjarvis.life.today import build_today, build_voice_today

TODAY = date(2026, 8, 10)


def _titles(briefing, app):
    return [a["title"] for a in briefing["alerts"] if a["app"] == app]


def test_empty_life_produces_no_alerts(life, user):
    briefing = life.today(user, anchor=TODAY)
    assert briefing["alerts"] == []
    assert briefing["date"] == "2026-08-10"
    assert set(briefing["badges"]) == {
        "finance",
        "fitness",
        "routine",
        "family",
        "work",
    }


def test_greeting_uses_first_name_and_hour(life, user):
    assert life.today(user, anchor=TODAY, now_hour=8)["greeting"] == "Bom dia, Alex"
    assert life.today(user, anchor=TODAY, now_hour=14)["greeting"] == "Boa tarde, Alex"
    assert life.today(user, anchor=TODAY, now_hour=21)["greeting"] == "Boa noite, Alex"


def test_overdue_bill_is_critical_and_deep_links(life, user):
    bill = life.store.insert(
        "bills", user.id, {"name": "Luz", "amount_cents": 18000, "due_on": "2026-08-01"}
    )
    briefing = life.today(user, anchor=TODAY)
    alert = briefing["alerts"][0]
    assert alert["severity"] == "critical"
    assert alert["app"] == "finance"
    assert alert["record_id"] == bill
    assert alert["action"] == "pay_bill"
    assert alert["amount_cents"] == 18000


def test_building_today_marks_bills_overdue(life, user):
    """The feed and the database must agree on what is late."""
    bill = life.store.insert(
        "bills", user.id, {"name": "Luz", "amount_cents": 100, "due_on": "2026-08-01"}
    )
    life.today(user, anchor=TODAY)
    assert life.store.get("bills", user.id, bill)["status"] == "overdue"


def test_bill_due_today_is_a_warning(life, user):
    life.store.insert(
        "bills",
        user.id,
        {"name": "Net", "amount_cents": 9900, "due_on": TODAY.isoformat()},
    )
    alerts = life.today(user, anchor=TODAY)["alerts"]
    assert alerts[0]["severity"] == "warning"
    assert "vence hoje" in alerts[0]["title"]


def test_bill_far_out_is_not_surfaced(life, user):
    life.store.insert(
        "bills",
        user.id,
        {"name": "IPTU", "amount_cents": 50000, "due_on": "2026-09-30"},
    )
    assert life.today(user, anchor=TODAY)["alerts"] == []


def test_blown_budget_raises_a_warning(life, user):
    life.store.insert("budgets", user.id, {"category": "lazer", "limit_cents": 10000})
    life.service.add_transaction(
        user.id, amount_cents=15000, category="lazer", occurred_on="2026-08-04"
    )
    titles = _titles(life.today(user, anchor=TODAY), "finance")
    assert any("Orçamento de lazer estourado" in t for t in titles)


def test_todays_pending_workout_is_surfaced(life, user):
    life.store.insert(
        "workouts",
        user.id,
        {
            "name": "Peito e tríceps",
            "scheduled_on": TODAY.isoformat(),
            "focus": "força",
        },
    )
    briefing = life.today(user, anchor=TODAY)
    assert "Treino de hoje: Peito e tríceps" in _titles(briefing, "fitness")
    assert briefing["badges"]["fitness"] == 1


def test_completed_workout_is_not_nagged_about(life, user):
    life.store.insert(
        "workouts",
        user.id,
        {
            "name": "Peito",
            "scheduled_on": TODAY.isoformat(),
            "completed_at": "2026-08-10T07:00:00Z",
        },
    )
    briefing = life.today(user, anchor=TODAY)
    assert briefing["badges"]["fitness"] == 0
    assert _titles(briefing, "fitness") == []


def test_inactivity_triggers_a_nudge(life, user):
    life.store.insert(
        "workouts",
        user.id,
        {
            "name": "Antigo",
            "scheduled_on": (TODAY - timedelta(days=6)).isoformat(),
            "completed_at": "2026-08-04T07:00:00Z",
        },
    )
    assert any(
        "6 dias sem treinar" in t
        for t in _titles(life.today(user, anchor=TODAY), "fitness")
    )


def test_pending_habit_is_listed(life, user):
    life.store.insert("habits", user.id, {"name": "Ler 20min"})
    briefing = life.today(user, anchor=TODAY)
    assert "Ler 20min" in _titles(briefing, "routine")
    assert briefing["routine"]["pending"] == ["Ler 20min"]
    assert briefing["badges"]["routine"] == 1


def test_long_streak_at_risk_escalates_to_warning(life, user):
    """Three days of momentum is worth interrupting for; day one is not."""
    habit = life.store.insert("habits", user.id, {"name": "Ler"})
    for offset in (1, 2, 3):
        life.service.check_in_habit(
            user.id, habit, done_on=(TODAY - timedelta(days=offset)).isoformat()
        )
    alert = next(
        a for a in life.today(user, anchor=TODAY)["alerts"] if a["app"] == "routine"
    )
    assert alert["severity"] == "warning"
    assert "Sequência de 3 dias em risco" in alert["detail"]


def test_checked_habit_disappears_from_the_feed(life, user):
    habit = life.store.insert("habits", user.id, {"name": "Ler"})
    life.service.check_in_habit(user.id, habit, done_on=TODAY.isoformat())
    briefing = life.today(user, anchor=TODAY)
    assert _titles(briefing, "routine") == []
    assert briefing["routine"]["completed"] == 1


def test_imminent_birthday_is_surfaced(life, user):
    life.store.insert(
        "family_members",
        user.id,
        {"name": "Maria", "relation": "mãe", "birthday": "1968-08-11"},
    )
    briefing = life.today(user, anchor=TODAY)
    assert any("Aniversário de Maria" in t for t in _titles(briefing, "family"))
    assert briefing["badges"]["family"] == 1


def test_distant_birthday_stays_off_the_feed(life, user):
    life.store.insert(
        "family_members", user.id, {"name": "Maria", "birthday": "1968-08-25"}
    )
    assert _titles(life.today(user, anchor=TODAY), "family") == []


def test_overdue_task_is_critical(life, user):
    life.store.insert(
        "work_tasks", user.id, {"title": "Enviar proposta", "due_on": "2026-08-05"}
    )
    alert = next(
        a for a in life.today(user, anchor=TODAY)["alerts"] if a["app"] == "work"
    )
    assert alert["severity"] == "critical"
    assert alert["detail"] == "Atrasada desde 2026-08-05"


def test_alerts_are_ranked_by_severity(life, user):
    life.store.insert("habits", user.id, {"name": "Ler"})
    life.store.insert(
        "work_tasks", user.id, {"title": "Atrasada", "due_on": "2026-08-01"}
    )
    life.store.insert(
        "bills",
        user.id,
        {"name": "Net", "amount_cents": 9900, "due_on": TODAY.isoformat()},
    )
    severities = [a["severity"] for a in life.today(user, anchor=TODAY)["alerts"]]
    assert severities == sorted(severities, key=["critical", "warning", "info"].index)
    assert severities[0] == "critical"


def test_badges_count_each_app(life, user):
    life.store.insert(
        "bills", user.id, {"name": "Luz", "amount_cents": 100, "due_on": "2026-08-01"}
    )
    life.store.insert("habits", user.id, {"name": "Ler"})
    life.store.insert("habits", user.id, {"name": "Meditar"})
    life.store.insert(
        "work_tasks", user.id, {"title": "Hoje", "due_on": TODAY.isoformat()}
    )
    badges = life.today(user, anchor=TODAY)["badges"]
    assert badges["finance"] == 1
    assert badges["routine"] == 2
    assert badges["work"] == 1


def test_feed_never_mixes_two_clients(life, user, other_user):
    life.store.insert(
        "bills",
        user.id,
        {"name": "Conta do Alex", "amount_cents": 100, "due_on": "2026-08-01"},
    )
    briefing = build_today(life.service, other_user, anchor=TODAY)
    assert briefing["alerts"] == []
    assert briefing["greeting"] == "Bom dia, Bruna"


def test_finance_block_reports_month_flow(life, user):
    life.store.insert("accounts", user.id, {"name": "Nubank", "balance_cents": 320000})
    life.service.add_transaction(
        user.id, amount_cents=500000, kind="income", occurred_on="2026-08-01"
    )
    life.service.add_transaction(
        user.id, amount_cents=125000, category="mercado", occurred_on="2026-08-03"
    )
    finance = life.today(user, anchor=TODAY)["finance"]
    assert finance["balance_cents"] == 320000
    assert finance["income_cents"] == 500000
    assert finance["expense_cents"] == 125000
    assert finance["net_cents"] == 375000


def test_voice_today_matches_spoken_cross_domain_context(life, user):
    life.store.insert("accounts", user.id, {"name": "Nubank", "balance_cents": 320000})
    life.service.add_transaction(
        user.id, amount_cents=500000, kind="income", occurred_on="2026-08-01"
    )
    life.store.insert(
        "bills", user.id, {"name": "Luz", "amount_cents": 18000, "due_on": "2026-08-01"}
    )
    life.store.insert("habits", user.id, {"name": "Ler"})
    life.store.insert(
        "work_tasks", user.id, {"title": "Enviar proposta", "due_on": TODAY.isoformat()}
    )

    briefing = build_voice_today(life.service, user, anchor=TODAY)

    assert briefing["finance"]["balance_cents"] == 320000
    assert briefing["finance"]["income_cents"] == 500000
    assert briefing["finance"]["overdue_count"] == 1
    assert briefing["routine"]["pending"] == ["Ler"]
    assert briefing["work"]["due_today_count"] == 1
    assert briefing["badges"] == {
        "finance": 1,
        "fitness": 0,
        "routine": 1,
        "family": 0,
        "work": 1,
    }


def test_voice_today_reads_all_domains_in_one_query(life, user, monkeypatch):
    calls = 0
    execute = life.connection.execute

    def counted_execute(sql, params=()):
        nonlocal calls
        calls += 1
        return execute(sql, params)

    monkeypatch.setattr(life.connection, "execute", counted_execute)

    build_voice_today(life.service, user, anchor=TODAY)

    assert calls == 1


def test_voice_today_never_mixes_two_clients(life, user, other_user):
    life.store.insert(
        "bills",
        user.id,
        {"name": "Conta do Alex", "amount_cents": 100, "due_on": "2026-08-01"},
    )

    briefing = build_voice_today(life.service, other_user, anchor=TODAY)

    assert briefing["alerts"] == []
    assert briefing["greeting"] == "Bom dia, Bruna"
