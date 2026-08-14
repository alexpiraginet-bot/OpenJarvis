"""Jarvis OS runtime with durable, tenant-bound action proposals.

The model may read Life data during a turn. Write tools are replaced by
proposal tools: they persist an auditable intent and return it to the model,
but cannot execute it. A separate authenticated endpoint confirms the
proposal and runs the original tenant-bound tool exactly once.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

from openjarvis.agents._stubs import AgentContext, AgentResult
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import Conversation, Message, Role, ToolResult
from openjarvis.life import LifeContext
from openjarvis.life.ai_budget import AiBudgetStore, BudgetedEngine
from openjarvis.life.db import POSTGRES
from openjarvis.life.dialogue import MAX_HISTORY_MESSAGES, pending_prompt
from openjarvis.life.integrations import IntegrationsStore
from openjarvis.life.schema import SCHEMA
from openjarvis.life.tools import (
    life_record_app,
    life_tools_for,
    normalize_life_record_fields,
)
from openjarvis.tools._stubs import BaseTool, ToolSpec

ACTION_TTL_MINUTES = 15
NATIVE_CLAIM_TTL_SECONDS = 120
_WRITABLE_TOOLS = frozenset({"life_record", "life_complete"})
_NATIVE_ACTION_TOOLS = frozenset({"calendar_create"})
_PROPOSABLE_TOOLS = _WRITABLE_TOOLS | _NATIVE_ACTION_TOOLS
_TARGET_SNAPSHOT_KEY = "_target_snapshot"
_CALENDAR_EVENT_MAX_DURATION = timedelta(days=31)
_COMPLETION_TABLES = {
    "bill": "bills",
    "workout": "workouts",
    "habit": "habits",
    "task": "work_tasks",
}


class JarvisActionError(RuntimeError):
    """Raised when an action proposal cannot change state safely."""


class _InteractiveLifeEngine:
    """Tune GPT-5 calls for a short, spoken interaction loop."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.engine_id = getattr(delegate, "engine_id", "")

    def generate(self, messages: List[Message], **kwargs: Any) -> Dict[str, Any]:
        model = str(kwargs.get("model", "")).lower()
        if model.startswith("gpt-5.6"):
            kwargs.setdefault("reasoning_effort", "none")
            kwargs.setdefault("verbosity", "low")
        elif model.startswith("gpt-5"):
            kwargs.setdefault("reasoning_effort", "minimal")
            kwargs.setdefault("verbosity", "low")
        return self._delegate.generate(messages, **kwargs)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _decode_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _claim_token_hash(claim_token: str) -> str:
    return hashlib.sha256(claim_token.encode("utf-8")).hexdigest()


def _validated_claim_token(claim_token: str) -> str:
    value = claim_token.strip() if isinstance(claim_token, str) else ""
    allowed = frozenset(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    )
    if not 32 <= len(value) <= 128 or any(char not in allowed for char in value):
        raise JarvisActionError("Native claim token is invalid")
    return value


def _action_dict(row: Any) -> Dict[str, Any]:
    payload = dict(row)
    payload["arguments"] = _decode_json(payload.pop("arguments_json"), {})
    payload["result"] = _decode_json(payload.pop("result_json"), None)
    return payload


def _format_money(amount_cents: Any, currency: str) -> str | None:
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
        return None
    sign = "-" if amount_cents < 0 else ""
    absolute = abs(amount_cents)
    whole, cents = divmod(absolute, 100)
    grouped = f"{whole:,}".replace(",", ".")
    unit = "R$" if currency.upper() == "BRL" else currency.upper()
    return f"{unit} {sign}{grouped},{cents:02d}"


def _summary(
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    currency: str = "BRL",
) -> str:
    if tool_name == "calendar_create":
        start_at = datetime.fromisoformat(str(arguments["start_at"]))
        when = start_at.strftime("%d/%m/%Y")
        if not arguments.get("is_all_day", False):
            when += start_at.strftime(" às %H:%M")
        return f"Confirmar evento no Calendário: {arguments['title']} em {when}"

    kind = str(arguments.get("kind", "ação"))
    labels = {
        "expense": "gasto",
        "income": "entrada",
        "bill": "conta",
        "account": "conta bancária",
        "budget": "orçamento",
        "goal": "meta",
        "workout": "treino",
        "exercise_set": "série",
        "measurement": "medição",
        "training_profile": "perfil de treino",
        "training_plan": "plano de treino",
        "training_checkin": "check-in de prontidão",
        "training_feedback": "feedback de treino",
        "habit": "hábito",
        "family_member": "familiar",
        "family_event": "evento familiar",
        "project": "projeto",
        "task": "tarefa",
        "health_profile": "perfil de saúde",
        "condition": "condição de saúde",
        "medication": "medicamento",
        "allergy": "alergia",
        "health_observation": "observação de saúde",
        "hydration": "registro de hidratação",
        "nutrition": "registro de alimentação",
        "health_document": "documento de saúde",
    }
    label_kind = labels.get(kind, kind)
    if tool_name == "life_complete":
        action = {
            "bill": "pagamento da conta",
            "habit": "check-in do hábito",
            "workout": "conclusão do treino",
            "task": "conclusão da tarefa",
        }.get(kind, f"conclusão de {label_kind}")
        snapshot = arguments.get(_TARGET_SNAPSHOT_KEY) or {}
        record = snapshot.get("record") if isinstance(snapshot, dict) else {}
        record = record if isinstance(record, dict) else {}
        label = record.get("name") or record.get("title")
        amount = _format_money(record.get("amount_cents"), currency)
        detail = f" de {amount}" if amount else ""
        return f"Confirmar {action}{detail}" + (f": {label}" if label else "")
    fields = arguments.get("fields") or {}
    label = fields.get("name") or fields.get("title") or fields.get("description")
    amount = _format_money(fields.get("amount_cents"), currency)
    detail = f" de {amount}" if amount else ""
    return f"Confirmar novo {label_kind}{detail}" + (f": {label}" if label else "")


def _record_snapshot(table: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Capture every domain field that can affect a completion action."""
    return {key: record.get(key) for key in ("id", *SCHEMA[table].columns)}


def action_requires_authenticated_app(
    tool_name: str,
    arguments: Mapping[str, Any],
) -> bool:
    """Require the authenticated app for every action owned by Finance."""
    kind = arguments.get("kind")
    if not isinstance(kind, str):
        return False
    if tool_name == "life_record":
        return life_record_app(kind) == "finance"
    if tool_name == "life_complete":
        table = _COMPLETION_TABLES.get(kind)
        return table is not None and SCHEMA[table].app == "finance"
    return False


def _prepare_arguments(
    life: LifeContext,
    user_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """Normalize proposal input and bind completion intent to current state."""
    if not isinstance(arguments, dict):
        raise JarvisActionError("Action arguments must be an object")

    prepared = dict(arguments)
    prepared.pop(_TARGET_SNAPSHOT_KEY, None)
    if tool_name == "calendar_create":
        return _prepare_calendar_arguments(prepared)
    if tool_name == "life_record":
        kind = prepared.get("kind", "")
        try:
            prepared["fields"] = normalize_life_record_fields(
                kind, prepared.get("fields")
            )
        except ValueError as exc:
            raise JarvisActionError(str(exc)) from exc
        return prepared

    kind = prepared.get("kind", "")
    table = _COMPLETION_TABLES.get(kind)
    if table is None:
        raise JarvisActionError(f"Unknown kind: {kind}")
    record_id = prepared.get("record_id")
    if not isinstance(record_id, str) or not record_id:
        raise JarvisActionError("record_id must be a non-empty string")
    record = life.store.get(table, user_id, record_id)
    if record is None:
        raise JarvisActionError(f"Target record not found: {record_id}")
    prepared[_TARGET_SNAPSHOT_KEY] = {
        "table": table,
        "record": _record_snapshot(table, record),
    }
    return prepared


def _parse_calendar_datetime(field: str, value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise JarvisActionError(f"{field} must be an ISO 8601 date-time with timezone")
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise JarvisActionError(
            f"{field} must be an ISO 8601 date-time with timezone"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise JarvisActionError(f"{field} must be an ISO 8601 date-time with timezone")
    return parsed


def _prepare_calendar_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize a native EventKit proposal before persisting it."""
    allowed = {
        "title",
        "start_at",
        "end_at",
        "is_all_day",
        "location",
        "notes",
    }
    unexpected = sorted(set(arguments) - allowed)
    if unexpected:
        raise JarvisActionError(f"Unknown calendar fields: {', '.join(unexpected)}")

    title = arguments.get("title")
    if not isinstance(title, str) or not title.strip():
        raise JarvisActionError("title must be a non-empty string")
    title = title.strip()
    if len(title) > 160:
        raise JarvisActionError("title must be at most 160 characters")

    start_at = _parse_calendar_datetime("start_at", arguments.get("start_at"))
    end_at = _parse_calendar_datetime("end_at", arguments.get("end_at"))
    if end_at <= start_at:
        raise JarvisActionError("end_at must be after start_at")
    if end_at - start_at > _CALENDAR_EVENT_MAX_DURATION:
        raise JarvisActionError("calendar event cannot last more than 31 days")

    is_all_day = arguments.get("is_all_day", False)
    if not isinstance(is_all_day, bool):
        raise JarvisActionError("is_all_day must be a boolean")
    if is_all_day:
        if any(
            (value.hour, value.minute, value.second, value.microsecond) != (0, 0, 0, 0)
            for value in (start_at, end_at)
        ):
            raise JarvisActionError("all-day events must start and end at midnight")

    prepared: Dict[str, Any] = {
        "title": title,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "is_all_day": is_all_day,
    }
    for field, maximum in (("location", 200), ("notes", 1000)):
        value = arguments.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise JarvisActionError(f"{field} must be a string")
        value = value.strip()
        if len(value) > maximum:
            raise JarvisActionError(f"{field} must be at most {maximum} characters")
        if value:
            prepared[field] = value
    return prepared


def _validate_native_calendar_result(
    arguments: Dict[str, Any], result: Dict[str, Any]
) -> None:
    """Bind an EventKit receipt to the exact proposal the user approved."""
    event = result.get("event")
    if not isinstance(event, dict):
        raise JarvisActionError("Native calendar result must contain an event")
    event_id = event.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise JarvisActionError("Native calendar result has no event id")
    if event.get("title") != arguments.get("title"):
        raise JarvisActionError("Native calendar result does not match the proposal")

    start_value = event.get("startAt", event.get("start_at"))
    end_value = event.get("endAt", event.get("end_at"))
    if _parse_calendar_datetime("event.start_at", start_value) != (
        _parse_calendar_datetime("start_at", arguments.get("start_at"))
    ):
        raise JarvisActionError("Native calendar result does not match the proposal")
    if _parse_calendar_datetime("event.end_at", end_value) != (
        _parse_calendar_datetime("end_at", arguments.get("end_at"))
    ):
        raise JarvisActionError("Native calendar result does not match the proposal")

    event_all_day = event.get("isAllDay", event.get("is_all_day", False))
    if event_all_day is not arguments.get("is_all_day", False):
        raise JarvisActionError("Native calendar result does not match the proposal")
    expected_location = arguments.get("location")
    if expected_location is not None and event.get("location") != expected_location:
        raise JarvisActionError("Native calendar result does not match the proposal")


def _validate_target_snapshot(
    life: LifeContext,
    user_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> None:
    """Reject stale or tampered completion proposals before side effects."""
    if tool_name != "life_complete":
        return
    kind = arguments.get("kind", "")
    table = _COMPLETION_TABLES.get(kind)
    snapshot = arguments.get(_TARGET_SNAPSHOT_KEY)
    if table is None or not isinstance(snapshot, dict):
        raise JarvisActionError("Completion proposal has no valid target snapshot")
    expected = snapshot.get("record")
    if snapshot.get("table") != table or not isinstance(expected, dict):
        raise JarvisActionError("Completion proposal has no valid target snapshot")
    record_id = arguments.get("record_id")
    lock_clause = " FOR UPDATE" if life.connection.backend == POSTGRES else ""
    row = life.connection.execute(
        f"SELECT * FROM {table} WHERE id = ? AND user_id = ?{lock_clause}",
        (record_id, user_id),
    ).fetchone()
    if row is None:
        raise JarvisActionError("Action target no longer exists")
    current = dict(row)
    if _record_snapshot(table, current) != expected:
        raise JarvisActionError("Action target changed after proposal creation")


class JarvisActionStore:
    """Persistence and exactly-once confirmation for model-proposed writes."""

    def __init__(self, life: LifeContext) -> None:
        self._life = life

    def create(
        self,
        user_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        if tool_name not in _PROPOSABLE_TOOLS:
            raise JarvisActionError(f"Tool cannot be proposed: {tool_name}")
        if tool_name in _NATIVE_ACTION_TOOLS and not IntegrationsStore(
            self._life
        ).is_connected(user_id, "apple_calendar"):
            raise JarvisActionError("Calendário do iPhone is not connected")
        created = _now()
        proposal_id = uuid.uuid4().hex
        user = self._life.users.get_user(user_id)
        currency = user.currency if user else "BRL"
        with self._life.store.transaction():
            prepared = _prepare_arguments(self._life, user_id, tool_name, arguments)
            self._life.connection.execute(
                "INSERT INTO jarvis_action_proposals"
                " (id, user_id, tool_name, arguments_json, summary, status,"
                " created_at, expires_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    proposal_id,
                    user_id,
                    tool_name,
                    json.dumps(prepared, ensure_ascii=False, sort_keys=True),
                    _summary(tool_name, prepared, currency=currency),
                    created.isoformat(),
                    (created + timedelta(minutes=ACTION_TTL_MINUTES)).isoformat(),
                ),
            )
        proposal = self.get(user_id, proposal_id)
        if proposal is None:  # pragma: no cover - SQLite write/read invariant
            raise JarvisActionError("Proposal was not persisted")
        return proposal

    def get(self, user_id: str, proposal_id: str) -> Dict[str, Any] | None:
        with self._life.store.locked():
            row = self._life.connection.execute(
                "SELECT * FROM jarvis_action_proposals WHERE id = ? AND user_id = ?",
                (proposal_id, user_id),
            ).fetchone()
        return _action_dict(row) if row else None

    def list_pending(self, user_id: str) -> List[Dict[str, Any]]:
        with self._life.store.locked():
            rows = self._life.connection.execute(
                "SELECT * FROM jarvis_action_proposals"
                " WHERE user_id = ? AND status IN ('pending', 'executing')"
                " AND expires_at > ?"
                " ORDER BY created_at DESC",
                (user_id, _now().isoformat()),
            ).fetchall()
        return [_action_dict(row) for row in rows]

    def prepare_native(
        self,
        user_id: str,
        proposal_id: str,
        *,
        device_id: str,
        claim_token: str,
        confirmation_method: str,
    ) -> Dict[str, Any]:
        """Atomically claim a native proposal before EventKit may write."""
        if confirmation_method not in {"explicit", "voice_explicit"}:
            raise JarvisActionError("Unsupported confirmation method")
        normalized_device_id = device_id.strip() if isinstance(device_id, str) else ""
        normalized_token = _validated_claim_token(claim_token)
        token_hash = _claim_token_hash(normalized_token)
        now = _now()
        claim_expires = now + timedelta(seconds=NATIVE_CLAIM_TTL_SECONDS)
        expired = False

        with self._life.store.transaction():
            proposal = self.get(user_id, proposal_id)
            if proposal is None:
                raise JarvisActionError("Action proposal not found")
            if proposal["tool_name"] not in _NATIVE_ACTION_TOOLS:
                raise JarvisActionError("Action is not executed by the iPhone")
            if proposal["status"] == "confirmed":
                return {"proposal": proposal, "replayed": True, "claim": None}
            if datetime.fromisoformat(proposal["expires_at"]) <= now:
                self._life.connection.execute(
                    "UPDATE jarvis_action_proposals SET status = 'expired',"
                    " resolved_at = ? WHERE id = ? AND user_id = ?"
                    " AND status IN ('pending', 'executing')",
                    (now.isoformat(), proposal_id, user_id),
                )
                expired = True
            elif not IntegrationsStore(self._life).is_device_connected(
                user_id,
                "apple_calendar",
                normalized_device_id,
                required_scopes=("events.write",),
            ):
                raise JarvisActionError("Calendário não está autorizado neste aparelho")
            else:
                active = self._life.connection.execute(
                    "SELECT * FROM jarvis_native_action_claims"
                    " WHERE proposal_id = ? AND user_id = ?",
                    (proposal_id, user_id),
                ).fetchone()
                if proposal["status"] == "executing" and active is not None:
                    active_expiry = datetime.fromisoformat(str(active["expires_at"]))
                    same_claim = hmac.compare_digest(
                        str(active["claim_token_hash"]), token_hash
                    ) and hmac.compare_digest(
                        str(active["device_id"]), normalized_device_id
                    )
                    if active_expiry > now and same_claim:
                        return {
                            "proposal": proposal,
                            "replayed": True,
                            "claim": {
                                "device_id": normalized_device_id,
                                "expires_at": str(active["expires_at"]),
                            },
                        }
                    if active_expiry > now:
                        raise JarvisActionError(
                            "Action is already claimed by another execution"
                        )
                    self._life.connection.execute(
                        "UPDATE jarvis_action_proposals SET status = 'pending'"
                        " WHERE id = ? AND user_id = ? AND status = 'executing'",
                        (proposal_id, user_id),
                    )

                proposal_expiry = datetime.fromisoformat(proposal["expires_at"])
                claim_expires = min(claim_expires, proposal_expiry)
                claimed = self._life.connection.execute(
                    "UPDATE jarvis_action_proposals SET status = 'executing',"
                    " confirmation_method = ?"
                    " WHERE id = ? AND user_id = ? AND status = 'pending'",
                    (confirmation_method, proposal_id, user_id),
                ).rowcount
                if not claimed:
                    raise JarvisActionError("Action could not be claimed atomically")
                self._life.connection.execute(
                    "INSERT INTO jarvis_native_action_claims"
                    " (proposal_id, user_id, device_id, claim_token_hash,"
                    " confirmation_method, claimed_at, expires_at, finalized_at,"
                    " receipt_hmac, event_id)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, '', '')"
                    " ON CONFLICT(proposal_id) DO UPDATE SET"
                    " user_id = excluded.user_id,"
                    " device_id = excluded.device_id,"
                    " claim_token_hash = excluded.claim_token_hash,"
                    " confirmation_method = excluded.confirmation_method,"
                    " claimed_at = excluded.claimed_at,"
                    " expires_at = excluded.expires_at,"
                    " finalized_at = NULL, receipt_hmac = '', event_id = ''",
                    (
                        proposal_id,
                        user_id,
                        normalized_device_id,
                        token_hash,
                        confirmation_method,
                        now.isoformat(),
                        claim_expires.isoformat(),
                    ),
                )
        if expired:
            raise JarvisActionError("Action proposal expired")
        updated = self.get(user_id, proposal_id)
        if updated is None:  # pragma: no cover - row cannot disappear here
            raise JarvisActionError("Action proposal not found after claim")
        return {
            "proposal": updated,
            "replayed": False,
            "claim": {
                "device_id": normalized_device_id,
                "expires_at": claim_expires.isoformat(),
            },
        }

    def cancel(self, user_id: str, proposal_id: str) -> Dict[str, Any]:
        with self._life.store.transaction():
            proposal = self.get(user_id, proposal_id)
            if proposal is None:
                raise JarvisActionError("Action proposal not found")
            if proposal["status"] == "canceled":
                return proposal
            if proposal["status"] != "pending":
                raise JarvisActionError(
                    f"Cannot cancel action with status {proposal['status']}"
                )
            canceled = self._life.connection.execute(
                "UPDATE jarvis_action_proposals"
                " SET status = 'canceled', resolved_at = ?"
                " WHERE id = ? AND user_id = ? AND status = 'pending'",
                (_now().isoformat(), proposal_id, user_id),
            ).rowcount
            if canceled != 1:
                current = self.get(user_id, proposal_id)
                if current is not None and current["status"] == "canceled":
                    return current
                raise JarvisActionError(
                    "Action was resolved by another request before cancellation"
                )
        updated = self.get(user_id, proposal_id)
        if updated is None:  # pragma: no cover - row cannot disappear here
            raise JarvisActionError("Action proposal not found after cancellation")
        return updated

    def confirm(
        self,
        user_id: str,
        proposal_id: str,
        *,
        confirmation_method: str,
    ) -> Dict[str, Any]:
        """Execute a proposal once; subsequent confirmations replay its result."""
        expired = False
        expiration_conflict = False
        with self._life.store.transaction():
            proposal = self.get(user_id, proposal_id)
            if proposal is None:
                raise JarvisActionError("Action proposal not found")
            if proposal["tool_name"] in _NATIVE_ACTION_TOOLS:
                raise JarvisActionError(
                    "Native calendar actions must be completed on the iPhone"
                )
            if proposal["status"] == "confirmed":
                return {"proposal": proposal, "replayed": True}
            if proposal["status"] != "pending":
                raise JarvisActionError(
                    f"Cannot confirm action with status {proposal['status']}"
                )
            if datetime.fromisoformat(proposal["expires_at"]) <= _now():
                marked_expired = self._life.connection.execute(
                    "UPDATE jarvis_action_proposals"
                    " SET status = 'expired', resolved_at = ?"
                    " WHERE id = ? AND user_id = ? AND status = 'pending'",
                    (_now().isoformat(), proposal_id, user_id),
                ).rowcount
                if marked_expired == 1:
                    expired = True
                else:
                    expiration_conflict = True
            else:
                tools = {
                    tool.spec.name: tool for tool in life_tools_for(self._life, user_id)
                }
                target = tools.get(proposal["tool_name"])
                if target is None or target.spec.name not in _WRITABLE_TOOLS:
                    raise JarvisActionError("Proposed tool is no longer available")
                execution_arguments = dict(proposal["arguments"])
                execution_arguments.pop(_TARGET_SNAPSHOT_KEY, None)
                # The proposal claim and every current writable Life tool share
                # this database transaction. PostgreSQL re-evaluates the
                # ``pending`` predicate after a concurrent row lock is released;
                # SQLite serializes the write transaction. A losing confirmer
                # therefore never reaches the domain write, while a process
                # failure rolls both the claim and the local effect back.
                claimed = self._life.connection.execute(
                    "UPDATE jarvis_action_proposals SET status = 'executing',"
                    " confirmation_method = ?"
                    " WHERE id = ? AND user_id = ? AND status = 'pending'",
                    (confirmation_method, proposal_id, user_id),
                ).rowcount
                if claimed != 1:
                    raise JarvisActionError(
                        "Action was claimed by another request before execution"
                    )

                _validate_target_snapshot(
                    self._life,
                    user_id,
                    proposal["tool_name"],
                    proposal["arguments"],
                )
                result = target.execute(**execution_arguments)
                serialized = {
                    "tool_name": result.tool_name,
                    "success": result.success,
                    "content": result.content,
                    "metadata": result.metadata,
                }
                status = "confirmed" if result.success else "failed"
                finalized = self._life.connection.execute(
                    "UPDATE jarvis_action_proposals"
                    " SET status = ?, result_json = ?, error = ?,"
                    " confirmation_method = ?, resolved_at = ?"
                    " WHERE id = ? AND user_id = ? AND status = 'executing'",
                    (
                        status,
                        json.dumps(serialized, ensure_ascii=False, default=str),
                        "" if result.success else result.content,
                        confirmation_method,
                        _now().isoformat(),
                        proposal_id,
                        user_id,
                    ),
                ).rowcount
                if finalized != 1:
                    raise JarvisActionError(
                        "Action execution could not be finalized atomically"
                    )
        if expiration_conflict:
            raise JarvisActionError(
                "Action was resolved by another request before expiration"
            )
        if expired:
            raise JarvisActionError("Action proposal expired")
        updated = self.get(user_id, proposal_id)
        if updated is None:  # pragma: no cover - row cannot disappear here
            raise JarvisActionError("Action proposal not found after confirmation")
        return {"proposal": updated, "replayed": False}

    def resolve_native(
        self,
        user_id: str,
        proposal_id: str,
        *,
        result: Dict[str, Any],
        device_id: str,
        claim_token: str,
    ) -> Dict[str, Any]:
        """Finalize a claim after the route verifies the native App Attest result."""
        if not isinstance(result, dict):
            raise JarvisActionError("Native action result must be an object")
        normalized_device_id = device_id.strip() if isinstance(device_id, str) else ""
        normalized_token = _validated_claim_token(claim_token)
        encoded_result = json.dumps(
            result, ensure_ascii=False, sort_keys=True, default=str
        )
        if len(encoded_result.encode("utf-8")) > 8192:
            raise JarvisActionError("Native action result is too large")

        expired = False
        expiration_conflict = False
        claim_expired = False
        with self._life.store.transaction():
            proposal = self.get(user_id, proposal_id)
            if proposal is None:
                raise JarvisActionError("Action proposal not found")
            if proposal["tool_name"] not in _NATIVE_ACTION_TOOLS:
                raise JarvisActionError("Action is not resolved by the iPhone")
            if proposal["status"] == "confirmed":
                return {"proposal": proposal, "replayed": True}
            if proposal["status"] != "executing":
                raise JarvisActionError(
                    f"Cannot resolve action with status {proposal['status']}"
                )
            claim = self._life.connection.execute(
                "SELECT * FROM jarvis_native_action_claims"
                " WHERE proposal_id = ? AND user_id = ?",
                (proposal_id, user_id),
            ).fetchone()
            if claim is None:
                raise JarvisActionError("Native action has no active claim")
            now = _now()
            if datetime.fromisoformat(str(claim["expires_at"])) <= now:
                self._life.connection.execute(
                    "UPDATE jarvis_action_proposals SET status = 'pending',"
                    " confirmation_method = ''"
                    " WHERE id = ? AND user_id = ? AND status = 'executing'",
                    (proposal_id, user_id),
                )
                claim_expired = True
            elif not hmac.compare_digest(str(claim["device_id"]), normalized_device_id):
                raise JarvisActionError("Native action belongs to another device")
            elif not hmac.compare_digest(
                str(claim["claim_token_hash"]),
                _claim_token_hash(normalized_token),
            ):
                raise JarvisActionError("Native action claim token does not match")
            elif not IntegrationsStore(self._life).is_device_connected(
                user_id,
                "apple_calendar",
                normalized_device_id,
                required_scopes=("events.write",),
            ):
                raise JarvisActionError("Calendário não está autorizado neste aparelho")
            else:
                _validate_native_calendar_result(proposal["arguments"], result)
                event = result["event"]
                if datetime.fromisoformat(proposal["expires_at"]) <= now:
                    marked_expired = self._life.connection.execute(
                        "UPDATE jarvis_action_proposals"
                        " SET status = 'expired', resolved_at = ?"
                        " WHERE id = ? AND user_id = ? AND status = 'executing'",
                        (_now().isoformat(), proposal_id, user_id),
                    ).rowcount
                    if marked_expired == 1:
                        expired = True
                    else:
                        expiration_conflict = True
                else:
                    serialized = {
                        "tool_name": proposal["tool_name"],
                        "success": True,
                        "content": "Evento criado no Calendário do iPhone.",
                        "metadata": {
                            "event": event,
                            "device_id": normalized_device_id,
                        },
                    }
                    finalized = self._life.connection.execute(
                        "UPDATE jarvis_action_proposals"
                        " SET status = 'confirmed', result_json = ?, error = '',"
                        " resolved_at = ?"
                        " WHERE id = ? AND user_id = ? AND status = 'executing'",
                        (
                            json.dumps(
                                serialized,
                                ensure_ascii=False,
                                sort_keys=True,
                                default=str,
                            ),
                            now.isoformat(),
                            proposal_id,
                            user_id,
                        ),
                    )
                    if not finalized.rowcount:
                        raise JarvisActionError(
                            "Native action could not finalize atomically"
                        )
                    self._life.connection.execute(
                        "UPDATE jarvis_native_action_claims"
                        " SET finalized_at = ?, receipt_hmac = ?, event_id = ?"
                        " WHERE proposal_id = ? AND user_id = ?"
                        " AND finalized_at IS NULL",
                        (
                            now.isoformat(),
                            "app-attest",
                            str(event["id"]),
                            proposal_id,
                            user_id,
                        ),
                    )
        if expiration_conflict:
            raise JarvisActionError(
                "Action was resolved by another request before expiration"
            )
        if claim_expired:
            raise JarvisActionError("Native action claim expired")
        if expired:
            raise JarvisActionError("Action proposal expired")
        updated = self.get(user_id, proposal_id)
        if updated is None:  # pragma: no cover - row cannot disappear here
            raise JarvisActionError("Action proposal not found after resolution")
        return {"proposal": updated, "replayed": False}

    def recover_native_expiry(self, user_id: str, proposal_id: str) -> bool:
        """Persist expiry recovery after a caller transaction has rolled back."""
        with self._life.store.transaction():
            proposal = self.get(user_id, proposal_id)
            if proposal is None or proposal["status"] != "executing":
                return False
            now = _now()
            if datetime.fromisoformat(str(proposal["expires_at"])) <= now:
                return (
                    self._life.connection.execute(
                        "UPDATE jarvis_action_proposals"
                        " SET status = 'expired', resolved_at = ?"
                        " WHERE id = ? AND user_id = ? AND status = 'executing'",
                        (now.isoformat(), proposal_id, user_id),
                    ).rowcount
                    == 1
                )
            claim = self._life.connection.execute(
                "SELECT expires_at FROM jarvis_native_action_claims"
                " WHERE proposal_id = ? AND user_id = ?",
                (proposal_id, user_id),
            ).fetchone()
            if claim is None or datetime.fromisoformat(str(claim["expires_at"])) > now:
                return False
            return (
                self._life.connection.execute(
                    "UPDATE jarvis_action_proposals SET status = 'pending',"
                    " confirmation_method = ''"
                    " WHERE id = ? AND user_id = ? AND status = 'executing'",
                    (proposal_id, user_id),
                ).rowcount
                == 1
            )


class _ProposalTool(BaseTool):
    """Expose a write schema while deferring persistence to the dialogue CAS."""

    def __init__(
        self,
        target: BaseTool,
        proposal_intents: List[Dict[str, Any]],
    ) -> None:
        self._target = target
        self._proposal_intents = proposal_intents
        self.tool_id = target.tool_id

    @property
    def spec(self) -> ToolSpec:
        original = self._target.spec
        return replace(
            original,
            description=(
                original.description
                + " This creates a pending proposal; the user must confirm it."
            ),
            requires_confirmation=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        arguments = dict(params)
        arguments.pop("user_id", None)
        intent = {"tool_name": self._target.spec.name, "arguments": arguments}
        self._proposal_intents.append(intent)
        return ToolResult(
            tool_name=self._target.spec.name,
            success=True,
            content=json.dumps(
                {
                    "action_required": "confirmation",
                    "proposal": intent,
                },
                ensure_ascii=False,
                default=str,
            ),
            metadata={
                "awaiting_confirmation": True,
            },
        )


class _CalendarProposalTool(BaseTool):
    """Create a confirmed-on-device proposal for EventKit."""

    tool_id = "calendar_create"

    def __init__(self, proposal_intents: List[Dict[str, Any]]) -> None:
        self._proposal_intents = proposal_intents

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_create",
            description=(
                "Prepare an event in the user's iPhone Calendar. Use ISO 8601 "
                "date-times with an explicit timezone offset. This only creates "
                "a pending proposal; the iPhone writes it after the user confirms."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 160},
                    "start_at": {
                        "type": "string",
                        "description": "ISO 8601 date-time with timezone offset.",
                    },
                    "end_at": {
                        "type": "string",
                        "description": "ISO 8601 date-time with timezone offset.",
                    },
                    "is_all_day": {"type": "boolean", "default": False},
                    "location": {"type": "string", "maxLength": 200},
                    "notes": {"type": "string", "maxLength": 1000},
                },
                "required": ["title", "start_at", "end_at"],
                "additionalProperties": False,
            },
            category="life",
            requires_confirmation=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        intent = {"tool_name": "calendar_create", "arguments": dict(params)}
        self._proposal_intents.append(intent)
        return ToolResult(
            tool_name="calendar_create",
            success=True,
            content=json.dumps(
                {"action_required": "confirmation", "proposal": intent},
                ensure_ascii=False,
                default=str,
            ),
            metadata={
                "awaiting_confirmation": True,
                "execution_target": "iphone",
            },
        )


class JarvisRuntime:
    """One tenant-bound tool-calling turn for the Life assistant."""

    def __init__(
        self,
        life: LifeContext,
        user_id: str,
        engine: Any,
        model: str,
    ) -> None:
        self._user_id = user_id
        self.actions = JarvisActionStore(life)
        self.budget = AiBudgetStore(life)
        self._proposal_intents: List[Dict[str, Any]] = []
        interactive_engine = _InteractiveLifeEngine(engine)
        guarded_engine = BudgetedEngine(interactive_engine, self.budget, user_id)
        overview, record, complete = life_tools_for(life, user_id)
        tools: List[BaseTool] = [
            overview,
            _ProposalTool(record, self._proposal_intents),
            _ProposalTool(complete, self._proposal_intents),
        ]
        if IntegrationsStore(life).is_connected(user_id, "apple_calendar"):
            tools.append(_CalendarProposalTool(self._proposal_intents))
        self._agent = OrchestratorAgent(
            guarded_engine,
            model,
            tools=tools,
            max_turns=6,
            temperature=0.3,
            max_tokens=500,
            parallel_tools=False,
        )

    def run(
        self,
        question: str,
        system_prompt: str,
        history: Optional[List[Dict[str, str]]] = None,
        pending_intent: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._proposal_intents.clear()
        conversation_messages = [
            Message(
                role=Role.SYSTEM,
                content=system_prompt + pending_prompt(pending_intent),
            )
        ]
        for item in (history or [])[-MAX_HISTORY_MESSAGES:]:
            role = item.get("role")
            content = item.get("content")
            if role not in {Role.USER.value, Role.ASSISTANT.value}:
                continue
            if not isinstance(content, str):
                continue
            conversation_messages.append(Message(role=Role(role), content=content))
        context = AgentContext(
            conversation=Conversation(messages=conversation_messages)
        )
        result: AgentResult = self._agent.run(question, context=context)
        return {
            "answer": result.content,
            "proposals": [],
            "proposal_intents": [
                {
                    "tool_name": intent["tool_name"],
                    "arguments": dict(intent["arguments"]),
                }
                for intent in self._proposal_intents
            ],
            "turns": result.turns,
            "usage": result.metadata,
            "budget": self.budget.snapshot(),
            "history": [
                *(history or []),
                {"role": Role.USER.value, "content": question},
                {"role": Role.ASSISTANT.value, "content": result.content},
            ][-MAX_HISTORY_MESSAGES:],
        }


__all__ = [
    "ACTION_TTL_MINUTES",
    "action_requires_authenticated_app",
    "JarvisActionError",
    "JarvisActionStore",
    "JarvisRuntime",
]
