"""Jarvis OS runtime with durable, tenant-bound action proposals.

The model may read Life data during a turn. Write tools are replaced by
proposal tools: they persist an auditable intent and return it to the model,
but cannot execute it. A separate authenticated endpoint confirms the
proposal and runs the original tenant-bound tool exactly once.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from openjarvis.agents._stubs import AgentContext, AgentResult
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import Conversation, Message, Role, ToolResult
from openjarvis.life import LifeContext
from openjarvis.life.ai_budget import AiBudgetStore, BudgetedEngine
from openjarvis.life.schema import SCHEMA
from openjarvis.life.tools import life_tools_for, normalize_life_record_fields
from openjarvis.tools._stubs import BaseTool, ToolSpec

ACTION_TTL_MINUTES = 15
_WRITABLE_TOOLS = frozenset({"life_record", "life_complete"})
_TARGET_SNAPSHOT_KEY = "_target_snapshot"
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
        "habit": "hábito",
        "family_member": "familiar",
        "family_event": "evento familiar",
        "project": "projeto",
        "task": "tarefa",
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
    current = life.store.get(table, user_id, record_id)
    if current is None:
        raise JarvisActionError("Action target no longer exists")
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
        if tool_name not in _WRITABLE_TOOLS:
            raise JarvisActionError(f"Tool cannot be proposed: {tool_name}")
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
                " WHERE user_id = ? AND status = 'pending' AND expires_at > ?"
                " ORDER BY created_at DESC",
                (user_id, _now().isoformat()),
            ).fetchall()
        return [_action_dict(row) for row in rows]

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
            self._life.connection.execute(
                "UPDATE jarvis_action_proposals"
                " SET status = 'canceled', resolved_at = ?"
                " WHERE id = ? AND user_id = ? AND status = 'pending'",
                (_now().isoformat(), proposal_id, user_id),
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
        with self._life.store.transaction():
            proposal = self.get(user_id, proposal_id)
            if proposal is None:
                raise JarvisActionError("Action proposal not found")
            if proposal["status"] == "confirmed":
                return {"proposal": proposal, "replayed": True}
            if proposal["status"] != "pending":
                raise JarvisActionError(
                    f"Cannot confirm action with status {proposal['status']}"
                )
            if datetime.fromisoformat(proposal["expires_at"]) <= _now():
                self._life.connection.execute(
                    "UPDATE jarvis_action_proposals"
                    " SET status = 'expired', resolved_at = ?"
                    " WHERE id = ? AND user_id = ?",
                    (_now().isoformat(), proposal_id, user_id),
                )
                expired = True
            else:
                tools = {
                    tool.spec.name: tool for tool in life_tools_for(self._life, user_id)
                }
                target = tools.get(proposal["tool_name"])
                if target is None or target.spec.name not in _WRITABLE_TOOLS:
                    raise JarvisActionError("Proposed tool is no longer available")
                _validate_target_snapshot(
                    self._life,
                    user_id,
                    proposal["tool_name"],
                    proposal["arguments"],
                )
                execution_arguments = dict(proposal["arguments"])
                execution_arguments.pop(_TARGET_SNAPSHOT_KEY, None)
                result = target.execute(**execution_arguments)
                serialized = {
                    "tool_name": result.tool_name,
                    "success": result.success,
                    "content": result.content,
                    "metadata": result.metadata,
                }
                status = "confirmed" if result.success else "failed"
                self._life.connection.execute(
                    "UPDATE jarvis_action_proposals"
                    " SET status = ?, result_json = ?, error = ?,"
                    " confirmation_method = ?, resolved_at = ?"
                    " WHERE id = ? AND user_id = ? AND status = 'pending'",
                    (
                        status,
                        json.dumps(serialized, ensure_ascii=False, default=str),
                        "" if result.success else result.content,
                        confirmation_method,
                        _now().isoformat(),
                        proposal_id,
                        user_id,
                    ),
                )
        if expired:
            raise JarvisActionError("Action proposal expired")
        updated = self.get(user_id, proposal_id)
        if updated is None:  # pragma: no cover - row cannot disappear here
            raise JarvisActionError("Action proposal not found after confirmation")
        return {"proposal": updated, "replayed": False}


class _ProposalTool(BaseTool):
    """Expose a write tool's schema while persisting intent instead of writing."""

    def __init__(
        self,
        target: BaseTool,
        actions: JarvisActionStore,
        user_id: str,
    ) -> None:
        self._target = target
        self._actions = actions
        self._user_id = user_id
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
        proposal = self._actions.create(
            self._user_id,
            self._target.spec.name,
            arguments,
        )
        return ToolResult(
            tool_name=self._target.spec.name,
            success=True,
            content=json.dumps(
                {
                    "action_required": "confirmation",
                    "proposal": proposal,
                },
                ensure_ascii=False,
                default=str,
            ),
            metadata={
                "proposal_id": proposal["id"],
                "awaiting_confirmation": True,
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
        interactive_engine = _InteractiveLifeEngine(engine)
        guarded_engine = BudgetedEngine(interactive_engine, self.budget, user_id)
        overview, record, complete = life_tools_for(life, user_id)
        tools: List[BaseTool] = [
            overview,
            _ProposalTool(record, self.actions, user_id),
            _ProposalTool(complete, self.actions, user_id),
        ]
        self._agent = OrchestratorAgent(
            guarded_engine,
            model,
            tools=tools,
            max_turns=6,
            temperature=0.3,
            max_tokens=500,
            parallel_tools=False,
        )

    def run(self, question: str, system_prompt: str) -> Dict[str, Any]:
        context = AgentContext(
            conversation=Conversation(
                messages=[Message(role=Role.SYSTEM, content=system_prompt)]
            )
        )
        result: AgentResult = self._agent.run(question, context=context)
        proposal_ids = [
            item.metadata["proposal_id"]
            for item in result.tool_results
            if item.metadata.get("proposal_id")
        ]
        proposals = [
            proposal
            for proposal_id in proposal_ids
            if (proposal := self.actions.get(self._user_id, proposal_id)) is not None
        ]
        return {
            "answer": result.content,
            "proposals": proposals,
            "turns": result.turns,
            "usage": result.metadata,
            "budget": self.budget.snapshot(),
        }


__all__ = [
    "ACTION_TTL_MINUTES",
    "JarvisActionError",
    "JarvisActionStore",
    "JarvisRuntime",
]
