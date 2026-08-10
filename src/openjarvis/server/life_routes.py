"""HTTP API for the Life OS — what the mobile app talks to.

Authentication here is deliberately *not* the server-wide
``OPENJARVIS_API_KEY``. That key is a single shared secret suited to one
operator on one machine; a phone in a client's pocket needs a credential that
identifies *which* client and can be revoked alone. So ``/v1/life/*`` is
exempted from the global key middleware (see
``auth_middleware.AuthMiddleware._requires_auth``) and authenticates every
request against a per-user bearer token instead.

Routes fall into three groups:

* ``/auth`` and ``/me`` — identity.
* ``/records/{table}`` — generic tenant-scoped CRUD over the schema.
* ``/actions/...`` — the operations with domain rules behind them (paying a
  bill is not an UPDATE, see :mod:`openjarvis.life.service`).

Generic CRUD lives under its own ``/records`` prefix rather than at
``/v1/life/{table}`` so a table can never shadow a named route like
``/today``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from openjarvis.life import LifeContext, open_life
from openjarvis.life.ai_budget import AiBudgetExceededError, AiBudgetStore
from openjarvis.life.jarvis import (
    JarvisActionError,
    JarvisActionStore,
    JarvisRuntime,
)
from openjarvis.life.money import format_money as _money
from openjarvis.life.schema import APPS, SCHEMA
from openjarvis.life.service import LifeServiceError, today_in
from openjarvis.life.store import Filter, LifeStoreError
from openjarvis.life.tenancy import AuthError, User
from openjarvis.server.life_integrations_routes import create_integrations_router
from openjarvis.server.life_voice_routes import create_voice_router

#: Query parameters that control the query itself rather than filtering it.
_CONTROL_PARAMS = frozenset({"order_by", "desc", "limit", "offset"})

#: Suffix → SQL operator for list filters (``?due_on__lte=2026-08-20``).
_OP_SUFFIXES = {
    "__gte": ">=",
    "__lte": "<=",
    "__gt": ">",
    "__lt": "<",
    "__ne": "!=",
    "__like": "LIKE",
}

# Fields maintained by multi-step domain actions. Generic PATCH must not
# bypass their side effects (ledger entries, streaks and completion stamps).
_ACTION_OWNED_PATCH_FIELDS = {
    "accounts": frozenset({"balance_cents"}),
    "bills": frozenset({"status", "paid_on"}),
    "workouts": frozenset({"completed_at"}),
    "work_tasks": frozenset({"status", "done_at"}),
    "transactions": frozenset(SCHEMA["transactions"].columns),
}

#: Springboard metadata. The client renders icons and labels from this so
#: adding an app to the home screen is a server-side change, not a release.
APP_MANIFEST: List[Dict[str, str]] = [
    {
        "id": "finance",
        "label": "Finanças",
        "icon": "wallet",
        "tint": "emerald",
        "description": "Contas, gastos, orçamentos e metas",
    },
    {
        "id": "fitness",
        "label": "Treino",
        "icon": "dumbbell",
        "tint": "orange",
        "description": "Treinos, cargas e medidas",
    },
    {
        "id": "routine",
        "label": "Rotina",
        "icon": "repeat",
        "tint": "violet",
        "description": "Hábitos e sequências",
    },
    {
        "id": "family",
        "label": "Família",
        "icon": "heart",
        "tint": "rose",
        "description": "Pessoas, aniversários e eventos",
    },
    {
        "id": "work",
        "label": "Trabalho",
        "icon": "briefcase",
        "tint": "indigo",
        "description": "Projetos e tarefas",
    },
]

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

_AUTH_WINDOW_SECONDS = 60.0
_AUTH_IP_LIMIT = 20
_AUTH_IDENTITY_LIMIT = 5
_ASK_WINDOW_SECONDS = 60.0
_ASK_USER_LIMIT = 30


class _AuthRateLimiter:
    """Small per-process guard against credential and signup brute force."""

    def __init__(self) -> None:
        self._events: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def allow(self, ip: str, identity: str) -> bool:
        now = time.monotonic()
        buckets = (
            (f"ip:{ip}", _AUTH_IP_LIMIT),
            (f"identity:{ip}:{identity}", _AUTH_IDENTITY_LIMIT),
        )
        with self._lock:
            if len(self._events) > 1024:
                self._events = {
                    key: active
                    for key, events in self._events.items()
                    if (
                        active := [
                            event
                            for event in events
                            if now - event < _AUTH_WINDOW_SECONDS
                        ]
                    )
                }
            for key, limit in buckets:
                recent = [
                    event
                    for event in self._events.get(key, [])
                    if now - event < _AUTH_WINDOW_SECONDS
                ]
                if len(recent) >= limit:
                    self._events[key] = recent
                    return False
            for key, _ in buckets:
                self._events.setdefault(key, []).append(now)
        return True

    def clear_identity(self, ip: str, identity: str) -> None:
        """Reset one credential bucket after a successful authentication."""
        with self._lock:
            self._events.pop(f"identity:{ip}:{identity}", None)


class _AskRateLimiter:
    """Bound paid inference per authenticated user in one server process."""

    def __init__(self) -> None:
        self._events: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def allow(self, user_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [
                event
                for event in self._events.get(user_id, [])
                if now - event < _ASK_WINDOW_SECONDS
            ]
            if len(recent) >= _ASK_USER_LIMIT:
                self._events[user_id] = recent
                return False
            recent.append(now)
            self._events[user_id] = recent
        return True


# -- Request/response models -------------------------------------------------


class RegisterRequest(BaseModel):
    """Payload to create a client account."""

    email: str
    password: str
    name: str = ""
    timezone: str = "America/Sao_Paulo"
    currency: str = "BRL"
    locale: str = "pt-BR"


class LoginRequest(BaseModel):
    """Payload to exchange credentials for a bearer token."""

    email: str
    password: str
    device: str = ""


class ProfileUpdate(BaseModel):
    """Mutable profile fields."""

    name: Optional[str] = None
    timezone: Optional[str] = None
    currency: Optional[str] = None
    locale: Optional[str] = None


class RecordPayload(BaseModel):
    """Arbitrary column/value pairs, validated against the schema by the store."""

    fields: Dict[str, Any] = Field(default_factory=dict)


class AskRequest(BaseModel):
    """A spoken or typed question for the assistant."""

    question: str
    model: str = ""


class ConfirmActionRequest(BaseModel):
    """Explicit approval for a pending Jarvis write."""

    confirmed: bool = False
    confirmation_method: Literal["explicit", "voice_explicit"] = "explicit"


class PayBillRequest(BaseModel):
    """Options when settling a bill."""

    account_id: str = ""
    paid_on: str = ""


class CompleteWorkoutRequest(BaseModel):
    """Options when finishing a workout."""

    duration_min: int = 0


class CheckHabitRequest(BaseModel):
    """Options when checking a habit in or out."""

    done_on: str = ""
    note: str = ""


# -- Router ------------------------------------------------------------------


def create_life_router(db_path: str = "") -> APIRouter:
    """Build the Life API router, optionally against a specific database."""
    router = APIRouter(prefix="/v1/life", tags=["life"])
    life: LifeContext = open_life(db_path or None)
    actions = JarvisActionStore(life)
    ai_budget = AiBudgetStore(life)
    auth_limiter = _AuthRateLimiter()
    ask_limiter = _AskRateLimiter()

    def guard_auth_attempt(request: Request, identity: str, scope: str) -> None:
        ip = request.client.host if request.client else "unknown"
        scoped_identity = f"{scope}:{identity.strip().lower()}"
        if not auth_limiter.allow(ip, scoped_identity):
            raise HTTPException(
                status_code=429,
                detail="Too many authentication attempts",
                headers={"Retry-After": str(int(_AUTH_WINDOW_SECONDS))},
            )

    def clear_auth_attempts(request: Request, identity: str, scope: str) -> None:
        ip = request.client.host if request.client else "unknown"
        scoped_identity = f"{scope}:{identity.strip().lower()}"
        auth_limiter.clear_identity(ip, scoped_identity)

    def current_user(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    ) -> User:
        """Resolve the bearer token to a client, or reject with 401."""
        if credentials is None or not credentials.credentials:
            raise HTTPException(status_code=401, detail="Missing bearer token")
        user_id = life.users.resolve_token(credentials.credentials)
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user = life.users.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Unknown user")
        return user

    # -- Identity ------------------------------------------------------------

    @router.post("/auth/register", status_code=201)
    async def register(body: RegisterRequest, request: Request) -> Dict[str, Any]:
        """Create a client account and return a session token.

        Open registration is off unless ``OPENJARVIS_LIFE_OPEN_SIGNUP=1``.
        Production bootstrap must therefore be an explicit operator decision,
        never a race won by the first remote visitor.
        """
        guard_auth_attempt(request, body.email, "register")
        open_signup = os.environ.get("OPENJARVIS_LIFE_OPEN_SIGNUP", "") == "1"
        if not open_signup:
            raise HTTPException(
                status_code=403, detail="Registration is closed on this server"
            )
        try:
            user = life.users.create_user(
                body.email,
                body.password,
                name=body.name,
                timezone_name=body.timezone,
                currency=body.currency,
                locale=body.locale,
            )
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        token = life.users.issue_token(user.id, label="signup")
        return {"token": token, "user": user.to_dict()}

    @router.post("/auth/login")
    async def login(body: LoginRequest, request: Request) -> Dict[str, Any]:
        """Exchange email and password for a bearer token.

        Throttled both per process/IP and persistently per address. The checks
        run before password verification and apply to unknown addresses too,
        so the 429 response cannot become a user-enumeration oracle.
        """
        guard_auth_attempt(request, body.email, "login")
        locked_for = life.users.seconds_until_unlocked(body.email)
        if locked_for > 0:
            raise HTTPException(
                status_code=429,
                detail="Muitas tentativas. Tente novamente em alguns minutos.",
                headers={"Retry-After": str(locked_for)},
            )

        user = life.users.authenticate(body.email, body.password)
        if user is None:
            life.users.record_login_failure(body.email)
            # One message for both failure modes — distinguishing them tells
            # an attacker which emails are registered.
            raise HTTPException(status_code=401, detail="Invalid credentials")

        life.users.clear_login_failures(body.email)
        clear_auth_attempts(request, body.email, "login")
        token = life.users.issue_token(user.id, label=body.device or "app")
        return {"token": token, "user": user.to_dict()}

    @router.post("/auth/logout")
    async def logout(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
        user: User = Depends(current_user),
    ) -> Dict[str, Any]:
        """Revoke the token used for this request."""
        revoked = False
        if credentials is not None:
            revoked = life.users.revoke_token(credentials.credentials)
        return {"revoked": revoked, "user_id": user.id}

    @router.get("/me")
    async def me(user: User = Depends(current_user)) -> Dict[str, Any]:
        """Return the authenticated client's profile and active sessions."""
        return {
            "user": user.to_dict(),
            "sessions": life.users.list_sessions(user.id),
        }

    @router.patch("/me")
    async def update_me(
        body: ProfileUpdate, user: User = Depends(current_user)
    ) -> Dict[str, Any]:
        """Update profile fields (name, timezone, currency, locale)."""
        updated = life.users.update_user(
            user.id,
            name=body.name,
            timezone=body.timezone,
            currency=body.currency,
            locale=body.locale,
        )
        return {"user": updated.to_dict() if updated else user.to_dict()}

    # -- Home ----------------------------------------------------------------

    @router.get("/apps")
    async def apps(user: User = Depends(current_user)) -> Dict[str, Any]:
        """Springboard manifest plus the badge counts to overlay on each icon."""
        briefing = _build_today(life, user)
        return {"apps": APP_MANIFEST, "badges": briefing["badges"]}

    @router.get("/today")
    async def today(user: User = Depends(current_user)) -> Dict[str, Any]:
        """The cross-domain Today briefing."""
        return _build_today(life, user)

    @router.get("/summary/{app}")
    async def summary(app: str, user: User = Depends(current_user)) -> Dict[str, Any]:
        """Per-app summary for the app's landing tab."""
        service = life.service
        anchor = today_in(user.timezone)
        if app == "finance":
            return service.finance_summary(user.id, anchor=anchor)
        if app == "fitness":
            return service.fitness_summary(user.id, anchor=anchor)
        if app == "routine":
            return service.routine_summary(user.id, anchor=anchor)
        if app == "family":
            return {"upcoming": service.upcoming_family(user.id, anchor=anchor)}
        if app == "work":
            return service.work_summary(user.id, anchor=anchor)
        raise HTTPException(status_code=404, detail=f"Unknown app: {app}")

    @router.post("/ask")
    async def ask(
        body: AskRequest,
        request: Request,
        user: User = Depends(current_user),
    ) -> Dict[str, Any]:
        """Answer a question grounded in *this client's* life data.

        The model receives tenant-bound read tools. Write tools persist pending
        proposals and cannot mutate data until a later authenticated request
        explicitly confirms one.
        """
        question = body.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="Empty question")
        if not ask_limiter.allow(user.id):
            raise HTTPException(
                status_code=429,
                detail="Too many assistant requests",
                headers={"Retry-After": str(int(_ASK_WINDOW_SECONDS))},
            )

        briefing = _build_voice_today(life, user)
        context = _life_context(briefing, user)
        engine = getattr(request.app.state, "engine", None)

        if engine is None:
            return {
                "answer": _fallback_answer(briefing, user, question),
                "source": "data",
                "context": briefing,
            }

        model = body.model or getattr(
            getattr(request.app.state, "config", None), "model", ""
        )
        try:
            from starlette.concurrency import run_in_threadpool

            runtime = JarvisRuntime(life, user.id, engine, str(model) or "default")
            result = await run_in_threadpool(
                runtime.run,
                question,
                _ASK_SYSTEM_PROMPT.format(context=context),
            )
            answer = str(result["answer"]).strip()
        except AiBudgetExceededError as exc:
            logger.warning("Life ask budget gate: %s", exc)
            return {
                "answer": _fallback_answer(briefing, user),
                "source": "data",
                "degraded_reason": "monthly_ai_budget",
                "budget": ai_budget.snapshot(),
                "context": briefing,
                "proposals": [],
            }
        except Exception as exc:  # noqa: BLE001 — degrade, never 500 the mic
            logger.warning("Life ask failed, serving data answer: %s", exc)
            return {
                "answer": _fallback_answer(briefing, user, question),
                "source": "data",
                "context": briefing,
            }

        return {
            "answer": answer or _fallback_answer(briefing, user, question),
            "source": "model" if answer else "data",
            "context": briefing,
            "proposals": result["proposals"],
            "usage": result["usage"],
            "budget": result["budget"],
        }

    @router.get("/ai-budget")
    async def ai_budget_status(user: User = Depends(current_user)) -> Dict[str, Any]:
        """Return the global monthly remote-inference cap and accounted usage."""
        return {"user_id": user.id, **ai_budget.snapshot()}

    @router.get("/actions/pending")
    async def pending_actions(user: User = Depends(current_user)) -> Dict[str, Any]:
        """List this client's unexpired actions awaiting confirmation."""
        pending = actions.list_pending(user.id)
        return {"count": len(pending), "proposals": pending}

    @router.post("/actions/{proposal_id}/confirm")
    async def confirm_action(
        proposal_id: str,
        body: ConfirmActionRequest,
        user: User = Depends(current_user),
    ) -> Dict[str, Any]:
        """Execute one pending action exactly once after explicit approval."""
        if not body.confirmed:
            raise HTTPException(
                status_code=400, detail="Explicit confirmation required"
            )
        try:
            return actions.confirm(
                user.id,
                proposal_id,
                confirmation_method=body.confirmation_method,
            )
        except JarvisActionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/actions/{proposal_id}/cancel")
    async def cancel_action(
        proposal_id: str,
        user: User = Depends(current_user),
    ) -> Dict[str, Any]:
        """Cancel one pending action without executing it."""
        try:
            return {"proposal": actions.cancel(user.id, proposal_id)}
        except JarvisActionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # -- Generic CRUD --------------------------------------------------------

    @router.get("/records/{table}")
    async def list_records(
        table: str,
        request: Request,
        order_by: str = Query("created_at"),
        desc: bool = Query(True),
        limit: int = Query(200, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        user: User = Depends(current_user),
    ) -> Dict[str, Any]:
        """List rows of ``table`` for the authenticated client.

        Any other query parameter becomes a filter: ``?status=pending`` for
        equality, ``?due_on__lte=2026-08-31`` for comparisons.
        """
        _require_table(table)
        filters = _filters_from_query(dict(request.query_params))
        try:
            rows = life.store.list_records(
                table,
                user.id,
                filters=filters,
                order_by=order_by,
                descending=desc,
                limit=limit,
                offset=offset,
            )
        except LifeStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"table": table, "count": len(rows), "records": rows}

    @router.post("/records/{table}", status_code=201)
    async def create_record(
        table: str, body: RecordPayload, user: User = Depends(current_user)
    ) -> Dict[str, Any]:
        """Create a row in ``table`` for the authenticated client."""
        _require_table(table)
        try:
            if table == "transactions":
                _require_known_fields(table, body.fields)
                record = life.service.add_transaction(
                    user.id,
                    amount_cents=int(body.fields.get("amount_cents", 0)),
                    kind=str(body.fields.get("kind", "expense")),
                    category=str(body.fields.get("category", "outros")),
                    description=str(body.fields.get("description", "")),
                    occurred_on=str(body.fields.get("occurred_on", "")),
                    account_id=str(body.fields.get("account_id", "")),
                    source="manual",
                )
                return {"record": record}
            record_id = life.store.insert(table, user.id, body.fields)
        except (LifeServiceError, LifeStoreError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"record": life.store.get(table, user.id, record_id)}

    @router.get("/records/{table}/{record_id}")
    async def get_record(
        table: str, record_id: str, user: User = Depends(current_user)
    ) -> Dict[str, Any]:
        """Fetch one row by id."""
        _require_table(table)
        record = life.store.get(table, user.id, record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Record not found")
        return {"record": record}

    @router.patch("/records/{table}/{record_id}")
    async def update_record(
        table: str,
        record_id: str,
        body: RecordPayload,
        user: User = Depends(current_user),
    ) -> Dict[str, Any]:
        """Patch a row. 404 when it does not belong to this client."""
        _require_table(table)
        protected = _ACTION_OWNED_PATCH_FIELDS.get(table, frozenset())
        bypassed = sorted(protected.intersection(body.fields))
        if bypassed:
            raise HTTPException(
                status_code=409,
                detail=("Fields require a domain action: " + ", ".join(bypassed)),
            )
        try:
            ok = life.store.update(table, user.id, record_id, body.fields)
        except LifeStoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not ok:
            raise HTTPException(status_code=404, detail="Record not found")
        return {"record": life.store.get(table, user.id, record_id)}

    @router.delete("/records/{table}/{record_id}")
    async def delete_record(
        table: str, record_id: str, user: User = Depends(current_user)
    ) -> Dict[str, Any]:
        """Delete a row."""
        _require_table(table)
        if table == "transactions":
            raise HTTPException(
                status_code=409,
                detail="Transactions require a reversal action and cannot be deleted",
            )
        if not life.store.delete(table, user.id, record_id):
            raise HTTPException(status_code=404, detail="Record not found")
        return {"deleted": record_id}

    # -- Domain actions ------------------------------------------------------

    @router.post("/actions/pay-bill/{bill_id}")
    async def pay_bill(
        bill_id: str,
        body: PayBillRequest,
        user: User = Depends(current_user),
    ) -> Dict[str, Any]:
        """Settle a bill: mark paid, book the expense, roll recurrence forward."""
        try:
            return life.service.pay_bill(
                user.id,
                bill_id,
                account_id=body.account_id,
                paid_on=body.paid_on,
            )
        except LifeServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/actions/complete-workout/{workout_id}")
    async def complete_workout(
        workout_id: str,
        body: CompleteWorkoutRequest,
        user: User = Depends(current_user),
    ) -> Dict[str, Any]:
        """Mark a workout as completed."""
        try:
            workout = life.service.complete_workout(
                user.id, workout_id, duration_min=body.duration_min
            )
        except LifeServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"workout": workout}

    @router.post("/actions/check-habit/{habit_id}")
    async def check_habit(
        habit_id: str,
        body: CheckHabitRequest,
        user: User = Depends(current_user),
    ) -> Dict[str, Any]:
        """Check a habit in for a day. Idempotent."""
        try:
            result = life.service.check_in_habit(
                user.id, habit_id, done_on=body.done_on, note=body.note
            )
        except LifeServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            **result,
            "streak": life.service.habit_streak(user.id, habit_id),
        }

    @router.delete("/actions/check-habit/{habit_id}")
    async def uncheck_habit(
        habit_id: str,
        done_on: str = Query(""),
        user: User = Depends(current_user),
    ) -> Dict[str, Any]:
        """Undo a habit check-in — the inevitable mis-tap."""
        removed = life.service.undo_habit_check_in(user.id, habit_id, done_on=done_on)
        return {
            "removed": removed,
            "streak": life.service.habit_streak(user.id, habit_id),
        }

    @router.post("/actions/complete-task/{task_id}")
    async def complete_task(
        task_id: str, user: User = Depends(current_user)
    ) -> Dict[str, Any]:
        """Mark a work task as done."""
        try:
            task = life.service.complete_task(user.id, task_id)
        except LifeServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"task": task}

    # O hub de integrações compartilha o mesmo contexto (e o mesmo bearer de
    # usuário), mas vive num módulo próprio para o router principal não crescer.
    router.include_router(create_integrations_router(life))
    router.include_router(create_voice_router(life))

    # Exposed so the app can hand the same context to agent tools (and so
    # tests can seed data without reaching for the database path).
    router.life_context = life  # type: ignore[attr-defined]
    return router


# -- Helpers -----------------------------------------------------------------


def _require_table(table: str) -> None:
    """Reject tables that are not part of the Life schema."""
    if table not in SCHEMA:
        raise HTTPException(status_code=404, detail=f"Unknown table: {table}")


def _require_known_fields(table: str, fields: Dict[str, Any]) -> None:
    """Validate fields before a domain service replaces generic insertion."""
    unknown = sorted(set(fields).difference(SCHEMA[table].columns))
    if unknown:
        raise LifeStoreError(f"Unknown column {unknown[0]!r} on table {table!r}")


def _filters_from_query(params: Dict[str, str]) -> List[Filter]:
    """Translate query parameters into store filters.

    Unknown columns are not silently ignored — the store validates them and
    raises, which the caller turns into a 400. A typo'd filter that quietly
    returns everything is how clients ship bugs to production.
    """
    filters: List[Filter] = []
    for key, value in params.items():
        if key in _CONTROL_PARAMS:
            continue
        for suffix, op in _OP_SUFFIXES.items():
            if key.endswith(suffix):
                filters.append(Filter(key[: -len(suffix)], op, value))
                break
        else:
            filters.append(Filter(key, "=", value))
    return filters


def _life_context(briefing: Dict[str, Any], user: User) -> str:
    """Condense the briefing into a compact block for the system prompt.

    Kept terse on purpose: this rides on every spoken question, so verbosity
    here is a latency and cost tax paid on each one.
    """
    finance = briefing["finance"]
    fitness = briefing["fitness"]
    routine = briefing["routine"]
    work = briefing["work"]
    currency = user.currency

    lines = [
        f"Cliente: {user.name} | Data: {briefing['date']}",
        f"Saldo: {_money(finance['balance_cents'], currency)}"
        f" | Entrou no mês: {_money(finance['income_cents'], currency)}"
        f" | Saiu: {_money(finance['expense_cents'], currency)}",
        f"Contas vencidas: {finance['overdue_count']}"
        f" | vencendo em breve: {finance['due_soon_count']}",
        f"Treinos na semana: {fitness['week_completed']}/{fitness['week_planned']}"
        f" | dias sem treinar: {fitness['days_since_last']}",
        f"Hábitos hoje: {routine['completed']}/{routine['total']}"
        + (
            f" | pendentes: {', '.join(routine['pending'])}"
            if routine["pending"]
            else ""
        ),
        f"Tarefas abertas: {work['open_count']}"
        f" | atrasadas: {work['overdue_count']}"
        f" | para hoje: {work['due_today_count']}",
    ]
    if briefing["family"]["upcoming"]:
        events = "; ".join(
            f"{e['title']} ({e['date']})" for e in briefing["family"]["upcoming"][:3]
        )
        lines.append(f"Família: {events}")
    if briefing["alerts"]:
        alerts = "; ".join(
            f"[{a['severity']}] {a['title']}" for a in briefing["alerts"][:6]
        )
        lines.append(f"Alertas: {alerts}")
    return "\n".join(lines)


#: Keyword → topic, for answering without a model. Deliberately small: this is
#: a fallback, not an intent classifier. Anything it cannot place falls through
#: to the briefing, which is a useful answer to almost any question.
_TOPIC_KEYWORDS = {
    "finance": (
        "gast",
        "gasto",
        "saldo",
        "dinheiro",
        "mês",
        "mes",
        "conta",
        "boleto",
        "vence",
        "vencendo",
        "orçamento",
        "orcamento",
        "pagar",
        "fatura",
        "receb",
        "entrou",
        "saiu",
    ),
    "fitness": ("treino", "treinar", "academia", "peso", "malh", "exerc"),
    "routine": ("hábito", "habito", "rotina", "sequência", "sequencia"),
    "family": ("família", "familia", "aniversário", "aniversario", "filh", "esposa"),
    "work": ("tarefa", "trabalho", "projeto", "prazo", "entrega"),
}


#: Within finance, these ask about specific obligations rather than totals.
_BILL_KEYWORDS = (
    "vence",
    "vencendo",
    "vencida",
    "boleto",
    "conta",
    "pagar",
    "fatura",
    "atrasad",
    "devendo",
)


def _asks_about_bills(question: str) -> bool:
    """Whether a money question is about *which* bills, not the totals."""
    lowered = question.lower()
    return any(keyword in lowered for keyword in _BILL_KEYWORDS)


def _detect_topic(question: str) -> str:
    """Best-guess topic for a question. Empty string when nothing matches."""
    lowered = question.lower()
    for topic, keywords in _TOPIC_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return topic
    return ""


def _fallback_answer(briefing: Dict[str, Any], user: User, question: str = "") -> str:
    """Answer from the data alone, for when no model is available.

    Routed by topic rather than always reciting the top alert. Without this,
    "como está meu mês?" and "o que vence hoje?" get the same sentence, which
    reads as broken even though the data behind it is right — and the voice
    screen is where that lands hardest, because the client hears it.
    """
    currency = user.currency
    finance = briefing["finance"]
    balance = _money(finance["balance_cents"], currency)
    topic = _detect_topic(question)

    if topic == "finance":
        # "O que tá vencendo?" wants the bills *named*. A summary that says
        # "1 conta vencida" is a worse answer than the name of the bill, and
        # spoken aloud it forces a second question.
        if _asks_about_bills(question):
            bills = [
                alert
                for alert in briefing["alerts"]
                if alert["app"] == "finance" and alert["action"] == "pay_bill"
            ]
            if not bills:
                return f"Nenhuma conta vencendo por perto. Seu saldo é {balance}."
            named = "; ".join(
                f"{bill['title']}"
                + (
                    f" ({_money(int(bill['amount_cents']), currency)})"
                    if bill["amount_cents"]
                    else ""
                )
                for bill in bills[:4]
            )
            more = len(bills) - 4
            tail = f" E mais {more}." if more > 0 else ""
            return f"{named}.{tail}"

        overdue = finance["overdue_count"]
        soon = finance["due_soon_count"]
        parts = [
            f"Seu saldo é {balance}.",
            f"No mês entraram {_money(finance['income_cents'], currency)}"
            f" e saíram {_money(finance['expense_cents'], currency)}.",
        ]
        if overdue:
            parts.append(f"Você tem {overdue} conta(s) vencida(s).")
        elif soon:
            parts.append(f"{soon} conta(s) vencem nos próximos dias.")
        else:
            parts.append("Nenhuma conta pendente por perto.")
        return " ".join(parts)

    if topic == "fitness":
        fitness = briefing["fitness"]
        done = fitness["week_completed"]
        planned = fitness["week_planned"]
        since = fitness["days_since_last"]
        tail = (
            "Você ainda não registrou nenhum treino."
            if since is None
            else f"Seu último treino foi há {since} dia(s)."
        )
        todays = fitness.get("todays_workout")
        head = f"Hoje tem {todays['name']}. " if todays else ""
        return f"{head}Nesta semana você fez {done} de {planned} treinos. {tail}"

    if topic == "routine":
        routine = briefing["routine"]
        if not routine["total"]:
            return "Você ainda não cadastrou hábitos."
        pending = routine["pending"]
        if not pending:
            return f"Todos os {routine['total']} hábitos de hoje estão feitos."
        return (
            f"Você fez {routine['completed']} de {routine['total']} hábitos hoje."
            f" Faltam: {', '.join(pending)}."
        )

    if topic == "family":
        upcoming = briefing["family"]["upcoming"]
        if not upcoming:
            return "Nada marcado com a família nos próximos dias."
        first = upcoming[0]
        when = "hoje" if first["days_away"] == 0 else f"em {first['days_away']} dia(s)"
        return f"{first['title']} {when}, no dia {first['date']}."

    if topic == "work":
        work = briefing["work"]
        if not work["open_count"]:
            return "Nenhuma tarefa em aberto."
        return (
            f"Você tem {work['open_count']} tarefa(s) em aberto,"
            f" {work['overdue_count']} atrasada(s)"
            f" e {work['due_today_count']} para hoje."
        )

    alerts = briefing["alerts"]
    if not alerts:
        return f"Tudo em dia. Seu saldo é {balance} e nada precisa de você agora."
    head = alerts[0]
    remaining = len(alerts) - 1
    tail = f" E mais {remaining} item(ns) pedindo atenção." if remaining else ""
    return f"{head['title']}. {head['detail']}. Saldo: {balance}.{tail}"


_ASK_SYSTEM_PROMPT = (
    "Você é o Jarvis, a interface central de voz e o cérebro orquestrador "
    "pessoal deste cliente. Você incorpora cinco especialistas: chefe de gabinete "
    "para rotina e calendário, coach para treinos e hábitos, diretor financeiro "
    "para orçamento e contas, assistente executivo para trabalho e projetos, e "
    "organizador da vida familiar. Responda em português do Brasil, em no máximo "
    "3 frases curtas e naturais, porque a resposta será lida em voz alta. "
    "Quando o cliente pedir para criar, registrar, planejar, concluir ou organizar "
    "algo, nunca o mande preencher uma tela: consulte os dados conhecidos e use as "
    "ferramentas para preparar a ação. Se faltar um dado obrigatório, faça somente "
    "uma pergunta objetiva por vez; assim que houver dados suficientes, prepare a "
    "proposta sem pedir que ele repita a solicitação. Ferramentas de escrita criam "
    "apenas propostas: descreva o que será feito, diga que aguarda confirmação por "
    "voz ou botão e nunca afirme que já foi executado. Não efetue transferências "
    "bancárias nem decisões médicas; registre, organize e recomende dentro dos "
    "dados e ferramentas disponíveis. Se a resposta não estiver nos dados ou nas "
    "ferramentas, diga que ainda não tem esse dado. Nunca invente valores.\n\n"
    "DADOS DO CLIENTE:\n{context}"
)


def _build_today(life: LifeContext, user: User) -> Dict[str, Any]:
    """Assemble the Today briefing using the client's local clock."""
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415 — optional tzdata

        now_hour = datetime.now(ZoneInfo(user.timezone)).hour
    except Exception:
        now_hour = datetime.now().hour
    return life.today(user, anchor=today_in(user.timezone), now_hour=now_hour)


def _build_voice_today(life: LifeContext, user: User) -> Dict[str, Any]:
    """Assemble the low-round-trip briefing for the spoken assistant."""
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415 — optional tzdata

        now_hour = datetime.now(ZoneInfo(user.timezone)).hour
    except Exception:
        now_hour = datetime.now().hour
    return life.voice_today(user, anchor=today_in(user.timezone), now_hour=now_hour)


def app_ids() -> List[str]:
    """Ids of every springboard app — used by tests and the client bundle."""
    return [entry["id"] for entry in APP_MANIFEST if entry["id"] in APPS]
