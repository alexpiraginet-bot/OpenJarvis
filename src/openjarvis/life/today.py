"""The Today feed — the one screen a client sees first.

Everything else in the Life OS is a place you *go*. Today is what greets you:
a single cross-domain briefing that answers "what needs me right now?" without
opening a single app icon.

The interesting output is :func:`build_today`'s ``alerts`` list. Sections are
inert data; alerts are ranked and actionable — an overdue bill outranks a
pending habit, and each carries the ``app`` and ``record_id`` the client needs
to deep-link straight to the thing that needs attention.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from openjarvis.life.service import (
    BILL_SOON_DAYS,
    LifeService,
    today_in,
)
from openjarvis.life.store import Filter
from openjarvis.life.tenancy import User

#: Alert severities, most urgent first. The client renders colour from these.
SEVERITIES = ("critical", "warning", "info")

#: Nudge after this many days without a completed session.
INACTIVITY_DAYS = 4

#: Family events inside this window make the Today screen.
FAMILY_LOOKAHEAD_DAYS = 7


def _greeting(now_hour: int) -> str:
    """Portuguese greeting appropriate to the hour."""
    if now_hour < 12:
        return "Bom dia"
    if now_hour < 18:
        return "Boa tarde"
    return "Boa noite"


def build_today(
    service: LifeService,
    user: User,
    *,
    anchor: Optional[date] = None,
    now_hour: int = 9,
) -> Dict[str, Any]:
    """Assemble the cross-domain Today briefing for one user.

    ``anchor`` defaults to the current date *in the user's timezone*, so the
    day rolls over at the client's midnight rather than the server's.
    """
    anchor = anchor or today_in(user.timezone)
    store = service.store
    today_iso = anchor.isoformat()

    # Late bills are marked before they are read, so the feed and the database
    # agree on what "overdue" means at this instant.
    service.refresh_bill_statuses(user.id, anchor)

    alerts: List[Dict[str, Any]] = []

    # -- Finance -------------------------------------------------------------
    finance = service.finance_summary(user.id, anchor=anchor)
    overdue_bills = store.list_records(
        "bills",
        user.id,
        filters=(Filter("status", "=", "overdue"),),
        order_by="due_on",
        descending=False,
        limit=50,
    )
    soon_horizon = (anchor + timedelta(days=BILL_SOON_DAYS)).isoformat()
    due_soon = store.list_records(
        "bills",
        user.id,
        filters=(
            Filter("status", "=", "pending"),
            Filter("due_on", ">=", today_iso),
            Filter("due_on", "<=", soon_horizon),
        ),
        order_by="due_on",
        descending=False,
        limit=50,
    )

    for bill in overdue_bills:
        alerts.append(
            {
                "severity": "critical",
                "app": "finance",
                "record_id": bill["id"],
                "title": f"{bill['name']} está vencida",
                "detail": f"Venceu em {bill['due_on']}",
                "amount_cents": int(bill["amount_cents"]),
                "action": "pay_bill",
            }
        )
    for bill in due_soon:
        days = (date.fromisoformat(bill["due_on"]) - anchor).days
        when = "vence hoje" if days == 0 else f"vence em {days} dia(s)"
        alerts.append(
            {
                "severity": "warning" if days <= 2 else "info",
                "app": "finance",
                "record_id": bill["id"],
                "title": f"{bill['name']} {when}",
                "detail": bill["due_on"],
                "amount_cents": int(bill["amount_cents"]),
                "action": "pay_bill",
            }
        )

    for budget in finance["budgets"]:
        if budget["pct_used"] >= 100:
            alerts.append(
                {
                    "severity": "warning",
                    "app": "finance",
                    "record_id": budget["id"],
                    "title": f"Orçamento de {budget['category']} estourado",
                    "detail": f"{budget['pct_used']}% do limite",
                    "amount_cents": budget["spent_cents"],
                    "action": "view_budget",
                }
            )

    # -- Fitness -------------------------------------------------------------
    fitness = service.fitness_summary(user.id, anchor=anchor)
    todays_workouts = store.list_records(
        "workouts",
        user.id,
        filters=(Filter("scheduled_on", "=", today_iso),),
        limit=10,
    )
    pending_workout = next(
        (w for w in todays_workouts if not w["completed_at"]), None
    )
    if pending_workout is not None:
        alerts.append(
            {
                "severity": "info",
                "app": "fitness",
                "record_id": pending_workout["id"],
                "title": f"Treino de hoje: {pending_workout['name']}",
                "detail": pending_workout["focus"] or "Bora treinar",
                "amount_cents": 0,
                "action": "complete_workout",
            }
        )
    days_since = fitness["days_since_last"]
    if days_since is not None and days_since >= INACTIVITY_DAYS:
        alerts.append(
            {
                "severity": "warning",
                "app": "fitness",
                "record_id": "",
                "title": f"{days_since} dias sem treinar",
                "detail": "Que tal retomar hoje?",
                "amount_cents": 0,
                "action": "plan_workout",
            }
        )

    # -- Routine -------------------------------------------------------------
    routine = service.routine_summary(user.id, anchor=anchor)
    pending_habits = [h for h in routine["habits"] if not h["done_today"]]
    for habit in pending_habits:
        # A long streak about to break is the one nudge worth interrupting for.
        at_risk = habit["streak"] >= 3
        alerts.append(
            {
                "severity": "warning" if at_risk else "info",
                "app": "routine",
                "record_id": habit["id"],
                "title": habit["name"],
                "detail": (
                    f"Sequência de {habit['streak']} dias em risco"
                    if at_risk
                    else "Pendente hoje"
                ),
                "amount_cents": 0,
                "action": "check_in_habit",
            }
        )

    # -- Family --------------------------------------------------------------
    family = service.upcoming_family(
        user.id, anchor=anchor, days=FAMILY_LOOKAHEAD_DAYS
    )
    for event in family:
        days_away = event["days_away"]
        when = "hoje" if days_away == 0 else f"em {days_away} dia(s)"
        alerts.append(
            {
                "severity": "warning" if days_away <= 1 else "info",
                "app": "family",
                "record_id": event["member_id"],
                "title": f"{event['title']} {when}",
                "detail": event["date"],
                "amount_cents": 0,
                "action": "view_member",
            }
        )

    # -- Work ----------------------------------------------------------------
    work = service.work_summary(user.id, anchor=anchor)
    for task in work["overdue"]:
        alerts.append(
            {
                "severity": "critical",
                "app": "work",
                "record_id": task["id"],
                "title": task["title"],
                "detail": f"Atrasada desde {task['due_on']}",
                "amount_cents": 0,
                "action": "complete_task",
            }
        )
    for task in work["due_today"]:
        alerts.append(
            {
                "severity": "warning",
                "app": "work",
                "record_id": task["id"],
                "title": task["title"],
                "detail": "Vence hoje",
                "amount_cents": 0,
                "action": "complete_task",
            }
        )

    alerts.sort(key=lambda item: SEVERITIES.index(item["severity"]))

    return {
        "date": today_iso,
        "greeting": f"{_greeting(now_hour)}, {user.name.split()[0]}",
        "currency": user.currency,
        "alerts": alerts,
        "badges": {
            "finance": len(overdue_bills) + len(due_soon),
            "fitness": 1 if pending_workout is not None else 0,
            "routine": len(pending_habits),
            "family": len([e for e in family if e["days_away"] <= 1]),
            "work": len(work["overdue"]) + len(work["due_today"]),
        },
        "finance": {
            "balance_cents": finance["balance_cents"],
            "net_cents": finance["net_cents"],
            "expense_cents": finance["expense_cents"],
            "income_cents": finance["income_cents"],
            "overdue_count": len(overdue_bills),
            "due_soon_count": len(due_soon),
        },
        "fitness": {
            "week_completed": fitness["week_completed"],
            "week_planned": fitness["week_planned"],
            "days_since_last": days_since,
            "todays_workout": pending_workout,
        },
        "routine": {
            "completed": routine["completed"],
            "total": routine["total"],
            "pending": [h["name"] for h in pending_habits],
        },
        "family": {"upcoming": family[:5]},
        "work": {
            "open_count": work["open_count"],
            "overdue_count": len(work["overdue"]),
            "due_today_count": len(work["due_today"]),
        },
    }
