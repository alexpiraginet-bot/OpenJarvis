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
    _next_occurrence,
    _parse_date,
    month_range,
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


def build_voice_today(
    service: LifeService,
    user: User,
    *,
    anchor: Optional[date] = None,
    now_hour: int = 9,
) -> Dict[str, Any]:
    """Build the spoken briefing with one tenant-scoped database round trip.

    The full Today feed deliberately performs rich per-domain calculations.
    Paying that remote-Postgres latency before every spoken turn makes the
    assistant feel broken, so the voice path fetches only the fields used by
    the prompt and data fallback. It preserves the public Today shape without
    mutating bill state as a side effect of asking a question.
    """
    anchor = anchor or today_in(user.timezone)
    today_iso = anchor.isoformat()
    span = month_range(anchor)
    week_start = (anchor - timedelta(days=6)).isoformat()
    bill_horizon = (anchor + timedelta(days=BILL_SOON_DAYS)).isoformat()
    family_horizon = (anchor + timedelta(days=FAMILY_LOOKAHEAD_DAYS)).isoformat()

    rows = service.store.connection.execute(
        """
        SELECT 'account' AS row_kind, id, name AS text_1,
               '' AS text_2, '' AS text_3, '' AS ref_id,
               balance_cents AS number_1, 0 AS number_2, 0 AS flag_1
          FROM accounts WHERE user_id = ? AND archived = 0
        UNION ALL
        SELECT 'transaction', id, kind, category, occurred_on, account_id,
               amount_cents, 0, 0
          FROM transactions
         WHERE user_id = ? AND occurred_on >= ? AND occurred_on <= ?
        UNION ALL
        SELECT 'budget', id, category, period, '', '', limit_cents, 0, 0
          FROM budgets WHERE user_id = ?
        UNION ALL
        SELECT 'bill', id, name, due_on, status, '', amount_cents, 0, 0
          FROM bills
         WHERE user_id = ? AND status != 'paid' AND due_on <= ?
        UNION ALL
        SELECT 'workout', id, name, focus, scheduled_on,
               COALESCE(completed_at, ''), duration_min, 0,
               CASE WHEN completed_at IS NULL OR completed_at = '' THEN 0 ELSE 1 END
          FROM workouts
         WHERE user_id = ? AND scheduled_on >= ? AND scheduled_on <= ?
        UNION ALL
        SELECT 'last_workout', id, name, focus, scheduled_on,
               COALESCE(completed_at, ''), duration_min, 0, 1
          FROM (
                SELECT * FROM workouts
                 WHERE user_id = ?
                   AND completed_at IS NOT NULL AND completed_at != ''
                 ORDER BY scheduled_on DESC LIMIT 1
               ) AS latest_completed_workout
        UNION ALL
        SELECT 'habit', id, name, cadence, created_at, '',
               target_per_period, 0, 0
          FROM habits WHERE user_id = ? AND archived = 0
        UNION ALL
        SELECT 'habit_checkin', id, habit_id, done_on, '', '', 0, 0, 1
          FROM habit_checkins WHERE user_id = ? AND done_on <= ?
        UNION ALL
        SELECT 'family_member', id, name, COALESCE(birthday, ''), relation, '',
               0, 0, 0
          FROM family_members WHERE user_id = ?
        UNION ALL
        SELECT 'family_event', id, title, event_on, kind, member_id, 0, 0, 0
          FROM family_events
         WHERE user_id = ? AND event_on >= ? AND event_on <= ?
        UNION ALL
        SELECT 'work_task', id, title, COALESCE(due_on, ''), status, project_id,
               0, 0, 0
          FROM work_tasks
         WHERE user_id = ? AND status IN ('todo', 'doing')
        """,
        (
            user.id,
            user.id,
            span.first,
            span.last,
            user.id,
            user.id,
            bill_horizon,
            user.id,
            week_start,
            today_iso,
            user.id,
            user.id,
            user.id,
            today_iso,
            user.id,
            user.id,
            today_iso,
            family_horizon,
            user.id,
        ),
    ).fetchall()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for raw in rows:
        row = dict(raw)
        grouped.setdefault(str(row["row_kind"]), []).append(row)

    alerts: List[Dict[str, Any]] = []

    transactions = grouped.get("transaction", [])
    income = sum(
        int(row["number_1"] or 0) for row in transactions if row["text_1"] == "income"
    )
    expense = sum(
        int(row["number_1"] or 0) for row in transactions if row["text_1"] == "expense"
    )
    spent: Dict[str, int] = {}
    for row in transactions:
        if row["text_1"] == "expense":
            category = str(row["text_2"])
            spent[category] = spent.get(category, 0) + int(row["number_1"] or 0)
    for budget in grouped.get("budget", []):
        limit_cents = int(budget["number_1"] or 0)
        used = spent.get(str(budget["text_1"]), 0)
        pct_used = round(used / limit_cents * 100, 1) if limit_cents else 0.0
        if pct_used >= 100:
            alerts.append(
                {
                    "severity": "warning",
                    "app": "finance",
                    "record_id": budget["id"],
                    "title": f"Orçamento de {budget['text_1']} estourado",
                    "detail": f"{pct_used}% do limite",
                    "amount_cents": used,
                    "action": "view_budget",
                }
            )

    overdue_bills: List[Dict[str, Any]] = []
    due_soon: List[Dict[str, Any]] = []
    for bill in grouped.get("bill", []):
        due = _parse_date(bill["text_2"])
        if due is None:
            continue
        (overdue_bills if due < anchor else due_soon).append({**bill, "due": due})
    overdue_bills.sort(key=lambda row: row["due"])
    due_soon.sort(key=lambda row: row["due"])
    for bill in overdue_bills:
        alerts.append(
            {
                "severity": "critical",
                "app": "finance",
                "record_id": bill["id"],
                "title": f"{bill['text_1']} está vencida",
                "detail": f"Venceu em {bill['text_2']}",
                "amount_cents": int(bill["number_1"] or 0),
                "action": "pay_bill",
            }
        )
    for bill in due_soon:
        days = (bill["due"] - anchor).days
        when = "vence hoje" if days == 0 else f"vence em {days} dia(s)"
        alerts.append(
            {
                "severity": "warning" if days <= 2 else "info",
                "app": "finance",
                "record_id": bill["id"],
                "title": f"{bill['text_1']} {when}",
                "detail": bill["text_2"],
                "amount_cents": int(bill["number_1"] or 0),
                "action": "pay_bill",
            }
        )

    workouts = grouped.get("workout", [])
    completed_workouts = [row for row in workouts if int(row["flag_1"] or 0)]
    pending_workout = next(
        (
            row
            for row in workouts
            if row["text_3"] == today_iso and not int(row["flag_1"] or 0)
        ),
        None,
    )
    latest = grouped.get("last_workout", [])
    latest_date = _parse_date(latest[0]["text_3"]) if latest else None
    days_since = (anchor - latest_date).days if latest_date else None
    todays_workout = None
    if pending_workout is not None:
        todays_workout = {
            "id": pending_workout["id"],
            "name": pending_workout["text_1"],
            "focus": pending_workout["text_2"],
            "scheduled_on": pending_workout["text_3"],
        }
        alerts.append(
            {
                "severity": "info",
                "app": "fitness",
                "record_id": pending_workout["id"],
                "title": f"Treino de hoje: {pending_workout['text_1']}",
                "detail": pending_workout["text_2"] or "Bora treinar",
                "amount_cents": 0,
                "action": "complete_workout",
            }
        )
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

    habits = sorted(grouped.get("habit", []), key=lambda row: str(row["text_3"]))
    checkin_days: Dict[str, set[date]] = {}
    for checkin in grouped.get("habit_checkin", []):
        done_on = _parse_date(checkin["text_2"])
        if done_on is not None:
            checkin_days.setdefault(str(checkin["text_1"]), set()).add(done_on)
    pending_habits: List[Dict[str, Any]] = []
    completed_habits = 0
    for habit in habits:
        days = checkin_days.get(str(habit["id"]), set())
        if anchor in days:
            completed_habits += 1
            continue
        cursor = anchor - timedelta(days=1)
        streak = 0
        while cursor in days:
            streak += 1
            cursor -= timedelta(days=1)
        pending_habits.append(habit)
        at_risk = streak >= 3
        alerts.append(
            {
                "severity": "warning" if at_risk else "info",
                "app": "routine",
                "record_id": habit["id"],
                "title": habit["text_1"],
                "detail": (
                    f"Sequência de {streak} dias em risco"
                    if at_risk
                    else "Pendente hoje"
                ),
                "amount_cents": 0,
                "action": "check_in_habit",
            }
        )

    family: List[Dict[str, Any]] = []
    family_limit = anchor + timedelta(days=FAMILY_LOOKAHEAD_DAYS)
    for member in grouped.get("family_member", []):
        birthday = _parse_date(member["text_2"])
        if birthday is None:
            continue
        occurrence = _next_occurrence(birthday, anchor)
        if occurrence <= family_limit:
            family.append(
                {
                    "kind": "birthday",
                    "title": f"Aniversário de {member['text_1']}",
                    "date": occurrence.isoformat(),
                    "days_away": (occurrence - anchor).days,
                    "member_id": member["id"],
                    "member_name": member["text_1"],
                    "turning": occurrence.year - birthday.year,
                }
            )
    for event in grouped.get("family_event", []):
        event_date = _parse_date(event["text_2"])
        if event_date is not None:
            family.append(
                {
                    "kind": event["text_3"] or "event",
                    "title": event["text_1"],
                    "date": event_date.isoformat(),
                    "days_away": (event_date - anchor).days,
                    "member_id": event["ref_id"],
                    "member_name": "",
                    "turning": 0,
                }
            )
    family.sort(key=lambda item: item["date"])
    for event in family:
        when = "hoje" if event["days_away"] == 0 else f"em {event['days_away']} dia(s)"
        alerts.append(
            {
                "severity": "warning" if event["days_away"] <= 1 else "info",
                "app": "family",
                "record_id": event["member_id"],
                "title": f"{event['title']} {when}",
                "detail": event["date"],
                "amount_cents": 0,
                "action": "view_member",
            }
        )

    open_tasks = grouped.get("work_task", [])
    overdue_tasks: List[Dict[str, Any]] = []
    due_today_tasks: List[Dict[str, Any]] = []
    for task in open_tasks:
        due = _parse_date(task["text_2"])
        if due is None:
            continue
        if due < anchor:
            overdue_tasks.append(task)
        elif due == anchor:
            due_today_tasks.append(task)
    for task in overdue_tasks:
        alerts.append(
            {
                "severity": "critical",
                "app": "work",
                "record_id": task["id"],
                "title": task["text_1"],
                "detail": f"Atrasada desde {task['text_2']}",
                "amount_cents": 0,
                "action": "complete_task",
            }
        )
    for task in due_today_tasks:
        alerts.append(
            {
                "severity": "warning",
                "app": "work",
                "record_id": task["id"],
                "title": task["text_1"],
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
            "family": len([event for event in family if event["days_away"] <= 1]),
            "work": len(overdue_tasks) + len(due_today_tasks),
            "health": 0,
        },
        "finance": {
            "balance_cents": sum(
                int(row["number_1"] or 0) for row in grouped.get("account", [])
            ),
            "net_cents": income - expense,
            "expense_cents": expense,
            "income_cents": income,
            "overdue_count": len(overdue_bills),
            "due_soon_count": len(due_soon),
        },
        "fitness": {
            "week_completed": len(completed_workouts),
            "week_planned": len(workouts),
            "days_since_last": days_since,
            "todays_workout": todays_workout,
        },
        "routine": {
            "completed": completed_habits,
            "total": len(habits),
            "pending": [str(habit["text_1"]) for habit in pending_habits],
        },
        "family": {"upcoming": family[:5]},
        "work": {
            "open_count": len(open_tasks),
            "overdue_count": len(overdue_tasks),
            "due_today_count": len(due_today_tasks),
        },
    }


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
    pending_workout = next((w for w in todays_workouts if not w["completed_at"]), None)
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
    family = service.upcoming_family(user.id, anchor=anchor, days=FAMILY_LOOKAHEAD_DAYS)
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

    # -- Health --------------------------------------------------------------
    # Health records are surfaced without deriving risk scores or medical
    # alerts. Those require clinical validation; the app remains an organizer.
    health = service.health_summary(user.id, anchor=anchor, timezone_name=user.timezone)

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
            "health": 0,
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
        "health": health,
    }
