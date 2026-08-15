"""Tests for the Life tools — the agent's read/write access to a client's life.

The binding tests are the important ones: a bound tool must ignore a
``user_id`` the model invents, or a prompt injection becomes a cross-tenant
data leak.
"""

from __future__ import annotations

import json

import pytest

from openjarvis.core.registry import ToolRegistry
from openjarvis.life.tools import (
    LifeCompleteTool,
    LifeOverviewTool,
    LifeRecordTool,
    ensure_registered,
    life_tools_for,
)
from openjarvis.life.training import TrainingCoachService


@pytest.fixture()
def tools(life, user):
    """The three Life tools bound to the fixture client."""
    return life_tools_for(life, user.id)


def test_tools_are_registered():
    """REVIEW.md requires new tools to be discoverable via ToolRegistry.

    ``ensure_registered`` is called explicitly because the autouse fixture in
    ``tests/conftest.py`` clears every registry before each test.
    """
    ensure_registered()
    for name in ("life_overview", "life_record", "life_complete"):
        assert ToolRegistry.get(name) is not None


def test_ensure_registered_is_idempotent():
    """A second call must not raise on the already-registered key."""
    ensure_registered()
    ensure_registered()
    assert ToolRegistry.get("life_record") is LifeRecordTool


def test_specs_expose_openai_function_schemas(tools):
    for tool in tools:
        schema = tool.to_openai_function()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == tool.spec.name
        assert schema["function"]["parameters"]["type"] == "object"


def test_write_tools_require_confirmation(tools):
    _, record, complete = tools
    assert record.spec.requires_confirmation is True
    assert complete.spec.requires_confirmation is True


def test_overview_returns_today(life, user, tools):
    life.store.insert(
        "bills", user.id, {"name": "Luz", "amount_cents": 18000, "due_on": "2020-01-01"}
    )
    result = tools[0].execute(section="today")
    assert result.success
    payload = json.loads(result.content)
    assert payload["badges"]["finance"] == 1
    assert payload["alerts"][0]["title"] == "Luz está vencida"


@pytest.mark.parametrize(
    "section", ["finance", "fitness", "routine", "family", "work", "connections"]
)
def test_overview_serves_each_section(tools, section):
    result = tools[0].execute(section=section)
    assert result.success
    assert isinstance(json.loads(result.content), dict)


def test_overview_connections_reads_only_the_bound_tenant(life, user, tools):
    now = "2099-08-14T12:00:00+00:00"
    life.connection.execute(
        "INSERT INTO integration_connections"
        " (id, user_id, provider, status, created_at, updated_at)"
        " VALUES (?, ?, 'google_calendar', 'connected', ?, ?)",
        ("calendar-connection", user.id, now, now),
    )
    life.connection.execute(
        "INSERT INTO integration_items"
        " (id, user_id, provider, external_id, kind, title, occurred_at,"
        " created_at, updated_at)"
        " VALUES (?, ?, 'google_calendar', ?, 'calendar', ?, ?, ?, ?)",
        ("calendar-item", user.id, "event-1", "Consulta", now, now, now),
    )
    life.connection.commit()

    result = tools[0].execute(section="connections")

    assert result.success
    assert json.loads(result.content)["calendar"][0]["title"] == "Consulta"


def test_fitness_overview_exposes_the_prescribed_coach_session(life, user, tools):
    coach = TrainingCoachService(life.store)
    coach.save_profile(
        user.id,
        {
            "primary_goal": "5k",
            "level": "beginner",
            "weekly_days": 3,
            "available_weekdays": [1, 3, 5],
            "session_minutes": 40,
            "current_weekly_km": 8,
            "longest_recent_run_km": 4,
            "equipment": [],
        },
    )
    coach.generate_plan(user.id, weeks=4)

    result = tools[0].execute(section="fitness")

    assert result.success
    payload = json.loads(result.content)
    assert payload["coach"]["active_plan"]["status"] == "active"
    assert payload["coach"]["next_session"]["objective"]
    assert len(payload["coach"]["sessions"]) == 12


def test_record_tool_can_operate_the_coach_from_voice(life, user, tools):
    _, record, _ = tools
    profile = record.execute(
        kind="training_profile",
        fields={
            "primary_sport": "canoeing",
            "secondary_sports": ["strength"],
            "primary_goal": "general_fitness",
            "level": "intermediate",
            "weekly_days": 4,
            "available_weekdays": [1, 3, 5, 7],
            "session_minutes": 50,
            "current_weekly_km": 12,
            "longest_recent_run_km": 6,
            "equipment": ["canoa", "halteres"],
        },
    )
    plan = record.execute(
        kind="training_plan",
        fields={"start_on": "2026-08-17", "weeks": 4},
    )
    session = TrainingCoachService(life.store).overview(user.id)["sessions"][0]
    checkin = record.execute(
        kind="training_checkin",
        fields={
            "session_id": session["id"],
            "sleep_quality": 7,
            "soreness": 3,
            "stress": 4,
            "motivation": 8,
            "pain": 1,
        },
    )
    feedback = record.execute(
        kind="training_feedback",
        fields={
            "session_id": session["id"],
            "completion_pct": 100,
            "actual_duration_min": 45,
            "rpe": 6,
            "energy": 8,
            "pain": 1,
        },
    )

    assert profile.success
    assert plan.success
    assert checkin.success
    assert feedback.success
    assert json.loads(profile.content)["record"]["primary_sport"] == "canoeing"
    assert json.loads(plan.content)["record"]["weeks"] == 4
    assert json.loads(checkin.content)["record"]["recommendation"] == "ready"
    assert json.loads(feedback.content)["record"]["feedback"]["completion_pct"] == 100


def test_record_tool_honours_the_goal_name_its_own_schema_documents(tools):
    """``life_record``'s schema advertises ``goal`` for a training profile.

    ``save_profile`` reads ``primary_goal`` and falls back to
    ``general_fitness`` when it is absent, so a model that followed the
    documented field name had the client's actual objective dropped in
    silence — "minha meta é a São Silvestre" stored as "condicionamento
    geral", with no exception and nothing in the reply to notice it by.
    """
    _, record, _ = tools
    result = record.execute(
        kind="training_profile",
        fields={
            "primary_sport": "running",
            "goal": "10k",
            "level": "beginner",
            "weekly_days": 3,
            "available_weekdays": [1, 3, 5],
        },
    )

    assert result.success
    assert json.loads(result.content)["record"]["primary_goal"] == "10k"


def test_record_tool_prefers_primary_goal_when_both_names_arrive(tools):
    """The schema's own name wins, so the alias can never shadow it."""
    _, record, _ = tools
    result = record.execute(
        kind="training_profile",
        fields={
            "primary_sport": "running",
            "primary_goal": "half_marathon",
            "goal": "10k",
            "level": "beginner",
            "weekly_days": 3,
            "available_weekdays": [1, 3, 5],
        },
    )

    assert result.success
    assert json.loads(result.content)["record"]["primary_goal"] == "half_marathon"


def test_overview_rejects_unknown_section(tools):
    result = tools[0].execute(section="astrologia")
    assert result.success is False
    assert "Unknown section" in result.content


def test_record_creates_an_expense(life, user, tools):
    result = tools[1].execute(
        kind="expense",
        fields={"amount_cents": 4590, "category": "mercado", "description": "Feira"},
    )
    assert result.success
    payload = json.loads(result.content)
    assert payload["created"] == "expense de R$ 45,90 em mercado"
    assert life.store.count("transactions", user.id) == 1


def test_record_normalizes_an_integer_amount_string(life, user, tools):
    result = tools[1].execute(
        kind="expense",
        fields={"amount_cents": "4590", "category": "mercado"},
    )
    assert result.success
    assert json.loads(result.content)["record"]["amount_cents"] == 4590
    assert life.store.count("transactions", user.id) == 1


@pytest.mark.parametrize("amount", [True, 4590.0, "45.90", None])
def test_record_rejects_non_integer_amounts(life, user, tools, amount):
    result = tools[1].execute(
        kind="expense",
        fields={"amount_cents": amount, "category": "mercado"},
    )
    assert result.success is False
    assert result.content == "amount_cents must be an integer"
    assert life.store.count("transactions", user.id) == 0


def test_record_creates_income(life, user, tools):
    tools[1].execute(kind="income", fields={"amount_cents": 900000})
    row = life.store.list_records("transactions", user.id)[0]
    assert row["kind"] == "income"
    assert row["source"] == "agent"


@pytest.mark.parametrize(
    ("kind", "fields", "table"),
    [
        ("habit", {"name": "Beber água"}, "habits"),
        (
            "family_event",
            {"title": "Consulta da família", "event_on": "2026-08-20"},
            "family_events",
        ),
        ("project", {"name": "Lançamento"}, "projects"),
        ("task", {"title": "Enviar proposta"}, "work_tasks"),
        (
            "health_profile",
            {"goals": "Melhorar condicionamento", "consent_health_memory": 1},
            "health_profiles",
        ),
        ("hydration", {"amount_ml": 500}, "hydration_logs"),
        (
            "nutrition",
            {"meal_type": "lunch", "description": "Arroz, feijão e legumes"},
            "nutrition_logs",
        ),
    ],
)
def test_record_tool_persists_every_specialist_domain(
    life, user, tools, kind, fields, table
):
    result = tools[1].execute(kind=kind, fields=fields)

    assert result.success
    assert life.store.count(table, user.id) == 1


def test_record_defaults_missing_dates_to_today(life, user, tools):
    """ "adiciona a conta de luz" with no date must not hit a NOT NULL error."""
    result = tools[1].execute(
        kind="bill", fields={"name": "Luz", "amount_cents": 18000}
    )
    assert result.success
    assert json.loads(result.content)["record"]["due_on"]


def test_record_rejects_unknown_kind(tools):
    result = tools[1].execute(kind="criptomoeda", fields={})
    assert result.success is False
    assert "Unknown kind" in result.content


def test_record_rejects_unknown_field(tools):
    result = tools[1].execute(kind="habit", fields={"nmae": "Ler"})
    assert result.success is False


def test_record_rejects_invalid_amount(tools):
    result = tools[1].execute(kind="expense", fields={"amount_cents": -100})
    assert result.success is False


@pytest.mark.parametrize(
    ("kind", "fields", "table"),
    [
        ("account", {"name": "Nubank", "balance_cents": 10000}, "accounts"),
        (
            "bill",
            {
                "name": "Luz",
                "amount_cents": 18000,
                "status": "paid",
                "paid_on": "2026-08-10",
            },
            "bills",
        ),
        (
            "workout",
            {"name": "Peito", "completed_at": "2026-08-10T10:00:00Z"},
            "workouts",
        ),
        (
            "task",
            {"title": "Proposta", "status": "done", "done_at": "2026-08-10"},
            "work_tasks",
        ),
    ],
)
def test_record_cannot_bypass_domain_actions(life, user, tools, kind, fields, table):
    result = tools[1].execute(kind=kind, fields=fields)
    assert result.success is False
    assert "Fields require a domain action" in result.content
    assert life.store.count(table, user.id) == 0
    assert life.store.count("transactions", user.id) == 0


def test_complete_pays_a_bill_and_recurs(life, user, tools):
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
    result = tools[2].execute(kind="bill", record_id=bill)
    assert result.success
    payload = json.loads(result.content)
    assert payload["bill"]["status"] == "paid"
    assert payload["next_bill_id"]


def test_complete_checks_in_a_habit_and_reports_streak(life, user, tools):
    habit = life.store.insert("habits", user.id, {"name": "Ler"})
    payload = json.loads(tools[2].execute(kind="habit", record_id=habit).content)
    assert payload["created"] is True
    assert payload["streak"] == 1


def test_complete_finishes_a_workout(life, user, tools):
    workout = life.store.insert(
        "workouts", user.id, {"name": "Peito", "scheduled_on": "2026-08-10"}
    )
    payload = json.loads(tools[2].execute(kind="workout", record_id=workout).content)
    assert payload["workout"]["completed_at"]


def test_complete_finishes_a_task(life, user, tools):
    task = life.store.insert("work_tasks", user.id, {"title": "Proposta"})
    payload = json.loads(tools[2].execute(kind="task", record_id=task).content)
    assert payload["task"]["status"] == "done"


def test_complete_rejects_unknown_kind(tools):
    result = tools[2].execute(kind="casamento", record_id="x")
    assert result.success is False


def test_complete_reports_missing_record(tools):
    result = tools[2].execute(kind="bill", record_id="nao-existe")
    assert result.success is False
    assert "not found" in result.content.lower()


# -- Tenant binding ----------------------------------------------------------


def test_bound_tool_ignores_a_user_id_from_the_model(life, user, other_user):
    """A hallucinated or injected user_id must not reach another client."""
    life.store.insert(
        "bills",
        other_user.id,
        {"name": "Conta da Bruna", "amount_cents": 100, "due_on": "2020-01-01"},
    )
    overview = LifeOverviewTool(life, user_id=user.id)
    payload = json.loads(
        overview.execute(section="today", user_id=other_user.id).content
    )
    assert payload["alerts"] == []
    assert payload["greeting"].endswith("Alex")


def test_bound_write_lands_on_the_bound_client(life, user, other_user):
    record = LifeRecordTool(life, user_id=user.id)
    record.execute(kind="expense", fields={"amount_cents": 1000}, user_id=other_user.id)
    assert life.store.count("transactions", user.id) == 1
    assert life.store.count("transactions", other_user.id) == 0


def test_bound_completion_cannot_touch_another_client(life, user, other_user):
    bill = life.store.insert(
        "bills",
        other_user.id,
        {"name": "Da Bruna", "amount_cents": 100, "due_on": "2026-08-01"},
    )
    complete = LifeCompleteTool(life, user_id=user.id)
    result = complete.execute(kind="bill", record_id=bill, user_id=other_user.id)
    assert result.success is False
    assert life.store.get("bills", other_user.id, bill)["status"] == "pending"


def test_unbound_tool_honours_an_explicit_user_id(life, user):
    """Unbound instances (CLI, tests) may still address a client directly."""
    payload = json.loads(
        LifeOverviewTool(life).execute(section="today", user_id=user.id).content
    )
    assert payload["greeting"].endswith("Alex")


def test_unbound_tool_rejects_an_unknown_user(life, user):
    result = LifeOverviewTool(life).execute(section="today", user_id="fantasma")
    assert result.success is False
    assert "Unknown user" in result.content


def test_unbound_tool_falls_back_to_the_only_client(life, user):
    payload = json.loads(LifeOverviewTool(life).execute(section="today").content)
    assert payload["greeting"].endswith("Alex")


def test_unbound_tool_refuses_to_guess_between_clients(life, user, other_user):
    """Two clients and no selection is ambiguity, not a coin flip."""
    result = LifeOverviewTool(life).execute(section="today")
    assert result.success is False
    assert "No user selected" in result.content
