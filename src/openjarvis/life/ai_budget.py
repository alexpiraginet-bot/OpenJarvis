"""Hard monthly spending gate for remote Jarvis inference."""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Sequence
from zoneinfo import ZoneInfo

from openjarvis.core.types import Message
from openjarvis.life import LifeContext

MICRO_USD_PER_USD = 1_000_000
MONTHLY_AI_CAP_MICRO_USD = 10 * MICRO_USD_PER_USD
RESERVATION_TTL_MINUTES = 10
_REMOTE_ENGINE_IDS = frozenset({"cloud", "litellm", "openai-compat"})
_BUDGET_TIMEZONE = ZoneInfo("America/Sao_Paulo")


class AiBudgetExceededError(RuntimeError):
    """Raised when a remote call cannot be accounted inside the monthly cap."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _month(now: datetime | None = None) -> str:
    current = now or _now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(_BUDGET_TIMEZONE).strftime("%Y-%m")


class AiBudgetStore:
    """Persist committed spend and in-flight reservations across processes.

    ``expires_at`` is an advisory stale marker only. A reservation remains
    charged until its caller explicitly finalizes or releases it: reclaiming
    solely by wall clock can free budget while a slow provider call is active.
    """

    def __init__(self, life: LifeContext) -> None:
        self._life = life

    def reserve(self, user_id: str, model: str, max_microusd: int) -> str:
        if max_microusd <= 0:
            raise ValueError("A positive cost reservation is required")
        now = _now()
        reservation_id = uuid.uuid4().hex
        with self._life.store.transaction():
            month = _month(now)
            self._lock_budget_month(month)
            used = self._used_microusd(month)
            if used + max_microusd > MONTHLY_AI_CAP_MICRO_USD:
                raise AiBudgetExceededError(
                    "Monthly AI budget exhausted; remote inference was not called"
                )
            self._life.connection.execute(
                "INSERT INTO jarvis_ai_cost_events"
                " (id, user_id, month, model, status, reserved_microusd,"
                " created_at, expires_at)"
                " VALUES (?, ?, ?, ?, 'reserved', ?, ?, ?)",
                (
                    reservation_id,
                    user_id,
                    month,
                    model,
                    max_microusd,
                    now.isoformat(),
                    (now + timedelta(minutes=RESERVATION_TTL_MINUTES)).isoformat(),
                ),
            )
        return reservation_id

    def _lock_budget_month(self, month: str) -> None:
        """Serialize the global cap across PostgreSQL workers for one month."""
        if self._life.connection.backend != "postgres":
            return
        self._life.connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(?))",
            (f"openjarvis:life:ai-budget:{month}",),
        ).fetchone()

    def finalize(self, reservation_id: str, actual_microusd: int) -> None:
        with self._life.store.transaction():
            row = self._life.connection.execute(
                "SELECT reserved_microusd FROM jarvis_ai_cost_events"
                " WHERE id = ? AND status = 'reserved'",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("AI cost reservation is not active")

            actual_microusd = max(0, actual_microusd)
            if actual_microusd > int(row["reserved_microusd"]):
                raise AiBudgetExceededError(
                    "Actual AI cost exceeded its reserved ceiling;"
                    " reservation remains locked"
                )

            cur = self._life.connection.execute(
                "UPDATE jarvis_ai_cost_events"
                " SET status = 'finalized', actual_microusd = ?, resolved_at = ?"
                " WHERE id = ? AND status = 'reserved'",
                (actual_microusd, _now().isoformat(), reservation_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError("AI cost reservation is not active")

    def release(self, reservation_id: str) -> None:
        with self._life.store.transaction():
            self._life.connection.execute(
                "UPDATE jarvis_ai_cost_events"
                " SET status = 'canceled', resolved_at = ?"
                " WHERE id = ? AND status = 'reserved'",
                (_now().isoformat(), reservation_id),
            )

    def snapshot(self) -> Dict[str, int | str]:
        now = _now()
        with self._life.store.locked():
            row = self._life.connection.execute(
                "SELECT"
                " COALESCE(SUM(CASE WHEN status = 'finalized'"
                " THEN actual_microusd ELSE 0 END), 0) AS spent,"
                " COALESCE(SUM(CASE WHEN status = 'reserved'"
                " THEN reserved_microusd ELSE 0 END), 0) AS reserved"
                " FROM jarvis_ai_cost_events WHERE month = ?",
                (_month(now),),
            ).fetchone()
        spent = int(row["spent"]) if row else 0
        reserved = int(row["reserved"]) if row else 0
        return {
            "month": _month(now),
            "cap_microusd": MONTHLY_AI_CAP_MICRO_USD,
            "spent_microusd": spent,
            "reserved_microusd": reserved,
            "remaining_microusd": max(0, MONTHLY_AI_CAP_MICRO_USD - spent - reserved),
        }

    def _used_microusd(self, month: str) -> int:
        row = self._life.connection.execute(
            "SELECT COALESCE(SUM("
            " CASE WHEN status = 'finalized' THEN actual_microusd"
            " WHEN status = 'reserved' THEN reserved_microusd"
            " ELSE 0 END), 0) AS used"
            " FROM jarvis_ai_cost_events WHERE month = ?",
            (month,),
        ).fetchone()
        return int(row["used"]) if row else 0


class BudgetedEngine:
    """Reserve a conservative maximum before each paid engine call."""

    def __init__(
        self,
        delegate: Any,
        budget: AiBudgetStore,
        user_id: str,
    ) -> None:
        self._delegate = delegate
        self._budget = budget
        self._user_id = user_id
        self.engine_id = getattr(delegate, "engine_id", "")

    def generate(self, messages: Sequence[Message], **kwargs: Any) -> Dict[str, Any]:
        model = str(kwargs.get("model", ""))
        if not self._is_remote_call(self._delegate, model):
            return self._delegate.generate(messages, **kwargs)

        max_tokens = int(kwargs.get("max_tokens", 0))
        max_microusd = self._max_cost_microusd(messages, model, max_tokens, kwargs)
        reservation = self._budget.reserve(self._user_id, model, max_microusd)
        try:
            result = self._delegate.generate(messages, **kwargs)
        except Exception:
            self._budget.release(reservation)
            raise

        actual_microusd = self._actual_cost_microusd(result, model)
        self._budget.finalize(reservation, actual_microusd)
        return result

    @staticmethod
    def _is_remote_call(delegate: Any, model: str) -> bool:
        """Recognize paid calls through telemetry and multi-engine wrappers."""
        from openjarvis.engine.cloud import PRICING

        if any(model == key or model.startswith(key) for key in PRICING):
            return True

        current = delegate
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            engine_id = str(getattr(current, "engine_id", ""))
            if engine_id in _REMOTE_ENGINE_IDS:
                return True
            if engine_id == "multi":
                route = getattr(current, "_engine_for", None)
                if callable(route):
                    current = route(model)
                    continue
            current = getattr(current, "_inner", None) or getattr(
                current, "_delegate", None
            )
        return False

    @staticmethod
    def _max_cost_microusd(
        messages: Sequence[Message],
        model: str,
        max_tokens: int,
        kwargs: Dict[str, Any],
    ) -> int:
        from openjarvis.engine._base import messages_to_dicts
        from openjarvis.engine.cloud import PRICING, estimate_cost

        if not any(model == key or model.startswith(key) for key in PRICING):
            raise AiBudgetExceededError(
                f"Remote model has no verified price ceiling: {model or '[empty]'}"
            )
        # GPT-family tokenizers fall back to non-empty byte tokens. Counting
        # every UTF-8 byte as one token plus protocol overhead is deliberately
        # conservative, including message tool calls and separate tool schemas.
        prompt_bytes = len(
            json.dumps(
                messages_to_dicts(messages),
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        )
        tools_bytes = len(
            json.dumps(kwargs.get("tools", []), ensure_ascii=False, default=str).encode(
                "utf-8"
            )
        )
        prompt_ceiling = prompt_bytes + tools_bytes + 256 * len(messages) + 512
        maximum = estimate_cost(model, prompt_ceiling, max(1, max_tokens))
        return max(1, math.ceil(maximum * MICRO_USD_PER_USD))

    @staticmethod
    def _actual_cost_microusd(result: Dict[str, Any], model: str) -> int:
        from openjarvis.engine.cloud import estimate_cost

        cost = float(result.get("cost_usd", 0.0) or 0.0)
        if cost <= 0:
            usage = result.get("usage", {})
            cost = estimate_cost(
                model,
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
            )
        return max(0, math.ceil(cost * MICRO_USD_PER_USD))


__all__ = [
    "AiBudgetExceededError",
    "AiBudgetStore",
    "BudgetedEngine",
    "MICRO_USD_PER_USD",
    "MONTHLY_AI_CAP_MICRO_USD",
]
