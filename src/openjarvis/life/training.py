"""Deterministic adaptive multimodal training coach for Jarvis Life.

The language model may explain this data, but it does not own progression or
safety arithmetic.  Keeping the prescription here makes the same plan flow
through the app, voice, briefings and future connectors without prompt drift.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Sequence

from openjarvis.life.store import Filter, LifeStore

GOALS = frozenset(
    {
        "general_fitness",
        "endurance",
        "performance",
        "technique",
        "strength",
        "hypertrophy",
        "mobility",
        "5k",
        "10k",
        "half_marathon",
    }
)
LEVELS = frozenset({"beginner", "intermediate", "advanced"})
SPORTS = frozenset(
    {
        "running",
        "canoeing",
        "cycling",
        "swimming",
        "strength",
        "mobility",
        "functional",
        "walking",
        "hiking",
        "rowing",
    }
)
MIN_PLAN_WEEKS = 4
MAX_PLAN_WEEKS = 16

_GOAL_LABELS = {
    "general_fitness": "Condicionamento geral",
    "endurance": "Resistência e endurance",
    "performance": "Performance na modalidade",
    "technique": "Técnica e eficiência",
    "strength": "Ganho de força",
    "hypertrophy": "Hipertrofia sustentável",
    "mobility": "Mobilidade e controle",
    "5k": "Primeiros 5 km",
    "10k": "Evolução para 10 km",
    "half_marathon": "Meia maratona",
}

def goal_label(goal: str) -> str:
    """Human name for a training goal, for anything that has to show it.

    Public because the confirmation card has to name the objective before the
    client approves it — reading the private map from another module would be
    worse than exposing the one lookup it needs.
    """
    return _GOAL_LABELS.get(goal, goal)


_DEFAULT_WEEKLY_KM = {"beginner": 8.0, "intermediate": 20.0, "advanced": 35.0}
_BASE_PACE = {"beginner": 7.0, "intermediate": 6.0, "advanced": 5.0}
_SESSION_PATTERNS = {
    2: ("easy", "long"),
    3: ("easy", "quality", "long"),
    4: ("easy", "quality", "strength", "long"),
    5: ("easy", "quality", "strength", "recovery", "long"),
    6: ("easy", "quality", "strength", "recovery", "strength", "long"),
}

_SPORT_COPY = {
    "running": ("Corrida", "passada"),
    "canoeing": ("Canoa", "remada"),
    "cycling": ("Pedal", "cadência"),
    "swimming": ("Natação", "braçada"),
    "strength": ("Força", "carga"),
    "mobility": ("Mobilidade", "amplitude"),
    "functional": ("Funcional", "movimento"),
    "walking": ("Caminhada", "passada"),
    "hiking": ("Trilha", "passada"),
    "rowing": ("Remo", "remada"),
}


class TrainingCoachError(ValueError):
    """Raised when a training prescription or transition is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_int(name: str, value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise TrainingCoachError(f"{name} must be between {minimum} and {maximum}")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise TrainingCoachError(
            f"{name} must be between {minimum} and {maximum}"
        ) from exc
    if normalized < minimum or normalized > maximum:
        raise TrainingCoachError(f"{name} must be between {minimum} and {maximum}")
    return normalized


def _bounded_float(name: str, value: Any, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise TrainingCoachError(f"{name} must be between {minimum} and {maximum}")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise TrainingCoachError(
            f"{name} must be between {minimum} and {maximum}"
        ) from exc
    if normalized < minimum or normalized > maximum:
        raise TrainingCoachError(f"{name} must be between {minimum} and {maximum}")
    return round(normalized, 2)


def _json_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise TrainingCoachError(f"{name} must be a list")
    return value


def _decode_list(value: Any) -> list[Any]:
    if not value:
        return []
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return decoded if isinstance(decoded, list) else []


def _parse_date(value: Any, *, name: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise TrainingCoachError(f"{name} must be an ISO date") from exc


class TrainingCoachService:
    """Tenant-scoped training prescription and adaptation."""

    def __init__(self, store: LifeStore) -> None:
        self._store = store

    def save_profile(self, user_id: str, fields: Mapping[str, Any]) -> Dict[str, Any]:
        """Validate and upsert the single current training profile."""
        primary_sport = str(fields.get("primary_sport", "running")).strip()
        if primary_sport not in SPORTS:
            raise TrainingCoachError("primary_sport is not supported")
        secondary_sports = [
            str(item).strip()
            for item in _json_list(
                fields.get("secondary_sports", []), name="secondary_sports"
            )
            if str(item).strip()
        ]
        if any(sport not in SPORTS for sport in secondary_sports):
            raise TrainingCoachError("secondary_sports contains an unsupported sport")
        secondary_sports = list(dict.fromkeys(secondary_sports))[:4]
        goal = str(fields.get("primary_goal", "general_fitness")).strip()
        if goal not in GOALS:
            raise TrainingCoachError("primary_goal is not supported")
        level = str(fields.get("level", "beginner")).strip()
        if level not in LEVELS:
            raise TrainingCoachError("level is not supported")

        weekly_days = _bounded_int("weekly_days", fields.get("weekly_days", 3), 2, 6)
        weekdays = sorted(
            {
                _bounded_int("available_weekdays", item, 1, 7)
                for item in _json_list(
                    fields.get("available_weekdays", [1, 3, 5]),
                    name="available_weekdays",
                )
            }
        )
        if len(weekdays) < weekly_days:
            raise TrainingCoachError(
                "available_weekdays must include at least weekly_days values"
            )
        equipment = [
            str(item).strip()
            for item in _json_list(fields.get("equipment", []), name="equipment")
            if str(item).strip()
        ]
        target_date = str(fields.get("target_date") or "").strip() or None
        if target_date is not None:
            _parse_date(target_date, name="target_date")

        payload = {
            "primary_sport": primary_sport,
            "secondary_sports_json": json.dumps(
                secondary_sports, separators=(",", ":")
            ),
            "primary_goal": goal,
            "target_distance_km": _bounded_float(
                "target_distance_km", fields.get("target_distance_km", 0), 0, 100
            ),
            "target_date": target_date,
            "level": level,
            "weekly_days": weekly_days,
            "available_weekdays_json": json.dumps(weekdays, separators=(",", ":")),
            "session_minutes": _bounded_int(
                "session_minutes", fields.get("session_minutes", 45), 20, 120
            ),
            "current_weekly_km": _bounded_float(
                "current_weekly_km", fields.get("current_weekly_km", 0), 0, 250
            ),
            "longest_recent_run_km": _bounded_float(
                "longest_recent_run_km",
                fields.get("longest_recent_run_km", 0),
                0,
                100,
            ),
            "equipment_json": json.dumps(equipment, separators=(",", ":")),
            "limitations": str(fields.get("limitations") or "").strip()[:1000],
        }
        existing = self._profile_row(user_id)
        with self._store.transaction():
            if existing is None:
                profile_id = self._store.insert("training_profiles", user_id, payload)
            else:
                profile_id = str(existing["id"])
                self._store.update("training_profiles", user_id, profile_id, payload)
        profile = self._store.get("training_profiles", user_id, profile_id)
        if profile is None:  # pragma: no cover - same-transaction insert invariant
            raise TrainingCoachError("training profile could not be saved")
        return self._hydrate_profile(profile)

    def generate_plan(
        self,
        user_id: str,
        *,
        start_on: date | str | None = None,
        weeks: int = 8,
    ) -> Dict[str, Any]:
        """Replace the pending coach schedule with one deterministic plan."""
        profile_row = self._profile_row(user_id)
        if profile_row is None:
            raise TrainingCoachError("training profile is required")
        profile = self._hydrate_profile(profile_row)
        plan_weeks = _bounded_int("weeks", weeks, MIN_PLAN_WEEKS, MAX_PLAN_WEEKS)
        anchor = (
            start_on
            if isinstance(start_on, date)
            else _parse_date(start_on or date.today().isoformat(), name="start_on")
        )
        weekly_days = int(profile["weekly_days"])
        weekdays = [int(item) for item in profile["available_weekdays"][:weekly_days]]
        dates = self._scheduled_dates(anchor, weekdays, plan_weeks * weekly_days)
        end_on = dates[-1]
        pattern = _SESSION_PATTERNS[weekly_days]
        primary_sport = str(profile["primary_sport"])
        secondary_sports = [str(item) for item in profile["secondary_sports"]]

        with self._store.transaction():
            self._replace_active_plan(user_id)
            plan_id = self._store.insert(
                "training_plans",
                user_id,
                {
                    "profile_id": profile["id"],
                    "name": _GOAL_LABELS[str(profile["primary_goal"])],
                    "primary_sport": primary_sport,
                    "goal": profile["primary_goal"],
                    "start_on": anchor.isoformat(),
                    "end_on": end_on.isoformat(),
                    "weeks": plan_weeks,
                    "current_week": 1,
                    "status": "active",
                    "source": "coach",
                },
            )
            for ordinal, scheduled in enumerate(dates):
                week_index = ordinal // weekly_days + 1
                day_index = ordinal % weekly_days + 1
                session_type = pattern[day_index - 1]
                sport = self._session_sport(
                    primary_sport,
                    secondary_sports,
                    session_type=session_type,
                )
                prescription = self._prescription(
                    profile,
                    sport=sport,
                    session_type=session_type,
                    week_index=week_index,
                )
                workout_id = self._store.insert(
                    "workouts",
                    user_id,
                    {
                        "name": prescription["title"],
                        "scheduled_on": scheduled.isoformat(),
                        "focus": prescription["objective"],
                        "notes": prescription["rationale"],
                        "source": "coach",
                    },
                )
                session_id = self._store.insert(
                    "training_sessions",
                    user_id,
                    {
                        "plan_id": plan_id,
                        "workout_id": workout_id,
                        "scheduled_on": scheduled.isoformat(),
                        "week_index": week_index,
                        "day_index": day_index,
                        "title": prescription["title"],
                        "sport": sport,
                        "session_type": session_type,
                        "objective": prescription["objective"],
                        "rationale": prescription["rationale"],
                        "estimated_min": prescription["estimated_min"],
                        "target_rpe": prescription["target_rpe"],
                        "status": "planned",
                        "adaptation_note": "",
                    },
                )
                for step_index, step in enumerate(prescription["steps"], start=1):
                    self._store.insert(
                        "training_steps",
                        user_id,
                        {"session_id": session_id, "step_index": step_index, **step},
                    )

        plan = self._store.get("training_plans", user_id, plan_id)
        if plan is None:  # pragma: no cover - same-transaction insert invariant
            raise TrainingCoachError("training plan could not be generated")
        return plan

    def overview(self, user_id: str, *, anchor: date | None = None) -> Dict[str, Any]:
        """Return profile, active plan, ordered sessions and the next session."""
        profile_row = self._profile_row(user_id)
        plans = self._store.list_records(
            "training_plans",
            user_id,
            filters=(Filter("status", "=", "active"),),
            order_by="created_at",
            limit=1,
        )
        active_plan = plans[0] if plans else None
        sessions: List[Dict[str, Any]] = []
        if active_plan is not None:
            sessions = self._store.list_records(
                "training_sessions",
                user_id,
                filters=(Filter("plan_id", "=", active_plan["id"]),),
                order_by="scheduled_on",
                descending=False,
                limit=200,
            )
        today = anchor or date.today()
        next_session = next(
            (
                session
                for session in sessions
                if session["status"] == "planned"
                and str(session["scheduled_on"]) >= today.isoformat()
            ),
            next(
                (session for session in sessions if session["status"] == "planned"),
                None,
            ),
        )
        return {
            "profile": self._hydrate_profile(profile_row) if profile_row else None,
            "active_plan": active_plan,
            "sessions": sessions,
            "next_session": next_session,
        }

    def get_session(self, user_id: str, session_id: str) -> Dict[str, Any] | None:
        """Hydrate one session and its private execution data."""
        session = self._store.get("training_sessions", user_id, session_id)
        if session is None:
            return None
        steps = self._store.list_records(
            "training_steps",
            user_id,
            filters=(Filter("session_id", "=", session_id),),
            order_by="step_index",
            descending=False,
            limit=100,
        )
        checkins = self._store.list_records(
            "training_checkins",
            user_id,
            filters=(Filter("session_id", "=", session_id),),
            limit=1,
        )
        feedback = self._store.list_records(
            "training_feedback",
            user_id,
            filters=(Filter("session_id", "=", session_id),),
            limit=1,
        )
        workout = (
            self._store.get("workouts", user_id, str(session["workout_id"]))
            if session.get("workout_id")
            else None
        )
        return {
            "session": session,
            "steps": steps,
            "checkin": checkins[0] if checkins else None,
            "feedback": self._hydrate_feedback(feedback[0]) if feedback else None,
            "workout": workout,
        }

    def check_in(
        self,
        user_id: str,
        session_id: str,
        *,
        sleep_quality: int,
        soreness: int,
        stress: int,
        motivation: int,
        pain: int,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Record pre-session readiness and return an explicit recommendation."""
        if self._store.get("training_sessions", user_id, session_id) is None:
            raise TrainingCoachError("training session not found")
        values = {
            "sleep_quality": _bounded_int("sleep_quality", sleep_quality, 0, 10),
            "soreness": _bounded_int("soreness", soreness, 0, 10),
            "stress": _bounded_int("stress", stress, 0, 10),
            "motivation": _bounded_int("motivation", motivation, 0, 10),
            "pain": _bounded_int("pain", pain, 0, 10),
        }
        score = round(
            (
                values["sleep_quality"]
                + values["motivation"]
                + (10 - values["soreness"])
                + (10 - values["stress"])
                + (10 - values["pain"])
            )
            * 2
        )
        if values["pain"] >= 7:
            score = min(score, 35)
            recommendation = "stop_and_seek_care"
        elif score < 45:
            recommendation = "recovery_only"
        elif score < 65:
            recommendation = "reduce_load"
        else:
            recommendation = "ready"
        payload = {
            "session_id": session_id,
            "observed_at": _now(),
            **values,
            "readiness_score": score,
            "recommendation": recommendation,
            "notes": str(notes).strip()[:1000],
        }
        existing = self._rows_for_session("training_checkins", user_id, session_id)
        with self._store.transaction():
            if existing:
                row_id = str(existing[0]["id"])
                self._store.update("training_checkins", user_id, row_id, payload)
            else:
                row_id = self._store.insert("training_checkins", user_id, payload)
        saved = self._store.get("training_checkins", user_id, row_id)
        if saved is None:  # pragma: no cover - same-transaction write invariant
            raise TrainingCoachError("training check-in could not be saved")
        return saved

    def complete_session(
        self,
        user_id: str,
        session_id: str,
        *,
        completion_pct: int,
        actual_duration_min: int,
        rpe: int,
        energy: int,
        pain: int,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Complete one session once and adapt future prescription."""
        existing_feedback = self._rows_for_session(
            "training_feedback", user_id, session_id
        )
        if existing_feedback:
            feedback = self._hydrate_feedback(existing_feedback[0])
            return {
                "replayed": True,
                "feedback": feedback,
                "adaptation": feedback["adaptation"],
            }
        session = self._store.get("training_sessions", user_id, session_id)
        if session is None:
            raise TrainingCoachError("training session not found")
        if session["status"] == "canceled":
            raise TrainingCoachError("canceled training session cannot be completed")

        completion = _bounded_int("completion_pct", completion_pct, 0, 100)
        duration = _bounded_int("actual_duration_min", actual_duration_min, 0, 300)
        exertion = _bounded_int("rpe", rpe, 0, 10)
        energy_value = _bounded_int("energy", energy, 0, 10)
        pain_value = _bounded_int("pain", pain, 0, 10)
        checkins = self._rows_for_session("training_checkins", user_id, session_id)
        readiness = int(checkins[0]["readiness_score"]) if checkins else 100
        decision = self._adaptation_decision(
            session,
            completion_pct=completion,
            rpe=exertion,
            pain=pain_value,
            readiness=readiness,
        )
        completed_at = _now()
        with self._store.transaction():
            self._store.update(
                "training_sessions",
                user_id,
                session_id,
                {"status": "completed"},
            )
            if session.get("workout_id"):
                self._store.update(
                    "workouts",
                    user_id,
                    str(session["workout_id"]),
                    {"completed_at": completed_at, "duration_min": duration},
                )
            adapted = self._adapt_future_sessions(user_id, session, decision)
            decision["sessions"] = adapted
            feedback_id = self._store.insert(
                "training_feedback",
                user_id,
                {
                    "session_id": session_id,
                    "completed_at": completed_at,
                    "completion_pct": completion,
                    "actual_duration_min": duration,
                    "rpe": exertion,
                    "energy": energy_value,
                    "pain": pain_value,
                    "notes": str(notes).strip()[:2000],
                    "adaptation": json.dumps(
                        decision, ensure_ascii=False, separators=(",", ":")
                    ),
                },
            )
        feedback = self._store.get("training_feedback", user_id, feedback_id)
        if feedback is None:  # pragma: no cover - same-transaction insert invariant
            raise TrainingCoachError("training feedback could not be saved")
        return {
            "replayed": False,
            "feedback": self._hydrate_feedback(feedback),
            "adaptation": decision,
        }

    def _profile_row(self, user_id: str) -> Dict[str, Any] | None:
        profiles = self._store.list_records(
            "training_profiles", user_id, order_by="created_at", limit=1
        )
        return profiles[0] if profiles else None

    @staticmethod
    def _hydrate_profile(row: Mapping[str, Any]) -> Dict[str, Any]:
        profile = dict(row)
        profile["secondary_sports"] = [
            str(item) for item in _decode_list(profile.pop("secondary_sports_json", ""))
        ]
        encoded_weekdays = profile.pop("available_weekdays_json", "")
        profile["available_weekdays"] = [
            int(item) for item in _decode_list(encoded_weekdays)
        ]
        profile["equipment"] = [
            str(item) for item in _decode_list(profile.pop("equipment_json", ""))
        ]
        return profile

    @staticmethod
    def _hydrate_feedback(row: Mapping[str, Any]) -> Dict[str, Any]:
        feedback = dict(row)
        try:
            adaptation = json.loads(str(feedback.get("adaptation") or "{}"))
        except (ValueError, json.JSONDecodeError):
            adaptation = {}
        feedback["adaptation"] = adaptation if isinstance(adaptation, dict) else {}
        return feedback

    def _rows_for_session(
        self, table: str, user_id: str, session_id: str
    ) -> List[Dict[str, Any]]:
        return self._store.list_records(
            table,
            user_id,
            filters=(Filter("session_id", "=", session_id),),
            limit=1,
        )

    @staticmethod
    def _scheduled_dates(
        start_on: date, weekdays: Sequence[int], total: int
    ) -> List[date]:
        allowed = frozenset(weekdays)
        cursor = start_on
        scheduled: List[date] = []
        while len(scheduled) < total:
            if cursor.isoweekday() in allowed:
                scheduled.append(cursor)
            cursor += timedelta(days=1)
        return scheduled

    def _replace_active_plan(self, user_id: str) -> None:
        active = self._store.list_records(
            "training_plans",
            user_id,
            filters=(Filter("status", "=", "active"),),
            limit=100,
        )
        for plan in active:
            sessions = self._store.list_records(
                "training_sessions",
                user_id,
                filters=(
                    Filter("plan_id", "=", plan["id"]),
                    Filter("status", "=", "planned"),
                ),
                limit=500,
            )
            for session in sessions:
                if session.get("workout_id"):
                    self._store.delete("workouts", user_id, str(session["workout_id"]))
                self._store.update(
                    "training_sessions",
                    user_id,
                    str(session["id"]),
                    {"status": "canceled", "workout_id": ""},
                )
            self._store.update(
                "training_plans", user_id, str(plan["id"]), {"status": "replaced"}
            )

    @staticmethod
    def _session_sport(
        primary_sport: str,
        secondary_sports: Sequence[str],
        *,
        session_type: str,
    ) -> str:
        if primary_sport == "strength":
            return "mobility" if session_type == "recovery" else "strength"
        if session_type == "strength":
            return "strength"
        if session_type == "recovery" and secondary_sports:
            return secondary_sports[0]
        return primary_sport

    def _prescription(
        self,
        profile: Mapping[str, Any],
        *,
        sport: str,
        session_type: str,
        week_index: int,
    ) -> Dict[str, Any]:
        base_minutes = int(profile["session_minutes"])
        minute_factor = {
            "easy": 0.85,
            "quality": 1.0,
            "strength": 0.8,
            "recovery": 0.65,
            "long": 1.35,
        }[session_type]
        estimated = max(20, min(120, round(base_minutes * minute_factor)))
        base_km = (
            float(profile["current_weekly_km"])
            or _DEFAULT_WEEKLY_KM[str(profile["level"])]
        )
        progression = 1 + 0.05 * (week_index - 1)
        if week_index % 4 == 0:
            progression *= 0.8
        weekly_km = base_km * progression
        distance_share = {
            "easy": 0.25,
            "quality": 0.2,
            "strength": 0,
            "recovery": 0.15,
            "long": 0.4,
        }[session_type]
        distance_m = round(weekly_km * distance_share * 1000)
        target_rpe = {
            "easy": 4,
            "quality": 7,
            "strength": 6,
            "recovery": 3,
            "long": 5,
        }[session_type]
        label, movement = _SPORT_COPY[sport]
        titles = {
            "easy": f"{label} · Técnica e base",
            "quality": f"{label} · Intervalos controlados",
            "strength": "Força · Sessão complementar",
            "recovery": f"{label} · Recuperação ativa",
            "long": f"{label} · Endurance confortável",
        }
        if sport == "strength":
            titles[session_type] = "Força · Sessão completa"
        elif sport == "mobility":
            titles[session_type] = "Mobilidade · Controle e amplitude"
        objectives = {
            "easy": "Construir base aeróbica sem acumular fadiga",
            "quality": f"Melhorar ritmo e economia de {label.lower()}",
            "strength": "Aumentar estabilidade, força e tolerância de carga",
            "recovery": "Circular, recuperar e manter consistência",
            "long": "Ampliar resistência em intensidade conversável",
        }
        rationale = (
            f"Semana {week_index}: progressão de {label.lower()} no nível "
            f"{profile['level']}, com foco técnico em {movement}."
        )
        return {
            "title": titles[session_type],
            "objective": objectives[session_type],
            "rationale": rationale,
            "estimated_min": estimated,
            "target_rpe": target_rpe,
            "steps": self._steps(
                profile,
                sport=sport,
                session_type=session_type,
                estimated_min=estimated,
                distance_m=distance_m,
                target_rpe=target_rpe,
                week_index=week_index,
            ),
        }

    @staticmethod
    def _steps(
        profile: Mapping[str, Any],
        *,
        sport: str,
        session_type: str,
        estimated_min: int,
        distance_m: int,
        target_rpe: int,
        week_index: int,
    ) -> List[Dict[str, Any]]:
        pace = _BASE_PACE[str(profile["level"])]

        def step(
            kind: str,
            title: str,
            instructions: str,
            **targets: Any,
        ) -> Dict[str, Any]:
            return {
                "kind": kind,
                "title": title,
                "instructions": instructions,
                "duration_sec": int(targets.get("duration_sec", 0)),
                "distance_m": int(targets.get("distance_m", 0)),
                "target_pace_min_km": float(targets.get("target_pace_min_km", 0)),
                "target_rpe": int(targets.get("target_rpe", 0)),
                "sets": int(targets.get("sets", 0)),
                "reps": int(targets.get("reps", 0)),
                "rest_sec": int(targets.get("rest_sec", 0)),
                "alternative": str(targets.get("alternative", "")),
            }

        label, movement = _SPORT_COPY[sport]
        warmup = step(
            "warmup",
            "Aquecimento progressivo",
            f"Prepare articulações e aumente o ritmo antes da {movement} principal.",
            duration_sec=480 if sport not in {"strength", "mobility"} else 360,
            target_rpe=2,
            alternative="Mobilidade confortável, sempre sem dor.",
        )
        cooldown = step(
            "cooldown",
            "Volta à calma",
            "Reduza gradualmente e normalize a respiração.",
            duration_sec=420,
            target_rpe=2,
            alternative="Movimento leve e respiração controlada.",
        )
        if sport == "mobility":
            return [
                warmup,
                step(
                    "mobility",
                    "Controle articular",
                    "Explore amplitude sem dor, com respiração lenta e controle.",
                    sets=3,
                    reps=8,
                    rest_sec=30,
                    target_rpe=3,
                ),
                step(
                    "mobility",
                    "Fluxo de mobilidade",
                    "Combine quadril, coluna torácica e ombros sem compensações.",
                    duration_sec=max(600, (estimated_min - 15) * 60),
                    target_rpe=3,
                ),
                cooldown,
            ]
        if sport == "strength" or session_type == "strength":
            return [
                warmup,
                step(
                    "strength",
                    "Agachamento",
                    (
                        "Escolha carga que preserve técnica e deixe 3 "
                        "repetições em reserva."
                    ),
                    sets=3,
                    reps=8,
                    rest_sec=75,
                    target_rpe=6,
                    alternative="Sente e levante de um banco.",
                ),
                step(
                    "strength",
                    "Remada unilateral",
                    "Mantenha o tronco firme e controle a carga em toda amplitude.",
                    sets=3,
                    reps=10,
                    rest_sec=60,
                    target_rpe=6,
                    alternative="Remada com elástico.",
                ),
                step(
                    "strength",
                    "Levantamento romeno",
                    "Leve o quadril para trás e preserve a coluna neutra.",
                    sets=3,
                    reps=8,
                    rest_sec=75,
                    target_rpe=6,
                    alternative="Ponte de glúteos no solo.",
                ),
                step(
                    "strength",
                    "Prancha",
                    "Mantenha costelas e pelve alinhadas sem prender a respiração.",
                    sets=3,
                    reps=30,
                    rest_sec=45,
                    target_rpe=6,
                    alternative="Prancha inclinada em um banco.",
                ),
                cooldown,
            ]
        if session_type == "quality":
            repeats = min(8, 5 + week_index // 2)
            return [
                warmup,
                step(
                    "drill",
                    f"Técnica de {label.lower()}",
                    f"Faça educativos leves com atenção à {movement} eficiente.",
                    duration_sec=300,
                    target_rpe=3,
                ),
                step(
                    "interval",
                    f"{repeats} intervalos de 2 minutos",
                    f"Aumente a intensidade da {movement} sem perder controle técnico.",
                    duration_sec=120,
                    target_pace_min_km=(
                        max(3.5, pace - 0.3) if sport == "running" else 0
                    ),
                    target_rpe=target_rpe,
                    sets=repeats,
                    reps=1,
                    rest_sec=90,
                    alternative=f"Reduza o ritmo da {movement} e preserve a técnica.",
                ),
                cooldown,
            ]
        main_duration = max(
            900 if session_type == "long" else 600,
            (estimated_min - 15) * 60,
        )
        is_recovery = session_type == "recovery"
        return [
            warmup,
            step(
                f"continuous_{sport}",
                (
                    f"{label} de endurance"
                    if session_type == "long"
                    else f"{label} muito leve"
                    if is_recovery
                    else f"{label} contínua"
                ),
                f"Mantenha esforço controlado e preserve a {movement} eficiente.",
                duration_sec=main_duration,
                distance_m=distance_m,
                target_pace_min_km=(
                    pace + (1.0 if is_recovery else 0.5) if sport == "running" else 0
                ),
                target_rpe=target_rpe,
                alternative=f"Reduza a intensidade da {movement} e mantenha o tempo.",
            ),
            cooldown,
        ]

    @staticmethod
    def _adaptation_decision(
        session: Mapping[str, Any],
        *,
        completion_pct: int,
        rpe: int,
        pain: int,
        readiness: int,
    ) -> Dict[str, Any]:
        if pain >= 7:
            return {
                "reason": "high_pain",
                "factor": 0.7,
                "window_start_days": 1,
                "window_end_days": 7,
                "message": (
                    "Intensidade pausada por 7 dias. Dor alta requer avaliação "
                    "profissional antes de retomar carga."
                ),
            }
        if rpe >= 9 or readiness < 45:
            return {
                "reason": "high_load",
                "factor": 0.8,
                "window_start_days": 1,
                "window_end_days": 7,
                "message": "Carga dos próximos 7 dias reduzida em 20%.",
            }
        if completion_pct < 70:
            return {
                "reason": "low_completion",
                "factor": 0.85,
                "window_start_days": 1,
                "window_end_days": 7,
                "message": "Carga dos próximos 7 dias reduzida em 15%.",
            }
        if completion_pct >= 90 and rpe <= 6:
            return {
                "reason": "ready_to_progress",
                "factor": 1.05,
                "window_start_days": 8,
                "window_end_days": 14,
                "message": "Próxima semana recebeu progressão conservadora de 5%.",
            }
        return {
            "reason": "maintain",
            "factor": 1.0,
            "window_start_days": 0,
            "window_end_days": 0,
            "message": "Plano mantido sem alteração de carga.",
        }

    def _adapt_future_sessions(
        self,
        user_id: str,
        completed: Mapping[str, Any],
        decision: Mapping[str, Any],
    ) -> List[Dict[str, Any]]:
        if float(decision["factor"]) == 1.0:
            return []
        completed_on = _parse_date(completed["scheduled_on"], name="scheduled_on")
        window_start = completed_on + timedelta(days=int(decision["window_start_days"]))
        window_end = completed_on + timedelta(days=int(decision["window_end_days"]))
        candidates = self._store.list_records(
            "training_sessions",
            user_id,
            filters=(
                Filter("plan_id", "=", completed["plan_id"]),
                Filter("status", "=", "planned"),
                Filter("scheduled_on", ">=", window_start.isoformat()),
                Filter("scheduled_on", "<=", window_end.isoformat()),
            ),
            order_by="scheduled_on",
            descending=False,
            limit=100,
        )
        adapted: List[Dict[str, Any]] = []
        factor = float(decision["factor"])
        high_pain = decision["reason"] == "high_pain"
        for session in candidates:
            recovery_week = int(session["week_index"]) % 4 == 0
            if decision["reason"] == "ready_to_progress" and recovery_week:
                continue
            patch = {
                "estimated_min": max(15, round(int(session["estimated_min"]) * factor)),
                "adaptation_note": str(decision["message"]),
            }
            if high_pain:
                patch["target_rpe"] = min(4, int(session["target_rpe"]))
                if session["session_type"] in {"quality", "long", "strength"}:
                    patch["session_type"] = "recovery"
                    patch["title"] = "Recuperação e mobilidade sem dor"
            self._store.update("training_sessions", user_id, str(session["id"]), patch)
            steps = self._rows_for_all_steps(user_id, str(session["id"]))
            for item in steps:
                step_patch = {
                    "duration_sec": max(0, round(int(item["duration_sec"]) * factor)),
                    "distance_m": max(0, round(int(item["distance_m"]) * factor)),
                }
                if high_pain:
                    step_patch["target_rpe"] = min(4, int(item["target_rpe"]))
                    step_patch["instructions"] = (
                        "Somente sem dor. Interrompa se os sintomas aumentarem."
                    )
                self._store.update(
                    "training_steps", user_id, str(item["id"]), step_patch
                )
            refreshed = self._store.get(
                "training_sessions", user_id, str(session["id"])
            )
            if refreshed is not None:
                adapted.append(refreshed)
        return adapted

    def _rows_for_all_steps(
        self, user_id: str, session_id: str
    ) -> List[Dict[str, Any]]:
        return self._store.list_records(
            "training_steps",
            user_id,
            filters=(Filter("session_id", "=", session_id),),
            order_by="step_index",
            descending=False,
            limit=100,
        )


__all__ = ["TrainingCoachError", "TrainingCoachService"]
