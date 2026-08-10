"""Domain rules for the Life OS — the logic that raw CRUD cannot express.

The store moves rows; this layer knows what those rows *mean*. Paying a bill is
not one UPDATE, it is: mark paid, debit the account, record the expense, and
roll a recurring bill forward to next month. Checking off a habit twice in one
day must not double a streak. Spending is only meaningful against a budget.

Every method takes ``user_id`` first and passes it straight through to the
store, so tenant isolation is preserved by construction rather than by review.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from openjarvis.life.store import Filter, LifeStore

#: Recurrences a bill may carry. ``none`` is a one-off.
RECURRENCES = ("none", "weekly", "monthly", "yearly")

#: How far ahead the app looks for birthdays and family events.
UPCOMING_WINDOW_DAYS = 30

#: A bill inside this window is surfaced as "due soon" on the Today screen.
BILL_SOON_DAYS = 7


class LifeServiceError(ValueError):
    """Raised when a domain rule rejects an operation."""


@dataclass(frozen=True, slots=True)
class MonthRange:
    """Inclusive first/last ISO dates of a calendar month."""

    first: str
    last: str


def today_in(timezone_name: str) -> date:
    """Return the current date in the user's timezone.

    A client in São Paulo must not see tomorrow's agenda because the server
    runs in UTC. Falls back to UTC when the zone database is unavailable
    (bare Windows images ship without tzdata).
    """
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415 — optional tzdata

        return datetime.now(ZoneInfo(timezone_name)).date()
    except Exception:
        return datetime.now(timezone.utc).date()


def month_range(anchor: date) -> MonthRange:
    """First and last day of the month containing ``anchor``."""
    last_day = calendar.monthrange(anchor.year, anchor.month)[1]
    return MonthRange(
        first=anchor.replace(day=1).isoformat(),
        last=anchor.replace(day=last_day).isoformat(),
    )


def add_months(anchor: date, months: int) -> date:
    """Shift by whole months, clamping to the target month's last day.

    Naively adding 31 days to a bill due on the 31st walks it through the
    calendar; clamping keeps "the 31st" meaning "month end" in February.
    """
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _parse_date(value: Any) -> Optional[date]:
    """Parse an ISO date, tolerating full timestamps. ``None`` when unusable."""
    if not value:
        return None
    text = str(value)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _next_occurrence(anchor: date, reference: date) -> date:
    """Next time an annual date (a birthday) falls on or after ``reference``."""
    try:
        candidate = anchor.replace(year=reference.year)
    except ValueError:
        # 29 February in a common year — observe it on the 28th.
        candidate = date(reference.year, 2, 28)
    if candidate < reference:
        try:
            candidate = anchor.replace(year=reference.year + 1)
        except ValueError:
            candidate = date(reference.year + 1, 2, 28)
    return candidate


class LifeService:
    """Domain operations across finance, fitness, routine, family and work."""

    def __init__(self, store: LifeStore) -> None:
        self._store = store

    @property
    def store(self) -> LifeStore:
        """The underlying tenant-scoped store."""
        return self._store

    # == Finance =============================================================

    def add_transaction(
        self,
        user_id: str,
        *,
        amount_cents: int,
        kind: str = "expense",
        category: str = "outros",
        description: str = "",
        occurred_on: str = "",
        account_id: str = "",
        source: str = "manual",
    ) -> Dict[str, Any]:
        """Record money moving and keep the account balance in step.

        ``amount_cents`` is always positive; ``kind`` carries the direction.
        Storing signed amounts invites double-negation bugs the first time
        someone edits a transaction.
        """
        if amount_cents <= 0:
            raise LifeServiceError("amount_cents must be positive")
        if kind not in ("income", "expense"):
            raise LifeServiceError("kind must be 'income' or 'expense'")

        record_id = self._store.insert(
            "transactions",
            user_id,
            {
                "amount_cents": int(amount_cents),
                "kind": kind,
                "category": category or "outros",
                "description": description,
                "occurred_on": occurred_on or date.today().isoformat(),
                "account_id": account_id,
                "source": source,
            },
        )
        if account_id:
            delta = amount_cents if kind == "income" else -amount_cents
            self._adjust_balance(user_id, account_id, delta)
        return self._store.get("transactions", user_id, record_id) or {}

    def _adjust_balance(self, user_id: str, account_id: str, delta: int) -> None:
        """Apply a signed delta to an account balance, if the account exists."""
        account = self._store.get("accounts", user_id, account_id)
        if account is None:
            return
        self._store.update(
            "accounts",
            user_id,
            account_id,
            {"balance_cents": int(account["balance_cents"]) + delta},
        )

    def pay_bill(
        self, user_id: str, bill_id: str, *, account_id: str = "", paid_on: str = ""
    ) -> Dict[str, Any]:
        """Settle a bill: mark it paid, book the expense, roll recurrence forward.

        Returns the updated bill plus the ids it produced, so the client can
        show "pago — próxima em 12/09" without a second round trip.
        """
        bill = self._store.get("bills", user_id, bill_id)
        if bill is None:
            raise LifeServiceError(f"Bill not found: {bill_id}")
        if bill["status"] == "paid":
            raise LifeServiceError("Bill is already paid")

        when = paid_on or date.today().isoformat()
        self._store.update(
            "bills", user_id, bill_id, {"status": "paid", "paid_on": when}
        )
        transaction = self.add_transaction(
            user_id,
            amount_cents=int(bill["amount_cents"]),
            kind="expense",
            category=bill["category"],
            description=f"Pagamento: {bill['name']}",
            occurred_on=when,
            account_id=account_id,
            source="bill",
        )

        next_bill_id = ""
        recurrence = bill["recurrence"]
        due = _parse_date(bill["due_on"])
        if recurrence != "none" and due is not None:
            if recurrence == "weekly":
                next_due = due + timedelta(days=7)
            elif recurrence == "monthly":
                next_due = add_months(due, 1)
            elif recurrence == "yearly":
                next_due = add_months(due, 12)
            else:
                raise LifeServiceError(f"Unknown recurrence: {recurrence}")
            next_bill_id = self._store.insert(
                "bills",
                user_id,
                {
                    "name": bill["name"],
                    "amount_cents": bill["amount_cents"],
                    "due_on": next_due.isoformat(),
                    "recurrence": recurrence,
                    "status": "pending",
                    "category": bill["category"],
                    "autopay": bill["autopay"],
                },
            )
        return {
            "bill": self._store.get("bills", user_id, bill_id),
            "transaction_id": transaction.get("id", ""),
            "next_bill_id": next_bill_id,
        }

    def finance_summary(
        self, user_id: str, *, anchor: Optional[date] = None
    ) -> Dict[str, Any]:
        """Month-to-date money picture: balance, flow, categories, budgets."""
        anchor = anchor or date.today()
        span = month_range(anchor)
        in_month = (
            Filter("occurred_on", ">=", span.first),
            Filter("occurred_on", "<=", span.last),
        )

        income = self._store.sum_column(
            "transactions",
            user_id,
            "amount_cents",
            filters=(*in_month, Filter("kind", "=", "income")),
        )
        expense = self._store.sum_column(
            "transactions",
            user_id,
            "amount_cents",
            filters=(*in_month, Filter("kind", "=", "expense")),
        )
        by_category = self._store.group_sum(
            "transactions",
            user_id,
            "category",
            "amount_cents",
            filters=(*in_month, Filter("kind", "=", "expense")),
        )
        spent = {row["label"]: row["total"] for row in by_category}

        budgets = []
        for budget in self._store.list_records("budgets", user_id, limit=100):
            used = spent.get(budget["category"], 0)
            limit_cents = int(budget["limit_cents"])
            budgets.append(
                {
                    "id": budget["id"],
                    "category": budget["category"],
                    "limit_cents": limit_cents,
                    "spent_cents": used,
                    "remaining_cents": limit_cents - used,
                    # Guard the zero-limit case rather than dividing blind.
                    "pct_used": round(used / limit_cents * 100, 1)
                    if limit_cents
                    else 0.0,
                }
            )

        balance = self._store.sum_column(
            "accounts",
            user_id,
            "balance_cents",
            filters=(Filter("archived", "=", 0),),
        )
        return {
            "month": anchor.strftime("%Y-%m"),
            "balance_cents": balance,
            "income_cents": income,
            "expense_cents": expense,
            "net_cents": income - expense,
            "by_category": by_category,
            "budgets": budgets,
        }

    def refresh_bill_statuses(self, user_id: str, today: date) -> int:
        """Flip pending bills past their due date to ``overdue``.

        Status is stored rather than derived so the scheduler can alert on a
        transition instead of recomputing "is it late?" on every read.
        """
        stale = self._store.list_records(
            "bills",
            user_id,
            filters=(
                Filter("status", "=", "pending"),
                Filter("due_on", "<", today.isoformat()),
            ),
            limit=500,
        )
        for bill in stale:
            self._store.update("bills", user_id, bill["id"], {"status": "overdue"})
        return len(stale)

    # == Fitness =============================================================

    def complete_workout(
        self, user_id: str, workout_id: str, *, duration_min: int = 0
    ) -> Dict[str, Any]:
        """Mark a workout done now, optionally recording its duration."""
        workout = self._store.get("workouts", user_id, workout_id)
        if workout is None:
            raise LifeServiceError(f"Workout not found: {workout_id}")
        patch: Dict[str, Any] = {
            "completed_at": datetime.now(timezone.utc).isoformat()
        }
        if duration_min:
            patch["duration_min"] = int(duration_min)
        self._store.update("workouts", user_id, workout_id, patch)
        return self._store.get("workouts", user_id, workout_id) or {}

    def fitness_summary(
        self, user_id: str, *, anchor: Optional[date] = None
    ) -> Dict[str, Any]:
        """Training load for the trailing week plus personal records."""
        anchor = anchor or date.today()
        week_start = (anchor - timedelta(days=6)).isoformat()
        recent = self._store.list_records(
            "workouts",
            user_id,
            filters=(
                Filter("scheduled_on", ">=", week_start),
                Filter("scheduled_on", "<=", anchor.isoformat()),
            ),
            order_by="scheduled_on",
            descending=False,
            limit=100,
        )
        done = [w for w in recent if w["completed_at"]]
        volume = 0.0
        for workout in done:
            for exercise_set in self._store.list_records(
                "exercise_sets",
                user_id,
                filters=(Filter("workout_id", "=", workout["id"]),),
                limit=500,
            ):
                volume += float(exercise_set["weight_kg"]) * int(exercise_set["reps"])

        latest = self._store.list_records(
            "measurements", user_id, order_by="taken_on", limit=1
        )
        return {
            "week_planned": len(recent),
            "week_completed": len(done),
            "week_minutes": sum(int(w["duration_min"]) for w in done),
            "week_volume_kg": round(volume, 1),
            "days_since_last": self._days_since_last_workout(user_id, anchor),
            "personal_records": self.personal_records(user_id),
            "latest_measurement": latest[0] if latest else None,
        }

    def _days_since_last_workout(self, user_id: str, anchor: date) -> Optional[int]:
        """Days since the last completed session, or ``None`` if never."""
        completed = self._store.list_records(
            "workouts",
            user_id,
            filters=(Filter("completed_at", "IS NOT NULL"),),
            order_by="scheduled_on",
            limit=1,
        )
        if not completed:
            return None
        last = _parse_date(completed[0]["scheduled_on"])
        return (anchor - last).days if last else None

    def personal_records(self, user_id: str, *, limit: int = 8) -> List[Dict[str, Any]]:
        """Heaviest logged set per exercise, best first."""
        rows = self._store.list_records(
            "exercise_sets", user_id, order_by="weight_kg", limit=1000
        )
        best: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            name = row["exercise"]
            current = best.get(name)
            if current is None or float(row["weight_kg"]) > float(
                current["weight_kg"]
            ):
                best[name] = {
                    "exercise": name,
                    "weight_kg": float(row["weight_kg"]),
                    "reps": int(row["reps"]),
                }
        ranked = sorted(best.values(), key=lambda r: r["weight_kg"], reverse=True)
        return ranked[:limit]

    # == Routine =============================================================

    def check_in_habit(
        self, user_id: str, habit_id: str, *, done_on: str = "", note: str = ""
    ) -> Dict[str, Any]:
        """Mark a habit done for a day. Idempotent — repeats never double-count."""
        habit = self._store.get("habits", user_id, habit_id)
        if habit is None:
            raise LifeServiceError(f"Habit not found: {habit_id}")
        when = done_on or date.today().isoformat()
        existing = self._store.list_records(
            "habit_checkins",
            user_id,
            filters=(
                Filter("habit_id", "=", habit_id),
                Filter("done_on", "=", when),
            ),
            limit=1,
        )
        if existing:
            return {"checkin": existing[0], "created": False}
        record_id = self._store.insert(
            "habit_checkins",
            user_id,
            {"habit_id": habit_id, "done_on": when, "note": note},
        )
        return {
            "checkin": self._store.get("habit_checkins", user_id, record_id),
            "created": True,
        }

    def undo_habit_check_in(
        self, user_id: str, habit_id: str, *, done_on: str = ""
    ) -> bool:
        """Remove a day's check-in — the inevitable mis-tap on a phone."""
        when = done_on or date.today().isoformat()
        removed = self._store.delete_where(
            "habit_checkins",
            user_id,
            (Filter("habit_id", "=", habit_id), Filter("done_on", "=", when)),
        )
        return removed > 0

    def habit_streak(
        self, user_id: str, habit_id: str, *, anchor: Optional[date] = None
    ) -> int:
        """Consecutive days completed, ending today or yesterday.

        A habit not yet done *today* still has a live streak — the day is not
        over. It only breaks once yesterday is also missing.
        """
        anchor = anchor or date.today()
        rows = self._store.list_records(
            "habit_checkins",
            user_id,
            filters=(Filter("habit_id", "=", habit_id),),
            order_by="done_on",
            limit=1000,
        )
        done_days = {_parse_date(row["done_on"]) for row in rows}
        done_days.discard(None)
        if not done_days:
            return 0
        cursor = anchor if anchor in done_days else anchor - timedelta(days=1)
        streak = 0
        while cursor in done_days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    def routine_summary(
        self, user_id: str, *, anchor: Optional[date] = None
    ) -> Dict[str, Any]:
        """Today's habits with completion state and current streaks."""
        anchor = anchor or date.today()
        today_iso = anchor.isoformat()
        habits = self._store.list_records(
            "habits",
            user_id,
            filters=(Filter("archived", "=", 0),),
            order_by="created_at",
            descending=False,
            limit=100,
        )
        done_today = {
            row["habit_id"]
            for row in self._store.list_records(
                "habit_checkins",
                user_id,
                filters=(Filter("done_on", "=", today_iso),),
                limit=500,
            )
        }
        items = [
            {
                **habit,
                "done_today": habit["id"] in done_today,
                "streak": self.habit_streak(user_id, habit["id"], anchor=anchor),
            }
            for habit in habits
        ]
        return {
            "date": today_iso,
            "habits": items,
            "completed": sum(1 for item in items if item["done_today"]),
            "total": len(items),
        }

    # == Family ==============================================================

    def upcoming_family(
        self,
        user_id: str,
        *,
        anchor: Optional[date] = None,
        days: int = UPCOMING_WINDOW_DAYS,
    ) -> List[Dict[str, Any]]:
        """Birthdays and family events in the next ``days``, soonest first.

        Birthdays are stored once with their original year and projected onto
        the coming window, so a date entered in 1978 still surfaces this May.
        """
        anchor = anchor or date.today()
        horizon = anchor + timedelta(days=days)
        upcoming: List[Dict[str, Any]] = []

        for member in self._store.list_records("family_members", user_id, limit=200):
            birthday = _parse_date(member["birthday"])
            if birthday is None:
                continue
            occurrence = _next_occurrence(birthday, anchor)
            if occurrence > horizon:
                continue
            upcoming.append(
                {
                    "kind": "birthday",
                    "title": f"Aniversário de {member['name']}",
                    "date": occurrence.isoformat(),
                    "days_away": (occurrence - anchor).days,
                    "member_id": member["id"],
                    "member_name": member["name"],
                    "turning": occurrence.year - birthday.year,
                }
            )

        for event in self._store.list_records(
            "family_events",
            user_id,
            filters=(
                Filter("event_on", ">=", anchor.isoformat()),
                Filter("event_on", "<=", horizon.isoformat()),
            ),
            order_by="event_on",
            descending=False,
            limit=200,
        ):
            event_date = _parse_date(event["event_on"])
            if event_date is None:
                continue
            upcoming.append(
                {
                    "kind": event["kind"] or "event",
                    "title": event["title"],
                    "date": event_date.isoformat(),
                    "days_away": (event_date - anchor).days,
                    "member_id": event["member_id"],
                    "member_name": "",
                    "turning": 0,
                }
            )

        upcoming.sort(key=lambda item: item["date"])
        return upcoming

    # == Work ================================================================

    def complete_task(self, user_id: str, task_id: str) -> Dict[str, Any]:
        """Mark a work task done, stamping the completion time."""
        task = self._store.get("work_tasks", user_id, task_id)
        if task is None:
            raise LifeServiceError(f"Task not found: {task_id}")
        self._store.update(
            "work_tasks",
            user_id,
            task_id,
            {
                "status": "done",
                "done_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return self._store.get("work_tasks", user_id, task_id) or {}

    def work_summary(
        self, user_id: str, *, anchor: Optional[date] = None
    ) -> Dict[str, Any]:
        """Open workload: what is late, what is due today, what is active."""
        anchor = anchor or date.today()
        today_iso = anchor.isoformat()
        open_filter = Filter("status", "IN", ["todo", "doing"])
        overdue = self._store.list_records(
            "work_tasks",
            user_id,
            filters=(open_filter, Filter("due_on", "<", today_iso)),
            order_by="due_on",
            descending=False,
            limit=100,
        )
        due_today = self._store.list_records(
            "work_tasks",
            user_id,
            filters=(open_filter, Filter("due_on", "=", today_iso)),
            limit=100,
        )
        return {
            "open_count": self._store.count(
                "work_tasks", user_id, filters=(open_filter,)
            ),
            "overdue": overdue,
            "due_today": due_today,
            "projects": self._store.list_records(
                "projects",
                user_id,
                filters=(Filter("status", "=", "active"),),
                limit=50,
            ),
        }
