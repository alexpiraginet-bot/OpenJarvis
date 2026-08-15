"""Adaptive coach domain tests."""

from __future__ import annotations

from datetime import date

import pytest

from openjarvis.life.training import TrainingCoachError, TrainingCoachService

PROFILE = {
    "primary_goal": "5k",
    "target_distance_km": 5,
    "target_date": "2026-10-10",
    "level": "beginner",
    "weekly_days": 4,
    "available_weekdays": [1, 3, 5, 7],
    "session_minutes": 45,
    "current_weekly_km": 10,
    "longest_recent_run_km": 4,
    "equipment": ["bodyweight", "dumbbells"],
    "limitations": "",
}


def test_profile_is_validated_and_round_trips_lists(life, user) -> None:
    coach = TrainingCoachService(life.store)

    profile = coach.save_profile(user.id, PROFILE)

    assert profile["primary_goal"] == "5k"
    assert profile["available_weekdays"] == [1, 3, 5, 7]
    assert profile["equipment"] == ["bodyweight", "dumbbells"]
    with pytest.raises(TrainingCoachError, match="weekly_days"):
        coach.save_profile(user.id, {**PROFILE, "weekly_days": 1})


def test_plan_generation_creates_detailed_sessions_and_legacy_workouts(
    life, user
) -> None:
    coach = TrainingCoachService(life.store)
    coach.save_profile(user.id, PROFILE)

    plan = coach.generate_plan(user.id, start_on=date(2026, 8, 17), weeks=4)
    overview = coach.overview(user.id, anchor=date(2026, 8, 17))
    sessions = overview["sessions"]

    assert plan["status"] == "active"
    assert plan["weeks"] == 4
    assert len(sessions) == 16
    assert {session["session_type"] for session in sessions} >= {
        "easy",
        "quality",
        "strength",
        "long",
    }
    detail = coach.get_session(user.id, sessions[0]["id"])
    assert detail is not None
    assert len(detail["steps"]) >= 3
    assert detail["steps"][0]["kind"] == "warmup"
    assert detail["steps"][-1]["kind"] == "cooldown"
    assert life.store.get("workouts", user.id, sessions[0]["workout_id"])


@pytest.mark.parametrize(
    ("sport", "expected_title_fragment", "expected_instruction_fragment"),
    [
        ("canoeing", "Canoa", "remada"),
        ("cycling", "Pedal", "cadência"),
        ("swimming", "Natação", "braçada"),
        ("strength", "Força", "carga"),
    ],
)
def test_coach_prescribes_sport_specific_sessions(
    life,
    user,
    sport,
    expected_title_fragment,
    expected_instruction_fragment,
) -> None:
    coach = TrainingCoachService(life.store)
    profile = coach.save_profile(
        user.id,
        {
            **PROFILE,
            "primary_sport": sport,
            "secondary_sports": ["strength"] if sport != "strength" else ["mobility"],
        },
    )

    coach.generate_plan(user.id, start_on=date(2026, 8, 17), weeks=4)
    sessions = coach.overview(user.id, anchor=date(2026, 8, 17))["sessions"]
    primary_session = next(session for session in sessions if session["sport"] == sport)
    detail = coach.get_session(user.id, primary_session["id"])

    assert profile["primary_sport"] == sport
    assert sport in {session["sport"] for session in sessions}
    assert (
        "strength" in {session["sport"] for session in sessions} or sport == "strength"
    )
    assert expected_title_fragment in primary_session["title"]
    assert detail is not None
    assert (
        expected_instruction_fragment
        in " ".join(step["instructions"] for step in detail["steps"]).lower()
    )


def test_session_detail_is_tenant_scoped(life, user, other_user) -> None:
    coach = TrainingCoachService(life.store)
    coach.save_profile(user.id, PROFILE)
    coach.generate_plan(user.id, start_on=date(2026, 8, 17), weeks=4)
    session_id = coach.overview(user.id)["sessions"][0]["id"]

    assert coach.get_session(other_user.id, session_id) is None


def test_readiness_checkin_stops_training_on_high_pain(life, user) -> None:
    coach = TrainingCoachService(life.store)
    coach.save_profile(user.id, PROFILE)
    coach.generate_plan(user.id, start_on=date(2026, 8, 17), weeks=4)
    session_id = coach.overview(user.id)["sessions"][0]["id"]

    checkin = coach.check_in(
        user.id,
        session_id,
        sleep_quality=7,
        soreness=4,
        stress=3,
        motivation=8,
        pain=8,
        notes="Dor aguda no joelho",
    )

    assert checkin["readiness_score"] < 60
    assert checkin["recommendation"] == "stop_and_seek_care"


def test_high_pain_feedback_completes_once_and_removes_near_term_intensity(
    life, user
) -> None:
    coach = TrainingCoachService(life.store)
    coach.save_profile(user.id, PROFILE)
    coach.generate_plan(user.id, start_on=date(2026, 8, 17), weeks=4)
    sessions = coach.overview(user.id)["sessions"]
    quality = next(
        session for session in sessions if session["session_type"] == "quality"
    )

    first = coach.complete_session(
        user.id,
        quality["id"],
        completion_pct=60,
        actual_duration_min=24,
        rpe=9,
        energy=3,
        pain=8,
        notes="Dor aguda após os intervalos",
    )
    replay = coach.complete_session(
        user.id,
        quality["id"],
        completion_pct=60,
        actual_duration_min=24,
        rpe=9,
        energy=3,
        pain=8,
        notes="Dor aguda após os intervalos",
    )

    completed = coach.get_session(user.id, quality["id"])
    upcoming = coach.overview(user.id)["sessions"]
    adapted = [
        session
        for session in upcoming
        if session["adaptation_note"]
        and session["scheduled_on"] > quality["scheduled_on"]
    ]
    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert completed is not None
    assert completed["session"]["status"] == "completed"
    assert completed["workout"]["completed_at"]
    assert first["adaptation"]["reason"] == "high_pain"
    assert adapted
    assert all(session["target_rpe"] <= 4 for session in adapted)
    assert life.store.count("training_feedback", user.id) == 1


def test_missing_profile_cannot_generate_a_plan(life, user) -> None:
    coach = TrainingCoachService(life.store)

    with pytest.raises(TrainingCoachError, match="profile"):
        coach.generate_plan(user.id, start_on=date(2026, 8, 17), weeks=4)
