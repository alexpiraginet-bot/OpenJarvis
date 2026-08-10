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
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from openjarvis.life import LifeContext, open_life
from openjarvis.life.schema import APPS, SCHEMA
from openjarvis.life.service import LifeServiceError, today_in
from openjarvis.life.store import Filter, LifeStoreError
from openjarvis.life.tenancy import AuthError, User

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
    async def register(body: RegisterRequest) -> Dict[str, Any]:
        """Create a client account and return a session token.

        Open registration is off unless ``OPENJARVIS_LIFE_OPEN_SIGNUP=1``. The
        sole exception is the very first account on an empty database, so a
        fresh deployment can be bootstrapped without shell access.
        """
        open_signup = os.environ.get("OPENJARVIS_LIFE_OPEN_SIGNUP", "") == "1"
        if not open_signup and life.users.count_users() > 0:
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
    async def login(body: LoginRequest) -> Dict[str, Any]:
        """Exchange email and password for a bearer token.

        Throttled per address. The check runs *before* the password is
        verified, and applies to unregistered addresses too — throttling only
        real accounts would make the 429 itself a user-enumeration oracle.
        """
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

        The client's data is injected as context rather than left for the model
        to fetch, because the voice screen needs one round trip: a tool-calling
        loop would add seconds of silence to every spoken question.

        When no inference engine is wired, this still answers — from the data
        alone — and says so via ``source``. A voice assistant that goes mute
        because the model is missing is worse than one that reads out the
        numbers it already has.
        """
        question = body.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="Empty question")

        briefing = _build_today(life, user)
        context = _life_context(briefing, user)
        engine = getattr(request.app.state, "engine", None)

        if engine is None:
            return {
                "answer": _fallback_answer(briefing, user),
                "source": "data",
                "context": briefing,
            }

        from openjarvis.core.types import Message, Role

        messages = [
            Message(
                role=Role.SYSTEM,
                content=_ASK_SYSTEM_PROMPT.format(context=context),
            ),
            Message(role=Role.USER, content=question),
        ]
        model = body.model or getattr(
            getattr(request.app.state, "config", None), "model", ""
        )
        try:
            from starlette.concurrency import run_in_threadpool

            # engine.generate is blocking; off the event loop it goes, or one
            # slow local model stalls every other request on this worker.
            result = await run_in_threadpool(
                lambda: engine.generate(
                    messages,
                    model=str(model) or "default",
                    temperature=0.3,
                    max_tokens=400,
                )
            )
            answer = str(result.get("content", "")).strip()
        except Exception as exc:  # noqa: BLE001 — degrade, never 500 the mic
            logger.warning("Life ask failed, serving data answer: %s", exc)
            return {
                "answer": _fallback_answer(briefing, user),
                "source": "data",
                "context": briefing,
            }

        return {
            "answer": answer or _fallback_answer(briefing, user),
            "source": "model" if answer else "data",
            "context": briefing,
        }

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
            record_id = life.store.insert(table, user.id, body.fields)
        except LifeStoreError as exc:
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

    # Exposed so the app can hand the same context to agent tools (and so
    # tests can seed data without reaching for the database path).
    router.life_context = life  # type: ignore[attr-defined]
    return router


# -- Helpers -----------------------------------------------------------------


def _require_table(table: str) -> None:
    """Reject tables that are not part of the Life schema."""
    if table not in SCHEMA:
        raise HTTPException(status_code=404, detail=f"Unknown table: {table}")


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


def _money(cents: int, currency: str) -> str:
    """Format integer cents for a spoken answer."""
    symbol = {"BRL": "R$", "USD": "$", "EUR": "€"}.get(currency, currency + " ")
    return f"{symbol}{cents / 100:,.2f}"


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


def _fallback_answer(briefing: Dict[str, Any], user: User) -> str:
    """Answer from the data alone, for when no model is available."""
    alerts = briefing["alerts"]
    balance = _money(briefing["finance"]["balance_cents"], user.currency)
    if not alerts:
        return f"Tudo em dia. Seu saldo é {balance} e nada precisa de você agora."
    head = alerts[0]
    remaining = len(alerts) - 1
    tail = f" E mais {remaining} item(ns) pedindo atenção." if remaining else ""
    return f"{head['title']}. {head['detail']}. Saldo: {balance}.{tail}"


_ASK_SYSTEM_PROMPT = (
    "Você é o Jarvis, assistente pessoal deste cliente. Responda em português "
    "do Brasil, em no máximo 3 frases curtas, em tom natural de fala — a "
    "resposta será lida em voz alta. Use apenas os dados abaixo; se a resposta "
    "não estiver neles, diga que não tem esse dado ainda. Nunca invente "
    "valores.\n\nDADOS DO CLIENTE:\n{context}"
)


def _build_today(life: LifeContext, user: User) -> Dict[str, Any]:
    """Assemble the Today briefing using the client's local clock."""
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415 — optional tzdata

        now_hour = datetime.now(ZoneInfo(user.timezone)).hour
    except Exception:
        now_hour = datetime.now().hour
    return life.today(user, anchor=today_in(user.timezone), now_hour=now_hour)


def app_ids() -> List[str]:
    """Ids of every springboard app — used by tests and the client bundle."""
    return [entry["id"] for entry in APP_MANIFEST if entry["id"] in APPS]
