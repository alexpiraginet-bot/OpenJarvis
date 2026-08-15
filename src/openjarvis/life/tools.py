"""Life OS exposed to the agent as tools.

This is the payoff of modelling life as data. Because these register through
``ToolRegistry`` and implement ``BaseTool``, every existing OpenJarvis agent —
orchestrator, ReAct, morning digest, the WhatsApp channel — can already answer
*"quanto gastei com mercado esse mês?"* or act on *"marca o treino de hoje como
feito"* without a line of agent code being written for it.

The surface is deliberately three tools rather than fifteen. An LLM choosing
between ``life_add_expense``, ``life_add_bill``, ``life_add_habit`` … picks
wrong far more often than one that reads a ``kind`` argument, and every extra
tool spec is permanent context cost on every turn.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.life import LifeContext, open_life
from openjarvis.life.integrations import IntegrationsStore
from openjarvis.life.money import format_money as _money
from openjarvis.life.schema import SCHEMA
from openjarvis.life.service import LifeServiceError
from openjarvis.life.tenancy import User
from openjarvis.life.training import TrainingCoachError, TrainingCoachService
from openjarvis.tools._stubs import BaseTool, ToolSpec

#: Which table each writable ``kind`` lands in.
_RECORD_TABLES = {
    "expense": "transactions",
    "income": "transactions",
    "bill": "bills",
    "account": "accounts",
    "budget": "budgets",
    "goal": "goals",
    "workout": "workouts",
    "exercise_set": "exercise_sets",
    "measurement": "measurements",
    "habit": "habits",
    "family_member": "family_members",
    "family_event": "family_events",
    "project": "projects",
    "task": "work_tasks",
    "health_profile": "health_profiles",
    "condition": "health_conditions",
    "medication": "medications",
    "allergy": "allergies",
    "health_observation": "health_observations",
    "hydration": "hydration_logs",
    "nutrition": "nutrition_logs",
    "health_document": "health_documents",
}

_COACH_RECORD_KINDS = frozenset(
    {
        "training_profile",
        "training_plan",
        "training_checkin",
        "training_feedback",
    }
)

#: Record kinds whose NOT NULL date column defaults to today when the model
#: omits it. Without this an "adiciona a conta de luz" with no date given
#: would fail on a constraint instead of doing the obvious thing.
_DATE_DEFAULTS = {
    "bill": "due_on",
    "workout": "scheduled_on",
    "measurement": "taken_on",
    "family_event": "event_on",
}

_DATETIME_DEFAULTS = {
    "health_observation": "observed_at",
    "hydration": "occurred_at",
    "nutrition": "occurred_at",
}

# Field names a model plausibly reaches for, mapped to the column that stores
# them. Three unrelated things in this domain are called a "goal" — the savings
# goal (``goals``), the plan's objective (``training_plans.goal``) and the
# athlete's own (``training_profiles.primary_goal``) — so a model naming the
# short one is following the domain, not misreading the schema. Aliasing beats
# rejecting here: ``save_profile`` defaults an absent ``primary_goal`` to
# ``general_fitness``, so an unmapped alias is not an error the client ever
# sees, it is their stated objective quietly replaced by the wrong one.
_RECORD_FIELD_ALIASES = {
    "training_profile": {"goal": "primary_goal"},
}

# Fields maintained by multi-step domain actions. Generic record creation must
# not bypass ledger entries, completion stamps or other side effects.
_ACTION_OWNED_RECORD_FIELDS = {
    "account": frozenset({"balance_cents"}),
    "bill": frozenset({"status", "paid_on"}),
    "workout": frozenset({"completed_at"}),
    "task": frozenset({"status", "done_at"}),
}


def life_record_app(kind: str) -> str | None:
    """Return the canonical Life app that owns a writable record kind."""
    if kind in _COACH_RECORD_KINDS:
        return "fitness"
    table = _RECORD_TABLES.get(kind)
    return SCHEMA[table].app if table is not None else None


class LifeUserError(RuntimeError):
    """Raised when a tool cannot determine which client it is acting for."""


def _resolve_user(life: LifeContext, user_id: str = "") -> User:
    """Determine the acting client.

    Order: the explicit argument, then ``OPENJARVIS_LIFE_USER_ID`` (how the
    server binds a session), then the sole registered user on a single-client
    install. Ambiguity raises instead of guessing — writing one client's
    expense onto another's ledger is not a failure worth recovering from
    silently.
    """
    candidate = user_id or os.environ.get("OPENJARVIS_LIFE_USER_ID", "")
    if candidate:
        user = life.users.get_user(candidate)
        if user is None:
            raise LifeUserError(f"Unknown user: {candidate}")
        return user

    if life.users.count_users() == 1:
        row = life.users.connection.execute("SELECT id FROM users").fetchone()
        if row is not None:
            user = life.users.get_user(row["id"])
            if user is not None:
                return user

    raise LifeUserError(
        "No user selected. Pass user_id or set OPENJARVIS_LIFE_USER_ID."
    )


def _apply_date_default(kind: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Fill the record's date column with today when the caller omitted it."""
    column = _DATE_DEFAULTS.get(kind)
    if column and not fields.get(column):
        fields[column] = date.today().isoformat()
    datetime_column = _DATETIME_DEFAULTS.get(kind)
    if datetime_column and not fields.get(datetime_column):
        fields[datetime_column] = datetime.now(timezone.utc).isoformat()
    return fields


def normalize_life_record_fields(kind: str, fields: Any) -> Dict[str, Any]:
    """Validate tool-call fields and normalize canonical integer money input."""
    if kind not in _RECORD_TABLES and kind not in _COACH_RECORD_KINDS:
        raise ValueError(f"Unknown kind: {kind}")
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")

    normalized = dict(fields)
    for alias, column in _RECORD_FIELD_ALIASES.get(kind, {}).items():
        # The canonical name wins: an alias may fill a gap, never shadow a
        # value the model already spelled correctly.
        if alias in normalized and not normalized.get(column):
            normalized[column] = normalized[alias]
        normalized.pop(alias, None)

    protected = _ACTION_OWNED_RECORD_FIELDS.get(kind, frozenset())
    bypassed = sorted(protected.intersection(normalized))
    if bypassed:
        raise ValueError("Fields require a domain action: " + ", ".join(bypassed))

    if "amount_cents" in normalized:
        amount = normalized["amount_cents"]
        if isinstance(amount, bool):
            raise ValueError("amount_cents must be an integer")
        if isinstance(amount, str):
            candidate = amount.strip()
            digits = candidate[1:] if candidate[:1] in {"+", "-"} else candidate
            if not digits.isdigit():
                raise ValueError("amount_cents must be an integer")
            amount = int(candidate)
        elif not isinstance(amount, int):
            raise ValueError("amount_cents must be an integer")
        normalized["amount_cents"] = amount

    return normalized


class _LifeTool(BaseTool):
    """Shared plumbing: an optional bound context and client."""

    def __init__(
        self,
        life: Optional[LifeContext] = None,
        *,
        user_id: str = "",
    ) -> None:
        self._life = life
        self._user_id = user_id

    def _context(self) -> LifeContext:
        return self._life or open_life()

    def _acting_user(self, params: Dict[str, Any], life: LifeContext) -> User:
        """Resolve the client, with the bound tenant overriding any argument.

        When the server binds a session's user, that binding is authoritative:
        a model that hallucinates (or is prompt-injected into emitting) another
        ``user_id`` must not be able to reach a second client's data. The
        argument is honoured only on unbound instances, where there is no
        session to contradict.
        """
        if self._user_id:
            return _resolve_user(life, self._user_id)
        return _resolve_user(life, params.get("user_id", ""))


@ToolRegistry.register("life_overview")
class LifeOverviewTool(_LifeTool):
    """Read the client's life: today's briefing or a per-app summary."""

    tool_id = "life_overview"

    @property
    def spec(self) -> ToolSpec:
        """Describe the read surface for tool-calling models."""
        return ToolSpec(
            name="life_overview",
            description=(
                "Read the user's life data: today's cross-domain briefing, or "
                "a summary of finance (balance, spending, budgets), fitness "
                "(coach plan, prescribed sessions, workouts, records), routine "
                "(habits and streaks), family "
                "(upcoming birthdays), work (open and overdue tasks) or health "
                "(user-confirmed profile, conditions, medications, allergies, "
                "hydration, nutrition, measurements and document metadata). Use "
                "connections for synchronized mail, calendars and activities. Use "
                "this before answering any question about the user's money, "
                "training, habits, family or tasks."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "enum": [
                            "today",
                            "finance",
                            "fitness",
                            "routine",
                            "family",
                            "work",
                            "health",
                            "connections",
                        ],
                        "description": "Which part of life to read.",
                    },
                    "user_id": {
                        "type": "string",
                        "description": "Client id. Omit on single-user installs.",
                    },
                },
                "required": ["section"],
            },
            category="life",
        )

    def execute(self, **params: Any) -> ToolResult:
        """Return the requested section as JSON."""
        life = self._context()
        section = params.get("section", "today")
        try:
            user = self._acting_user(params, life)
        except LifeUserError as exc:
            return ToolResult(
                tool_name="life_overview", success=False, content=str(exc)
            )

        service = life.service
        if section == "today":
            payload: Dict[str, Any] = life.today(user)
        elif section == "finance":
            payload = service.finance_summary(user.id)
        elif section == "fitness":
            payload = {
                **service.fitness_summary(user.id),
                "coach": TrainingCoachService(life.store).overview(user.id),
            }
        elif section == "routine":
            payload = service.routine_summary(user.id)
        elif section == "family":
            payload = {"upcoming": service.upcoming_family(user.id)}
        elif section == "work":
            payload = service.work_summary(user.id)
        elif section == "health":
            payload = service.health_summary(user.id, timezone_name=user.timezone)
        elif section == "connections":
            payload = IntegrationsStore(life).context_snapshot(user.id)
        else:
            return ToolResult(
                tool_name="life_overview",
                success=False,
                content=f"Unknown section: {section}",
            )
        return ToolResult(
            tool_name="life_overview",
            success=True,
            content=json.dumps(payload, ensure_ascii=False, default=str),
            metadata={"section": section, "user_id": user.id},
        )


@ToolRegistry.register("life_record")
class LifeRecordTool(_LifeTool):
    """Write a new entry into any Life app."""

    tool_id = "life_record"

    @property
    def spec(self) -> ToolSpec:
        """Describe the write surface for tool-calling models."""
        return ToolSpec(
            name="life_record",
            description=(
                "Record something in the user's life: an expense or income, a "
                "bill to pay, a budget, a savings goal, a workout, a body "
                "measurement, a habit, a family member or event, a project or "
                "a task; or a user-confirmed health profile, condition, "
                "medication, allergy, observation, hydration, meal or document "
                "metadata. Amounts are in cents (R$45,90 = 4590). Never infer "
                "or diagnose a health fact."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": sorted(set(_RECORD_TABLES).union(_COACH_RECORD_KINDS)),
                        "description": "What kind of entry to create.",
                    },
                    "fields": {
                        "type": "object",
                        "description": (
                            "Entry fields. expense/income: amount_cents, "
                            "category, description, occurred_on. bill: name, "
                            "amount_cents, due_on, recurrence. habit: name, "
                            "cadence. workout: name, scheduled_on, focus. "
                            "task: title, due_on, priority. hydration: amount_ml. "
                            "health_observation: kind, value, unit. condition and "
                            "medication: name. nutrition: meal_type, description. "
                            "training_profile: primary_sport, secondary_sports, "
                            "primary_goal, level, weekly_days, "
                            "available_weekdays, session_minutes, "
                            "current_weekly_km, longest_recent_run_km, "
                            "target_distance_km, target_date, equipment, "
                            "limitations. training_plan: "
                            "start_on and weeks. training_checkin/training_feedback: "
                            "session_id plus readiness or completion metrics."
                        ),
                    },
                    "user_id": {
                        "type": "string",
                        "description": "Client id. Omit on single-user installs.",
                    },
                },
                "required": ["kind", "fields"],
            },
            category="life",
            requires_confirmation=True,
        )

    def execute(self, **params: Any) -> ToolResult:
        """Create the record and report it back in human terms."""
        life = self._context()
        kind = params.get("kind", "")
        table = _RECORD_TABLES.get(kind)
        if table is None and kind not in _COACH_RECORD_KINDS:
            return ToolResult(
                tool_name="life_record",
                success=False,
                content=f"Unknown kind: {kind}",
            )
        try:
            user = self._acting_user(params, life)
        except LifeUserError as exc:
            return ToolResult(tool_name="life_record", success=False, content=str(exc))

        try:
            fields = normalize_life_record_fields(kind, params.get("fields"))
            if kind in _COACH_RECORD_KINDS:
                coach = TrainingCoachService(life.store)
                if kind == "training_profile":
                    record = coach.save_profile(user.id, fields)
                    summary = "perfil de treino atualizado"
                elif kind == "training_plan":
                    record = coach.generate_plan(
                        user.id,
                        start_on=fields.get("start_on"),
                        weeks=fields.get("weeks", 8),
                    )
                    summary = "plano adaptativo criado"
                elif kind == "training_checkin":
                    session_id = str(fields.pop("session_id", ""))
                    record = coach.check_in(user.id, session_id, **fields)
                    summary = "prontidão analisada"
                else:
                    session_id = str(fields.pop("session_id", ""))
                    record = coach.complete_session(user.id, session_id, **fields)
                    summary = "sessão concluída e plano adaptado"
            elif kind in ("expense", "income"):
                record = life.service.add_transaction(
                    user.id,
                    amount_cents=fields.get("amount_cents", 0),
                    kind=kind,
                    category=fields.get("category", "outros"),
                    description=fields.get("description", ""),
                    occurred_on=fields.get("occurred_on", ""),
                    account_id=fields.get("account_id", ""),
                    source="agent",
                )
                amount = _money(int(record["amount_cents"]), user.currency)
                summary = f"{kind} de {amount} em {record['category']}"
            else:
                record_id = life.store.insert(
                    table, user.id, _apply_date_default(kind, fields)
                )
                record = life.store.get(table, user.id, record_id) or {}
                summary = f"{kind} criado"
        except (LifeServiceError, TrainingCoachError, TypeError, ValueError) as exc:
            return ToolResult(tool_name="life_record", success=False, content=str(exc))

        return ToolResult(
            tool_name="life_record",
            success=True,
            content=json.dumps(
                {"created": summary, "record": record},
                ensure_ascii=False,
                default=str,
            ),
            metadata={"kind": kind, "user_id": user.id},
        )


@ToolRegistry.register("life_complete")
class LifeCompleteTool(_LifeTool):
    """Mark something done: pay a bill, finish a workout, tick a habit."""

    tool_id = "life_complete"

    @property
    def spec(self) -> ToolSpec:
        """Describe the completion surface for tool-calling models."""
        return ToolSpec(
            name="life_complete",
            description=(
                "Mark something in the user's life as done: pay a bill "
                "(records the expense and rolls a recurring bill forward), "
                "complete a workout, check in a habit for today, or finish a "
                "work task. Find the record id with life_overview first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["bill", "workout", "habit", "task"],
                        "description": "What is being completed.",
                    },
                    "record_id": {
                        "type": "string",
                        "description": "Id of the record to complete.",
                    },
                    "user_id": {
                        "type": "string",
                        "description": "Client id. Omit on single-user installs.",
                    },
                },
                "required": ["kind", "record_id"],
            },
            category="life",
            requires_confirmation=True,
        )

    def execute(self, **params: Any) -> ToolResult:
        """Complete the record and describe what changed."""
        life = self._context()
        kind = params.get("kind", "")
        record_id = params.get("record_id", "")
        try:
            user = self._acting_user(params, life)
        except LifeUserError as exc:
            return ToolResult(
                tool_name="life_complete", success=False, content=str(exc)
            )

        try:
            if kind == "bill":
                payload: Dict[str, Any] = life.service.pay_bill(user.id, record_id)
            elif kind == "workout":
                payload = {"workout": life.service.complete_workout(user.id, record_id)}
            elif kind == "habit":
                result = life.service.check_in_habit(user.id, record_id)
                payload = {
                    **result,
                    "streak": life.service.habit_streak(user.id, record_id),
                }
            elif kind == "task":
                payload = {"task": life.service.complete_task(user.id, record_id)}
            else:
                return ToolResult(
                    tool_name="life_complete",
                    success=False,
                    content=f"Unknown kind: {kind}",
                )
        except LifeServiceError as exc:
            return ToolResult(
                tool_name="life_complete", success=False, content=str(exc)
            )

        return ToolResult(
            tool_name="life_complete",
            success=True,
            content=json.dumps(payload, ensure_ascii=False, default=str),
            metadata={"kind": kind, "user_id": user.id},
        )


def life_tools_for(
    life: LifeContext, user_id: str
) -> Tuple[LifeOverviewTool, LifeRecordTool, LifeCompleteTool]:
    """Build the three Life tools bound to one client.

    The hosted server calls this per session so a tool invocation can never
    read across tenants, whatever the model puts in its arguments.
    """
    return (
        LifeOverviewTool(life, user_id=user_id),
        LifeRecordTool(life, user_id=user_id),
        LifeCompleteTool(life, user_id=user_id),
    )


def ensure_registered() -> None:
    """Idempotently register the Life tools in ``ToolRegistry``.

    The decorators fire once at import time, but ``tests/conftest.py`` clears
    every registry before each test, so anything relying on discovery must be
    able to re-register. Matches the convention used by the mining providers.
    """
    for name, tool_cls in (
        ("life_overview", LifeOverviewTool),
        ("life_record", LifeRecordTool),
        ("life_complete", LifeCompleteTool),
    ):
        if not ToolRegistry.contains(name):
            ToolRegistry.register_value(name, tool_cls)


__all__ = [
    "LifeCompleteTool",
    "LifeOverviewTool",
    "LifeRecordTool",
    "LifeUserError",
    "ensure_registered",
    "life_record_app",
    "life_tools_for",
]
